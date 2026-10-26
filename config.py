"""路径和默认参数。所有值都可以用同名环境变量覆盖，方便换机器。"""
import os

HOME = os.path.dirname(os.path.abspath(__file__))

RVC_ROOT = os.environ.get(
    "CHORUS_RVC_ROOT", r"D:\PYMSS-RVC\RVC20260723Nvidia50x0\RVC20260718Nvidia50x0"
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
CHORUS_MIN_SECONDS = 40.0  # 软切后允许的下限
CHORUS_MAX_SECONDS = 50.0  # 软切后允许的上限
CHORUS_SNAP = 4.0          # 软切时在目标前后各找多少秒的换气点

# 分离模型（RVC WebUI 下拉框里的中文标签，不能改）
SEP_MODEL = os.environ.get("CHORUS_SEP_MODEL", "去伴奏")
DERERVERB_MODEL = os.environ.get("CHORUS_DEREVERB_MODEL", "去混响")

# 推理默认值
DEFAULT_F0 = os.environ.get("CHORUS_F0", "rmvpe")
DEFAULT_INDEX_RATE = float(os.environ.get("CHORUS_INDEX_RATE", "0.75"))
DEFAULT_RMS_MIX = float(os.environ.get("CHORUS_RMS_MIX", "0.35"))
DEFAULT_PROTECT = float(os.environ.get("CHORUS_PROTECT", "0.33"))

# 网易云兜底下载（插件传歌名而不是文件路径时用）
NETEASE_SEARCH = "http://music.163.com/api/search/get/web"
NETEASE_PARSE = "https://www.hhlqilongzhu.cn/api/dg_wyymusic.php"


def ensure_dirs():
    for path in (WORK_DIR, OUT_DIR, CACHE_DIR):
        os.makedirs(path, exist_ok=True)


for _d in (WORK_DIR, OUT_DIR, CACHE_DIR):
    os.makedirs(_d, exist_ok=True)
