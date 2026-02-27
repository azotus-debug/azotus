from typing import Dict, Tuple

# =============================================================================
# BROADCAST SUBTITLE STANDARDS
# =============================================================================
# These constants define broadcast-quality subtitle formatting.
# Based on Netflix Timed Text Style Guide, BBC Guidelines, and EBU-TT.
# =============================================================================

# --- TEXT LIMITS ---
MAX_CHARS_PER_LINE = 42  # Netflix/BBC standard
MAX_LINES = 2            # Maximum lines per subtitle

MAX_CHARS_TOTAL = MAX_CHARS_PER_LINE * MAX_LINES

# --- TIMING STANDARDS ---
# Minimum duration: 1.5s allows comfortable reading (was 1.0s)
# Netflix requires 5/6 second (0.833s) minimum, but 1.5s is better for non-native speakers
MIN_DURATION = 1.5

# Maximum duration: 7s prevents subtitle "sticking" on screen
MAX_DURATION = 7.0

# Minimum gap between subtitles: 2 frames at 24fps = 0.083s
# This prevents "flashing" when subtitles change rapidly
GAP_SECONDS = 0.083

# --- FRAME RATE ---
# Default framerate for frame-accurate timing
# Common rates: 23.976 (film), 24 (digital cinema), 25 (PAL/broadcast), 29.97 (NTSC)
DEFAULT_FRAMERATE = 23.976

# --- CPS (CHARACTERS PER SECOND) ---
# Reading speed targets - the key to subtitle readability
# Lower CPS = more time to read = better viewer experience
IDEAL_CPS = 15.0   # Target for optimal readability (was 17.0)
TIGHT_CPS = 17.0   # Maximum for broadcast (was 20.0)
MAX_CPS = 20.0     # Hard ceiling - truncate if exceeded

# Per-language CPS settings (ideal, tight)
# Based on reading speed research for broadcast subtitles
# Languages with compound words (Icelandic, Dutch, German) need slower CPS
# Romance languages with shorter words can handle faster CPS
LANGUAGE_CPS: Dict[str, Tuple[float, float]] = {
    "is": (12.0, 15.0),  # Icelandic: long compound words, complex morphology
    "nl": (13.0, 16.0),  # Dutch: compound words, similar reading speed to German
    "de": (13.0, 16.0),  # German: compound words
    "en": (15.0, 17.0),  # English: Netflix standard
    "es": (16.0, 18.0),  # Spanish: phonetic, faster reading
    "pt": (15.0, 17.0),  # Portuguese: similar to English
    "fr": (14.0, 17.0),  # French: slightly slower than English
    "it": (15.0, 17.0),  # Italian: similar to English
}
DEFAULT_CPS = (15.0, 17.0)

# --- MERGE PARAMETERS ---
# Used when combining short/fast subtitles
MERGE_GAP_MAX = 0.35        # Max gap to consider for merge (seconds)
MERGE_MAX_DURATION = 6.5    # Don't merge if result exceeds this (seconds)
MERGE_CPS_TRIGGER = 18.0    # Merge if CPS exceeds this
MERGE_SHORT_TRIGGER = 1.2   # Merge if duration below this (seconds)

# --- SPEECH ANCHORING (BBC/Netflix timing rules) ---
# These constants implement published broadcast standards for when subtitles appear/disappear
ANTICIPATION_MS = 200          # Netflix: subtitle may appear up to 200ms before first word
MAX_ANTICIPATION_S = 1.5       # BBC: never more than 1.5s before speech
MAX_HANG_S = 1.5               # BBC: never more than 1.5s after last word
MIN_HANG_S = 0.3               # BBC: minimum 0.3s after last word for eye adjustment
BBC_PER_WORD_S = 0.3           # BBC: 0.3 seconds per word minimum display time
NETFLIX_MIN_DURATION = 0.833   # Netflix: 5/6 second absolute floor

# --- SCENE DETECTION ---
# For speech-aware scene snapping
SCENE_THRESHOLD = 0.3       # FFmpeg scene detection threshold (0.0-1.0)
SCENE_SNAP_WINDOW = 0.5     # Snap if cut is within this many seconds of subtitle boundary
SCENE_SNAP_FRAMES = 11      # Netflix: snap if cut within 11 frames of subtitle boundary
SPEECH_GAP_THRESHOLD = 0.3  # Minimum speech gap to allow scene snap (seconds)

# --- CPS REBALANCING ---
# When consecutive subtitles have wildly different CPS, redistribute text
MIN_READABLE_CPS = 5.0        # Below this = text sitting too long, viewer gets bored
REBALANCE_CPS_RATIO = 3.0     # If max/min CPS ratio > this within a cluster, rebalance
REBALANCE_CLUSTER_GAP = 0.5   # Max gap (seconds) between segments to consider them a cluster

# --- PRE-SEGMENTATION ---
# Split transcription segments into subtitle-sized chunks BEFORE translation
TARGET_BLOCK_DURATION = 4.5   # Ideal subtitle duration (seconds)
MIN_BLOCK_DURATION = 2.0      # Never create a chunk shorter than this
MAX_BLOCK_DURATION = MAX_DURATION  # 7.0s ceiling

# Language expansion ratios (English → target)
EXPANSION_RATIOS: Dict[str, float] = {
    "is": 1.1, "nl": 1.05, "de": 1.15,
    "es": 1.1, "fr": 1.15, "pt": 1.1, "it": 1.05,
}
DEFAULT_EXPANSION_RATIO = 1.0

