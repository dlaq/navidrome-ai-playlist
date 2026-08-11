"""Cross-platform song normalization and Navidrome matching.

The search providers do not agree on how to represent the same recording.  A
song may arrive as ``Title (Live)`` or ``Title - Live版``, with artists joined
by ``/``, ``、``, ``feat.`` or ``&``.  This module keeps the original metadata
for display, but compares stable title/artist features and scores candidates
instead of relying on a single string key.

The implementation intentionally uses only the Python standard library so the
Docker image does not need a native fuzzy-matching dependency.
"""

from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


# Version/edition words are removed from the title's base form, but retained in
# ``version_tags`` so an exact Live/Remix candidate wins over an album version.
_VERSION_MARKERS: Tuple[Tuple[str, str], ...] = (
    ("live", "live"),
    ("live版", "live"),
    ("现场", "live"),
    ("现场版", "live"),
    ("演唱会", "live"),
    ("演出现场", "live"),
    ("remix", "remix"),
    ("re-mix", "remix"),
    ("混音", "remix"),
    ("重混", "remix"),
    ("mix", "remix"),
    ("remastered", "remaster"),
    ("remaster", "remaster"),
    ("重制", "remaster"),
    ("高清修复", "remaster"),
    ("dj", "dj"),
    ("舞曲版", "dj"),
    ("acoustic", "acoustic"),
    ("不插电", "acoustic"),
    ("unplugged", "acoustic"),
    ("instrumental", "instrumental"),
    ("伴奏", "instrumental"),
    ("纯音乐", "instrumental"),
    ("karaoke", "karaoke"),
    ("ktv", "karaoke"),
    ("demo", "demo"),
    ("小样", "demo"),
    ("cover", "cover"),
    ("翻唱", "cover"),
    ("edit", "edit"),
    ("radio edit", "edit"),
    ("单曲版", "edit"),
    ("album version", "album"),
    ("album version", "album"),
    ("original mix", "original"),
    ("原版", "original"),
    ("原唱", "original"),
    ("explicit", "explicit"),
    ("clean", "clean"),
    ("试听", "preview"),
    ("片段", "preview"),
    ("完整版", "full"),
)

_BRACKET_PAIRS = {"(": ")", "[": "]", "（": "）", "【": "】", "「": "」", "『": "』"}
_BRACKET_CONTENT = re.compile(r"[\(\[（【「『][^\)\]）】」』]*[\)\]）】」』]")
_SPACE_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\u3400-\u9fff]+", re.UNICODE)
_TITLE_LEADING_VERSION_RE = re.compile(
    r"^(?:live|live版|现场版|现场|remix|混音|dj版|acoustic|unplugged|伴奏|karaoke)\s*[-:：|·]+\s*",
    re.IGNORECASE,
)
_TITLE_SUFFIX_VERSION_RE = re.compile(
    r"(?:\s*[-–—|·:：]\s*|\s+)(?P<version>"
    r"live(?:\s+at)?|live版|现场版|现场|演唱会|remix|re-mix|混音|重混|mix|dj(?:\s*版)?|"
    r"(?:\d{4}\s+)?remaster(?:ed)?|重制版|高清修复|acoustic|unplugged|不插电|instrumental|伴奏|纯音乐|karaoke|ktv|demo|小样|cover|翻唱|"
    r"radio\s+edit|edit|album\s+version|original\s+mix|原版|原唱|explicit|clean|试听|片段|完整版"
    r")\s*$",
    re.IGNORECASE,
)
_TITLE_FEAT_RE = re.compile(
    r"(?:\s*[-–—|·:：]\s*|\s+)(?:feat\.?|ft\.?|featuring|with|和|与|合唱)\s+.+$",
    re.IGNORECASE,
)
_ARTIST_SPLIT_RE = re.compile(
    r"\s*(?:/|／|&|＆|、|,|，|;|；|•|·|×|\bx\b|\bfeat\.?\b|\bft\.?\b|"
    r"\bfeaturing\b|\bwith\b|\bvs\.?\b|\band\b|\bpres(?:ents)?\.?\b|合唱|对唱|与|和)\s*",
    re.IGNORECASE,
)


