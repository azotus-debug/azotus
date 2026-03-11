"""
AI Editor — stateless AI editing functions for the subtitle editor.

Two features:
  1. batch_qc_fix()  — Fix all QC violations (CPS / line-length) in one call
  2. get_alternatives() — Get 3 alternative translations for a single segment

Uses google.genai SDK (same as translation_engine.py).
Does NOT write to disk — returns suggestions only.
"""

import json
import logging
import os
from typing import List, Dict, Any, Optional

from google import genai
from google.genai import types

import config
import profiles

logger = logging.getLogger("Omega.AIEditor")

# QC thresholds (must match frontend constants)
MAX_CHARS_PER_LINE = 42
CPS_IDEAL = 14.0
CPS_TIGHT = 17.0
MIN_DURATION = 1.0


def _get_client() -> genai.Client:
    """Create a google.genai client with Vertex AI backend."""
    project_id = (
        os.environ.get("GOOGLE_CLOUD_PROJECT")
        or getattr(config, "OMEGA_CLOUD_PROJECT", None)
    )
    location = (
        os.environ.get("GOOGLE_CLOUD_LOCATION")
        or getattr(config, "GEMINI_LOCATION", "global")
    )
    return genai.Client(vertexai=True, project=project_id, location=location)


def _compute_cps(text: str, start: float, end: float) -> float:
    """Compute characters per second for a segment."""
    duration = end - start
    if duration <= 0:
        return 999.0
    char_count = len(text.replace("\n", ""))
    return char_count / duration


def _longest_line(text: str) -> int:
    """Return the length of the longest line in the text."""
    lines = text.split("\n")
    return max((len(line) for line in lines), default=0)


def _has_qc_issue(seg: dict) -> bool:
    """Check if a segment has a QC violation."""
    text = seg.get("text", "")
    start = float(seg.get("start", 0))
    end = float(seg.get("end", 0))

    if not text.strip():
        return False

    cps = _compute_cps(text, start, end)
    longest = _longest_line(text)

    return cps > CPS_TIGHT or longest > MAX_CHARS_PER_LINE


def _describe_issue(seg: dict) -> str:
    """Describe the QC issue for a segment."""
    text = seg.get("text", "")
    start = float(seg.get("start", 0))
    end = float(seg.get("end", 0))
    issues = []

    cps = _compute_cps(text, start, end)
    if cps > CPS_TIGHT:
        issues.append(f"CPS {cps:.1f} (max {CPS_TIGHT})")

    longest = _longest_line(text)
    if longest > MAX_CHARS_PER_LINE:
        issues.append(f"Line {longest} chars (max {MAX_CHARS_PER_LINE})")

    return " + ".join(issues) if issues else "OK"


def batch_qc_fix(
    segments: List[Dict],
    language: str = "is",
    profile_key: str = "standard",
) -> List[Dict[str, Any]]:
    """
    Fix all QC violations in a batch of segments.

    Args:
        segments: Full segment list from the editor.
        language: Target language code.
        profile_key: Profile key for language rules.

    Returns:
        List of fix suggestions:
        [{"id": 5, "original": "...", "suggested": "...", "reason": "CPS 19.2 -> 14.1"}]
    """
    # 1. Filter segments with QC issues
    problem_segments = [s for s in segments if _has_qc_issue(s)]

    if not problem_segments:
        return []

    logger.info("batch_qc_fix: %d segments with QC issues out of %d total",
                len(problem_segments), len(segments))

    # 2. Build prompt
    lang_rules = profiles.get_system_instruction(language, profile_key)

    # Build the segment data for the prompt
    seg_data = []
    for seg in problem_segments:
        text = seg.get("text", "")
        start = float(seg.get("start", 0))
        end = float(seg.get("end", 0))
        duration = end - start
        cps = _compute_cps(text, start, end)
        longest = _longest_line(text)
        source = seg.get("source_text", "")

        seg_data.append({
            "id": seg.get("id"),
            "text": text,
            "source_text": source,
            "duration": round(duration, 2),
            "current_cps": round(cps, 1),
            "longest_line": longest,
            "issue": _describe_issue(seg),
        })

    prompt = f"""You are fixing subtitle QC violations for broadcast.

{lang_rules}

--- BROADCAST CONSTRAINTS ---
- MAX {MAX_CHARS_PER_LINE} characters per line
- MAX 2 lines per subtitle
- Target CPS (characters per second) <= {CPS_IDEAL}
- Hard limit CPS <= {CPS_TIGHT}
- Duration of each segment is FIXED — you cannot change timing

--- TASK ---
Fix each segment below to satisfy the constraints. Keep the meaning intact.
Strategies: shorten words, use synonyms, restructure sentences, abbreviate where natural.
If source_text is provided, you may re-translate from scratch to get a shorter result.

--- SEGMENTS TO FIX ---
{json.dumps(seg_data, ensure_ascii=False, indent=2)}

--- OUTPUT FORMAT ---
Return a JSON array. For each segment:
{{"id": <segment_id>, "text": "<fixed text>", "reason": "<brief explanation>"}}

Only include segments you actually changed. If a segment cannot be fixed without losing meaning, omit it."""

    # 3. Call Gemini
    try:
        client = _get_client()
        response = client.models.generate_content(
            model=config.MODEL_ASSISTANT,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.3,
                response_mime_type="application/json",
            ),
        )

        raw = response.text or "[]"
        fixes_raw = json.loads(raw)

        if not isinstance(fixes_raw, list):
            fixes_raw = fixes_raw.get("fixes", []) if isinstance(fixes_raw, dict) else []

    except Exception as e:
        logger.error("batch_qc_fix AI call failed: %s", e, exc_info=True)
        return []

    # 4. Enrich with original text and compute new metrics
    original_map = {s.get("id"): s for s in problem_segments}
    fixes = []

    for fix in fixes_raw:
        seg_id = fix.get("id")
        new_text = fix.get("text", "")
        reason = fix.get("reason", "")

        if seg_id is None or not new_text:
            continue

        orig = original_map.get(seg_id)
        if not orig:
            continue

        original_text = orig.get("text", "")
        start = float(orig.get("start", 0))
        end = float(orig.get("end", 0))

        # Skip if AI returned the same text
        if new_text.strip() == original_text.strip():
            continue

        old_cps = _compute_cps(original_text, start, end)
        new_cps = _compute_cps(new_text, start, end)
        old_longest = _longest_line(original_text)
        new_longest = _longest_line(new_text)

        # Build a descriptive reason
        parts = []
        if old_cps > CPS_TIGHT:
            parts.append(f"CPS {old_cps:.1f} -> {new_cps:.1f}")
        if old_longest > MAX_CHARS_PER_LINE:
            parts.append(f"Line {old_longest} -> {new_longest}")
        auto_reason = " + ".join(parts) if parts else reason

        fixes.append({
            "id": seg_id,
            "original": original_text,
            "suggested": new_text,
            "reason": auto_reason,
            "old_cps": round(old_cps, 1),
            "new_cps": round(new_cps, 1),
            "old_longest_line": old_longest,
            "new_longest_line": new_longest,
        })

    logger.info("batch_qc_fix: returning %d fixes", len(fixes))
    return fixes


