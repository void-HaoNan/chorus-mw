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
    label = TASKS[args.task]

    # 关键：清掉 argv，避免被 RVC 的 Config() 当成自己的参数
    sys.argv = [sys.argv[0]]
    os.chdir(app)
    sys.path.insert(0, app)

    from tools.pymss_webui import pymss_separate  # noqa: E402

    os.makedirs(args.vocal, exist_ok=True)
    os.makedirs(args.inst, exist_ok=True)

    last = ""
    for info in pymss_separate(
        label, "", args.vocal, [args.input], args.inst, args.fmt
    ):
        last = info or last
        lines = str(last).strip().splitlines()
        if lines:
            print(lines[-1][:200], flush=True)
    print("WORKER_DONE", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        import traceback

        traceback.print_exc()
        print("WORKER_FAILED: %s" % error, flush=True)
        sys.exit(1)
