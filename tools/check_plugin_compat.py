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
import re
import sys
import urllib.request

DEFAULT_URL = "http://127.0.0.1:3333/"
DEFAULT_PLUGIN_MAIN = r"C:\Users\21903\data\plugins\astrbot_plugin_matsuko_cover\main.py"


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
    parser.add_argument("--plugin-main", default=DEFAULT_PLUGIN_MAIN)
    args = parser.parse_args()

    have = server_params(args.url)
    try:
        want = plugin_params(args.plugin_main)
    except FileNotFoundError:
        print("找不到插件 main.py：%s" % args.plugin_main)
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