def get_alternatives(
    segment: Dict,
    context_before: List[Dict] = None,
    context_after: List[Dict] = None,
    language: str = "is",
    profile_key: str = "standard",
) -> List[Dict[str, Any]]:
    """
    Get 3 alternative translations for a single segment.

    Args:
        segment: The segment to get alternatives for.
        context_before: Up to 3 segments before (for context).
        context_after: Up to 3 segments after (for context).
        language: Target language code.
        profile_key: Profile key for language rules.

    Returns:
        List of 3 alternatives:
        [{"label": "Tight", "text": "...", "cps": 12.1}, ...]
    """
    context_before = context_before or []
    context_after = context_after or []

    text = segment.get("text", "")
    source_text = segment.get("source_text", "")
    start = float(segment.get("start", 0))
    end = float(segment.get("end", 0))
    duration = end - start

    if not text.strip():
        return []

    lang_rules = profiles.get_system_instruction(language, profile_key)

    # Build context strings
    before_ctx = "\n".join(
        f"  [{s.get('id')}] {s.get('text', '')}"
        for s in context_before[-3:]
    )
    after_ctx = "\n".join(
        f"  [{s.get('id')}] {s.get('text', '')}"
        for s in context_after[:3]
    )

    prompt = f"""You are providing alternative translations for a broadcast subtitle.

{lang_rules}

--- BROADCAST CONSTRAINTS ---
- MAX {MAX_CHARS_PER_LINE} characters per line
- MAX 2 lines per subtitle
- Target CPS <= {CPS_IDEAL}, hard limit <= {CPS_TIGHT}
- Segment duration: {duration:.2f}s (FIXED — cannot change timing)

--- CONTEXT (surrounding subtitles) ---
Before:
{before_ctx if before_ctx else "  (start of program)"}

>>> CURRENT SEGMENT [{segment.get('id')}]:
  Source (English): {source_text if source_text else "(not available)"}
  Current translation: {text}
  Duration: {duration:.2f}s
  Current CPS: {_compute_cps(text, start, end):.1f}

After:
{after_ctx if after_ctx else "  (end of program)"}

--- TASK ---
Provide exactly 3 alternative translations for the current segment.
Each must be a complete, broadcast-quality subtitle.

1. **Tight** — Minimize character count / CPS. Shortest natural phrasing.
2. **Natural** — Best-sounding Icelandic, optimal flow and readability.
3. **Literal** — Closest to the English source while staying natural.

All three MUST respect the {MAX_CHARS_PER_LINE}-char line limit and 2-line maximum.

--- OUTPUT FORMAT ---
Return a JSON array of exactly 3 objects:
[
  {{"label": "Tight", "text": "<translation>"}},
  {{"label": "Natural", "text": "<translation>"}},
  {{"label": "Literal", "text": "<translation>"}}
]"""

    try:
        client = _get_client()
        response = client.models.generate_content(
            model=config.MODEL_ASSISTANT,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.7,
                response_mime_type="application/json",
            ),
        )

        raw = response.text or "[]"
        alts_raw = json.loads(raw)

        if not isinstance(alts_raw, list):
            alts_raw = alts_raw.get("alternatives", []) if isinstance(alts_raw, dict) else []

    except Exception as e:
        logger.error("get_alternatives AI call failed: %s", e, exc_info=True)
        return []

    # Enrich with CPS calculations
    alternatives = []
    for alt in alts_raw[:3]:
        alt_text = alt.get("text", "")
        label = alt.get("label", "Option")

        if not alt_text.strip():
            continue

        cps = _compute_cps(alt_text, start, end)
        longest = _longest_line(alt_text)

        alternatives.append({
            "label": label,
            "text": alt_text,
            "cps": round(cps, 1),
            "longest_line": longest,
        })

    logger.info("get_alternatives: returning %d alternatives for segment %s",
                len(alternatives), segment.get("id"))
    return alternatives
