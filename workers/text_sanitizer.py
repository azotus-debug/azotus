import re
from typing import Any, Dict, Iterable, Mapping, Optional

# This corruption was observed in Icelandic payloads where JSON "\t" escapes
# were emitted instead of literal "t" characters.
T_ESCAPE_GUARD_LANGS = {"is"}

_TAB_PREFIX_RE = re.compile(r'(^|[\s(\[{"\'“”„«»])\t(?=\w)', flags=re.UNICODE)
_TAB_INLINE_RE = re.compile(r"(?<=\w)\t(?=\w)", flags=re.UNICODE)
_TAB_SUFFIX_RE = re.compile(r'(?<=\w)\t($|[\s)\]}"\'”».,;:!?])', flags=re.UNICODE)


def repair_tab_escaped_t(text: Any) -> Any:
    """
    Repair strings where escaped tab tokens (\t) replaced intended letter 't'.
    """
    if not isinstance(text, str) or "\t" not in text:
        return text
    repaired = _TAB_INLINE_RE.sub("t", text)
    repaired = _TAB_PREFIX_RE.sub(r"\1t", repaired)
    repaired = _TAB_SUFFIX_RE.sub(r"t\1", repaired)
    return repaired.replace("\t", " ")


def _segment_text_stats(
    segments: Iterable[Mapping[str, Any]],
    text_key: str,
) -> Dict[str, float]:
    chunks = []
    for seg in segments:
        value = seg.get(text_key, "")
        if isinstance(value, str):
            chunks.append(value)
    joined = " ".join(chunks)
    letters = [ch.lower() for ch in joined if ch.isalpha()]
    letter_count = len(letters)
    t_count = sum(1 for ch in letters if ch == "t")
    t_ratio = float(t_count) / float(letter_count) if letter_count else 0.0
    return {
        "char_count": len(joined),
        "letter_count": letter_count,
        "t_count": t_count,
        "t_ratio": t_ratio,
    }


def detect_likely_t_escape_corruption(
    segments: Iterable[Mapping[str, Any]],
    *,
    target_language: Optional[str],
    text_key: str = "text",
    source_key: str = "source_text",
    min_letters: int = 3000,
    min_t_ratio: float = 0.01,
) -> Dict[str, Any]:
    """
    Detect catastrophic loss of lowercase 't' characters in subtitle text.
    """
    safe_segments = [seg for seg in segments if isinstance(seg, Mapping)]
    language = str(target_language or "").strip().lower()
    enabled = language in T_ESCAPE_GUARD_LANGS

    text_stats = _segment_text_stats(safe_segments, text_key=text_key)
    source_stats = _segment_text_stats(safe_segments, text_key=source_key)

    suspicious = False
    reason = ""

    if enabled and text_stats["letter_count"] >= min_letters:
        if text_stats["t_ratio"] < min_t_ratio:
            suspicious = True
            reason = (
                f"low t-ratio ({text_stats['t_ratio']:.6f} < {min_t_ratio:.6f}) "
                f"across {text_stats['letter_count']} letters"
            )

        source_t_count = int(source_stats["t_count"])
        current_t_count = int(text_stats["t_count"])
        if source_t_count >= 300 and current_t_count <= max(10, int(source_t_count * 0.15)):
            suspicious = True
            reason = (
                f"translation/source t-count mismatch ({current_t_count} vs {source_t_count})"
            )

    return {
        "enabled": enabled,
        "language": language,
        "suspicious": suspicious,
        "reason": reason,
        "thresholds": {
            "min_letters": int(min_letters),
            "min_t_ratio": float(min_t_ratio),
        },
        "text": text_stats,
        "source": source_stats,
    }
