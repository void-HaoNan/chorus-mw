"""PyMSS 的失败判定。

为什么需要这个文件：RVC 整合包里 ``tools.pymss_webui.pymss_separate`` 是生成器，
**内部把异常 except 成一行文字就结束了，永远不往外抛**。也就是说「子进程 OOM 崩了」
和「正常跑完」在调用方看来一模一样，只能靠两件事判断：

1. 输出目录里有没有**新增**的音频文件（确定的证据）；
2. 生成器吐出来的最后几行文字里有没有 OOM / 失败的痕迹（给用户看的理由）。

0.8.1 之前没做这个判断，于是插件把 CUDA 显存不足报成了「分离没有产出人声」，
用户根本看不出真因。
"""
import os

AUDIO_SUFFIXES = (".wav", ".flac")

_OOM_HINTS = (
    "outofmemoryerror",
    "cuda out of memory",
    "cuda error: out of memory",
    "显存不足",
)

_NOT_FOUND_HINTS = ("没有找到可处理的音频文件",)


def audio_files(directory):
    """目录里的音频文件名集合；目录不存在就当空。"""
    try:
        return {
            name
            for name in os.listdir(directory)
            if name.lower().endswith(AUDIO_SUFFIXES)
        }
    except OSError:
        return set()


def _first_line_with(text, hints):
    """在全文里找第一条命中关键词的行（OOM 那行通常在 traceback 最末尾）。"""
    for line in str(text or "").splitlines():
        low = line.lower()
        if any(hint in low for hint in hints):
            return line.strip()[:400]
    return ""


def failure_reason(text, vocal_dir="", inst_dir=""):
    """把 PyMSS 的输出压成一句能直接给用户看的话。"""
    raw = str(text or "")
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    tail = "\n".join(lines[-4:])
    produced = sorted(audio_files(vocal_dir) | audio_files(inst_dir)) if (vocal_dir or inst_dir) else []
    where = ""
    if produced:
        where = "（目录里已有：%s）" % "、".join(produced[:3])

    oom_line = _first_line_with(raw, _OOM_HINTS)
    if oom_line:
        return (
            "显存不足（CUDA out of memory），分离子进程被系统拒掉了%s\n%s"
            % (where, oom_line)
        )
    not_found = _first_line_with(raw, _NOT_FOUND_HINTS)
    if not_found:
        return "找不到要分离的音频文件%s\n%s" % (where, not_found)
    return "PyMSS 没有产出音频%s\n%s" % (where, tail or "（子进程没有任何输出）")
