"""选歌打分：从网易云搜索结果里挑出「用户点的这一首」。

规则照搬 astrbot-plugin-chorus-cover 的 `source_router.pick_best_song`
（2026-11-14 用户要求「直接参考/照抄」）。中间层原来用的是 `_score_song`：
+100 完全一致 / +70 包含 / -45 翻唱词，然后**无条件取最高分**，没有门槛。
歌名一旦脏了（「一下晴天」「起风了 谢谢」），就唱了另一首歌。

现在：硬条件淘汰 + 权重打分 + 50 分门槛，确认不了就报错，绝不拿无关歌顶替。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

MIN_SCORE = 50.0

_NON_WORD_RE = re.compile(r"[\W_]+", re.UNICODE)

_ACCOMPANIMENT_WORDS = (
    "伴奏", "纯享", "纯音乐", "消音", "无人声",
    "offvocal", "instrumental", "karaoke", "dj版",
)

_COVER_VERSION_WORDS = (
    "翻唱", "cover", "深情版", "女声版", "男声版", "童声版", "钢琴版", "吉他版",
    "dj版", "remix", "慢速", "加速", "降调", "升调", "片段", "剪辑", "8d",
    "抖音", "翻自", "原唱",
)

_ARTIST_SPLIT_RE = re.compile(r"[、/&,，;；]|\bfeat\.?\b|\bx\b", re.IGNORECASE)


@dataclass
class Candidate:
    id: int
    name: str
    artists: str = ""
    duration_ms: int = 0
    alias: str = ""
    score: float = 0.0


def normalize_text(value: Any) -> str:
    return _NON_WORD_RE.sub("", str(value or "").casefold())


def _artist_text(song: dict) -> str:
    artists = song.get("artists") or song.get("ar") or []
    if isinstance(artists, str):
        return artists.strip()
    return "、".join(str(a.get("name") or "") for a in artists if isinstance(a, dict)).strip()


def _alias_text(song: dict) -> str:
    value = song.get("alias") or song.get("alia") or song.get("tns") or ""
    if isinstance(value, (list, tuple)):
        return " / ".join(str(item) for item in value if str(item).strip())
    return str(value or "").strip()


def candidate_from_song(song: dict) -> Candidate | None:
    """网易云搜索结果 → 候选。"""
    try:
        song_id = int(song.get("id") or 0)
    except (TypeError, ValueError):
        return None
    name = str(song.get("name") or "").strip()
    if not song_id or not name:
        return None
    duration = song.get("dt") or song.get("duration") or 0
    if duration and duration < 1000:      # 秒制的数据换成毫秒
        duration = int(duration) * 1000
    return Candidate(id=song_id, name=name, artists=_artist_text(song),
                     duration_ms=int(duration or 0), alias=_alias_text(song))


def is_accompaniment(*texts: Any) -> bool:
    joined = "".join(str(text or "") for text in texts)
    compact = re.sub(r"\s+", "", joined.casefold())
    if any(word in compact for word in _ACCOMPANIMENT_WORDS):
        return True
    return bool(re.search(r"(?<![a-z])inst(?![a-z])", joined.casefold()))


def is_cover_version(*texts: Any) -> bool:
    joined = "".join(str(text or "") for text in texts)
    compact = re.sub(r"\s+", "", joined.casefold())
    return any(word in compact for word in _COVER_VERSION_WORDS)


def _strip_version_markers(name: Any) -> str:
    text = str(name or "")
    text = re.sub(r"[（(\[【][^）)\]】]*[）)\]】]", "", text)
    for word in _COVER_VERSION_WORDS:
        text = re.sub(word, "", text, flags=re.IGNORECASE)
    return text.strip()


def _artist_matches(candidate_artists: str, wanted: str) -> bool:
    if not wanted:
        return False
    wanted_text = re.sub(r"\s+", "", str(wanted)).casefold()
    for part in _ARTIST_SPLIT_RE.split(str(candidate_artists or "")):
        text = re.sub(r"\s+", "", part).casefold()
        if not text:
            continue
        if text == wanted_text:
            return True
        if wanted_text in text and len(text) - len(wanted_text) <= 8:
            return True
    return False


def _one_char_apart(left: str, right: str) -> bool:
    if len(left) != len(right) or len(left) < 3:
        return False
    return sum(1 for a, b in zip(left, right) if a != b) <= 1


def pick_best_song(candidates: list, query: str, artist: str = "") -> tuple:
    """返回 (候选或 None, 状态, 说明)。状态：ok / empty / no_match / only_accompaniment。"""
    usable = [c for c in candidates if isinstance(c, Candidate)]
    if not usable:
        return None, "empty", "没有候选"

    normalized_query = normalize_text(query)
    normalized_artist = normalize_text(artist)
    if not normalized_query:
        return usable[0], "ok", "空查询，取第一条"

    user_versions = [w for w in _COVER_VERSION_WORDS if w in normalized_query]
    scored = []
    excluded = 0
    for index, candidate in enumerate(usable):
        name_norm = normalize_text(f"{candidate.name} {candidate.alias}")
        stripped_norm = normalize_text(_strip_version_markers(candidate.name))

        if is_accompaniment(candidate.name, candidate.alias):
            excluded += 1
            continue

        if normalized_artist and not _artist_matches(candidate.artists, artist):
            continue

        if user_versions and not any(word in name_norm for word in user_versions):
            continue

        title_norm = normalize_text(candidate.name)
        if normalized_query == title_norm:
            score = 100.0
        elif stripped_norm == normalized_query:
            score = 88.0
        elif normalized_query in title_norm and len(title_norm) - len(normalized_query) <= 12:
            score = 70.0
        elif _one_char_apart(title_norm, normalized_query):
            score = 62.0
        elif normalized_query in stripped_norm and len(stripped_norm) - len(normalized_query) <= 8:
            score = 65.0
        else:
            continue

        if normalized_artist and _artist_matches(candidate.artists, artist):
            score += 45.0
        if user_versions and any(word in name_norm for word in user_versions):
            score += 25.0

        live_dj_remix = (
            re.search(r"(?<![a-z])(live|dj|remix|inst)", name_norm)
            or re.search(r"[（(\[]\s*(dj|remix)", candidate.name, re.IGNORECASE)
            or any(word in candidate.name for word in ("现场", "演唱会", "音乐会", "翻唱", "翻自"))
        )
        if live_dj_remix and not any(word in user_versions for word in ("live", "dj", "remix")):
            score -= 30.0

        if not normalized_artist and is_cover_version(candidate.name, candidate.alias):
            score -= 45.0

        duration = candidate.duration_ms or 0
        if 0 < duration < 60000:
            score -= 40.0
        elif duration >= 120000:
            score += 3.0

        candidate.score = score
        scored.append((score, index, candidate))

    if not scored:
        status = "only_accompaniment" if excluded else "no_match"
        return None, status, f"{len(usable)} 个候选全部被淘汰"

    scored.sort(key=lambda item: (-item[0], item[1]))
    best_score, _index, best = scored[0]
    summary = " | ".join(f"{s:.0f}:{c.name}-{c.artists or '?'}" for s, _i, c in scored[:3])
    if best_score < MIN_SCORE:
        return None, "no_match", f"最高分 {best_score:.0f} < {MIN_SCORE:.0f}：{summary}"
    return best, "ok", summary
