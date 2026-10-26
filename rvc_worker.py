"""在 RVC 运行时里跑一次变声：先 /infer_change_voice 预热模型，再 /infer_convert。

为什么要单独一个进程：RVC WebUI 是 Gradio 3.14（ws 队列协议），
新版中间层跑 Gradio 4（sse 协议）。gradio_client 2.x 只会 sse，遇到 ws 直接报
"Unknown protocol: ws"；gradio_client 0.2.9 只会 ws。两个版本不能同进程共存，
所以把「跟 WebUI 说话」这件事扔给这个子进程，父进程专心做 sse 服务端。

用法（父进程 rvc.convert 会拼好这些参数）：
    python rvc_worker.py --port 7897 --input a.wav --model Yuuka.pth --shift 2
成功时 stdout 打印一行：WORKER_RESULT {"audio": "..."}
"""
import argparse
import json
import os
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=7897)
    parser.add_argument("--input", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--shift", type=int, default=0)
    parser.add_argument("--f0", default="rmvpe")
    parser.add_argument("--index-rate", type=float, default=0.75)
    parser.add_argument("--rms", type=float, default=0.35)
    parser.add_argument("--protect", type=float, default=0.33)
    parser.add_argument("--speaker", type=int, default=0)
    parser.add_argument("--index", default="")
    args = parser.parse_args()

    # libs 里是 gradio_client 0.2.9（唯一能跟 Gradio 3.14 的 ws 协议对话的版本）
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "libs"))
    from gradio_client import Client

    try:
        client = Client("http://127.0.0.1:%d/" % args.port, verbose=False)
        # 必须先切一次音色，否则 WebUI 里的 pipeline 是 None
        loaded = client.predict(
            args.model,
            float(args.protect),
            float(args.protect),
            api_name="/infer_change_voice",
        )
        index_from_model = ""
        if isinstance(loaded, (list, tuple)) and len(loaded) >= 4:
            index_from_model = loaded[3] or ""
        index_path = index_from_model or args.index or ""

        _text, audio = client.predict(
            int(args.speaker),
            args.input,
            int(args.shift),
            args.f0,
            index_path,
            float(args.index_rate),
            0,
            float(args.rms),
            float(args.protect),
            api_name="/infer_convert",
        )
        if isinstance(audio, dict):
            audio = audio.get("name") or audio.get("path")
        if not audio or not os.path.exists(str(audio)):
            raise RuntimeError("RVC 推理没有返回音频：%r" % (audio,))
        print("WORKER_RESULT " + json.dumps({"audio": str(audio)}, ensure_ascii=False), flush=True)
    except Exception as error:  # noqa: BLE001
        print("WORKER_FAILED %r" % (error,), flush=True)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
