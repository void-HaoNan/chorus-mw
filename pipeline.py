"""整条链路：拿到歌 → 先切一段 → 只分离这一段 → 精挑 → 去混响 ∥ 伴奏处理 → RVC → 混音。

0.7.0 起的顺序（默认，收益最大）：
1. 读整首音频，用能量包络在**整首混音**上挑一个候选副歌窗口
   （目标长度 = 目标时长 + 15 秒余量，最长不超过整首）；
2. 只把这个窗口切出来，送去做人声分离；
3. 在分离出的人声里再精挑最终那段，切准；
4. 去混响与「伴奏切段 + 移调」并行跑，然后变声、混音。

以前是「整首分离 → 再切」，三四分钟的歌光分离就要二三十秒。现在只分离 60 秒左右。

保留原来的整首分离路径：
- ``render_mode=full`` 本来就要整首分离；
- 新顺序任何一步报错 → 自动退回「整首分离再切」，日志里记 ``fallback=full_separation``。
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

import config
import chorus
import rvc


_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

RENDER_MODES = ("chorus", "full", "custom")
DEFAULT_RENDER_MODE = "chorus"
CUSTOM_MIN_SECONDS = 20.0
CUSTOM_MAX_SECONDS = 180.0


def _run(cmd, timeout=600):
    proc = subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
        creationflags=_NO_WINDOW,
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

def probe_duration(path):
    """ffprobe 量时长；量不到返回 0（调用方按「不知道」处理）。"""
    try:
        proc = subprocess.run(
            [config.FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
            creationflags=_NO_WINDOW,
        )
        return float(proc.stdout.decode("utf-8", "ignore").strip() or 0)
    except Exception:
        return 0.0


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


def mix(vocal_path, inst_path, out_path, inst_volume_db=-2.0, shift_instrumental=0.0,
        vocal_volume_db=0.0):
    """人声与伴奏按各自增益相加，只做峰值归一，不做动态压缩。

    以前这里挂的是单遍 `loudnorm`：它为了打到 -14 LUFS 会动态压缩，听感上「上下空间」
    被压扁。现在改成两步——先混出临时文件，量一下峰值，再整体加一个固定增益到 -1.5 dBFS。
    固定增益只改整体音量，不动动态范围，鼓点和弱音之间的落差保住了。
    """
    vol = 10.0 ** (float(inst_volume_db) / 20.0)
    vocal_vol = 10.0 ** (float(vocal_volume_db) / 20.0)
    shifted_inst = inst_path
    temp = None
    if shift_instrumental:
        temp = os.path.join(
            os.path.dirname(out_path) or ".",
            "_inst_shift_%d.wav" % int(time.time() * 1000),
        )
        shifted_inst = pitch_shift(inst_path, temp, shift_instrumental)
    raw = os.path.join(os.path.dirname(out_path) or ".", "_mix_raw_%d.wav" % int(time.time() * 1000))
    try:
        if config.VOCAL_CHAIN:
            # 人声：切掉 85Hz 以下的隆隆声，再轻压 2.5:1 把强弱拉稳；
            # 伴奏：用侧链压缩在人声出现时退一点，人声就不会被埋。
            vocal_chain = (
                "[0:a]aresample=44100,aformat=channel_layouts=stereo,volume=%.4f,"
                "highpass=f=85,acompressor=threshold=-18dB:ratio=2.5:attack=8:release=180,"
                "asplit=2[voice][key]"
                % vocal_vol
            )
            if config.VOCAL_CHAIN_DUCK:
                graph = (
                    vocal_chain + ";"
                    "[1:a]aresample=44100,aformat=channel_layouts=stereo,volume=%.4f[bed];"
                    "[bed][key]sidechaincompress=threshold=0.05:ratio=1.8:attack=15:release=250[ducked];"
                    "[voice][ducked]amix=inputs=2:duration=longest,volume=2.0"
                    % vol
                )
            else:
                graph = (
                    vocal_chain + ";"
                    "[1:a]aresample=44100,aformat=channel_layouts=stereo,volume=%.4f[bed];"
                    "[voice][bed]amix=inputs=2:duration=longest,volume=2.0"
                    % vol
                )
        else:
            graph = (
                "[0:a]aresample=44100,aformat=channel_layouts=stereo,volume=%.4f[a];"
                "[1:a]aresample=44100,aformat=channel_layouts=stereo,volume=%.4f[b];"
                "[a][b]amix=inputs=2:duration=longest,volume=2.0"
                % (vocal_vol, vol)
            )
        if config.NORMALIZE_MODE == "loudnorm":
            graph += ",loudnorm=I=-14:TP=-1.5:LRA=11"
        _run([
            config.FFMPEG, "-y", "-v", "error",
            "-i", vocal_path, "-i", shifted_inst,
            "-filter_complex", graph,
            "-ar", "44100", "-c:a", "pcm_s16le", raw,
        ])
        if config.NORMALIZE_MODE == "loudnorm":
            _run([
                config.FFMPEG, "-y", "-v", "error",
                "-i", vocal_path, "-i", shifted_inst,
                "-filter_complex", graph,
                "-ar", "44100", "-c:a", "pcm_s16le", out_path,
            ])
            print("[chorus-mw] mix 使用 loudnorm（旧行为）", flush=True)
            return out_path
        peak_db = _measure_peak_db(raw)
        gain_db = 0.0
        if peak_db is not None and peak_db < 0:
            gain_db = max(-12.0, min(24.0, -1.5 - peak_db))
        _run([
            config.FFMPEG, "-y", "-v", "error",
            "-i", raw,
            "-af", "volume=%.2fdB" % gain_db if abs(gain_db) > 0.05 else "anull",
            "-ar", "44100", "-c:a", "pcm_s16le", out_path,
        ])
        print("[chorus-mw] mix peak=%.1f dBFS gain=%+.1f dB（只做峰值归一，不压缩动态）" % (
            peak_db if peak_db is not None else 0.0, gain_db), flush=True)
    finally:
        for leftover in (temp, raw):
            if leftover and os.path.exists(leftover):
                try:
                    os.remove(leftover)
                except OSError:
                    pass
    return out_path


def _measure_peak_db(path):
    """用 ffmpeg volumedetect 量峰值，量不到返回 None。"""
    try:
        result = _run([
            config.FFMPEG, "-hide_banner", "-i", path, "-af", "volumedetect",
            "-f", "null", "-",
        ], timeout=120)
    except Exception as error:
        print("[chorus-mw] 峰值测量失败（%s），不做归一" % error, flush=True)
        return None
    output = (result.stderr or b"").decode("utf-8", "ignore")
    match = re.search(r"max_volume:\s*(-?[0-9.]+)\s*dB", output)
    return float(match.group(1)) if match else None


def _prepare_instrumental(inst_cut, work, semitones):
    """伴奏切段已经在外面做完了，这里只负责移调（没有移调就直接用原文件）。"""
    if abs(float(semitones or 0)) < 1e-6:
        return inst_cut
    dst = os.path.join(work, "chorus", "inst_shift.wav")
    return pitch_shift(inst_cut, dst, semitones)


# ---------------- 模式 ----------------

def normalize_mode(value):
    """chorus / full / custom，认中文别名；不认识的一律按 chorus。"""
    mode = str(value or "").strip().lower()
    aliases = {
        "chorus": "chorus", "副歌": "chorus", "默认": "chorus",
        "full": "full", "whole": "full", "整首": "full", "完整": "full",
        "custom": "custom", "自定义": "custom",
    }
    return aliases.get(mode, DEFAULT_RENDER_MODE)


def resolve_target_seconds(mode, chorus_seconds=None):
    """返回目标翻唱时长；full 返回 0（表示整首）。"""
    try:
        seconds = float(chorus_seconds) if chorus_seconds not in (None, "") else 0.0
    except (TypeError, ValueError):
        seconds = 0.0
    if mode == "full":
        return 0.0
    if mode == "custom":
        base = seconds or config.CHORUS_SECONDS
        return min(CUSTOM_MAX_SECONDS, max(CUSTOM_MIN_SECONDS, base))
    base = seconds or config.CHORUS_SECONDS
    return min(CUSTOM_MAX_SECONDS, max(CUSTOM_MIN_SECONDS, base))


# ---------------- 缓存 ----------------

def _fingerprint(path):
    try:
        st = os.stat(path)
        return "%s|%d|%d" % (os.path.basename(path), st.st_size, int(st.st_mtime))
    except OSError:
        return "missing"


def cache_key(source, model, key_shift, extra):
    extra = extra or {}
    payload = {
        "src": _fingerprint(source),
        "model": model,
        "shift": key_shift,
        # 模式与目标时长必须进 key：同一首歌 chorus 和 full 是两个完全不同的成品
        "mode": extra.get("mode", DEFAULT_RENDER_MODE),
        # 混音链和响度模式也会改变成品，进 key 才不会拿错缓存
        "norm": config.NORMALIZE_MODE,
        "chain": [bool(config.VOCAL_CHAIN), bool(config.VOCAL_CHAIN_DUCK)],
        "chorus": extra.get("target", config.CHORUS_SECONDS),
        "sep": config.SEP_MODEL,
        "dereverb": extra.get("dereverb", config.DERERVERB_MODEL),
        # 推理参数与两条轨道的音量都会改变成品，必须一起进 key，
        # 否则「改了 index_rate / 伴奏音量却拿到上次的缓存」会让人以为参数没生效。
        "infer": {
            "f0": extra.get("f0"),
            "index_rate": extra.get("index_rate"),
            "rms_mix_rate": extra.get("rms_mix_rate"),
            "protect": extra.get("protect"),
            "inst_volume_db": extra.get("inst_volume_db"),
            "vocal_volume_db": extra.get("vocal_volume_db"),
        },
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


# ---------------- 挑窗口 ----------------

def _pick_on_mix(source, chorus_dir, want, minimum):
    """在整首混音上挑候选窗口。soundfile 读不了（m4a 等）就转码后再挑。"""
    want = max(1.0, float(want))
    minimum = min(float(minimum), want)
    try:
        return chorus.pick_window(source, want, min_seconds=minimum, max_seconds=want)
    except Exception as error:
        print("[chorus-mw] mix envelope failed (%s)，改用 ffmpeg 解码后挑" % error, flush=True)
        probe = os.path.join(chorus_dir, "mix_probe.wav")
        _run([
            config.FFMPEG, "-y", "-v", "error", "-i", source,
            "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", probe,
        ], timeout=300)
        return chorus.pick_window(probe, want, min_seconds=minimum, max_seconds=want)


def _window_limits(target, available):
    """精挑时允许的长度区间：±3 秒，别让换气点吸附把 45 秒切成 38 秒。"""
    min_keep = max(5.0, min(float(target) * 0.85, float(available)))
    max_keep = max(min_keep, min(float(available), float(target) + 3.0))
    return min_keep, max_keep


def _prepare_window(source, work, target, duration, start_hint=None):
    """新顺序：整首混音挑候选 → 只切这一段 → 只分离这一段 → 人声里精挑。

    ``start_hint`` 给了就从这一秒开始切（用户指定起点），不再自动挑段落；
    副歌段里的说唱/念白 RVC 转出来会糊，这种时候由调用方指定起点更靠谱。
    """
    chorus_dir = os.path.join(work, "chorus")
    sep_vocal = os.path.join(work, "sep_vocal")
    sep_inst = os.path.join(work, "sep_inst")

    cut_start, cut_end = 0.0, duration
    candidate = source
    margin = float(config.CHORUS_MARGIN_SECONDS)
    forced = None
    if start_hint is not None:
        try:
            forced = max(0.0, float(start_hint))
        except (TypeError, ValueError):
            forced = None
    if forced is not None and duration:
        margin = 0.0
        cut_start = min(forced, max(0.0, duration - target - 1.0))
        cut_end = min(duration, cut_start + target)
        candidate = crop(source, os.path.join(chorus_dir, "candidate.wav"), cut_start, cut_end)
        print("[chorus-mw] forced window=%.1fs-%.1fs" % (cut_start, cut_end), flush=True)
    elif duration and duration > target + margin + 1.0:
        want = target + margin
        cut_start, cut_end = _pick_on_mix(source, chorus_dir, want, minimum=target + 2.0)
        candidate = crop(source, os.path.join(chorus_dir, "candidate.wav"), cut_start, cut_end)
    span = max(0.5, float(cut_end) - float(cut_start))

    try:
        vocals, instrumental = rvc.separate(candidate, sep_vocal, sep_inst, config.SEP_MODEL)
    except Exception as error:
        # 提主旋律吃显存更多，显存紧张时退回普通分离，别让整首歌直接失败
        if config.SEP_MODEL == config.SEP_FALLBACK_MODEL:
            raise
        print("[chorus-mw] %s 失败（%s），退回 %s" % (
            config.SEP_MODEL, str(error).splitlines()[0][:120], config.SEP_FALLBACK_MODEL), flush=True)
        vocals, instrumental = rvc.separate(
            candidate, sep_vocal, sep_inst, config.SEP_FALLBACK_MODEL
        )
    if not instrumental:
        raise RuntimeError("分离没有产出伴奏，无法混音")
    # 0.9.6：两段式——人声里再拆一次主唱，去掉和声（对副歌叠唱最有效）
    if config.TWO_STAGE:
        try:
            lead, _backing = rvc.separate(
                vocals,
                os.path.join(work, "sep_vocal_lead"),
                os.path.join(work, "sep_backing"),
                "提主旋律",
            )
            if lead:
                print("[chorus-mw] two_stage lead extracted", flush=True)
                vocals = lead
        except Exception as error:
            print("[chorus-mw] two_stage failed (%s)，退回单段结果" % error, flush=True)

    if forced is not None:
        local_start, local_end = 0.0, min(span, float(target))
    else:
        min_keep, max_keep = _window_limits(target, span)
        local_start, local_end = chorus.pick_window(
            vocals, target, min_seconds=min_keep, max_seconds=max_keep
        )
    vocal_cut = crop(vocals, os.path.join(chorus_dir, "vocal_cut.wav"), local_start, local_end)
    inst_cut = crop(instrumental, os.path.join(chorus_dir, "inst_cut.wav"), local_start, local_end)
    return {
        "vocal": vocal_cut,
        "inst": inst_cut,
        "start": round(cut_start + local_start, 2),
        "end": round(cut_start + local_end, 2),
        "sep_span": round(span, 2),
    }


def _prepare_full(source, work, target):
    """旧顺序（兜底 / full 模式）：整首分离，再挑副歌。"""
    chorus_dir = os.path.join(work, "chorus")
    vocals, instrumental = rvc.separate(
        source,
        os.path.join(work, "sep_vocal_full"),
        os.path.join(work, "sep_inst_full"),
        config.SEP_MODEL,
    )
    if not instrumental:
        raise RuntimeError("分离没有产出伴奏，无法混音")
    duration = probe_duration(vocals) or 0.0
    min_keep, max_keep = _window_limits(target, duration or target)
    start, end = chorus.pick_window(vocals, target, min_seconds=min_keep, max_seconds=max_keep)
    return {
        "vocal": crop(vocals, os.path.join(chorus_dir, "vocal_cut.wav"), start, end),
        "inst": crop(instrumental, os.path.join(chorus_dir, "inst_cut.wav"), start, end),
        "start": round(start, 2),
        "end": round(end, 2),
        "sep_span": round(duration, 2),
    }


# ---------------- 主流程 ----------------

def run(song_name_src, key_shift=0, model_dropdown="", chorus_seconds=None,
        dereverb_model=None, inst_volume_db=None, shift_instrumental=None,
        progress=None, use_cache=True, render_mode=DEFAULT_RENDER_MODE,
        f0_method=None, index_rate=None, rms_mix_rate=None, protect=None,
        vocal_volume_db=None, chorus_start=None):
    """返回 (输出音频路径, 是否命中缓存, 说明文本)。

    render_mode:
        chorus —— 默认，副歌片段（默认 45 秒，先切 60 秒去分离）
        full   —— 整首翻唱（分离整首、变声整首，耗时可能是副歌模式的 5~10 倍）
        custom —— 自定义时长，用 chorus_seconds（20~180），同样先切（时长 + 15 秒）

    0.8.0：f0_method / index_rate / rms_mix_rate / protect / inst_volume_db /
    vocal_volume_db 都会真的传给 RVC 和混音；传 None 表示用 config 里的默认值。
    """
    def report(pct, text):
        if progress is not None:
            try:
                progress(pct / 100.0, desc=text)
            except Exception:
                pass

    started = time.time()
    mode = normalize_mode(render_mode)
    target = resolve_target_seconds(mode, chorus_seconds)
    # 调用方没传就用 config 默认值；这里再兜一层，pipeline 单独被调用也不会用错值
    f0 = str(f0_method or config.DEFAULT_F0).strip().lower()
    idx = config.DEFAULT_INDEX_RATE if index_rate is None else float(index_rate)
    rms = config.DEFAULT_RMS_MIX if rms_mix_rate is None else float(rms_mix_rate)
    prot = config.DEFAULT_PROTECT if protect is None else float(protect)
    inst_db = config.DEFAULT_INST_VOLUME_DB if inst_volume_db is None else float(inst_volume_db)
    vocal_db = (
        config.DEFAULT_VOCAL_VOLUME_DB if vocal_volume_db is None else float(vocal_volume_db)
    )
    source = resolve_source(song_name_src)
    report(4, "音频就位：%s" % os.path.basename(source))

    model = model_dropdown or _default_model()
    dereverb_label = dereverb_model or config.DERERVERB_MODEL
    if shift_instrumental is None:
        shift_instrumental = float(key_shift)
    extra = {
        "dereverb": dereverb_label,
        "mode": mode,
        "target": round(target, 2),
        "f0": f0,
        "index_rate": round(idx, 4),
        "rms_mix_rate": round(rms, 4),
        "protect": round(prot, 4),
        "inst_volume_db": round(inst_db, 2),
        "vocal_volume_db": round(vocal_db, 2),
        # 0.9.3：自动移调会改变实际移调量，必须进缓存键，否则会拿到旧的 0 调产物
        "auto_pitch": bool(config.AUTO_PITCH),
        "target_f0": round(config.TARGET_F0, 1),
        # 指定起点也是产物的一部分
        "chorus_start": (round(float(chorus_start), 1) if chorus_start else 0),
    }
    key = cache_key(source, model, key_shift, extra)
    if use_cache:
        hit = cache_lookup(key)
        if hit:
            report(100, "命中缓存")
            return hit, True, "缓存命中"

    token = "%s_%d" % (key, int(started))
    work = os.path.join(config.WORK_DIR, token)
    for sub in ("sep_vocal", "sep_inst", "sep_vocal_full", "sep_inst_full",
                "dereverb", "chorus", "out"):
        os.makedirs(os.path.join(work, sub), exist_ok=True)

    def mark(name):
        elapsed = time.time() - started
        print("[chorus-mw] mode=%s step=%s elapsed=%.1fs" % (mode, name, elapsed), flush=True)

    duration = probe_duration(source)

    # 0.8.1：分离前先看显存。8G 卡上被 OBS / 直播 / GPT-SoVITS 占满时，
    # 分离峰值（约 1.6GB）会顶爆，报出来的错还很难懂；这里先等一等再上。
    report(6, "检查显存")
    rvc.ensure_vram(notify=lambda text: report(7, text))

    if mode == "full":
        report(8, "整首人声分离中（完整翻唱，耗时明显更长）")
        vocals, instrumental = rvc.separate(
            source,
            os.path.join(work, "sep_vocal_full"),
            os.path.join(work, "sep_inst_full"),
            config.SEP_MODEL,
        )
        if not instrumental:
            raise RuntimeError("分离没有产出伴奏，无法混音")
        mark("separate")
        vocal_cut, inst_cut = vocals, instrumental
        window = {"start": 0.0, "end": round(duration or probe_duration(vocals), 2),
                  "sep_span": round(duration, 2)}
        report(45, "整首分离完成，开始去混响与变声")
    else:
        report(8, "在整首混音上挑候选副歌")
        try:
            window = _prepare_window(source, work, target, duration,
                                 start_hint=(chorus_start if mode != "full" else None))
        except Exception as error:
            print("[chorus-mw] fallback=full_separation reason=%s" % error, flush=True)
            report(12, "先切后分离失败，回退整首分离")
            window = _prepare_full(source, work, target)
        vocal_cut, inst_cut = window["vocal"], window["inst"]
        mark("separate")
        report(52, "分离了 %.1fs，窗口 %.1fs-%.1fs" % (
            window.get("sep_span", 0), window["start"], window["end"]))
        print("[chorus-mw] window=%.1fs-%.1fs sep_span=%.1fs" % (
            window["start"], window["end"], window.get("sep_span", 0)), flush=True)

    # 0.9.3：按音域自动移调。调用方没指定升降调（key_shift=0）时，
    # 先量这段人声的中位基频，再把它挪到目标区间；人声和伴奏一起挪。
    auto_shift_note = ""
    if key_shift == 0 and config.AUTO_PITCH:
        try:
            import pitch as pitch_mod
            auto_semis, measured_hz = pitch_mod.auto_shift(vocal_cut, config.TARGET_F0, config.MAX_AUTO_SHIFT)
            if auto_semis:
                key_shift = auto_semis
                shift_instrumental = float(auto_semis)
                auto_shift_note = "自动移调 %+d 半音（人声 %.0fHz → 目标 %.0fHz）" % (
                    auto_semis, measured_hz, config.TARGET_F0)
                print("[chorus-mw] auto_pitch measured=%.1fHz target=%.0fHz shift=%+d" % (
                    measured_hz, config.TARGET_F0, auto_semis), flush=True)
                report(57, auto_shift_note)
            else:
                auto_shift_note = "自动移调 0 半音（人声 %.0fHz 已在目标附近）" % measured_hz
                print("[chorus-mw] auto_pitch measured=%.1fHz target=%.0fHz shift=0" % (
                    measured_hz, config.TARGET_F0), flush=True)
        except Exception as error:
            print("[chorus-mw] auto_pitch failed: %s" % error, flush=True)

    # 并行：伴奏切段+移调（CPU / rubberband）与去混响（GPU）同时开跑
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        inst_future = executor.submit(_prepare_instrumental, inst_cut, work, shift_instrumental)
        report(58, "去混响（%s）" % dereverb_label)
        clean_vocal = rvc.dereverb(
            vocal_cut,
            os.path.join(work, "dereverb", "vocal"),
            os.path.join(work, "dereverb", "rest"),
            dereverb_label,
        )
        mark("dereverb")
        report(70, "去混响完成，开始变声")
        converted = rvc.convert(
            clean_vocal,
            model,
            key_shift,
            f0,
            idx,
            rms,
            prot,
        )
        mark("convert")
        shifted_inst = inst_future.result()
        mark("inst_prep")
    finally:
        executor.shutdown(wait=True)

    report(88, "变声完成，混音中")
    final = os.path.join(
        config.OUT_DIR,
        "%s_%s_%s.wav" % (_safe(os.path.splitext(os.path.basename(source))[0], 40),
                          _safe(os.path.splitext(model)[0], 20), token[-6:]),
    )
    mix(converted, shifted_inst, final, inst_db, 0.0, vocal_db)

    total = round(time.time() - started, 1)
    meta = {
        "source": source,
        "model": model,
        "key_shift": key_shift,
        "mode": mode,
        "target": round(target, 1),
        "chorus": [window["start"], window["end"]],
        "sep_span": window.get("sep_span", 0),
        "seconds": total,
        "f0_method": f0,
        "index_rate": round(idx, 4),
        "rms_mix_rate": round(rms, 4),
        "protect": round(prot, 4),
        "inst_volume_db": round(inst_db, 2),
        "vocal_volume_db": round(vocal_db, 2),
    }
    cache_store(key, final, meta)
    print("[chorus-mw] done mode=%s target=%.1fs seped=%.1fs window=%.1fs-%.1fs "
          "f0=%s index_rate=%.2f rms_mix=%.2f protect=%.2f inst_db=%.1f vocal_db=%.1f "
          "total=%.1fs" % (
              mode, target, window.get("sep_span", 0), window["start"], window["end"],
              f0, idx, rms, prot, inst_db, vocal_db, total), flush=True)
    report(100, "完成")
    shutil.rmtree(work, ignore_errors=True)
    note = "模式 %s，窗口 %.1fs-%.1fs，耗时 %.1fs" % (
        mode, window["start"], window["end"], total)
    if auto_shift_note:
        note += "，" + auto_shift_note
    return final, False, note


def _default_model():
    models = rvc.list_models()
    if not models:
        raise RuntimeError("assets/weights 里没有 .pth 模型")
    return models[0]
