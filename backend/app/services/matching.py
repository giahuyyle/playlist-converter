import re
import unicodedata
from difflib import SequenceMatcher

from app.models.track import Track

NOISE = re.compile(
    r"\b(official\s+(music\s+)?(video|audio)|music video|lyrics?|visuali[sz]er|hd|4k|1080p)\b",
    re.IGNORECASE,
)
VERSIONS = (
    "live",
    "remix",
    "acoustic",
    "instrumental",
    "radio edit",
    "slowed",
    "sped up",
    "karaoke",
    "cover",
    "extended",
    "remaster",
)


def normalize(text):
    text = unicodedata.normalize("NFKC", text).casefold()
    text = NOISE.sub(" ", text)
    text = re.sub(r"\b(?:featuring|feat|ft)\b\.?\s*", " feat ", text)
    return " ".join(re.sub(r"[^\w\s]", " ", text).split())


def versions(text):
    normalized = normalize(text)
    result = {
        v
        for v in VERSIONS
        if re.search(r"\b" + v + (r"(?:ed)?" if v == "remaster" else "") + r"\b", normalized)
    }
    if re.search(r"\b\d+\s*(hours?|hrs?)\b", normalized):
        result.add("long mix")
    return result


def similarity(a, b):
    return SequenceMatcher(None, normalize(a), normalize(b)).ratio()


def score(source: Track, candidate: Track):
    if not source.available or not candidate.available:
        return 0.0
    if versions(source.title) != versions(candidate.title):
        return min(0.65, similarity(source.title, candidate.title) * 0.65)
    if source.isrc and candidate.isrc and source.isrc.upper() == candidate.isrc.upper():
        return 1.0
    parts = [(similarity(source.title, candidate.title), 0.45)]
    if source.artists and candidate.artists:
        a, b = " ".join(source.artists), " ".join(candidate.artists)
        artist = similarity(a, b)
        if normalize(a) == normalize(candidate.artists[0]) or normalize(b) == normalize(
            source.artists[0]
        ):
            artist = 1.0
        parts.append((artist, 0.35))
    if source.duration_ms and candidate.duration_ms:
        difference = abs(source.duration_ms - candidate.duration_ms)
        if difference > max(30000, source.duration_ms * 0.2):
            return 0.3
        parts.append((max(0, 1 - difference / 30000), 0.15))
    if source.album and candidate.album:
        parts.append((similarity(source.album, candidate.album), 0.05))
    result = sum(s * w for s, w in parts) / sum(w for _, w in parts)
    if not source.artists or not candidate.artists:
        result = min(result, 0.85)
    if candidate.provider == "youtube":
        result = min(
            1,
            result + (0.01 if candidate.official else 0) + (0.005 if candidate.music else 0),
        )
    return round(result, 4)


def rank(source, candidates):
    return sorted(
        [{"track": c.model_dump(), "confidence": score(source, c)} for c in candidates],
        key=lambda x: x["confidence"],
        reverse=True,
    )[:5]
