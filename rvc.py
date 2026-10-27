"""分离 / 去混响 / 变声的调度：优先常驻 worker，失败自动退回「每次起进程」。

为什么这些事不能在本进程里做：RVC WebUI 是 Gradio 3.14 的 ws 协议，本中间层是
Gradio 4 的 sse 协议，对应两个互不兼容的 gradio_client 版本，只能靠进程隔离。

0.7.0 起：
- 常驻进程 ``resident_worker.py`` 由 app.py 启动时在后台线程里拉起，通过
  stdin/stdout 收发行 JSON。工具导入、CUDA 初始化只付一次，省掉每次 3~5 秒。
- 常驻进程没起来 / 中途崩了 / 超时，自动退回旧的 ``sep_worker.py`` /
  ``rvc_worker.py``（每次起新进程），日志里记 ``worker=fallback``。
"""
import json
import os
import queue
import subprocess
import threading
import time
import urllib.request

import config

# Windows 下不要给子进程开控制台窗口，否则每跑一步都闪一下黑框
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# 显存不足的原文特征：PyTorch 的异常类型名、CUDA 的报错、以及我们自己拼的中文
_OOM_MARKERS = (
    "outofmemoryerror",
    "cuda out of memory",
    "cuda error: out of memory",
    "cuda oom",
    "显存不足",
)


def _is_oom(error):
    text = str(error or "").lower()
    return any(marker in text for marker in _OOM_MARKERS)


def gpu_free_mb(index=0):
    """显卡空闲显存（MB）。查不到（没装 nvidia-smi、没独显、驱动坏了）返回 None。"""
    try:
        proc = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.free",
                "--format=csv,noheader,nounits",
                "-i",
                str(index),
            ],
            capture_output=True,
            timeout=6,
            creationflags=_NO_WINDOW,
        )
        if proc.returncode != 0:
            return None
        first = proc.stdout.decode("ascii", "ignore").strip().splitlines()[0]
        return int(first.strip())
    except Exception:
        return None


def ensure_vram(notify=None, need_mb=None, wait_seconds=None):
    """分离前看一眼显存，不够就等一会儿（0.8.1）。

    返回 (free_mb, waited_seconds)。**不抛异常**：等不到也照样放行，真失败时由
    ``_run_task`` 给出明确原因——查不到显存（笔记本切核显、驱动异常）不该把整首歌挡住。
    """
    need = int(config.VRAM_MIN_FREE_MB if need_mb is None else need_mb)
    budget = float(config.VRAM_WAIT_SECONDS if wait_seconds is None else wait_seconds)
    free = gpu_free_mb()
    if free is None:
        print("[chorus-mw] vram=unknown（nvidia-smi 查不到，跳过预检）", flush=True)
        return None, 0.0
    if free >= need:
        print("[chorus-mw] vram free=%dMB ok（阈值 %dMB）" % (free, need), flush=True)
        return free, 0.0

    print(
        "[chorus-mw] vram free=%dMB < 阈值 %dMB，最多等 %.0fs"
        % (free, need, budget),
        flush=True,
    )
    if notify is not None:
        try:
            notify("显存只剩 %dMB，等别的程序让一让（最多 %.0f 秒）" % (free, budget))
        except Exception:
            pass

    started = time.time()
    while time.time() - started < budget:
        time.sleep(config.VRAM_POLL_SECONDS)
        free = gpu_free_mb()
        if free is None or free >= need:
            break

    waited = round(time.time() - started, 1)
    print(
        "[chorus-mw] vram free=%sMB（等了 %.1fs%s）"
        % (free, waited, "，仍偏低，继续尝试" if (free is not None and free < need) else ""),
        flush=True,
    )
    return free, waited



def _port_open(port):
    try:
        urllib.request.urlopen("http://127.0.0.1:%d/" % port, timeout=2)
        return True
    except Exception:
        return False


def ensure_rvc():
    """RVC WebUI 没起来就拉起来。返回端口。"""
    if _port_open(config.RVC_PORT):
        return config.RVC_PORT
    creation = 0
    if os.name == "nt":
        creation = subprocess.CREATE_NEW_PROCESS_GROUP | 0x00000008
    subprocess.Popen(
        [config.RVC_PYTHON, "webui.py", "--port", str(config.RVC_PORT), "--noautoopen"],
        cwd=config.RVC_APP,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creation,
    )
    deadline = time.time() + config.RVC_BOOT_TIMEOUT
    while time.time() < deadline:
        if _port_open(config.RVC_PORT):
            return config.RVC_PORT
        time.sleep(2)
    raise RuntimeError("RVC WebUI 在 %d 秒内没起来" % config.RVC_BOOT_TIMEOUT)


# ---------------- 常驻 worker ----------------

