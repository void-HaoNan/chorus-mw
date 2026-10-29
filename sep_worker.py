"""独立分离/去混响工作进程。

为什么不走 RVC WebUI 的 /pymss_separate：
那是个 generator 接口，gradio_client 一断开 Gradio 就取消生成器，文件还没写完就没了，
WebUI 里的任务锁还会卡成「已有任务正在运行」。这里每次起一个独立子进程跑到底，
退出码即结果。

两个坑写在代码里：
1. 导入 tools.pymss_webui 会连带构造 configs.config.Config()，它要解析 sys.argv，
   所以必须先把自己的参数存好、再把 sys.argv 清掉，否则它会报「unrecognized arguments」。
2. 任务名只用 ASCII 传参，中文标签在这个进程里映射，避免命令行编码问题。
"""
import argparse
import os
import sys

# 任务名 → RVC WebUI 下拉框里的中文标签（必须一字不差）
TASKS = {
    "sep": "去伴奏",
    "sep_aggr": "去伴奏（激进）",
    "dereverb": "去混响",
    "dereverb_aggr": "去混响（激进）",
    "karaoke": "提主旋律",
    # 2026-10-27 换链：Studio 那套模型，label 必须和 pymss_webui.py 的 ModelSpec 一致
    "sep_deux": "去伴奏（becruily deux）",
    "dereverb_anvuew": "去混响（anvuew 22.505）",
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", required=True, help="RVC 项目根目录")
    parser.add_argument("--task", required=True, choices=sorted(TASKS))
    parser.add_argument("--input", required=True)
    parser.add_argument("--vocal", required=True)
    parser.add_argument("--inst", required=True)
    parser.add_argument("--fmt", default="wav")
    args = parser.parse_args()

    app = os.path.abspath(args.app)
    # RVC 便携版 python 的 sys.path[0] 是 runtime 目录，不是脚本目录，
    # 所以必须把自己的目录显式加进去，否则 import sep_guard 会失败。
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    label = TASKS[args.task]

    # 关键：清掉 argv，避免被 RVC 的 Config() 当成自己的参数
    sys.argv = [sys.argv[0]]
    os.chdir(app)
    sys.path.insert(0, app)

    from tools.pymss_webui import pymss_separate  # noqa: E402

    from sep_guard import audio_files, failure_reason  # noqa: E402

    os.makedirs(args.vocal, exist_ok=True)
    os.makedirs(args.inst, exist_ok=True)

    before = audio_files(args.vocal) | audio_files(args.inst)
    last = ""
    for info in pymss_separate(
        label, "", args.vocal, [args.input], args.inst, args.fmt
    ):
        last = info or last
        lines = str(last).strip().splitlines()
        if lines:
            print(lines[-1][:200], flush=True)

    # pymss_separate 不抛异常，失败也会走到这里（0.8.1）
    produced = (audio_files(args.vocal) | audio_files(args.inst)) - before
    if not produced:
        print("WORKER_FAILED: %s" % failure_reason(last, args.vocal, args.inst), flush=True)
        sys.exit(1)

    print("WORKER_DONE", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        import traceback

        traceback.print_exc()
        print("WORKER_FAILED: %s" % error, flush=True)
        sys.exit(1)
