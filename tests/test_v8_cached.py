#!/usr/bin/env python3
"""
v8 Cached Context Translation Test
====================================
Tests the v8 architecture: full transcript cached as context, 200 segments
per chunk, clean prompt. This is the Gemini-recommended approach.

Architecture:
1. Pre-segment the full transcript
2. Create a context cache: system instruction + full transcript + brief
3. Send one chunk (200 segments) referencing the cache
4. Validate output quality, CPS, and compare with v7

Usage:
    cd /Users/haukurhauksson/Azotus
    python tests/test_v8_cached.py
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
logger = logging.getLogger("v8_cached_test")

# --- Configuration ---
SKELETON_PATH = "/Users/haukurhauksson/Azotus/2_VAULT/Data/the_brides_revival_-_joe_sweet_pt_1_-_56min-20260212T014851023059Z_SKELETON.json"
V7_APPROVED_PATH = "/Users/haukurhauksson/Azotus/3_TRANSLATED_DONE/the_brides_revival_-_joe_sweet_pt_1_-_56min-20260212T014851023059Z_APPROVED.json"

TARGET_LANG = "is"
PROGRAM_PROFILE = "standard"
CHUNK_SIZE = 200  # segments per chunk (Gemini's recommended safe zone)
MODEL = "gemini-3-pro-preview"


# ─── Clean v8 System Instruction ────────────────────────────────────────
# Stripped down per Gemini's advice: just the essential Icelandic rules.
# The full profiles.py system instruction is ~730 tokens — already compact.
# We keep it as-is since it's proven and not bloated.


def build_full_transcript_context(all_segments):
    """Build the full English transcript with timings for context caching.

    This is the key v8 insight: every chunk sees the ENTIRE transcript.
    At ~1,620 segments × ~30 tokens each ≈ 48K tokens — well under 5% of 1M.
    """
    lines = []
    lines.append("FULL ENGLISH TRANSCRIPT (for context — translate ONLY the segments requested below)")
    lines.append(f"Total segments: {len(all_segments)}")
    lines.append("=" * 60)

    for seg in all_segments:
        start_mm = int(seg["start"] // 60)
        start_ss = int(seg["start"] % 60)
        speaker = seg.get("speaker", "?")
        text = seg.get("text", "")
        lines.append(f"[{start_mm:02d}:{start_ss:02d}] ({speaker}) {text}")

    return "\n".join(lines)


def build_v8_chunk_prompt(chunk_segments, chunk_index, total_chunks, total_segments, lang_config):
    """Build the clean v8 per-chunk prompt.

    This is drastically simpler than v7's per-paragraph prompt:
    - No omission hierarchy (the model knows from the system instruction)
    - No sliding context (the full transcript IS the context)
    - No paragraph boundaries (the model sees the whole sermon)
    - Just: which segments to translate + their character budgets
    """
    is_icelandic = True  # hardcoded for now
    max_cps = 15 if is_icelandic else 17

    # Build segment array with char budgets
    seg_array = []
    for seg in chunk_segments:
        duration = seg["end"] - seg["start"]
        max_chars = min(int(duration * max_cps), 84)
        seg_array.append({
            "id": str(seg["id"]),
            "start": seg["start"],
            "end": seg["end"],
            "duration": round(duration, 2),
            "max_chars": max_chars,
            "text": seg["text"],
        })

    prompt = f"""Translate chunk {chunk_index + 1}/{total_chunks} ({len(seg_array)} segments) into {lang_config['name']}.

You have the full English transcript above for context. Translate ONLY these segments:

{json.dumps(seg_array, ensure_ascii=False)}

RULES:
1. Each translation MUST be ≤ max_chars for that segment.
2. If too long, cut fillers/repetition first — NEVER cut theological meaning.
3. Return ALL {len(seg_array)} segments. No blanks. No missing IDs.
4. Natural {lang_config['name']} — not translationese.
5. Short interjections ("Amen", "Já", "Halelúja") stay as-is.

