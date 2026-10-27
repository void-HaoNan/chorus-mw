"""分离子进程调用 + RVC 变声（走 WebUI 的 Gradio 接口）。

分离/去混响、变声都放在独立子进程里跑（见 sep_worker.py / rvc_worker.py）：
RVC WebUI 是 Gradio 3.14 的 ws 协议，本中间层是 Gradio 4 的 sse 协议，
对应两个互不兼容的 gradio_client 版本，只能靠进程隔离。
"""
import json
import os
import subprocess
import time
import urllib.request

import config


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
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
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
    "提主旋律": "karaoke",
    "去伴奏（becruily deux）": "sep_deux",
}
DEREVERB_TASKS = {
    "去混响": "dereverb",
    "去混响（激进）": "dereverb_aggr",
    "去混响（anvuew 22.505）": "dereverb_anvuew",
}


def separate(song_path, vocal_dir, inst_dir, model_label=None):
    """整首分离，返回 (人声, 伴奏)。"""
    label = model_label or config.SEP_MODEL
    task = SEP_TASKS.get(label, "sep")
    _worker(task, song_path, vocal_dir, inst_dir)
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
    _worker(task, vocal_path, out_vocal_dir, out_rest_dir)
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
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
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
    """调 RVC 单次推理，返回音频路径。"""
    ensure_rvc()
    audio = _convert_worker(
        vocal_path, model_name, key_shift, f0_method, index_rate,
        rms_mix_rate, protect, speaker_id, resolve_index(model_name),
    )
    if not os.path.exists(str(audio)):
        raise RuntimeError("RVC 推理返回的音频不存在：%s" % audio)
    return str(audio)


def list_models():
    directory = config.WEIGHTS_DIR
    if not os.path.isdir(directory):
        return []
    return sorted(
        name for name in os.listdir(directory)
        if name.lower().endswith(".pth")
    )
