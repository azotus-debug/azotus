from .models import SubtitleEvent
from .main import finalize, normalize_segments_for_review, compute_srt_text_coverage
from .formatting import strip_metadata_tags


def _lowercase_spurious_midline_caps(text: str) -> str:
    # Guardrail for legacy quality expectations: "Var Og ..." -> "Var og ..."
    if not text:
        return text
    protected = {"Guð", "Jesús", "Kristur", "Andi", "Ísrael"}
    fixed_lines = []
    for line in text.split("\n"):
        parts = line.split()
        if len(parts) >= 3:
            first = parts[0]
            second = parts[1]
            if (
                not first.endswith((".", "!", "?", ":"))
                and second[:1].isupper()
                and not second.isupper()
                and second not in protected
            ):
                parts[1] = second[:1].lower() + second[1:]
        fixed_lines.append(" ".join(parts) if parts else line)
    return "\n".join(fixed_lines)


def _text_quality_pass(events: list, target_language: str = "is", logger=None) -> list:
    """
    Compatibility shim used by legacy tests/callers.
    Prefer the legacy implementation when available; otherwise run a minimal fallback.
    """
    try:
        from workers.finalizer_v1_backup import _text_quality_pass as legacy_text_quality_pass

        processed = legacy_text_quality_pass(events, target_language=target_language, logger=logger)
        for ev in processed:
            if isinstance(ev, dict) and isinstance(ev.get("text"), str):
                ev["text"] = _lowercase_spurious_midline_caps(ev["text"])
        return processed
    except Exception:
        processed = []
        for ev in events or []:
            if not isinstance(ev, dict):
                continue
            current = dict(ev)
            text = current.get("text")
            if isinstance(text, str):
                text = strip_metadata_tags(text)
                text = _lowercase_spurious_midline_caps(text)
                current["text"] = text
            processed.append(current)
        return processed


__all__ = [
    "SubtitleEvent",
    "finalize",
    "normalize_segments_for_review",
    "compute_srt_text_coverage",
    "_text_quality_pass",
]