Return JSON: {{"segments": [{{"id": "...", "text": "..."}}]}}"""

    return prompt, seg_array


def run_test():
    """Main test: context-cached translation with 200 segments."""

    # 1. Load data
    logger.info(f"Loading skeleton from {SKELETON_PATH}")
    with open(SKELETON_PATH) as f:
        skeleton = json.load(f)
    logger.info(f"  Loaded {len(skeleton['segments'])} raw segments")

    logger.info(f"Loading v7 approved from {V7_APPROVED_PATH}")
    with open(V7_APPROVED_PATH) as f:
        v7_approved = json.load(f)
    logger.info(f"  Loaded {len(v7_approved['segments'])} translated segments")

    # 2. Pre-segment the full transcript
    raw_segments = skeleton["segments"]
    pre_segmented = pre_segment_for_translation(raw_segments, target_language_code=TARGET_LANG)
    logger.info(f"  Pre-segmented: {len(raw_segments)} raw → {len(pre_segmented)} chunks")

    # Filter out audio events (not translatable)
    translatable = [s for s in pre_segmented if not s.get("is_audio_event")]
    logger.info(f"  Translatable segments: {len(translatable)}")

    # 3. Calculate chunking plan
    total_chunks = (len(translatable) + CHUNK_SIZE - 1) // CHUNK_SIZE
    logger.info(f"\n{'='*60}")
    logger.info(f"v8 CHUNKING PLAN")
    logger.info(f"  Total segments: {len(translatable)}")
    logger.info(f"  Chunk size: {CHUNK_SIZE} segments")
    logger.info(f"  Total chunks: {total_chunks}")
    logger.info(f"  Testing: chunk 1 (segments 0-{min(CHUNK_SIZE, len(translatable))-1})")
    logger.info(f"{'='*60}\n")

    # 4. Build full transcript context (for caching)
    transcript_context = build_full_transcript_context(translatable)
    logger.info(f"Full transcript context: {len(transcript_context)} chars (~{len(transcript_context)//4} tokens)")

    # 5. Get system instruction + language config
    system_instruction = profiles.get_system_instruction(TARGET_LANG, PROGRAM_PROFILE)
    lang_config = profiles.LANGUAGES[TARGET_LANG]
    logger.info(f"System instruction: {len(system_instruction)} chars")

    # 6. Initialize Gemini client
    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT") or getattr(config, 'OMEGA_CLOUD_PROJECT', None)
    location = os.environ.get("GOOGLE_CLOUD_LOCATION") or getattr(config, 'GEMINI_LOCATION', 'global')
    client = genai.Client(vertexai=True, project=project_id, location=location)
    logger.info(f"Gemini client: project={project_id}, location={location}, model={MODEL}")

    # 7. Create context cache
    logger.info(f"\n📦 Creating context cache (system instruction + full transcript)...")
    cache_start = time.time()

    try:
        cache = client.caches.create(
            model=MODEL,
            config=types.CreateCachedContentConfig(
                system_instruction=system_instruction,
                contents=[
                    types.Content(
                        parts=[types.Part(text=transcript_context)],
                        role="user",
                    ),
                    types.Content(
                        parts=[types.Part(text="I have the full English transcript. Ready to translate chunks into Icelandic.")],
                        role="model",
                    ),
                ],
                ttl="3600s",  # 1 hour
                display_name="v8-test-transcript-cache",
            ),
        )
    except Exception as e:
        logger.error(f"❌ Cache creation failed: {e}")
        logger.info("Falling back to non-cached approach...")
        cache = None

    cache_elapsed = time.time() - cache_start
    if cache:
        logger.info(f"✅ Cache created in {cache_elapsed:.1f}s: {cache.name}")
        cache_usage = getattr(cache, 'usage_metadata', None)
        if cache_usage:
            logger.info(f"  Cached tokens: {getattr(cache_usage, 'total_token_count', 'N/A')}")
    else:
        logger.info(f"⚠️ Running without cache (non-cached fallback)")

    # 8. Select first chunk (segments 0 to CHUNK_SIZE-1)
    chunk_segments = translatable[:CHUNK_SIZE]
    chunk_prompt, input_segments = build_v8_chunk_prompt(
        chunk_segments, chunk_index=0, total_chunks=total_chunks,
        total_segments=len(translatable), lang_config=lang_config,
    )
    logger.info(f"\nChunk prompt: {len(chunk_prompt)} chars")
    logger.info(f"Input segments: {len(input_segments)}")
    logger.info(f"Time range: {chunk_segments[0]['start']:.1f}s - {chunk_segments[-1]['end']:.1f}s")

    # 9. Budget math
    thinking_budget = 24576
    response_budget = max(4096, len(input_segments) * 200)
    max_output = thinking_budget + response_budget
    logger.info(f"Budget: thinking={thinking_budget}, response={response_budget}, max_output={max_output}")

    # 10. Build API config
    api_config = types.GenerateContentConfig(
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
    )

    # 11. Send chunk to Gemini (with or without cache)
    logger.info(f"\n🚀 Sending chunk to Gemini 3 Pro Preview {'(CACHED)' if cache else '(non-cached)'}...")
    call_start = time.time()

    try:
        if cache:
            response = client.models.generate_content(
                model=MODEL,
                contents=chunk_prompt,
                config=types.GenerateContentConfig(
                    cached_content=cache.name,
                    thinking_config=api_config.thinking_config,
                    max_output_tokens=api_config.max_output_tokens,
                    response_mime_type=api_config.response_mime_type,
                    response_schema=api_config.response_schema,
                    temperature=api_config.temperature,
                    safety_settings=api_config.safety_settings,
                ),
            )
        else:
            # Non-cached: send system instruction + transcript + chunk prompt together
            combined_prompt = f"{transcript_context}\n\n---\n\n{chunk_prompt}"
            response = client.models.generate_content(
                model=MODEL,
                contents=combined_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    thinking_config=api_config.thinking_config,
                    max_output_tokens=api_config.max_output_tokens,
                    response_mime_type=api_config.response_mime_type,
                    response_schema=api_config.response_schema,
                    temperature=api_config.temperature,
                    safety_settings=api_config.safety_settings,
                ),
            )
    except Exception as e:
        logger.error(f"❌ API call failed: {e}")
        raise

    call_elapsed = time.time() - call_start
    logger.info(f"✅ Response received in {call_elapsed:.1f}s")

    # 12. Parse response
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

    result = json.loads(response_text)
    v8_segments = result.get("segments", [])

    # 13. Token usage
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
            logger.info(f"  Cached tokens:   {cached} (90% discount)")

    # 14. Validate results
    logger.info(f"\n📋 VALIDATION:")
    logger.info(f"  Input segments:  {len(input_segments)}")
    logger.info(f"  Output segments: {len(v8_segments)}")

    # Check all IDs present
    input_ids = {str(s["id"]) for s in input_segments}
    output_ids = {s["id"] for s in v8_segments}
    missing = input_ids - output_ids
    extra = output_ids - input_ids
    if missing:
        logger.warning(f"  ⚠️ MISSING IDs ({len(missing)}): {sorted(missing)[:10]}...")
    if extra:
        logger.warning(f"  ⚠️ EXTRA IDs ({len(extra)}): {sorted(extra)[:10]}...")
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
    cps_details = []
    for seg in v8_segments:
        input_seg = seg_lookup.get(seg["id"])
        if input_seg and len(seg["text"]) > input_seg["max_chars"]:
            cps_violations += 1
            over = len(seg["text"]) - input_seg["max_chars"]
            if cps_violations <= 10:
                cps_details.append(
                    f"  id={seg['id']} len={len(seg['text'])} > max={input_seg['max_chars']} (+{over}): "
                    f"'{seg['text'][:50]}...'"
                )
    pct = cps_violations / max(1, len(v8_segments)) * 100
    logger.info(f"  CPS violations: {cps_violations}/{len(v8_segments)} ({pct:.1f}%)")
    if cps_details:
        logger.info(f"  CPS violation samples:")
        for d in cps_details:
            logger.warning(f"    {d}")

    # 15. Compare with v7
    logger.info(f"\n📊 QUALITY COMPARISON (v8 cached vs v7 per-paragraph):")

    v7_by_time = {}
    for seg in v7_approved["segments"]:
        key = f"{seg['start']:.2f}-{seg['end']:.2f}"
        v7_by_time[key] = seg["text"]

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

    # Show first 10 differences for manual review
    logger.info(f"\n📝 SAMPLE DIFFERENCES (first 10):")
    for diff in diffs[:10]:
        logger.info(f"  [{diff['time']}] EN: {diff['english'][:60]}")
        logger.info(f"     v7: {diff['v7'][:60]}")
        logger.info(f"     v8: {diff['v8'][:60]}")
        logger.info("")

    # 16. Cleanup cache
    if cache:
        try:
            client.caches.delete(name=cache.name)
            logger.info(f"🗑️  Cache deleted: {cache.name}")
        except Exception as e:
            logger.warning(f"⚠️ Could not delete cache: {e}")

    # 17. Save results
    output_path = "/Users/haukurhauksson/Azotus/tests/v8_cached_test_result.json"
    test_result = {
        "test": "v8_cached_translation",
        "model": MODEL,
        "chunk_size": CHUNK_SIZE,
        "segments_in_chunk": len(input_segments),
        "total_program_segments": len(translatable),
        "total_chunks_needed": total_chunks,
        "cache_creation_seconds": round(cache_elapsed, 1),
        "translation_seconds": round(call_elapsed, 1),
        "total_seconds": round(cache_elapsed + call_elapsed, 1),
        "used_cache": cache is not None,
        "v8_segments": v8_segments,
        "validation": {
            "missing_ids": sorted(list(missing)),
            "extra_ids": sorted(list(extra)),
            "blank_segments": blank_count,
            "cps_violations": cps_violations,
            "cps_violation_pct": round(pct, 1),
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
            "cached": getattr(usage, 'cached_content_token_count', None),
        }

    with open(output_path, "w") as f:
        json.dump(test_result, f, ensure_ascii=False, indent=2)
    logger.info(f"\n💾 Results saved to {output_path}")

    # 18. Summary
    logger.info(f"\n{'='*60}")
    logger.info(f"SUMMARY")
    logger.info(f"{'='*60}")
    logger.info(f"  Model:            {MODEL}")
    logger.info(f"  Architecture:     v8 (full transcript cached, 200-seg chunks)")
    logger.info(f"  Segments:         {len(input_segments)} (chunk 1 of {total_chunks})")
    logger.info(f"  Cache creation:   {cache_elapsed:.1f}s")
    logger.info(f"  Translation:      {call_elapsed:.1f}s")
    logger.info(f"  Total:            {cache_elapsed + call_elapsed:.1f}s")
    logger.info(f"  Segment match:    {len(v8_segments)}/{len(input_segments)}")
    logger.info(f"  CPS violations:   {cps_violations}/{len(v8_segments)} ({pct:.1f}%)")
    logger.info(f"  vs v7 exact:      {matches}/{total_comparable}")

    # v7 equivalent time estimate
    # 200 segments ≈ 35 paragraphs ≈ 5 waves × 10s = 50s translation only
    v7_est_time = (200 / 8) * 10  # 25 waves × 10s
    logger.info(f"\n  v7 estimated time:   ~{v7_est_time:.0f}s (200 segs in waves of 8)")
    logger.info(f"  v8 actual time:      {call_elapsed:.1f}s (translation only)")
    logger.info(f"  v8 total (w/ cache): {cache_elapsed + call_elapsed:.1f}s")
    if call_elapsed > 0:
        speedup = v7_est_time / call_elapsed
        logger.info(f"  Speedup:             {speedup:.1f}x (translation only)")

    # Full program projection
    logger.info(f"\n  📈 FULL PROGRAM PROJECTION (all {total_chunks} chunks):")
    logger.info(f"  v7: ~{total_chunks * v7_est_time / total_chunks * 221 / 200 * 10 / 60:.0f} min ({len(translatable)} segs × 220 calls)")
    est_v8_per_chunk = call_elapsed
    est_v8_parallel = cache_elapsed + est_v8_per_chunk  # all chunks in parallel
    est_v8_serial = cache_elapsed + est_v8_per_chunk * total_chunks
    logger.info(f"  v8 parallel (all {total_chunks} at once): ~{est_v8_parallel:.0f}s ({est_v8_parallel/60:.1f} min)")
    logger.info(f"  v8 serial (one-by-one):  ~{est_v8_serial:.0f}s ({est_v8_serial/60:.1f} min)")
    logger.info(f"{'='*60}")


if __name__ == "__main__":
    run_test()
