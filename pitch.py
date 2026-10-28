"""按音域自动移调：把源人声的中位基频挪到目标区间。

RVC 的变调只是整体移调，变多少最舒服取决于模型训练素材的音域。
固定 ±5 对不同歌都不一样，这里改成「测源人声 → 算差额」：

    shift = round(12 * log2(target / 源中位基频))

只依赖 numpy + soundfile，不引模型，几十毫秒。
"""

import numpy as np
import soundfile as sf

TARGET_DEFAULT = 220.0
MAX_SHIFT_DEFAULT = 12


def _load_mono_16k(path):
    data, sr = sf.read(path, dtype="float32", always_2d=True)
    mono = data.mean(axis=1)
    if sr != 16000:
        # 简单线性插值重采样够用：只用来测基频，不做回放
        target_len = int(len(mono) * 16000.0 / sr)
        if target_len < 1:
            return np.zeros(0, dtype="float32"), 16000
        idx = np.linspace(0, len(mono) - 1, target_len)
        mono = np.interp(idx, np.arange(len(mono)), mono).astype("float32")
    return mono, 16000


def median_f0(path, fmin=70.0, fmax=600.0):
    """返回人声帧的中位基频；测不出来返回 0。

    用自相关做粗估：取 40ms 一帧、20ms 一跳，只统计能量够、周期清晰的帧，
    避免把伴奏残响和静音算进去。
    """
    mono, sr = _load_mono_16k(path)
    if len(mono) < sr // 10:
        return 0.0
    frame = int(0.04 * sr)
    hop = int(0.02 * sr)
    if frame < 64 or len(mono) < frame:
        return 0.0
    rms_all = np.sqrt(np.mean(mono ** 2) + 1e-12)
    if rms_all < 1e-5:
        return 0.0
    lag_min = max(1, int(sr / fmax))
    lag_max = int(sr / fmin)
    values = []
    for start in range(0, len(mono) - frame, hop):
        seg = mono[start:start + frame]
        rms = float(np.sqrt(np.mean(seg ** 2)))
        if rms < rms_all * 0.35:
            continue
        seg = seg - seg.mean()
        corr = np.correlate(seg, seg, mode="full")[frame - 1:]
        if corr[0] <= 0:
            continue
        corr = corr / corr[0]
        lo = lag_min
        hi = min(lag_max, len(corr) - 1)
        if hi <= lo:
            continue
        window = corr[lo:hi]
        peak = int(np.argmax(window)) + lo
        clarity = float(corr[peak])
        if clarity < 0.45:
            continue
        values.append(sr / peak)
    if len(values) < 8:
        return 0.0
    median = float(np.median(values))
    # 八度误判修正：自相关容易抓到二倍频，男声 160Hz 会被测成 320Hz。
    # 中位数明显偏高时，把高值折半再取中位。
    if median > 280.0:
        folded = [v / 2.0 if v > 280.0 else v for v in values]
        folded_median = float(np.median(folded))
        if 70.0 <= folded_median <= 280.0:
            median = folded_median
    return median


def semitones_to_target(measured_hz, target_hz=TARGET_DEFAULT, max_shift=MAX_SHIFT_DEFAULT):
    """算出要把 measured 挪到 target 需要几个半音；四舍五入并夹在 ±max_shift。"""
    try:
        measured = float(measured_hz)
        target = float(target_hz)
    except (TypeError, ValueError):
        return 0
    if measured <= 0 or target <= 0:
        return 0
    shift = 12.0 * np.log2(target / measured)
    shift = int(round(shift))
    limit = int(max_shift)
    return max(-limit, min(limit, shift))


def auto_shift(path, target_hz=TARGET_DEFAULT, max_shift=MAX_SHIFT_DEFAULT):
    """一步到位：测基频 + 算移调量，返回 (shift, measured_hz)。"""
    measured = median_f0(path)
    return semitones_to_target(measured, target_hz, max_shift), measured
