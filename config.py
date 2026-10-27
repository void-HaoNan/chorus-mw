"""路径和默认参数。所有值都可以用同名环境变量覆盖，方便换机器。

RVC 整合包的路径**不写死在代码里**，必须用环境变量给：

    set CHORUS_RVC_ROOT=D:\\你的路径\\RVC20260718Nvidia50x0

仓库自带的 start.ps1 会替你设好这一项，直接用它启动就不用管。
"""
import os

HOME = os.path.dirname(os.path.abspath(__file__))

# 服务版本（和 CHANGELOG / README 对齐），启动日志与 /cache_info 都会带出来
VERSION = "0.8.0"


class ConfigError(RuntimeError):
    """启动前检查没通过时抛这个，消息直接给用户看。"""


RVC_ROOT = (os.environ.get("CHORUS_RVC_ROOT") or "").strip()

if not RVC_ROOT:
    raise ConfigError(
        "没有设置 CHORUS_RVC_ROOT。\n"
        "它是 RVC 整合包根目录，里面应该有 runtime\\python.exe、ffmpeg.exe、assets\\weights。\n"
        "用法（PowerShell）：\n"
        "    $env:CHORUS_RVC_ROOT = 'D:\\你的路径\\RVC20260718Nvidia50x0'\n"
        "    python app.py\n"
        "或者直接用仓库里的 scripts\\start.ps1（它读 CHORUS_RVC_ROOT，缺了会给出提示）。"
    )

RVC_APP = RVC_ROOT
RVC_PYTHON = os.path.join(RVC_ROOT, "runtime", "python.exe")
RVC_PORT = int(os.environ.get("CHORUS_RVC_PORT", "7897"))
RVC_BOOT_TIMEOUT = int(os.environ.get("CHORUS_RVC_BOOT_TIMEOUT", "180"))

FFMPEG = os.path.join(RVC_ROOT, "ffmpeg.exe")
FFPROBE = os.path.join(RVC_ROOT, "ffprobe.exe")

WEIGHTS_DIR = os.path.join(RVC_ROOT, "assets", "weights")
INDEX_DIRS = [
    os.path.join(RVC_ROOT, "assets", "indices"),
    os.path.join(RVC_ROOT, "indices"),
]

RUBBERBAND = os.environ.get(
    "CHORUS_RUBBERBAND", os.path.join(HOME, "bin", "rubberband.exe")
)

WORK_DIR = os.path.join(HOME, "_work")
OUT_DIR = os.path.join(HOME, "output")
CACHE_DIR = os.path.join(HOME, "cache")
LIBS_DIR = os.path.join(HOME, "libs")

# 副歌截取
CHORUS_SECONDS = float(os.environ.get("CHORUS_SECONDS", "45"))
CHORUS_HOP = 0.5          # 候选窗口滑动步长（秒）
CHORUS_MIN_SECONDS = 40.0  # 软切后允许的下限（默认 45 秒模式，自定义模式会另算）
CHORUS_MAX_SECONDS = 50.0  # 软切后允许的上限
CHORUS_SNAP = 4.0          # 软切时在目标前后各找多少秒的换气点
# 先切后分离时的余量：候选窗口 = 目标时长 + 这么多秒，留给人声里精挑和换气点吸附
CHORUS_MARGIN_SECONDS = float(os.environ.get("CHORUS_MARGIN_SECONDS", "15"))

# 常驻 worker（分离/去混响/变声共用一个进程，省掉每次 3~5 秒的导入）
RESIDENT_ENABLED = os.environ.get("CHORUS_RESIDENT", "1").strip().lower() not in (
    "0", "false", "off", "no",
)

# 分离模型（RVC WebUI 下拉框里的中文标签，不能改）
# 2026-10-27 起默认走 Studio 那套：becruily_deux 分离 + anvuew 22.5050 去混响
SEP_MODEL = os.environ.get("CHORUS_SEP_MODEL", "去伴奏（becruily deux）")
DERERVERB_MODEL = os.environ.get("CHORUS_DEREVERB_MODEL", "去混响（anvuew 22.505）")

