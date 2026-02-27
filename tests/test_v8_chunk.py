#!/usr/bin/env python3
"""
v8 Chunk Translation Test
=========================
Tests the chunked translation approach: send 25 paragraphs (~5 min of content)
in a single Gemini API call instead of 25 separate calls.

Goal: Verify that Gemini 3 Pro can handle a chunk of 25 paragraphs (100-150 segments)
in one call and produce broadcast-quality Icelandic translations.

Usage:
    cd /Users/haukurhauksson/Azotus
    python tests/test_v8_chunk.py
"""

import json
import logging
import os
import sys
import time

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from google import genai
from google.genai import types

import profiles
import config
from workers.translation_engine import TranslationEngine, TranslationParagraph
from workers.pre_segmenter import pre_segment_for_translation

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("v8_test")

# --- Configuration ---
SKELETON_PATH = "/Users/haukurhauksson/Azotus/2_VAULT/Data/the_brides_revival_-_joe_sweet_pt_1_-_56min-20260212T014851023059Z_SKELETON.json"
V7_APPROVED_PATH = "/Users/haukurhauksson/Azotus/3_TRANSLATED_DONE/the_brides_revival_-_joe_sweet_pt_1_-_56min-20260212T014851023059Z_APPROVED.json"

TARGET_LANG = "is"
PROGRAM_PROFILE = "standard"
CHUNK_SIZE = 25  # paragraphs per chunk
MODEL = "gemini-3-pro-preview"


def load_skeleton():
    """Load and return the skeleton JSON."""
    logger.info(f"Loading skeleton from {SKELETON_PATH}")
    with open(SKELETON_PATH) as f:
        skeleton = json.load(f)
    logger.info(f"  Loaded {len(skeleton['segments'])} raw segments")
    return skeleton


def load_v7_approved():
    """Load the v7 approved translation for comparison."""
    logger.info(f"Loading v7 approved from {V7_APPROVED_PATH}")
    with open(V7_APPROVED_PATH) as f:
        approved = json.load(f)
    logger.info(f"  Loaded {len(approved['segments'])} translated segments")
    return approved


def pre_segment_and_build_paragraphs(skeleton):
    """Pre-segment raw transcription and build paragraphs."""
    # Pre-segment: split long segments into subtitle-sized chunks
    raw_segments = skeleton["segments"]
    pre_segmented = pre_segment_for_translation(raw_segments, target_language_code=TARGET_LANG)
    logger.info(f"  Pre-segmented: {len(raw_segments)} raw → {len(pre_segmented)} chunks")

    # Build paragraphs from pre-segmented chunks
    mock_skeleton = {"segments": pre_segmented}
    paragraphs = TranslationEngine.build_paragraphs_from_skeleton(mock_skeleton)
    logger.info(f"  Built {len(paragraphs)} paragraphs")

    return pre_segmented, paragraphs


