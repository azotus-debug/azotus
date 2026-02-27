"""Pre-Translation Segmenter — the architectural fix.

Splits ElevenLabs transcription segments (1–28+ seconds) into subtitle-sized
chunks (2–7 seconds) BEFORE translation.  Each chunk gets a hard character
budget so the translator knows exactly how much text to produce.

This eliminates the root cause of CPS problems: translating on the wrong unit.
"""

import logging
from math import ceil
from typing import Dict, List, Optional, Tuple

from subtitle_standards import (
    MAX_CHARS_TOTAL,
    MAX_CHARS_PER_LINE,
    MAX_DURATION,
    MIN_DURATION,
    GAP_SECONDS,
    TARGET_BLOCK_DURATION,
    MIN_BLOCK_DURATION,
    MAX_BLOCK_DURATION,
    EXPANSION_RATIOS,
    DEFAULT_EXPANSION_RATIO,
    LANGUAGE_CPS,
    DEFAULT_CPS,
)

logger = logging.getLogger("PreSegmenter")

# ── Sentence / clause boundary characters ──────────────────────────────
_SENTENCE_END = frozenset(".?!")
_CLAUSE_END = frozenset(",;:–—")
_CONJUNCTIONS = frozenset({
    "and", "but", "or", "so", "yet", "for", "nor",       # English
    "that", "which", "who", "when", "where", "because",
    "although", "while", "after", "before", "until", "if",
})
# English prepositions/articles — splitting after these creates orphans in translation
_PREPOSITIONS = frozenset({
    "in", "on", "at", "to", "of", "by", "with", "from", "for",
    "the", "a", "an", "this", "that", "these", "those",
})


def _expansion_ratio(target_lang: str) -> float:
    """Get English → target expansion ratio."""
    return EXPANSION_RATIOS.get(target_lang.lower(), DEFAULT_EXPANSION_RATIO)


def _ideal_cps(target_lang: str) -> float:
    """Get the ideal CPS for the target language."""
    return LANGUAGE_CPS.get(target_lang.lower(), DEFAULT_CPS)[0]


def _char_budget(duration: float, target_lang: str) -> int:
    """Calculate max characters for a chunk based on its duration and language.

    Budget = duration × ideal_cps, capped at MAX_CHARS_TOTAL (84).
    """
    cps = _ideal_cps(target_lang)
    budget = int(duration * cps)
    return min(budget, MAX_CHARS_TOTAL)


def _estimate_translated_chars(english_text: str, target_lang: str) -> int:
    """Estimate how many characters the translated text will be."""
    ratio = _expansion_ratio(target_lang)
    return int(len(english_text) * ratio)


def _num_blocks_needed(
    duration: float, english_text: str, target_lang: str,
) -> int:
    """Calculate how many subtitle blocks a segment needs.

    Takes the max of:
    - Time-based: ceil(duration / TARGET_BLOCK_DURATION)
    - Char-based:  ceil(estimated_translated_chars / MAX_CHARS_TOTAL)
    Both constrained so each block is >= MIN_BLOCK_DURATION.
    """
    if duration <= 0:
        return 1
    by_time = ceil(duration / TARGET_BLOCK_DURATION)
    est_chars = _estimate_translated_chars(english_text, target_lang)
    by_chars = ceil(est_chars / MAX_CHARS_TOTAL)
    n = max(by_time, by_chars)
    # Ensure no block would be shorter than MIN_BLOCK_DURATION
    max_by_min_dur = max(1, int(duration / MIN_BLOCK_DURATION))
    return min(n, max_by_min_dur)


def _score_split_point(word_text: str, prev_words: list, idx: int, total_words: int) -> float:
    """Score a word boundary for splitting.  Lower = better split point.

    Returns a penalty (0.0 = perfect) added to the time-distance score.
    Prefers: sentence ends > clause ends > after conjunctions > any word.
    """
    stripped = word_text.rstrip()
    if not stripped:
        return 2.0

    last_char = stripped[-1]

    # Sentence end (. ? !) — best split point
    if last_char in _SENTENCE_END:
        return -2.0

    # Clause boundary (, ; : — –)
    if last_char in _CLAUSE_END:
        return -1.0

    # After a conjunction (split before the next clause)
    word_lower = stripped.lower().rstrip(".,;:!?")
    if word_lower in _CONJUNCTIONS:
        return -0.5

    # Penalize splitting after prepositions/articles (creates orphans in translation)
    if word_lower in _PREPOSITIONS:
        return 0.8

    # Avoid splitting right at start or end of segment
    if idx < 2 or idx > total_words - 3:
        return 1.5

    return 0.0