def _text(value: Any) -> str:
    """Return Unicode-normalized text suitable for comparison."""

    if value is None:
        return ""
    value = html.unescape(str(value))
    value = unicodedata.normalize("NFKC", value)
    value = value.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return _SPACE_RE.sub(" ", value).strip()


def _compact(value: str) -> str:
    """Remove separators while keeping letters, digits and CJK characters."""

    # NFKD + combining-mark removal makes ``Beyoncé`` and ``Beyonce`` compare
    # consistently without changing the original text shown in the UI.
    value = unicodedata.normalize("NFKD", _text(value)).casefold()
    value = "".join(char for char in value if not unicodedata.combining(char))
    return _PUNCT_RE.sub("", value)


def _version_tag(value: str) -> Optional[str]:
    compact = _compact(value)
    if not compact:
        return None
    for marker, tag in sorted(_VERSION_MARKERS, key=lambda item: len(item[0]), reverse=True):
        if _compact(marker) in compact:
            return tag
    return None


def _is_version_descriptor(value: str) -> bool:
    """Whether bracket/suffix content describes an edition rather than title."""

    value = _text(value).casefold()
    if not value:
        return False
    if _version_tag(value):
        return True
    # These are common source-platform labels that are not useful in a title.
    return bool(re.search(r"\b(?:version|ver\.?|official|audio|video|lyrics?)\b|版本|版$", value))


def title_features(title: str) -> Tuple[str, str, Tuple[str, ...]]:
    """Return ``(display-normalized, compact-base, version-tags)`` for a title."""

    value = _text(title)
    tags = set()

    # Remove bracketed edition labels while preserving meaningful brackets,
    # e.g. a title such as "Song (二)".
    def remove_bracket(match: re.Match[str]) -> str:
        content = match.group(0)[1:-1]
        tag = _version_tag(content)
        if tag or _is_version_descriptor(content):
            if tag:
                tags.add(tag)
            return " "
        # Feat/with in a title is artist metadata, not part of the title key.
        if re.search(r"\b(?:feat\.?|ft\.?|featuring|with)\b|合唱|对唱", content, re.I):
            return " "
        return match.group(0)

    value = _BRACKET_CONTENT.sub(remove_bracket, value)
    value = _TITLE_FEAT_RE.sub(" ", value)
    value = _TITLE_LEADING_VERSION_RE.sub("", value)

    # Strip one or more trailing edition labels ("Song - Live版", "Song Remix").
    for _ in range(3):
        match = _TITLE_SUFFIX_VERSION_RE.search(value)
        if not match:
            break
        tag = _version_tag(match.group("version"))
        if tag:
            tags.add(tag)
        value = value[: match.start()].strip()

    # A few providers append a bare edition marker without a separator.
    bare_suffix = re.search(
        r"(?P<version>(?:live版|现场版|remix版|混音版|伴奏版|纯音乐版|翻唱版|完整版))$",
        value,
        re.IGNORECASE,
    )
    if bare_suffix:
        tag = _version_tag(bare_suffix.group("version"))
        if tag:
            tags.add(tag)
        value = value[: bare_suffix.start()].strip()

    display = _SPACE_RE.sub(" ", value).strip(" -–—|·:：")
    return display, _compact(display), tuple(sorted(tags))


def artist_features(artist: str) -> Tuple[Tuple[str, ...], str]:
    """Return normalized artist components and a stable set key."""

    value = _text(artist)
    if not value:
        return (), ""

    if _compact(value) in {"unknown", "unknownartist", "variousartists", "未知", "未知歌手", "群星"}:
        return (), ""

    # Parenthesized artist aliases and featured artists are represented as
    # separate components.  This makes ``周杰伦 (Jay Chou)`` compatible with
    # ``周杰伦`` and still retains ``feat.`` guests for set comparison.
    value = re.sub(r"[\(\[（【「『\)\]）】」』]", "/", value)
    parts = []
    for part in _ARTIST_SPLIT_RE.split(value):
        compact = _compact(part)
        if compact and compact not in parts:
            parts.append(compact)
    return tuple(parts), "|".join(sorted(parts))