# 推理默认值（调用方没传 / 传了非法值时用这些）
F0_CHOICES = ("rmvpe", "pm", "crepe", "harvest")
DEFAULT_F0 = (os.environ.get("CHORUS_F0") or "rmvpe").strip().lower()
if DEFAULT_F0 not in F0_CHOICES:  # 环境变量写错别让服务起不来
    DEFAULT_F0 = "rmvpe"
DEFAULT_INDEX_RATE = float(os.environ.get("CHORUS_INDEX_RATE", "0.75"))
DEFAULT_RMS_MIX = float(os.environ.get("CHORUS_RMS_MIX", "0.35"))
DEFAULT_PROTECT = float(os.environ.get("CHORUS_PROTECT", "0.33"))
# filter_radius：RVC WebUI 这一版的 /infer_convert 没有这个入参（只有 9 个 input），
# 中间层照收、照记日志，但**传不下去**，不要假装它生效了。
DEFAULT_FILTER_RADIUS = int(os.environ.get("CHORUS_FILTER_RADIUS", "3"))

# 混音默认电平（dB）：伴奏默认压一点，人声保持原样
DEFAULT_INST_VOLUME_DB = float(os.environ.get("CHORUS_INST_VOLUME_DB", "-6.0"))
DEFAULT_VOCAL_VOLUME_DB = float(os.environ.get("CHORUS_VOCAL_VOLUME_DB", "0.0"))

# 网易云兜底下载（插件传歌名而不是文件路径时用）
NETEASE_SEARCH = "http://music.163.com/api/search/get/web"
NETEASE_PARSE = "https://www.hhlqilongzhu.cn/api/dg_wyymusic.php"


def ensure_dirs():
    for path in (WORK_DIR, OUT_DIR, CACHE_DIR):
        os.makedirs(path, exist_ok=True)


def check_paths():
    """启动前的体检。返回问题列表，空列表表示一切就绪。

    不在这里抛异常，方便 start.ps1 一次把问题全列出来，而不是改一个报一个。
    """
    problems = []

    def need(label, path, kind="file"):
        if kind == "file":
            if not os.path.isfile(path):
                problems.append("缺 %s：%s" % (label, path))
        elif not os.path.isdir(path):
            problems.append("缺 %s：%s" % (label, path))

    if not os.path.isdir(RVC_ROOT):
        problems.append("CHORUS_RVC_ROOT 指向的目录不存在：%s" % RVC_ROOT)
        return problems

    need("RVC 运行时 python", RVC_PYTHON)
    need("ffmpeg", FFMPEG)
    need("模型目录 assets\\weights", WEIGHTS_DIR, kind="dir")
    need("rubberband.exe", RUBBERBAND)

    dll = os.path.join(os.path.dirname(RUBBERBAND), "sndfile.dll")
    if os.path.isfile(RUBBERBAND) and not os.path.isfile(dll):
        problems.append(
            "rubberband 缺依赖库，会把 sndfile.dll 一起放进 %s（跑 scripts\\fetch_rubberband.ps1）"
            % os.path.dirname(RUBBERBAND)
        )

    models = []
    if os.path.isdir(WEIGHTS_DIR):
        models = [n for n in os.listdir(WEIGHTS_DIR) if n.lower().endswith(".pth")]
        if not models:
            problems.append("assets\\weights 里没有 .pth 模型")

    libs_web = os.path.join(HOME, "libs_web", "gradio")
    if not os.path.isdir(libs_web):
        problems.append("缺 libs_web（pip install --target libs_web -r requirements.txt）")

    return problems


for _d in (WORK_DIR, OUT_DIR, CACHE_DIR):
    os.makedirs(_d, exist_ok=True)