def build_chunk_prompt(paragraphs, chunk_index, total_chunks, target_lang, continuity=None):
    """
    Build a single prompt for a chunk of paragraphs.
    This is the v8 architecture: send multiple paragraphs in one call.
    """
    lang_config = profiles.LANGUAGES.get(target_lang, profiles.LANGUAGES["is"])
    is_icelandic = target_lang.lower() == "is"
    ideal_cps = 12 if is_icelandic else 15
    max_cps = 15 if is_icelandic else 17

    # Assemble all segments from all paragraphs in this chunk
    all_segments = []
    paragraph_markers = []

    for para in paragraphs:
        paragraph_markers.append({
            "para_id": para.id,
            "speaker": para.speaker,
            "start_time": para.start_time,
            "first_seg_index": len(all_segments),
        })
        for seg in para.segments:
            duration = seg["end"] - seg["start"]
            max_chars = min(int(duration * max_cps), 84)
            all_segments.append({
                "id": str(seg["id"]),
                "start": seg["start"],
                "end": seg["end"],
                "duration": round(duration, 2),
                "max_chars": max_chars,
                "text": seg["text"],
                "speaker": seg.get("speaker", "Unknown"),
            })

    # Build paragraph boundary annotations
    para_boundaries = ""
    for i, marker in enumerate(paragraph_markers):
        start_mm = int(marker["start_time"] // 60)
        start_ss = int(marker["start_time"] % 60)
        para_boundaries += f"  - {marker['para_id']}: Speaker {marker['speaker']} at {start_mm:02d}:{start_ss:02d} (segments starting at index {marker['first_seg_index']})\n"

    # Continuity block
    continuity_block = ""
    if continuity:
        continuity_block = f"""
CONTINUITY (Previous translated segments — for flow only, do NOT re-translate):
{json.dumps(continuity, ensure_ascii=False)}
"""

    # Omission hierarchy
    omission = """HIERARCHY OF OMISSION (When you must condense):
1. DELETE FIRST (67% of cuts): Fillers ("um", "well"), Phatic phrases ("you know"), Redundant markers.
2. DELETE SECOND (32% of cuts): Non-essential details, adjectives, background chatter.
3. NEVER DELETE (0%): Theological terms, Name of God, Scripture references, specific Names/Places."""

    prompt = f"""TASK: Translate the following CHUNK of segments into {lang_config['name']}.
This is chunk {chunk_index + 1}/{total_chunks} of a sermon translation.

The viewer reads at {ideal_cps} chars/second. MAX CPS: {max_cps} (Hard Limit).

STYLE GUIDELINES:
- Natural Flow: Translate meaning, not words. Avoid "Translationese". Produce natural {lang_config['name']} a native speaker would use.
- Formatting: Do NOT use ALL CAPS. Use standard sentence case. Preserve acronyms.
- Line Limits: Maximum 42 characters per line, maximum 2 lines per subtitle.

{omission}

CONTENT PRESERVATION (CRITICAL):
- NEVER omit, censor, or filter any segment. This is professional broadcast content.
- Translate ALL content faithfully, including references to violence, politics, or sensitive topics.
- NEVER drop core theological meaning: God, Jesus Christ, Holy Spirit, salvation, grace, sin, etc.
- Do not leave incomplete sentence fragments that lose the final theological point.
- Every input segment MUST have a translation. NEVER return empty text.

PARAGRAPH STRUCTURE (this chunk contains {len(paragraphs)} paragraphs):
{para_boundaries}
Maintain natural flow WITHIN each paragraph and smooth transitions BETWEEN paragraphs.
{continuity_block}
INPUT SEGMENTS ({len(all_segments)} segments — translate strictly 1:1, preserving IDs):
{json.dumps(all_segments, ensure_ascii=False)}

CONSTRAINTS:
1. `text` length MUST be <= `max_chars` for each segment.
2. If the translation is too long, APPLY THE OMISSION HIERARCHY to condense it.
3. Use {lang_config['bible']} for scripture references.

FINAL SELF-CHECK BEFORE OUTPUT:
1. Every input ID appears exactly once in output (all {len(all_segments)} segments).
2. No segment is blank.
3. Every translation is <= its max_chars limit.
4. Translation is complete (not abruptly cut off).
5. No segment ends with a dangling preposition (á, í, um, til, við, frá, með).
6. Key phrases must stay within a single segment — never split "ÉG ER" (I AM), "Heilagur Andi" (Holy Spirit), or proper names across segment boundaries.

Return JSON: {{"segments": [{{"id": "...", "text": "..."}}]}}"""

    return prompt, all_segments


def run_test():
    """Main test: send one chunk to Gemini and compare with v7."""

    # 1. Load data
    skeleton = load_skeleton()
    v7_approved = load_v7_approved()
    pre_segmented, paragraphs = pre_segment_and_build_paragraphs(skeleton)

    # 2. Select first chunk (paragraphs 0-24)
    chunk_paragraphs = paragraphs[:CHUNK_SIZE]
    total_chunks = (len(paragraphs) + CHUNK_SIZE - 1) // CHUNK_SIZE

    logger.info(f"\n{'='*60}")
    logger.info(f"CHUNK TEST: {len(chunk_paragraphs)} paragraphs in one API call")
    logger.info(f"  Time range: {chunk_paragraphs[0].start_time:.1f}s - {chunk_paragraphs[-1].end_time:.1f}s")
    logger.info(f"  Duration: {chunk_paragraphs[-1].end_time - chunk_paragraphs[0].start_time:.1f}s")
    total_segs = sum(len(p.segments) for p in chunk_paragraphs)
    logger.info(f"  Total segments: {total_segs}")
    logger.info(f"  Full program: {len(paragraphs)} paragraphs → {total_chunks} chunks")
    logger.info(f"{'='*60}\n")

    # 3. Build system instruction
    system_instruction = profiles.get_system_instruction(TARGET_LANG, PROGRAM_PROFILE)
    logger.info(f"System instruction: {len(system_instruction)} chars")

    # 4. Build chunk prompt
    chunk_prompt, input_segments = build_chunk_prompt(
        chunk_paragraphs, chunk_index=0, total_chunks=total_chunks,
        target_lang=TARGET_LANG,
    )
    logger.info(f"Chunk prompt: {len(chunk_prompt)} chars")
    logger.info(f"Input segments: {len(input_segments)}")

    # 5. Estimate token counts
    est_input_tokens = (len(system_instruction) + len(chunk_prompt)) // 4
    est_output_tokens = len(input_segments) * 30  # ~30 tokens per translated segment
    logger.info(f"Estimated input tokens: ~{est_input_tokens}")
    logger.info(f"Estimated output tokens: ~{est_output_tokens}")

    # 6. Initialize Gemini client
    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT") or getattr(config, 'OMEGA_CLOUD_PROJECT', None)
    location = os.environ.get("GOOGLE_CLOUD_LOCATION") or getattr(config, 'GEMINI_LOCATION', 'global')
    client = genai.Client(vertexai=True, project=project_id, location=location)
    logger.info(f"Gemini client: project={project_id}, location={location}, model={MODEL}")

    # 7. Budget math for chunk
    thinking_budget = 24576  # Generous for chunk
    response_budget = max(4096, len(input_segments) * 200)
    max_output = thinking_budget + response_budget
    logger.info(f"Budget: thinking={thinking_budget}, response={response_budget}, max_output={max_output}")

    # 8. Send to Gemini
    logger.info(f"\n🚀 Sending chunk to Gemini 3 Pro Preview...")
    start_time = time.time()

    try:
        response = client.models.generate_content(
            model=MODEL,
            contents=chunk_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                thinking_config=types.ThinkingConfig(
                    thinking_budget=thinking_budget,
                ),
                max_output_tokens=max_output,
                response_mime_type="application/json",
                response_schema={
                    "type": "OBJECT",
                    "properties": {
                        "segments": {
                            "type": "ARRAY",
                            "items": {
                                "type": "OBJECT",
                                "properties": {
                                    "id": {"type": "STRING"},
                                    "text": {"type": "STRING"},
                                },
                                "required": ["id", "text"],
                            },
                        },
                    },
                    "required": ["segments"],
                },
                temperature=0.25,
                safety_settings=[
                    types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="BLOCK_NONE"),
                    types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="BLOCK_NONE"),
                    types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="BLOCK_NONE"),
                    types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="BLOCK_NONE"),
                ],
            ),
        )
    except Exception as e:
        logger.error(f"❌ API call failed: {e}")
        raise

    elapsed = time.time() - start_time
    logger.info(f"✅ Response received in {elapsed:.1f}s")

    # 9. Parse response
    response_text = None
    try:
        response_text = response.text
    except ValueError:
        pass

    if not response_text:
        finish_reason = None
        if response.candidates:
            finish_reason = getattr(response.candidates[0], 'finish_reason', None)
        logger.error(f"❌ Empty response: finish_reason={finish_reason}")
        return

    # Check for truncation
    if response.candidates:
        finish_reason = getattr(response.candidates[0], 'finish_reason', None)
        if finish_reason and 'MAX_TOKENS' in str(finish_reason):
            logger.error(f"❌ TRUNCATED: finish_reason={finish_reason}")
            return

    result = json.loads(response_text)
    v8_segments = result.get("segments", [])

    # 10. Token usage
    usage = getattr(response, 'usage_metadata', None)
    if usage:
        logger.info(f"\n📊 TOKEN USAGE:")
        logger.info(f"  Input tokens:    {getattr(usage, 'prompt_token_count', 'N/A')}")
        logger.info(f"  Output tokens:   {getattr(usage, 'candidates_token_count', 'N/A')}")
        logger.info(f"  Thinking tokens: {getattr(usage, 'thoughts_token_count', 'N/A')}")
        total = getattr(usage, 'total_token_count', 'N/A')
        logger.info(f"  Total tokens:    {total}")
        cached = getattr(usage, 'cached_content_token_count', 0)
        if cached:
            logger.info(f"  Cached tokens:   {cached}")

    # 11. Validate results
    logger.info(f"\n📋 VALIDATION:")
    logger.info(f"  Input segments:  {len(input_segments)}")
    logger.info(f"  Output segments: {len(v8_segments)}")

    # Check all IDs present
    input_ids = {str(s["id"]) for s in input_segments}
    output_ids = {s["id"] for s in v8_segments}
    missing = input_ids - output_ids
    extra = output_ids - input_ids
    if missing:
        logger.warning(f"  ⚠️ MISSING IDs: {missing}")
    if extra:
        logger.warning(f"  ⚠️ EXTRA IDs: {extra}")
    if not missing and not extra:
        logger.info(f"  ✅ All {len(input_ids)} segment IDs match perfectly")

    # Check blank segments
    blank_count = sum(1 for s in v8_segments if not s["text"].strip())
    if blank_count:
        logger.warning(f"  ⚠️ {blank_count} blank segments")
    else:
        logger.info(f"  ✅ No blank segments")

    # Check CPS violations
    seg_lookup = {str(s["id"]): s for s in input_segments}
    cps_violations = 0
    for seg in v8_segments:
        input_seg = seg_lookup.get(seg["id"])
        if input_seg and len(seg["text"]) > input_seg["max_chars"]:
            cps_violations += 1
            if cps_violations <= 5:  # Show first 5
                logger.warning(
                    f"  ⚠️ CPS violation: id={seg['id']} "
                    f"text='{seg['text'][:40]}...' "
                    f"len={len(seg['text'])} > max={input_seg['max_chars']}"
                )
    logger.info(f"  CPS violations: {cps_violations}/{len(v8_segments)} ({cps_violations/len(v8_segments)*100:.1f}%)")

    # 12. Compare with v7
    logger.info(f"\n📊 QUALITY COMPARISON (v8 chunk vs v7 per-paragraph):")

    # Build v7 lookup by matching on start/end times (IDs may differ between pre-segmentation runs)
    v7_by_time = {}
    for seg in v7_approved["segments"]:
        key = f"{seg['start']:.2f}-{seg['end']:.2f}"
        v7_by_time[key] = seg["text"]

    # Also build lookup by segment ID for direct matches
    v7_by_id = {}
    for seg in v7_approved["segments"]:
        # v7 approved doesn't have "id" field in same format — match by time
        pass

    matches = 0
    diffs = []
    for v8_seg in v8_segments:
        input_seg = seg_lookup.get(v8_seg["id"])
        if not input_seg:
            continue
        key = f"{input_seg['start']:.2f}-{input_seg['end']:.2f}"
        v7_text = v7_by_time.get(key)
        if v7_text:
            if v7_text.strip() == v8_seg["text"].strip():
                matches += 1
            else:
                diffs.append({
                    "id": v8_seg["id"],
                    "time": key,
                    "english": input_seg["text"],
                    "v7": v7_text,
                    "v8": v8_seg["text"],
                })

    total_comparable = matches + len(diffs)
    logger.info(f"  Comparable segments: {total_comparable}")
    logger.info(f"  Exact matches: {matches} ({matches/max(1,total_comparable)*100:.1f}%)")
    logger.info(f"  Differences: {len(diffs)}")

    # Show first 15 differences for manual review
    logger.info(f"\n📝 SAMPLE DIFFERENCES (first 15):")
    for diff in diffs[:15]:
        logger.info(f"  [{diff['time']}] EN: {diff['english'][:60]}")
        logger.info(f"     v7: {diff['v7'][:60]}")
        logger.info(f"     v8: {diff['v8'][:60]}")
        logger.info("")

    # 13. Save results
    output_path = "/Users/haukurhauksson/Azotus/tests/v8_chunk_test_result.json"
    test_result = {
        "test": "v8_chunk_translation",
        "model": MODEL,
        "chunk_size": CHUNK_SIZE,
        "paragraphs_in_chunk": len(chunk_paragraphs),
        "segments_in_chunk": len(input_segments),
        "elapsed_seconds": round(elapsed, 1),
        "thinking_budget": thinking_budget,
        "v8_segments": v8_segments,
        "validation": {
            "missing_ids": list(missing),
            "extra_ids": list(extra),
            "blank_segments": blank_count,
            "cps_violations": cps_violations,
        },
        "comparison": {
            "total_comparable": total_comparable,
            "exact_matches": matches,
            "differences_count": len(diffs),
            "sample_differences": diffs[:30],
        },
    }
    if usage:
        test_result["token_usage"] = {
            "input": getattr(usage, 'prompt_token_count', None),
            "output": getattr(usage, 'candidates_token_count', None),
            "thinking": getattr(usage, 'thoughts_token_count', None),
            "total": getattr(usage, 'total_token_count', None),
        }

    with open(output_path, "w") as f:
        json.dump(test_result, f, ensure_ascii=False, indent=2)
    logger.info(f"\n💾 Results saved to {output_path}")

    # 14. Summary
    logger.info(f"\n{'='*60}")
    logger.info(f"SUMMARY")
    logger.info(f"{'='*60}")
    logger.info(f"  Model:            {MODEL}")
    logger.info(f"  Chunk:            {len(chunk_paragraphs)} paragraphs, {len(input_segments)} segments")
    logger.info(f"  API call time:    {elapsed:.1f}s")
    logger.info(f"  Segment match:    {len(v8_segments)}/{len(input_segments)}")
    logger.info(f"  CPS violations:   {cps_violations}")
    logger.info(f"  vs v7 exact:      {matches}/{total_comparable}")

    # v7 equivalent time estimate (25 paragraphs × ~10s each in waves of 8)
    v7_est_time = (CHUNK_SIZE / 8) * 10  # 3 waves × 10 seconds
    logger.info(f"\n  v7 estimated time: ~{v7_est_time:.0f}s (25 paragraphs in waves of 8)")
    logger.info(f"  v8 actual time:    {elapsed:.1f}s")
    if elapsed > 0:
        speedup = v7_est_time / elapsed
        logger.info(f"  Speedup:           {speedup:.1f}x")
    logger.info(f"{'='*60}")


if __name__ == "__main__":
    run_test()