# --- CONTEXT ---
CONTEXT_GAP_MAX = 3.0
MAX_PRIORITY_SEGMENTS = 120


def get_cps_for_language(lang_code: str) -> Tuple[float, float]:
    """Get (ideal_cps, tight_cps) for a language code."""
    return LANGUAGE_CPS.get(lang_code.lower(), DEFAULT_CPS)


def status_for_cps(cps: float) -> str:
    if cps <= IDEAL_CPS:
        return "OPTIMAL"
    if cps <= TIGHT_CPS:
        return "TIGHT"
    return "CRITICAL"


def build_constraint_items(
    source_segments: list[dict],
    translated_segments: list[dict],
) -> list[dict]:
    trans_map: Dict[str, str] = {}
    for seg in translated_segments or []:
        seg_id = seg.get("id")
        if seg_id is None:
            continue
        seg_id = str(seg_id)
        text = str(seg.get("text") or "").strip()
        if text:
            trans_map[seg_id] = text

    items: list[dict] = []
    for idx, seg in enumerate(source_segments or []):
        seg_id = seg.get("id")
        if seg_id is None:
            continue
        seg_id = str(seg_id)
        start = float(seg.get("start") or 0.0)
        end = float(seg.get("end") or start)
        duration = max(0.0, end - start)
        next_start = None
        if idx + 1 < len(source_segments):
            try:
                next_start = float(source_segments[idx + 1].get("start") or 0.0)
            except Exception:
                next_start = None
        gap_to_next = None
        max_available = None
        if next_start is not None:
            gap_to_next = next_start - end
            max_available = max(0.0, (next_start - GAP_SECONDS) - start)

        effective_duration = max(duration, MIN_DURATION)
        if max_available is not None and max_available > 0:
            effective_duration = min(effective_duration, max_available)

        text = trans_map.get(seg_id) or ""
        char_count = len(text)
        cps = (char_count / effective_duration) if effective_duration > 0 else 0.0
        status = status_for_cps(cps) if text else "OPTIMAL"

        items.append(
            {
                "id": seg_id,
                "duration": round(duration, 3),
                "effective_duration": round(effective_duration, 3),
                "gap_to_next": round(gap_to_next, 3) if gap_to_next is not None else None,
                "max_chars_total": MAX_CHARS_TOTAL,
                "max_chars_per_line": MAX_CHARS_PER_LINE,
                "target_cps": IDEAL_CPS,
                "current_cps": round(cps, 2),
                "status": status,
            }
        )
    return items


def build_priority_context(
    source_segments: list[dict],
    translated_segments: list[dict],
    *,
    include_tight: bool = True,
) -> list[dict]:
    items = build_constraint_items(source_segments, translated_segments)
    trans_map: Dict[str, str] = {}
    for seg in translated_segments or []:
        seg_id = seg.get("id")
        if seg_id is None:
            continue
        seg_id = str(seg_id)
        trans_map[seg_id] = str(seg.get("text") or "").strip()

    priority = []
    for idx, item in enumerate(items):
        status = item.get("status")
        if status == "CRITICAL" or (include_tight and status == "TIGHT"):
            priority.append((idx, item))

    critical = [entry for entry in priority if entry[1].get("status") == "CRITICAL"]
    tight = [entry for entry in priority if entry[1].get("status") == "TIGHT"]
    critical.sort(key=lambda entry: entry[1].get("current_cps", 0.0), reverse=True)
    tight.sort(key=lambda entry: entry[1].get("current_cps", 0.0), reverse=True)

    selected = []
    for entry in critical:
        if len(selected) >= MAX_PRIORITY_SEGMENTS:
            break
        selected.append(entry)
    if len(selected) < MAX_PRIORITY_SEGMENTS:
        for entry in tight:
            if len(selected) >= MAX_PRIORITY_SEGMENTS:
                break
            selected.append(entry)

    result = []
    for idx, item in selected:
        seg = source_segments[idx]
        start = float(seg.get("start") or 0.0)
        end = float(seg.get("end") or start)
        prev_ctx = None
        if idx > 0:
            prev_seg = source_segments[idx - 1]
            prev_end = float(prev_seg.get("end") or 0.0)
            if start - prev_end <= CONTEXT_GAP_MAX:
                prev_id = str(prev_seg.get("id"))
                prev_ctx = {
                    "id": prev_id,
                    "src": str(prev_seg.get("text") or "").strip(),
                    "draft": trans_map.get(prev_id, ""),
                }
        next_ctx = None
        if idx + 1 < len(source_segments):
            next_seg = source_segments[idx + 1]
            next_start = float(next_seg.get("start") or 0.0)
            if next_start - end <= CONTEXT_GAP_MAX:
                next_id = str(next_seg.get("id"))
                next_ctx = {
                    "id": next_id,
                    "src": str(next_seg.get("text") or "").strip(),
                    "draft": trans_map.get(next_id, ""),
                }

        active = {
            "id": item["id"],
            "src": str(seg.get("text") or "").strip(),
            "draft": trans_map.get(item["id"], ""),
            "effective_duration": item["effective_duration"],
            "gap_to_next": item["gap_to_next"],
            "target_cps": item["target_cps"],
            "max_chars_total": item["max_chars_total"],
            "max_chars_per_line": item["max_chars_per_line"],
            "current_cps": item["current_cps"],
            "status": item["status"],
        }

        result.append(
            {
                "context_prev": prev_ctx,
                "active": active,
                "context_next": next_ctx,
            }
        )
    return result