def _build_word_items(segment: dict) -> List[dict]:
    """Extract word-level timing from a segment.

    Each word item: {"text": str, "start": float, "end": float}
    Falls back to ratio-based estimation if no word timing.
    """
    words_timing = segment.get("words")
    text = str(segment.get("text") or "")
    seg_start = float(segment.get("start", 0))
    seg_end = float(segment.get("end", 0))
    duration = seg_end - seg_start

    if isinstance(words_timing, list) and words_timing:
        items = []
        for w in words_timing:
            items.append({
                "text": str(w.get("text", "")),
                "start": float(w.get("start", seg_start)),
                "end": float(w.get("end", seg_end)),
            })
        return items

    # No word timing — estimate from character positions
    text_words = text.split()
    if not text_words:
        return []
    total_chars = max(len(text), 1)
    char_pos = 0
    items = []
    for tw in text_words:
        ratio_start = char_pos / total_chars
        char_pos += len(tw) + 1  # +1 for space
        ratio_end = min(char_pos / total_chars, 1.0)
        items.append({
            "text": tw,
            "start": seg_start + ratio_start * duration,
            "end": seg_start + ratio_end * duration,
        })
    return items


def _split_segment(
    segment: dict,
    num_blocks: int,
    target_lang: str,
) -> List[dict]:
    """Split a single segment into `num_blocks` subtitle-sized chunks.

    Uses word-level timing to find natural split points.
    Each chunk gets timing, text, words, and a character budget.
    """
    seg_id = segment.get("id")
    seg_start = float(segment.get("start", 0))
    seg_end = float(segment.get("end", 0))
    duration = seg_end - seg_start
    speaker = segment.get("speaker")
    original_words = segment.get("words")

    word_items = _build_word_items(segment)
    if not word_items or num_blocks <= 1:
        # Single block — pass through with budget
        budget = _char_budget(max(duration, MIN_DURATION), target_lang)
        return [{
            "id": f"{seg_id}_0",
            "parent_id": seg_id,
            "text": str(segment.get("text") or "").strip(),
            "start": seg_start,
            "end": seg_end,
            "words": original_words,
            "speaker": speaker,
            "char_budget": budget,
            "duration": round(duration, 3),
        }]

    chunk_dur = duration / num_blocks
    chunks: List[dict] = []
    remaining_words = list(word_items)
    remaining_original = list(original_words) if isinstance(original_words, list) else None

    for block_i in range(num_blocks):
        if block_i == num_blocks - 1:
            # Last block — take everything remaining
            chunk_words = remaining_words
            chunk_orig = remaining_original
            remaining_words = []
            remaining_original = None
        else:
            ideal_split_time = seg_start + (block_i + 1) * chunk_dur

            # Find best split point among remaining words
            best_idx = None
            best_score = float("inf")
            search_window = 2.5  # seconds around ideal split

            for wi in range(len(remaining_words) - 1):
                w = remaining_words[wi]
                split_time = w["end"]
                time_diff = abs(split_time - ideal_split_time)

                if time_diff > search_window:
                    # Only consider if we're within window
                    if split_time > ideal_split_time + search_window:
                        break
                    continue

                # Combined score: time distance + boundary quality
                boundary_score = _score_split_point(
                    w["text"], remaining_words[:wi], wi, len(remaining_words),
                )
                score = time_diff + boundary_score

                if score < best_score:
                    best_score = score
                    best_idx = wi + 1  # split AFTER this word

            # Fallback: split at word closest to ideal time
            if best_idx is None:
                for wi in range(len(remaining_words)):
                    if remaining_words[wi]["end"] >= ideal_split_time:
                        best_idx = max(1, wi)
                        break
                if best_idx is None:
                    best_idx = max(1, len(remaining_words) // 2)

            # Ensure at least 1 word per remaining block
            remaining_blocks = num_blocks - block_i - 1
            max_take = len(remaining_words) - remaining_blocks
            best_idx = min(best_idx, max_take)
            best_idx = max(1, best_idx)

            chunk_words = remaining_words[:best_idx]
            chunk_orig = remaining_original[:best_idx] if remaining_original else None
            remaining_words = remaining_words[best_idx:]
            remaining_original = remaining_original[best_idx:] if remaining_original else None

        if not chunk_words:
            continue

        # Build chunk
        chunk_text = " ".join(w["text"] for w in chunk_words)
        chunk_start = chunk_words[0]["start"]
        chunk_end = chunk_words[-1]["end"]

        # Ensure gap from previous chunk
        if chunks:
            min_start = chunks[-1]["end"] + GAP_SECONDS
            if chunk_start < min_start:
                chunk_start = min_start

        # Ensure minimum duration
        if chunk_end - chunk_start < MIN_DURATION:
            chunk_end = chunk_start + MIN_DURATION

        chunk_duration = chunk_end - chunk_start
        budget = _char_budget(max(chunk_duration, MIN_DURATION), target_lang)

        chunk = {
            "id": f"{seg_id}_{block_i}",
            "parent_id": seg_id,
            "text": chunk_text,
            "start": round(chunk_start, 3),
            "end": round(chunk_end, 3),
            "words": chunk_orig,
            "speaker": speaker,
            "char_budget": budget,
            "duration": round(chunk_duration, 3),
        }
        chunks.append(chunk)

    return chunks


def _is_music_segment(segment: dict) -> bool:
    """Check if segment is a music/non-speech marker."""
    text = str(segment.get("text") or "").strip().upper()
    return text in ("(MUSIC)", "[MUSIC]", "♪", "♪♪", "(SINGING)")


def pre_segment_for_translation(
    segments: List[dict],
    target_language_code: str,
) -> List[dict]:
    """Transform transcription segments into subtitle-ready chunks for translation.

    This is the core architectural fix: instead of translating 18-second
    transcription segments and then running 15 finalizer passes to fix the
    resulting CPS disasters, we split segments into 2–7 second chunks
    BEFORE translation.  Each chunk gets a hard character budget.

    Args:
        segments: ElevenLabs skeleton segments with word-level timing.
        target_language_code: ISO 639-1 code (e.g. "is", "en", "de").

    Returns:
        List of pre-segmented chunks, each with:
        - id: Sub-ID like "42_0" (parent_id + block index)
        - parent_id: Original segment ID
        - text: English source text for this chunk
        - start/end: Timing for this chunk
        - words: Word-level timing for this chunk (if available)
        - speaker: Speaker ID (preserved from parent)
        - char_budget: Hard character limit for the translator
        - duration: Duration in seconds
    """
    result: List[dict] = []
    total_splits = 0

    for seg in segments:
        seg_id = seg.get("id")
        text = str(seg.get("text") or "").strip()
        start = float(seg.get("start", 0))
        end = float(seg.get("end", 0))
        duration = end - start

        # Audio events (applause, laughter, etc.) — pass through with flag preserved
        if seg.get("is_audio_event", False):
            budget = _char_budget(max(duration, MIN_DURATION), target_language_code)
            result.append({
                "id": f"{seg_id}_0",
                "parent_id": seg_id,
                "text": text,
                "start": start,
                "end": end,
                "words": seg.get("words"),
                "speaker": seg.get("speaker"),
                "char_budget": budget,
                "duration": round(duration, 3),
                "is_audio_event": True,
            })
            continue

        # Music segments — pass through unchanged
        if _is_music_segment(seg):
            budget = _char_budget(max(duration, MIN_DURATION), target_language_code)
            result.append({
                "id": f"{seg_id}_0",
                "parent_id": seg_id,
                "text": text,
                "start": start,
                "end": end,
                "words": seg.get("words"),
                "speaker": seg.get("speaker"),
                "char_budget": budget,
                "duration": round(duration, 3),
                "is_music": True,
            })
            continue

        # Worship segments (flagged by audio classifier or text repetition) — pass through with flag
        if seg.get("is_worship") or seg.get("is_music"):
            budget = _char_budget(max(duration, MIN_DURATION), target_language_code)
            result.append({
                "id": f"{seg_id}_0",
                "parent_id": seg_id,
                "text": text,
                "start": start,
                "end": end,
                "words": seg.get("words"),
                "speaker": seg.get("speaker"),
                "char_budget": budget,
                "duration": round(duration, 3),
                "is_worship": seg.get("is_worship", False),
                "is_music": seg.get("is_music", False),
            })
            continue

        # Short segments that already fit — pass through with budget
        est_chars = _estimate_translated_chars(text, target_language_code)
        if duration <= MAX_BLOCK_DURATION and est_chars <= MAX_CHARS_TOTAL:
            budget = _char_budget(max(duration, MIN_DURATION), target_language_code)
            result.append({
                "id": f"{seg_id}_0",
                "parent_id": seg_id,
                "text": text,
                "start": start,
                "end": end,
                "words": seg.get("words"),
                "speaker": seg.get("speaker"),
                "char_budget": budget,
                "duration": round(duration, 3),
            })
            continue

        # Long segment — split into subtitle-sized blocks
        num_blocks = _num_blocks_needed(duration, text, target_language_code)
        if num_blocks <= 1:
            # Even the calculation says 1 — pass through
            budget = _char_budget(max(duration, MIN_DURATION), target_language_code)
            result.append({
                "id": f"{seg_id}_0",
                "parent_id": seg_id,
                "text": text,
                "start": start,
                "end": end,
                "words": seg.get("words"),
                "speaker": seg.get("speaker"),
                "char_budget": budget,
                "duration": round(duration, 3),
            })
            continue

        chunks = _split_segment(seg, num_blocks, target_language_code)
        result.extend(chunks)
        total_splits += 1

        if num_blocks > 1:
            logger.debug(
                "Split segment %s (%.1fs, %d chars) → %d blocks",
                seg_id, duration, len(text), len(chunks),
            )

    if total_splits > 0:
        logger.info(
            "Pre-segmented %d → %d chunks (%d segments split)",
            len(segments), len(result), total_splits,
        )
    else:
        logger.info(
            "Pre-segmentation: %d segments, none needed splitting", len(segments),
        )

    return result


def reassemble_translations(
    pre_segmented: List[dict],
    translated_map: Dict[str, str],
) -> Tuple[List[dict], Dict[str, str]]:
    """Reassemble pre-segmented translations back to original segment IDs.

    The translator produces one translation per sub-ID ("42_0", "42_1", etc.).
    The rest of the pipeline (editor, assembly) works with original integer IDs.

    This function:
    1. Groups sub-ID translations by parent_id
    2. Creates one translated segment per parent_id with concatenated text
    3. Returns both the segment list AND the updated translated_map

    The timing comes from the pre-segmented chunks (not the original segments)
    because the chunks define the actual subtitle display windows.
    """
    # Group chunks by parent_id, preserving order
    from collections import OrderedDict
    parent_groups: OrderedDict[int, List[dict]] = OrderedDict()
    for chunk in pre_segmented:
        pid = chunk["parent_id"]
        if pid not in parent_groups:
            parent_groups[pid] = []
        parent_groups[pid].append(chunk)

    # Build the assembled output: one entry per SUB-SEGMENT (not per parent)
    # because each chunk IS a subtitle display unit
    assembled: List[dict] = []
    new_map: Dict[str, str] = {}

    for parent_id, chunks in parent_groups.items():
        for chunk in chunks:
            sub_id = chunk["id"]
            text = translated_map.get(str(sub_id), "")
            assembled.append({
                "id": sub_id,
                "parent_id": parent_id,
                "text": text,
                "start": chunk["start"],
                "end": chunk["end"],
                "words": chunk.get("words"),
                "speaker": chunk.get("speaker"),
                "char_budget": chunk.get("char_budget"),
                "duration": chunk.get("duration"),
            })
            new_map[str(sub_id)] = text

    return assembled, new_map
