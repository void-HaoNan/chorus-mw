"""整条链路：拿到歌 → 整首分离 → 挑副歌 → 软切 → 去混响 → RVC → 混音 → 出成品。"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import time

import config
import chorus
import rvc


def _run(cmd, timeout=600):
    proc = subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout
    )
    if proc.returncode != 0:
        tail = proc.stderr.decode("utf-8", "ignore")[-800:]
        raise RuntimeError("命令失败：%s\n%s" % (" ".join(cmd[:2]), tail))
    return proc


def _safe(text, limit=60):
    cleaned = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", str(text or "song")).strip(" ._")
    return (cleaned or "song")[:limit]


# ---------------- 音源 ----------------

def resolve_source(song_name_src):
    """插件给的是本地文件就用它；只给了歌名就去网易云下一首。"""
    if song_name_src and os.path.isfile(song_name_src):
        return song_name_src
    name = str(song_name_src or "").strip()
    if not name:
        raise ValueError("没有拿到歌曲文件，也没有歌名")
    return download_netease(name)


def download_netease(keyword):
    import requests

    os.makedirs(os.path.join(config.WORK_DIR, "dl"), exist_ok=True)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "http://music.163.com",
    }
    query = keyword[:60]
    search = requests.post(
        config.NETEASE_SEARCH,
        data={"s": query, "limit": 3, "type": 1, "offset": 0},
        headers=headers,
        timeout=20,
    ).json()
    songs = (search.get("result") or {}).get("songs") or []
    if not songs:
        raise RuntimeError("网易云搜不到这首歌：%s" % query)
    song = songs[0]
    detail = requests.get(
        config.NETEASE_PARSE,
        params={"id": song["id"], "br": 7, "type": "json"},
        headers=headers,
        timeout=25,
    ).json()
    url = detail.get("music_url")
    if not url:
        raise RuntimeError("网易云没给播放地址：%s" % query)
    out = os.path.join(config.WORK_DIR, "dl", _safe(song.get("name") or query) + ".mp3")
    with requests.get(url, headers=headers, timeout=120, stream=True) as resp:
        resp.raise_for_status()
        with open(out, "wb") as f:
            for chunk in resp.iter_content(1 << 20):
                f.write(chunk)
    if os.path.getsize(out) < 100 * 1024:
        raise RuntimeError("下载到的文件太小，可能被风控：%s" % out)
    return out


# ---------------- 音频处理 ----------------

def crop(src, dst, start, end):
    duration = max(0.5, float(end) - float(start))
    _run([
        config.FFMPEG, "-y", "-v", "error",
        "-ss", "%.3f" % float(start), "-t", "%.3f" % duration,
        "-i", src, "-c:a", "pcm_s16le", dst,
    ])
    return dst


def pitch_shift(src, dst, semitones, timeout=300):
    """用 rubberband 做正经音高变换：音高变、时长不变，不碰采样率。

    以前用 ffmpeg 的 asetrate+atempo，asetrate 会把采样率乘到十几万，
    重采样阶段直接卡死（实测 600 秒超时），所以改成外部工具。
    """
    semis = float(semitones)
    if abs(semis) < 1e-6:
        return src
    exe = config.RUBBERBAND
    if not os.path.isfile(exe):
        raise RuntimeError(
            "找不到 rubberband：%s（把 rubberband.exe + sndfile.dll 放进 bin\\）" % exe
        )
    _run(
        [exe, "-3", "--pitch", "%.4f" % semis, src, dst],
        timeout=timeout,
    )
    if not os.path.isfile(dst) or os.path.getsize(dst) < 1024:
        raise RuntimeError("rubberband 没有产出音频：%s" % dst)
    return dst


def mix(vocal_path, inst_path, out_path, inst_volume_db=-2.0, shift_instrumental=0.0):
    vol = 10.0 ** (float(inst_volume_db) / 20.0)
    shifted_inst = inst_path
    temp = None
    if shift_instrumental:
        temp = os.path.join(
            os.path.dirname(out_path) or ".",
            "_inst_shift_%d.wav" % int(time.time() * 1000),
        )
        shifted_inst = pitch_shift(inst_path, temp, shift_instrumental)
    try:
        graph = (
            "[0:a]aresample=44100,aformat=channel_layouts=stereo,volume=1.0[a];"
            "[1:a]aresample=44100,aformat=channel_layouts=stereo,volume=%.4f[b];"
            "[a][b]amix=inputs=2:duration=longest,volume=2.0,"
            "loudnorm=I=-14:TP=-1.5:LRA=11" % vol
        )
        _run([
            config.FFMPEG, "-y", "-v", "error",
            "-i", vocal_path, "-i", shifted_inst,
            "-filter_complex", graph,
            "-ar", "44100", "-c:a", "pcm_s16le", out_path,
        ])
    finally:
        if temp and os.path.exists(temp):
            try:
                os.remove(temp)
            except OSError:
                pass
    return out_path


# ---------------- 缓存 ----------------

def _fingerprint(path):
    try:
        st = os.stat(path)
        return "%s|%d|%d" % (os.path.basename(path), st.st_size, int(st.st_mtime))
    except OSError:
        return "missing"


def cache_key(source, model, key_shift, extra):
    payload = {
        "src": _fingerprint(source),
        "model": model,
        "shift": key_shift,
        "chorus": config.CHORUS_SECONDS,
        "sep": config.SEP_MODEL,
        "dereverb": config.DERERVERB_MODEL,
        "index_rate": config.DEFAULT_INDEX_RATE,
        "extra": extra,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]
    return digest


def cache_lookup(key):
    path = os.path.join(config.CACHE_DIR, key + ".json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    audio = data.get("audio")
    return audio if audio and os.path.exists(audio) else None


def cache_store(key, audio, meta):
    os.makedirs(config.CACHE_DIR, exist_ok=True)
    with open(os.path.join(config.CACHE_DIR, key + ".json"), "w", encoding="utf-8") as f:
        json.dump({"audio": audio, "meta": meta, "at": int(time.time())}, f, ensure_ascii=False)


# ---------------- 主流程 ----------------

def run(song_name_src, key_shift=0, model_dropdown="", chorus_seconds=None,
        dereverb_model=None, inst_volume_db=-2.0, shift_instrumental=None,
        progress=None, use_cache=True):
    """返回 (输出音频路径, 是否命中缓存, 说明文本)。"""
    def report(pct, text):
        if progress is not None:
            try:
                progress(pct / 100.0, desc=text)
            except Exception:
                pass

    started = time.time()
    source = resolve_source(song_name_src)
    report(4, "音频就位：%s" % os.path.basename(source))

    model = model_dropdown or _default_model()
    dereverb_label = dereverb_model or config.DERERVERB_MODEL
    key = cache_key(source, model, key_shift, {"dereverb": dereverb_label})
    if use_cache:
        hit = cache_lookup(key)
        if hit:
            report(100, "命中缓存")
            return hit, True, "缓存命中"

    token = "%s_%d" % (key, int(started))
    work = os.path.join(config.WORK_DIR, token)
    for sub in ("sep_vocal", "sep_inst", "dereverb", "chorus", "out"):
        os.makedirs(os.path.join(work, sub), exist_ok=True)

    # 1) 整首分离
    report(8, "整首人声分离中")
    vocals, instrumental = rvc.separate(
        source,
        os.path.join(work, "sep_vocal"),
        os.path.join(work, "sep_inst"),
        config.SEP_MODEL,
    )
    if not instrumental:
        raise RuntimeError("分离没有产出伴奏，无法混音")
    report(45, "分离完成，正在挑副歌")

    # 2) 挑副歌 + 软切
    start, end = chorus.pick_window(vocals, chorus_seconds)
    report(52, chorus.describe(vocals, start, end))

    vocal_cut = crop(vocals, os.path.join(work, "chorus", "vocal_cut.wav"), start, end)
    inst_cut = crop(instrumental, os.path.join(work, "chorus", "inst_cut.wav"), start, end)

    # 3) 去混响
    report(58, "去混响（%s）" % dereverb_label)
    clean_vocal = rvc.dereverb(
        vocal_cut,
        os.path.join(work, "dereverb", "vocal"),
        os.path.join(work, "dereverb", "rest"),
        dereverb_label,
    )
    report(70, "去混响完成，开始变声")

    # 4) RVC 变声
    converted = rvc.convert(
        clean_vocal,
        model,
        key_shift,
        config.DEFAULT_F0,
        config.DEFAULT_INDEX_RATE,
        config.DEFAULT_RMS_MIX,
        config.DEFAULT_PROTECT,
    )
    report(88, "变声完成，混音中")

    # 5) 混音
    if shift_instrumental is None:
        shift_instrumental = float(key_shift)
    final = os.path.join(
        config.OUT_DIR,
        "%s_%s_%s.wav" % (_safe(os.path.splitext(os.path.basename(source))[0], 40),
                          _safe(os.path.splitext(model)[0], 20), token[-6:]),
    )
    mix(converted, inst_cut, final, inst_volume_db, shift_instrumental)

    meta = {
        "source": source,
        "model": model,
        "key_shift": key_shift,
        "chorus": [start, end],
        "seconds": round(time.time() - started, 1),
    }
    cache_store(key, final, meta)
    report(100, "完成")
    shutil.rmtree(work, ignore_errors=True)
    return final, False, "副歌 %.1fs-%.1fs，耗时 %.1fs" % (start, end, meta["seconds"])


def _default_model():
    models = rvc.list_models()
    if not models:
        raise RuntimeError("assets/weights 里没有 .pth 模型")
    return models[0]