def normalize_title(title: str) -> str:
    """Return the edition-independent title key."""

    return title_features(title)[1]


def normalize_artists(artist: str) -> Tuple[str, ...]:
    """Return normalized individual artist keys."""

    return artist_features(artist)[0]


def _similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    if left in right or right in left:
        short, long = sorted((left, right), key=len)
        # Containment is useful for subtitles, but a one-character title must
        # not become a high-confidence match merely because it is contained.
        if len(short) >= 3:
            return max(SequenceMatcher(None, left, right).ratio(), len(short) / len(long) * 0.98)
    return SequenceMatcher(None, left, right).ratio()


def _artist_similarity(source: Sequence[str], candidate: Sequence[str]) -> Tuple[float, str]:
    if not source or not candidate:
        return 0.30, "missing"

    source_set, candidate_set = set(source), set(candidate)
    if source_set == candidate_set:
        return 1.0, "exact"
    if source_set.issubset(candidate_set) or candidate_set.issubset(source_set):
        return 0.92, "subset"
    if source_set.intersection(candidate_set):
        return 0.78, "overlap"

    pair_scores = []
    for left in source_set:
        pair_scores.append(max((_similarity(left, right) for right in candidate_set), default=0.0))
    best = sum(pair_scores) / len(pair_scores) if pair_scores else 0.0
    if best >= 0.86:
        return best, "fuzzy"
    return best, "none"


@dataclass(frozen=True)
class SongFeatures:
    title_display: str
    title_key: str
    title_tags: Tuple[str, ...]
    artists: Tuple[str, ...]
    artist_key: str
    album_key: str


def features_for(song: Any) -> SongFeatures:
    title_display, title_key, title_tags = title_features(getattr(song, "title", ""))
    artists, artist_key = artist_features(getattr(song, "artist", ""))
    album_key = _compact(getattr(song, "album", ""))
    return SongFeatures(title_display, title_key, title_tags, artists, artist_key, album_key)


def song_dedupe_key(song: Any) -> str:
    """Build a source-list key without collapsing Live and Remix variants."""

    feature = features_for(song)
    if not feature.title_key:
        return ""
    tags = ",".join(feature.title_tags)
    return f"{feature.title_key}|{feature.artist_key}|{tags}"


def deduplicate_songs(songs: Iterable[Any]) -> List[Any]:
    """Deduplicate source results while preserving first-seen playlist order."""

    unique: List[Any] = []
    seen = set()
    for song in songs:
        key = song_dedupe_key(song)
        if not key:
            continue
        if key not in seen:
            seen.add(key)
            unique.append(song)
    return unique


@dataclass(frozen=True)
class SongMatch:
    library_song: Any
    score: float
    method: str
    title_score: float
    artist_score: float
    artist_match: str
    source_song: Any = None


