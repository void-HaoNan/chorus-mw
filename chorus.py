"""从分离后的人声里挑一段副歌，并做软切。

思路（不需要任何模型）：
1. 人声轨算 50ms 帧的 RMS 包络；
2. 用一个 45 秒窗口在整首上滑动，对每个位置打分 = 平均能量 × 覆盖率，
   覆盖率 = 能量超过全曲 90 分位的帧占比。副歌的特征就是「又响又密、几乎不空」；
3. 取分数最高的位置；
4. 软切：在目标起止点前后各几秒内找能量最低的帧（换气/句尾的停顿），
   把边界挪到那个位置，避免从半个字中间切开。

只依赖 numpy + soundfile，毫秒级，比调 librosa 的自相似矩阵快得多也稳得多。
"""
import os

import numpy as np
import soundfile as sf

import config


def _envelope(path, hop=0.05):
    data, sr = sf.read(path, dtype="float32", always_2d=True)
    mono = data.mean(axis=1)
    step = max(1, int(sr * hop))
    frames = len(mono) // step
    if frames < 4:
        return np.zeros(4, dtype="float32"), sr, len(mono) / float(sr)
    trimmed = mono[: frames * step].reshape(frames, step)
    env = np.sqrt(np.mean(trimmed ** 2, axis=1) + 1e-12)
    return env.astype("float32"), sr, len(mono) / float(sr)


def pick_window(vocal_path, seconds=None, hop_seconds=None,
                min_seconds=None, max_seconds=None):
    """返回 (start_sec, end_sec)。音频比目标短就整首。

    min_seconds / max_seconds 是软切后允许的长度区间，默认用 config 里的
    40~50 秒（只适合 45 秒副歌）。0.7.0 起先在整首混音上挑候选窗口、
    再在人声里精挑，两处目标长度不同，所以要能传进来。
    """
    seconds = float(seconds or config.CHORUS_SECONDS)
    hop_seconds = float(hop_seconds or config.CHORUS_HOP)
    min_keep = float(config.CHORUS_MIN_SECONDS if min_seconds is None else min_seconds)
    max_keep = float(config.CHORUS_MAX_SECONDS if max_seconds is None else max_seconds)
    if max_keep < min_keep:
        max_keep = min_keep
    env, sr, duration = _envelope(vocal_path)
    if duration <= seconds + 2:
        return 0.0, duration

    fps = 1.0 / 0.05  # 包络帧率
    win = int(seconds * fps)
    step = max(1, int(hop_seconds * fps))
    if len(env) <= win:
        return 0.0, duration

    csum = np.concatenate([[0.0], np.cumsum(env.astype("float64"))])
    csum_sq = np.concatenate([[0.0], np.cumsum((env > np.percentile(env, 90)).astype("float64"))])

    best_start, best_score = 0, -1.0
    for start in range(0, len(env) - win + 1, step):
        mean_energy = (csum[start + win] - csum[start]) / win
        coverage = (csum_sq[start + win] - csum_sq[start]) / win
        score = mean_energy * (0.35 + 0.65 * coverage)
        if score > best_score:
            best_start, best_score = start, score

    start_sec = best_start / fps
    end_sec = start_sec + seconds
    start_sec = _snap(env, fps, start_sec, duration)
    end_sec = _snap(env, fps, end_sec, duration)

    # 软切后长度兜底，别切出个 20 秒
    if end_sec - start_sec < min_keep:
        mid = (start_sec + end_sec) / 2.0
        start_sec = max(0.0, mid - seconds / 2.0)
        end_sec = min(duration, start_sec + seconds)
    if end_sec - start_sec > max_keep:
        end_sec = start_sec + max_keep
    return round(start_sec, 2), round(min(end_sec, duration), 2)


def _snap(env, fps, target_sec, duration):
    """把边界挪到附近能量最低的位置（换气点），挪不动就原样返回。"""
    if target_sec <= 0.2 or target_sec >= duration - 0.2:
        return target_sec
    radius = int(config.CHORUS_SNAP * fps)
    center = int(target_sec * fps)
    lo = max(1, center - radius)
    hi = min(len(env) - 2, center + radius)
    if hi <= lo:
        return target_sec
    # 用三点平滑后的局部能量，避免挑到单个零值噪声点
    seg = env[lo - 1: hi + 2]
    smooth = (seg[:-2] + seg[1:-1] + seg[2:]) / 3.0
    idx = int(np.argmin(smooth)) + lo
    return idx / fps


def describe(vocal_path, start_sec, end_sec):
    return "副歌窗口 %.1fs → %.1fs（时长 %.1fs）" % (
        start_sec, end_sec, max(0.0, end_sec - start_sec)
    )
