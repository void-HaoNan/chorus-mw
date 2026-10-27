"""常驻工作进程：分离 / 去混响 / 变声都在这个进程里跑，不再每次都起新进程。

协议：stdin 一行一个 JSON 请求，stdout 一行一个 JSON 响应，其余输出一律走 stderr。
所以本文件里**绝对不能**往 stdout 打印任何东西（已经把它重定向到 stderr 了）。

为什么要常驻：以前每次调用都起一个新进程，而每个新进程都要重新
``import tools.pymss_webui``（连带 torch / CUDA 初始化），实测每次 3~5 秒，
一首歌三步就是 10~15 秒纯启动开销。常驻进程只付一次。

支持的操作（op）：
    ping / warmup
    sep / sep_aggr / sep_deux / karaoke          —— 分离
    dereverb / dereverb_aggr / dereverb_anvuew   —— 去混响
    convert                                      —— RVC 变声

崩溃/超时由父进程（rvc.py）负责杀掉重来，并自动退回旧的「每次起进程」。
"""
import json
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
# libs 里是 gradio_client 0.2.9，只能跟 RVC WebUI 的 Gradio 3.14（ws）说话
sys.path.insert(0, os.path.join(HERE, "libs"))
sys.path.insert(0, HERE)

from sep_guard import audio_files, failure_reason  # noqa: E402

# 协议通道单独留出来，之后所有 print / 第三方输出都落到 stderr
_REAL_STDOUT = sys.stdout
sys.stdout = sys.stderr

# op 名 → RVC WebUI 下拉框里的中文标签（必须一字不差）
TASKS = {
    "sep": "去伴奏",
    "sep_aggr": "去伴奏（激进）",
    "sep_deux": "去伴奏（becruily deux）",
    "karaoke": "提主旋律",
    "dereverb": "去混响",
    "dereverb_aggr": "去混响（激进）",
    "dereverb_anvuew": "去混响（anvuew 22.505）",
}

_APP_READY = ""
_CLIENTS = {}


def _prepare_app(app):
    """第一次用到 RVC 之前把工作目录和 argv 收拾干净。

    tools.pymss_webui 在 import 时会构造 configs.config.Config()，它要解析
    sys.argv，不清掉就会报「unrecognized arguments」。
    """
    global _APP_READY
    app = os.path.abspath(app)
    if _APP_READY:
        return
    sys.argv = [sys.argv[0]]
    os.chdir(app)
    sys.path.insert(0, app)
    _APP_READY = app


def _pymss(label, input_path, vocal_dir, inst_dir, fmt):
    # import 放在 _prepare_app 之后：tools.pymss_webui 依赖当前工作目录
    from tools.pymss_webui import pymss_separate

    os.makedirs(vocal_dir, exist_ok=True)
    os.makedirs(inst_dir, exist_ok=True)
    before = audio_files(vocal_dir) | audio_files(inst_dir)
    last = ""
    for info in pymss_separate(label, "", vocal_dir, [input_path], inst_dir, fmt):
        last = info or last

    # pymss_separate 把异常吞成文字，只能靠「有没有新增产物」判断成败（0.8.1）
    produced = (audio_files(vocal_dir) | audio_files(inst_dir)) - before
    if not produced:
        raise RuntimeError(failure_reason(last, vocal_dir, inst_dir))

    return {"message": str(last).strip().splitlines()[-1][:200] if str(last).strip() else ""}


def _client(port):
    from gradio_client import Client

    client = _CLIENTS.get(port)
    if client is None:
        client = Client("http://127.0.0.1:%d/" % port, verbose=False)
        _CLIENTS[port] = client
    return client


def _convert(req):
    client = _client(int(req["port"]))
    model = req["model"]
    protect = float(req.get("protect", 0.33))
    # 必须先切一次音色，否则 WebUI 里的 pipeline 是 None
    loaded = client.predict(model, protect, protect, api_name="/infer_change_voice")
    index_from_model = ""
    if isinstance(loaded, (list, tuple)) and len(loaded) >= 4:
        index_from_model = loaded[3] or ""
    index_path = index_from_model or str(req.get("index") or "")

    _text, audio = client.predict(
        int(req.get("speaker", 0)),
        req["input"],
        int(req.get("shift", 0)),
        str(req.get("f0") or "rmvpe"),
        index_path,
        float(req.get("index_rate", 0.75)),
        0,
        float(req.get("rms", 0.35)),
        protect,
        api_name="/infer_convert",
    )
    if isinstance(audio, dict):
        audio = audio.get("name") or audio.get("path")
    if not audio or not os.path.exists(str(audio)):
        raise RuntimeError("RVC 推理没有返回音频：%r" % (audio,))
    return {"audio": str(audio)}


def handle(req):
    op = str(req.get("op") or "").strip()
    if op == "ping":
        return {"ok": True, "pid": os.getpid()}
    if op == "warmup":
        _prepare_app(req["app"])
        import tools.pymss_webui  # noqa: F401  提前付掉 3~5 秒的导入
        return {"ok": True, "pid": os.getpid(), "warm": True}
    if op in TASKS:
        _prepare_app(req["app"])
        result = _pymss(TASKS[op], req["input"], req["vocal"], req["inst"], req.get("fmt", "wav"))
        return dict(result, ok=True)
    if op == "convert":
        return dict(_convert(req), ok=True)
    raise ValueError("不认识的 op：%s" % op)


def main():
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        req_id = None
        try:
            req = json.loads(raw)
            req_id = req.get("id")
            payload = handle(req)
        except Exception as error:  # noqa: BLE001
            traceback.print_exc()
            payload = {
                "ok": False,
                "error": "%s: %s" % (type(error).__name__, error),
                "trace": traceback.format_exc()[-1200:],
            }
        payload["id"] = req_id
        _REAL_STDOUT.write(json.dumps(payload, ensure_ascii=False) + "\n")
        _REAL_STDOUT.flush()


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, BrokenPipeError):
        pass
