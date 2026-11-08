"""检查「插件发给中间层的参数」和「中间层声明的参数」对不对得上。

插件更新后先跑这个，比翻日志快：

    python tools/check_plugin_compat.py
    python tools/check_plugin_compat.py --plugin-main "C:\\Users\\me\\data\\plugins\\astrbot_plugin_matsuko_cover\\main.py"

判读规则：
  ✔ 两边都有            → 正常
  ✘ 插件发、中间层没有  → 一定会报 `Parameter xxx is not a valid key-word argument`，
                          得在 app.py 的 convert() 里补形参 + 在 _hidden_inputs() 里补一个同名 label 的组件
  · 中间层有、插件不发  → 没事，走默认值
"""
import argparse
import json
import os
import re
import sys
import urllib.request

DEFAULT_URL = "http://127.0.0.1:3333/"


def default_plugin_main() -> str:
    """插件 main.py 的位置：CHORUS_PLUGIN_MAIN > ASTRBOT_DATA_DIR > %USERPROFILE%\\data。

    以前这里写死了某台机器的 ``C:\\Users\\...\\astrbot_plugin_matsuko_cover\\main.py``，
    换机器必然找不到。现在只按环境变量和用户目录推，找不到就让人用 --plugin-main 指定。
    """
    explicit = (os.environ.get("CHORUS_PLUGIN_MAIN") or "").strip()
    if explicit:
        return explicit
    data_dir = (os.environ.get("ASTRBOT_DATA_DIR") or "").strip()
    if not data_dir:
        data_dir = os.path.join(os.path.expanduser("~"), "data")
    return os.path.join(data_dir, "plugins", "astrbot_plugin_chorus_cover", "main.py")


def server_params(base_url, endpoint="/convert"):
    url = base_url.rstrip("/") + "/info?serialize=False"
    with urllib.request.urlopen(url, timeout=15) as resp:
        info = json.loads(resp.read().decode("utf-8"))
    conv = info.get("named_endpoints", {}).get(endpoint)
    if not conv:
        raise SystemExit("服务器没有 %s 这个接口，确认中间层版本" % endpoint)
    names = []
    for param in conv.get("parameters", []):
        names.append(param.get("parameter_name") or param.get("label") or "")
    return names


def plugin_params(main_path):
    with open(main_path, "r", encoding="utf-8") as f:
        source = f.read()
    match = re.search(r"legacy_kwargs\s*=\s*\{(.*?)\n            \}", source, re.S)
    if not match:
        raise SystemExit("没在 %s 里找到 legacy_kwargs 字典，插件可能改写了调用方式" % main_path)
    keys = re.findall(r'"([a-zA-Z_][a-zA-Z0-9_]*)"\s*:', match.group(1))
    # 插件后面还会 legacy_kwargs.update({...}) 补 f0_method / index_rate / filter_radius
    for extra in re.findall(r"legacy_kwargs\.update\(\s*\{(.*?)\}\s*\)", source, re.S):
        keys.extend(re.findall(r'"([a-zA-Z_][a-zA-Z0-9_]*)"\s*:', extra))
    deduped = []
    for key in keys:
        if key not in deduped:
            deduped.append(key)
    return deduped


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument(
        "--plugin-main",
        default="",
        help="插件 main.py 路径；不给就用 CHORUS_PLUGIN_MAIN，再退到 AstrBot 数据目录下的默认位置",
    )
    args = parser.parse_args()

    have = server_params(args.url)
    plugin_main = args.plugin_main or default_plugin_main()
    try:
        want = plugin_params(plugin_main)
    except FileNotFoundError:
        print("找不到插件 main.py：%s" % plugin_main)
        print("用 --plugin-main 指定，或设环境变量 CHORUS_PLUGIN_MAIN / ASTRBOT_DATA_DIR。")
        print("中间层声明的参数（%d 个）：%s" % (len(have), ", ".join(have)))
        return 0

    missing = [n for n in want if n not in have]
    unused = [n for n in have if n not in want]

    print("中间层参数 %d 个，插件传参 %d 个" % (len(have), len(want)))
    print("插件还会额外展开 **msst_kwargs（msst_* 那几个），这里没算进去")
    print("")
    for name in want:
        print(("  ✔ " if name in have else "  ✘ ") + name)
    if missing:
        print("\n缺这些参数，插件一调用就会报错，必须补：")
        for name in missing:
            print("  - " + name)
        print("\n补法：app.py 的 convert() 加形参，_hidden_inputs() 加同名 label 的组件")
    if unused:
        print("\n中间层多出来的（插件不传，走默认值，不影响）：")
        print("  " + ", ".join(unused))
    print("\n结论：" + ("不兼容，要改" if missing else "兼容"))
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