class _ResidentWorker:
    """一个进程吃掉所有分离/去混响/变声请求，串行处理（GPU 本来也不能并行）。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._proc = None
        self._queue = None
        self._stderr = None
        self._seq = 0
        self._failures = 0
        self._disabled_until = 0.0
        self._last_error = ""
        self.stats = {"requests": 0, "ok": 0, "fallback": 0, "starts": 0}

    # -------- 生命周期 --------
    def available(self):
        return bool(config.RESIDENT_ENABLED) and time.time() >= self._disabled_until

    def _spawn_locked(self):
        if self._proc is not None and self._proc.poll() is None:
            return
        self._close_locked()
        os.makedirs(config.WORK_DIR, exist_ok=True)
        log = open(os.path.join(config.WORK_DIR, "resident.log"), "a", encoding="utf-8", errors="replace")
        try:
            self._proc = subprocess.Popen(
                [config.RVC_PYTHON, "-X", "utf8", os.path.join(config.HOME, "resident_worker.py")],
                cwd=config.HOME,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=log,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=_NO_WINDOW,
            )
        except Exception:
            log.close()
            raise
        self._stderr = log
        self._queue = queue.Queue()
        threading.Thread(target=self._reader, args=(self._proc, self._queue),
                         daemon=True, name="resident-reader").start()
        self.stats["starts"] += 1
        print("[chorus-mw] resident worker started pid=%d" % self._proc.pid, flush=True)

    def _reader(self, proc, q):
        try:
            for line in proc.stdout:
                q.put(line)
        except Exception:
            pass
        finally:
            q.put(None)

    def _close_locked(self):
        proc, self._proc = self._proc, None
        if proc is not None:
            try:
                if proc.poll() is None:
                    proc.kill()
            except Exception:
                pass
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
        if self._stderr is not None:
            try:
                self._stderr.close()
            except Exception:
                pass
            self._stderr = None
        self._queue = None

    # -------- 请求 --------
    def request(self, payload, timeout):
        if not self.available():
            raise RuntimeError("常驻 worker 已停用（上次失败：%s）" % (self._last_error or "未知"))
        with self._lock:
            self._spawn_locked()
            self._seq += 1
            req_id = self._seq
            body = dict(payload)
            body["id"] = req_id
            self.stats["requests"] += 1
            try:
                self._proc.stdin.write(json.dumps(body, ensure_ascii=False) + "\n")
                self._proc.stdin.flush()
                deadline = time.time() + float(timeout)
                while True:
                    left = deadline - time.time()
                    if left <= 0:
                        raise RuntimeError("等待常驻 worker 超时（%ss）" % timeout)
                    try:
                        line = self._queue.get(timeout=left)
                    except queue.Empty:
                        raise RuntimeError("等待常驻 worker 超时（%ss）" % timeout)
                    if line is None:
                        raise RuntimeError("常驻 worker 已退出")
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except ValueError:
                        continue  # 不是协议行，忽略
                    if data.get("id") != req_id:
                        continue
                    if not data.get("ok"):
                        raise RuntimeError(str(data.get("error") or "常驻 worker 返回失败"))
                    self._failures = 0
                    self.stats["ok"] += 1
                    return data
            except Exception as error:
                self._failures += 1
                self._last_error = str(error)
                if self._failures >= 2:
                    self._disabled_until = time.time() + 120
                self._close_locked()
                raise

    def warmup(self):
        """后台把进程拉起来并付掉 import 的钱，不阻塞服务启动。"""
        def _run():
            try:
                self.request({"op": "warmup", "app": config.RVC_APP}, timeout=300)
                print("[chorus-mw] resident worker ready（导入已完成）", flush=True)
            except Exception as error:
                print("[chorus-mw] resident worker warmup failed: %s" % error, flush=True)

        threading.Thread(target=_run, daemon=True, name="resident-warmup").start()

    def status(self):
        return {
            "enabled": bool(config.RESIDENT_ENABLED),
            "available": self.available(),
            "pid": self._proc.pid if self._proc is not None and self._proc.poll() is None else None,
            "last_error": self._last_error,
            **self.stats,
        }


RESIDENT = _ResidentWorker()


def warmup_resident():
    """app.py 启动时调一次。"""
    RESIDENT.warmup()


def resident_status():
    return RESIDENT.status()


# ---------------- 旧路：每次起一个新进程（兜底） ----------------

def _worker(task, input_path, vocal_dir, inst_dir, timeout=1800):
    os.makedirs(vocal_dir, exist_ok=True)
    os.makedirs(inst_dir, exist_ok=True)
    cmd = [
        config.RVC_PYTHON,
        os.path.join(config.HOME, "sep_worker.py"),
        "--app", config.RVC_APP,
        "--task", task,
        "--input", input_path,
        "--vocal", vocal_dir,
        "--inst", inst_dir,
        "--fmt", "wav",
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=_NO_WINDOW)
    out = proc.stdout.decode("utf-8", "ignore")
    err = proc.stderr.decode("utf-8", "ignore")
    if proc.returncode != 0 or "WORKER_FAILED" in out:
        tail = (out[-600:] + "\n" + err[-600:]).strip()
        raise RuntimeError("分离子进程失败（%s）：\n%s" % (task, tail))
    return out


def _pick_newest(directory, keywords):
    best, best_mtime = None, -1.0
    if not os.path.isdir(directory):
        return None
    for name in os.listdir(directory):
        if not name.lower().endswith((".wav", ".flac")):
            continue
        if keywords and not any(k in name.lower() for k in keywords):
            continue
        path = os.path.join(directory, name)
        mtime = os.path.getmtime(path)
        if mtime > best_mtime:
            best, best_mtime = path, mtime
    return best


SEP_TASKS = {
    "去伴奏": "sep",
    "去伴奏（激进）": "sep_aggr",
    "去伴奏（becruily deux）": "sep_deux",
    "提主旋律": "karaoke",
}
DEREVERB_TASKS = {
    "去混响": "dereverb",
    "去混响（激进）": "dereverb_aggr",
    "去混响（anvuew 22.505）": "dereverb_anvuew",
}


TASK_LABELS = {
    "sep": "去伴奏",
    "sep_aggr": "去伴奏（激进）",
    "sep_deux": "去伴奏（becruily deux）",
    "karaoke": "提主旋律",
    "dereverb": "去混响",
    "dereverb_aggr": "去混响（激进）",
    "dereverb_anvuew": "去混响（anvuew 22.505）",
}


def _friendly_error(task, error):
    """把底层异常翻成「用户看得懂 + 知道下一步怎么办」的一句话。"""
    label = TASK_LABELS.get(task, task)
    text = str(error or "").strip()
    if _is_oom(text):
        free = gpu_free_mb()
        free_text = "现在空闲 %dMB" % free if free is not None else "查不到空闲显存"
        return RuntimeError(
            "显存不足（%s 这一步 OOM，%s）。PyMSS 分离峰值要 1.6GB 左右，8G 卡上被 "
            "OBS / 直播伴侣 / NVIDIA Broadcast / GPT-SoVITS 占住就会顶到 7.4G 以上。"
            "关掉这些程序，或者先停 GPT-SoVITS（占 2GB）再重试。"
            "原始错误：%s" % (label, free_text, text[:400])
        )
    return RuntimeError("%s失败：%s" % (label, text[:600]))


def _attempt(mode, task, input_path, vocal_dir, inst_dir, timeout):
    if mode == "resident":
        RESIDENT.request(
            {
                "op": task,
                "app": config.RVC_APP,
                "input": input_path,
                "vocal": vocal_dir,
                "inst": inst_dir,
                "fmt": "wav",
            },
            timeout=timeout,
        )
    else:
        _worker(task, input_path, vocal_dir, inst_dir, timeout=timeout)
    print("[chorus-mw] worker=%s op=%s" % (mode, task), flush=True)


def _run_task(task, input_path, vocal_dir, inst_dir, timeout=1800):
    """先试常驻 worker；失败（没起来 / 崩了 / 超时 / 显存不足）就退回每次起进程。

    0.8.1 起多一条规矩：**显存不足会等几秒、换一个全新进程重试一次**。
    常驻进程 OOM 之后 CUDA 上下文有可能已经坏了，同一个进程再试也是白搭；
    新进程有独立的上下文，别的程序这时候让出一点显存就能过。
    """
    plans = ["resident", "process"] if RESIDENT.available() else ["process"]
    last_error = None

    for index, mode in enumerate(plans):
        try:
            _attempt(mode, task, input_path, vocal_dir, inst_dir, timeout)
            return
        except Exception as error:  # noqa: BLE001
            last_error = error
            print(
                "[chorus-mw] worker=%s failed op=%s reason=%s"
                % (mode, task, str(error).replace("\n", " | ")[:400]),
                flush=True,
            )
            if not _is_oom(error):
                continue  # 普通失败：照老规矩换下一种模式

            if index + 1 >= len(plans):
                break  # 已经没有下一次了

            free = gpu_free_mb()
            wait = config.OOM_RETRY_WAIT_SECONDS
            print(
                "[chorus-mw] oom op=%s free=%sMB，%.0fs 后换新进程重试一次"
                % (task, free, wait),
                flush=True,
            )
            plans[index + 1] = "process"  # 重试一律用新进程，别赌常驻进程的上下文
            time.sleep(wait)

    raise _friendly_error(task, last_error)



def separate(song_path, vocal_dir, inst_dir, model_label=None):
    """分离，返回 (人声, 伴奏)。整首或一小段都走这里。"""
    label = model_label or config.SEP_MODEL
    task = SEP_TASKS.get(label, "sep")
    _run_task(task, song_path, vocal_dir, inst_dir)
    vocal = _pick_newest(vocal_dir, ("vocals", "noreverb"))
    inst = _pick_newest(inst_dir, ("instrumental", "other"))
    if not vocal:
        raise RuntimeError("分离没有产出人声：%s" % vocal_dir)
    if not inst:
        raise RuntimeError("分离没有产出伴奏：%s" % inst_dir)
    return vocal, inst


def dereverb(vocal_path, out_vocal_dir, out_rest_dir, model_label=None):
    """去混响，返回 *_noreverb 文件。"""
    label = model_label or config.DERERVERB_MODEL
    task = DEREVERB_TASKS.get(label, "dereverb")
    _run_task(task, vocal_path, out_vocal_dir, out_rest_dir)
    path = _pick_newest(out_vocal_dir, ("noreverb",))
    if not path:
        raise RuntimeError("去混响没有产出 noreverb：%s" % out_vocal_dir)
    return path


def resolve_index(model_name):
    stem = os.path.splitext(os.path.basename(model_name))[0]
    for root in config.INDEX_DIRS:
        if not os.path.isdir(root):
            continue
        for name in os.listdir(root):
            if name.lower().endswith(".index") and stem.lower() in name.lower():
                return os.path.join(root, name)
    return ""


def _convert_worker(vocal_path, model_name, key_shift, f0_method, index_rate,
                    rms_mix_rate, protect, speaker_id, index_path, timeout=900):
    cmd = [
        config.RVC_PYTHON,
        os.path.join(config.HOME, "rvc_worker.py"),
        "--port", str(config.RVC_PORT),
        "--input", vocal_path,
        "--model", model_name,
        "--shift", str(int(key_shift)),
        "--f0", str(f0_method),
        "--index-rate", "%.4f" % float(index_rate),
        "--rms", "%.4f" % float(rms_mix_rate),
        "--protect", "%.4f" % float(protect),
        "--speaker", str(int(speaker_id)),
        "--index", index_path or "",
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=_NO_WINDOW)
    out = proc.stdout.decode("utf-8", "ignore")
    err = proc.stderr.decode("utf-8", "ignore")
    if proc.returncode != 0 or "WORKER_FAILED" in out:
        tail = (out[-800:] + "\n" + err[-800:]).strip()
        raise RuntimeError("变声子进程失败：\n%s" % tail)
    line = ""
    for candidate in out.splitlines():
        if candidate.startswith("WORKER_RESULT "):
            line = candidate
    if not line:
        raise RuntimeError("变声子进程没有返回结果：\n%s" % out[-800:])
    return json.loads(line[len("WORKER_RESULT "):])["audio"]


def convert(vocal_path, model_name, key_shift, f0_method, index_rate,
            rms_mix_rate, protect, speaker_id=0):
    """调 RVC 单次推理，返回音频路径。先常驻 worker，失败退回子进程。"""
    ensure_rvc()
    index_path = resolve_index(model_name)
    if RESIDENT.available():
        try:
            data = RESIDENT.request(
                {
                    "op": "convert",
                    "port": config.RVC_PORT,
                    "input": vocal_path,
                    "model": model_name,
                    "shift": int(key_shift),
                    "f0": str(f0_method),
                    "index_rate": float(index_rate),
                    "rms": float(rms_mix_rate),
                    "protect": float(protect),
                    "speaker": int(speaker_id),
                    "index": index_path or "",
                },
                timeout=1800,
            )
            audio = str(data.get("audio") or "")
            if not audio or not os.path.exists(audio):
                raise RuntimeError("RVC 推理返回的音频不存在：%s" % audio)
            print("[chorus-mw] worker=resident op=convert", flush=True)
            return audio
        except Exception as error:
            print("[chorus-mw] worker=fallback op=convert reason=%s" % error, flush=True)
    audio = _convert_worker(
        vocal_path, model_name, key_shift, f0_method, index_rate,
        rms_mix_rate, protect, speaker_id, index_path,
    )
    if not os.path.exists(str(audio)):
        raise RuntimeError("RVC 推理返回的音频不存在：%s" % audio)
    print("[chorus-mw] worker=process op=convert", flush=True)
    return str(audio)


def list_models():
    directory = config.WEIGHTS_DIR
    if not os.path.isdir(directory):
        return []
    return sorted(
        name for name in os.listdir(directory)
        if name.lower().endswith(".pth")
    )