class SongMatcher:
    """Indexed, score-based matcher for source songs and Navidrome songs."""

    # A title-only candidate must be very strong.  Artist metadata is often
    # absent in scraped results, but an ambiguous short title should not match.
    FUZZY_THRESHOLD = 0.70
    TITLE_ONLY_THRESHOLD = 0.94

    def __init__(self, library: Sequence[Any]):
        self.library = list(library)
        self._features: Dict[int, SongFeatures] = {}
        self._title_index: Dict[str, List[Any]] = {}
        for song in self.library:
            feature = features_for(song)
            self._features[id(song)] = feature
            if feature.title_key:
                self._title_index.setdefault(feature.title_key, []).append(song)

    def _candidate_songs(self, source_feature: SongFeatures) -> List[Any]:
        exact = list(self._title_index.get(source_feature.title_key, []))
        if exact:
            return exact

        # Different providers may add a subtitle, a leading artist, or a
        # localized punctuation mark.  Search only similarly-sized titles to
        # keep fuzzy matching bounded for large libraries.
        source_len = len(source_feature.title_key)
        candidates = []
        for song in self.library:
            feature = self._features[id(song)]
            if not feature.title_key:
                continue
            length_delta = abs(len(feature.title_key) - source_len)
            if length_delta > max(8, int(max(source_len, len(feature.title_key)) * 0.55)):
                continue
            if _similarity(source_feature.title_key, feature.title_key) >= 0.55:
                candidates.append(song)
        return candidates

    @staticmethod
    def _version_score(source: SongFeatures, candidate: SongFeatures) -> float:
        source_tags, candidate_tags = set(source.title_tags), set(candidate.title_tags)
        if source_tags == candidate_tags:
            return 1.0
        if not source_tags or not candidate_tags:
            # A missing edition label should not block a match.
            return 0.86
        if source_tags.intersection(candidate_tags):
            return 0.95
        return 0.72

    def _score(self, source: SongFeatures, candidate: SongFeatures) -> Tuple[float, float, float, str]:
        title_score = _similarity(source.title_key, candidate.title_key)
        artist_score, artist_match = _artist_similarity(source.artists, candidate.artists)
        version_score = self._version_score(source, candidate)
        album_score = _similarity(source.album_key, candidate.album_key) if source.album_key and candidate.album_key else 0.0

        # Title is the strongest signal.  Artist set overlap handles featured
        # artists and platform-specific ordering; edition/album signals break
        # ties between Original, Live and Remix files.
        score = title_score * 0.64 + artist_score * 0.25 + version_score * 0.08 + album_score * 0.03
        return score, title_score, artist_score, artist_match

    def match(self, source_song: Any) -> Optional[SongMatch]:
        source_feature = features_for(source_song)
        if not source_feature.title_key:
            return None

        candidates = self._candidate_songs(source_feature)
        if not candidates:
            return None

        scored = []
        for candidate in candidates:
            candidate_feature = self._features[id(candidate)]
            score, title_score, artist_score, artist_match = self._score(source_feature, candidate_feature)
            scored.append((score, title_score, artist_score, artist_match, candidate))
        scored.sort(key=lambda item: item[0], reverse=True)
        best_score, title_score, artist_score, artist_match, best_song = scored[0]

        exact_title = title_score >= 0.995
        has_artist_evidence = artist_match in {"exact", "subset", "overlap", "fuzzy"} and artist_score >= 0.55
        unique_title = len(candidates) == 1

        accepted = False
        method = ""
        if exact_title and has_artist_evidence:
            accepted, method = True, "title+artist"
        elif exact_title and unique_title and (
            not source_feature.artists
            or artist_match == "missing"
            or artist_score >= 0.55
        ):
            accepted, method = True, "title"
        elif best_score >= self.FUZZY_THRESHOLD and title_score >= 0.74 and has_artist_evidence:
            accepted, method = True, "fuzzy"
        elif unique_title and title_score >= self.TITLE_ONLY_THRESHOLD and (
            not source_feature.artists
            or artist_match == "missing"
            or artist_score >= 0.55
        ):
            accepted, method = True, "fuzzy-title"

        if not accepted:
            return None
        return SongMatch(best_song, best_score, method, title_score, artist_score, artist_match)


def match_songs(source_songs: Iterable[Any], library: Sequence[Any]) -> Tuple[List[SongMatch], List[Any]]:
    """Match a source playlist and return ``(matches, unmatched)``."""

    matcher = SongMatcher(library)
    matches: List[SongMatch] = []
    unmatched: List[Any] = []
    used_library_ids = set()
    for source_song in deduplicate_songs(source_songs):
        result = matcher.match(source_song)
        if result is None:
            unmatched.append(source_song)
            continue
        library_id = getattr(result.library_song, "id", None)
        if library_id and library_id in used_library_ids:
            continue
        if library_id:
            used_library_ids.add(library_id)
        matches.append(SongMatch(
            library_song=result.library_song,
            score=result.score,
            method=result.method,
            title_score=result.title_score,
            artist_score=result.artist_score,
            artist_match=result.artist_match,
            source_song=source_song,
        ))
    return matches, unmatched
