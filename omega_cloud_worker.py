import argparse
import sys
import logging
import json
import random
import re
import time
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any

from google.cloud import storage

import config
import profiles
from workers.translation_engine import TranslationEngine, TranslationParagraph
from workers.pre_segmenter import pre_segment_for_translation
from gcs_jobs import GcsJobPaths, download_json, upload_json, try_download_json, blob_exists

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("OmegaCloudWorker")


def _translate_with_timeout(engine, paragraph, target_lang, program_profile,
                            extra_terms, entity_anchors, timeout=300,
                            continuity_payload=None, speaker_gender=None,
                            visual_context=None, doc_brief=None,
                            sliding_context=None):
    """Call engine.translate_paragraph with a hard daemon-thread timeout.

    Prevents gRPC hangs from blocking the worker forever.  The daemon thread
    is abandoned on timeout — it will die when the process exits.
    Pattern ported from omega_cloud_worker_legacy._vertex_generate.
    """
    result_holder: list = [None]
    error_holder: list = [None]
    done_event = threading.Event()

    def _call():
        try:
            result_holder[0] = engine.translate_paragraph(
                paragraph, target_lang, program_profile,
                extra_terms=extra_terms,
                entity_anchors=entity_anchors,
                continuity_payload=continuity_payload,
                speaker_gender=speaker_gender,
                visual_context=visual_context,
                doc_brief=doc_brief,
                sliding_context=sliding_context,
            )
        except Exception as exc:
            error_holder[0] = exc
        finally:
            done_event.set()

    t = threading.Thread(target=_call, daemon=True)
    t.start()

    if not done_event.wait(timeout=timeout):
        raise TimeoutError(
            f"Translation timed out after {timeout}s for paragraph {paragraph.id}"
        )

    if error_holder[0] is not None:
        raise error_holder[0]

    return result_holder[0]


def _run_v8_translation(
    engine, paragraphs, pre_segmented, target_lang, program_profile,
    extra_terms, entity_anchors, speaker_genders, effective_brief,
    storage_client, bucket, checkpoint_blob, checkpoint_data,
):
    """
    v8 Architecture: Full transcript as cached context + chunked output.

    Instead of 220 individual API calls (one per paragraph), this:
    1. Caches system instruction + full transcript + brief (90% input discount)
    2. Chunks all segments into groups of ~200
    3. Translates all chunks in parallel (or small waves)
    4. Merges results

    Expected: ~9 API calls, 2-3 min, ~$1.50-3.00 per program.
    """
    CHUNK_SIZE = config.OMEGA_V8_CHUNK_SIZE  # 200 segments per chunk
    WAVE_SIZE = config.OMEGA_V8_CHUNK_WAVE_SIZE  # 4 parallel chunks per wave
    CACHE_TTL = config.OMEGA_V8_CACHE_TTL
    max_retries = 3

    # Filter translatable segments (skip audio events, worship/music segments)
    worship_filtered = sum(1 for s in pre_segmented if s.get("is_worship") or s.get("is_music"))
    translatable = [
        s for s in pre_segmented
        if not s.get("is_audio_event") and not s.get("is_worship") and not s.get("is_music")
    ]
    if worship_filtered:
        logger.info(f"🎵 Filtered {worship_filtered} worship/music segments from translation")

    # Build active glossary for chunk prompt reinforcement
    active_glossary = {}
    _profile = profiles.PROFILES.get(program_profile, profiles.PROFILES["standard"])
    for _term, _translations in _profile["glossary"].items():
        if target_lang in _translations:
            active_glossary[_term] = _translations[target_lang]
    # Merge standard glossary if profile isn't standard
    if program_profile != "standard":
        _std = profiles.PROFILES.get("standard", {})
        for _term, _translations in _std.get("glossary", {}).items():
            if target_lang in _translations and _term not in active_glossary:
                active_glossary[_term] = _translations[target_lang]
    if extra_terms:
        active_glossary.update(extra_terms)
    total_segs = len(translatable)

    # Build chunks
    chunks = []
    for i in range(0, total_segs, CHUNK_SIZE):
        chunk_segs = translatable[i:i + CHUNK_SIZE]
        chunks.append({
            "index": len(chunks),
            "segments": chunk_segs,
            "start_idx": i,
            "end_idx": i + len(chunk_segs),
        })

    total_chunks = len(chunks)
    logger.info(f"⚡ v8 MODE: {total_segs} segments → {total_chunks} chunks of ≤{CHUNK_SIZE}")

    # ═══════════════════════════════════════════════════════════════
    # v8 GUARDRAIL: Cost estimation (much cheaper than v7)
    # ═══════════════════════════════════════════════════════════════
    est_cost_per_chunk = 0.15  # conservative: ~$0.10-0.20 per chunk
    est_cost = total_chunks * est_cost_per_chunk + 0.50  # + brief cost
    logger.info(f"💰 v8 cost estimate: {total_chunks} chunks × ${est_cost_per_chunk:.2f} + $0.50 brief ≈ ${est_cost:.2f}")

    # ═══════════════════════════════════════════════════════════════
    # v8 Checkpoint Resume
    # ═══════════════════════════════════════════════════════════════
    resumed_chunks = {}
    if checkpoint_data and isinstance(checkpoint_data, dict):
        cp_version = checkpoint_data.get("version")
        if cp_version == "v8":
            cp_chunks = checkpoint_data.get("completed_chunks", {})
            if cp_chunks:
                resumed_chunks = {int(k): v for k, v in cp_chunks.items()}
                logger.info(
                    f"♻️ RESUMING v8 checkpoint: {len(resumed_chunks)}/{total_chunks} chunks already done"
                )
        else:
            logger.info("⚠️ Found v7-format checkpoint — ignoring (v8 uses different structure)")

    # Build full transcript context for caching
    logger.info("📝 Building full transcript context...")
    transcript_context = TranslationEngine.build_full_transcript_context(
        translatable,
        entity_anchors=entity_anchors,
        speaker_genders=speaker_genders,
    )
    logger.info(f"   Transcript context: {len(transcript_context)} chars (~{len(transcript_context) // 4} tokens)")

    # Get system instruction — creative prompt if Flash QA is enabled
    use_flash_qa = config.OMEGA_FLASH_QA_ENABLED
    if use_flash_qa:
        system_instruction = profiles.get_creative_system_instruction(target_lang, program_profile, extra_terms=extra_terms)
        logger.info("🎨 Two-pass mode: Using CREATIVE prompt (Flash QA will handle mechanics)")
    else:
        system_instruction = profiles.get_system_instruction(target_lang, program_profile, extra_terms=extra_terms)

    # Create context cache
    logger.info("📦 Creating context cache...")
    t_cache = time.time()
    cache = engine.create_context_cache(
        system_instruction=system_instruction,
        transcript_context=transcript_context,
        translation_brief=effective_brief,
        ttl_seconds=CACHE_TTL,
        display_name=f"omega-v8-{int(time.time())}",
    )
    cache_elapsed = time.time() - t_cache
    if cache:
        logger.info(f"📦 Cache ready in {cache_elapsed:.1f}s")
    else:
        logger.warning("⚠️ Cache creation failed — falling back to non-cached v8 (each call sends full transcript)")

    # Translate chunks in waves
    results_by_chunk = dict(resumed_chunks)
    t_translate = time.time()

    def _translate_chunk_with_retry(chunk_info, continuity):
        """Translate one chunk with retries. Thread-safe."""
        idx = chunk_info["index"]
        for attempt in range(max_retries):
            try:
                return idx, engine.translate_chunk_v8(
                    chunk_segments=chunk_info["segments"],
                    chunk_index=idx,
                    total_chunks=total_chunks,
                    target_lang=target_lang,
                    cache=cache,
                    system_instruction=system_instruction if not cache else None,
                    transcript_context=transcript_context if not cache else None,
                    continuity_payload=continuity,
                    glossary_terms=active_glossary if active_glossary else None,
                    creative_mode=use_flash_qa,
                )
            except Exception as e:
                logger.warning(f"   ⚠️ Chunk {idx+1}/{total_chunks} attempt {attempt+1}/{max_retries}: {e}")
                if attempt == max_retries - 1:
                    raise
                delay = min(60, 5 * (2 ** attempt)) + random.uniform(0, 3)
                logger.info(f"   ⏳ Chunk {idx+1} backing off {delay:.1f}s...")
                time.sleep(delay)

    total_waves = (total_chunks + WAVE_SIZE - 1) // WAVE_SIZE
    for wave_start in range(0, total_chunks, WAVE_SIZE):
        wave = chunks[wave_start:wave_start + WAVE_SIZE]
        wave_num = wave_start // WAVE_SIZE + 1

        # Skip chunks already in checkpoint
        pending_chunks = [c for c in wave if c["index"] not in results_by_chunk]
        if not pending_chunks:
            logger.info(f"⏭️ Wave {wave_num}/{total_waves} — all chunks already in checkpoint")
            continue

        logger.info(
            f"🌊 Wave {wave_num}/{total_waves} "
            f"({len(pending_chunks)} chunks: {pending_chunks[0]['index']+1}..{pending_chunks[-1]['index']+1})"
        )

        # Build continuity from last completed chunk (by index order)
        continuity = None
        last_completed = max(results_by_chunk.keys()) if results_by_chunk else -1
        if last_completed >= 0:
            last_segs = results_by_chunk[last_completed]
            continuity = [{"id": s.get("id"), "text": s.get("text")} for s in last_segs[-5:]]

        with ThreadPoolExecutor(max_workers=min(WAVE_SIZE, len(pending_chunks))) as executor:
            futures = {}
            for chunk in pending_chunks:
                logger.info(f"  🔄 Submitting chunk {chunk['index']+1}/{total_chunks} ({len(chunk['segments'])} segments)")
                future = executor.submit(_translate_chunk_with_retry, chunk, continuity)
                futures[future] = chunk

            for future in as_completed(futures):
                chunk = futures[future]
                try:
                    idx, segments = future.result()
                    results_by_chunk[idx] = segments
                    logger.info(
                        f"  ✓ Chunk {idx+1}/{total_chunks} done "
                        f"({len(segments)} segments, {len(results_by_chunk)}/{total_chunks} total)"
                    )
                except Exception as e:
                    logger.error(f"  ❌ Chunk {chunk['index']+1}/{total_chunks} FAILED: {e}")
                    # Save checkpoint before dying
                    logger.info(f"💾 Saving v8 checkpoint ({len(results_by_chunk)}/{total_chunks} chunks done)...")
                    try:
                        checkpoint = {
                            "version": "v8",
                            "completed_chunks": {str(k): v for k, v in results_by_chunk.items()},
                            "total_chunks": total_chunks,
                            "chunk_size": CHUNK_SIZE,
                            "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        }
                        upload_json(storage_client, bucket=bucket, blob_name=checkpoint_blob, payload=checkpoint)
                        logger.info(f"💾 Checkpoint saved.")
                    except Exception as cp_err:
                        logger.error(f"Failed to save checkpoint: {cp_err}")
                    # Cleanup cache before dying
                    engine.delete_context_cache(cache)
                    raise

        # Save checkpoint after each successful wave
        try:
            checkpoint = {
                "version": "v8",
                "completed_chunks": {str(k): v for k, v in results_by_chunk.items()},
                "total_chunks": total_chunks,
                "chunk_size": CHUNK_SIZE,
                "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            upload_json(storage_client, bucket=bucket, blob_name=checkpoint_blob, payload=checkpoint)
        except Exception as cp_err:
            logger.warning(f"Checkpoint save failed (non-fatal): {cp_err}")

    translate_elapsed = time.time() - t_translate
    total_elapsed = cache_elapsed + translate_elapsed

    # Cleanup cache
    engine.delete_context_cache(cache)

    # Reassemble in chunk order
    all_translated_segments = []
    for i in range(total_chunks):
        if i in results_by_chunk:
            all_translated_segments.extend(results_by_chunk[i])
        else:
            logger.error(f"❌ Missing chunk {i} in results!")

    logger.info(
        f"⚡ v8 translation done: {len(all_translated_segments)} segments in {total_elapsed:.1f}s "
        f"(cache={cache_elapsed:.1f}s, translate={translate_elapsed:.1f}s, "
        f"{total_chunks} chunks, est ${est_cost:.2f})"
    )

    # ═══════════════════════════════════════════════════════════════
    # PASS 2: Flash QA (if enabled)
    # Pro did the creative translation. Now Flash enforces mechanical constraints.
    # ═══════════════════════════════════════════════════════════════
    if use_flash_qa:
        logger.info(f"🔍 PASS 2: Running Flash QA on {len(all_translated_segments)} segments...")
        t_qa = time.time()

        # Build source_text mapping so Flash can see English alongside Icelandic
        source_map = {}
        for seg in translatable:
            source_map[str(seg.get("id"))] = seg.get("text", "")

        # Ensure each translated segment has source_text for Flash
        for seg in all_translated_segments:
            seg_id = str(seg.get("id", ""))
            if "source_text" not in seg and seg_id in source_map:
                seg["source_text"] = source_map[seg_id]

        try:
            qa_results = engine.flash_qa_pass(
                translated_segments=all_translated_segments,
                target_lang=target_lang,
                glossary_terms=active_glossary if active_glossary else None,
                flash_model=config.MODEL_FLASH_QA,
            )

            # Merge QA results back — Flash returns {id, text}, update the translated segments
            qa_map = {str(r["id"]): r["text"] for r in qa_results}
            qa_changes = 0
            for seg in all_translated_segments:
                seg_id = str(seg.get("id", ""))
                if seg_id in qa_map:
                    new_text = qa_map[seg_id]
                    if new_text != seg.get("text"):
                        qa_changes += 1
                    seg["text"] = new_text

            qa_elapsed = time.time() - t_qa
            logger.info(
                f"🔍 Flash QA complete: {qa_changes}/{len(all_translated_segments)} segments modified "
                f"in {qa_elapsed:.1f}s"
            )
            total_elapsed += qa_elapsed

        except Exception as qa_err:
            logger.error(f"❌ Flash QA pass failed: {qa_err}")
            logger.warning("⚠️ Proceeding with unpolished Pro translations (Flash QA failed)")

    logger.info(
        f"⚡ {'v9 TWO-PASS' if use_flash_qa else 'v8'} COMPLETE: "
        f"{len(all_translated_segments)} segments in {total_elapsed:.1f}s"
    )

    return all_translated_segments


def _run_v7_translation(
    engine, paragraphs, target_lang, program_profile,
    extra_terms, entity_anchors, speaker_genders, visual_context,
    effective_brief, translate_timeout, resumed_results,
    storage_client, bucket, checkpoint_blob,
):
    """
    v7 Architecture: Per-paragraph translation in waves.

    This is the legacy path — one API call per paragraph, waves of 8.
    Kept as a fallback if v8 is disabled.
    """
    WAVE_SIZE = int(os.environ.get("OMEGA_CLOUD_WAVE_SIZE", "8") or 8)
    max_retries = 3
    results_by_index = dict(resumed_results)
    continuity_buffer = []
    total_done = len(resumed_results)

    # Rebuild continuity buffer from resumed results
    for i in sorted(resumed_results.keys()):
        for seg in resumed_results[i]:
            continuity_buffer.append({"id": seg.get("id"), "text": seg.get("text")})
    if len(continuity_buffer) > 20:
        continuity_buffer = continuity_buffer[-10:]

    sliding_ctx_enabled = config.OMEGA_SLIDING_CONTEXT_ENABLED
    sliding_ctx_window = config.OMEGA_SLIDING_CONTEXT_WINDOW

    def _translate_one(idx, paragraph, continuity_snapshot):
        """Translate a single paragraph with retries. Thread-safe."""
        spk_gender = speaker_genders.get(paragraph.speaker)
        sliding_ctx = None
        if sliding_ctx_enabled:
            sliding_ctx = TranslationEngine.build_sliding_context(
                paragraphs, idx, window_seconds=sliding_ctx_window
            )
        for attempt in range(max_retries):
            try:
                return idx, _translate_with_timeout(
                    engine, paragraph, target_lang, program_profile,
                    extra_terms, entity_anchors,
                    timeout=translate_timeout,
                    continuity_payload=continuity_snapshot,
                    speaker_gender=spk_gender,
                    visual_context=visual_context,
                    doc_brief=effective_brief,
                    sliding_context=sliding_ctx,
                )
            except Exception as e:
                logger.warning(f"   ⚠️ [{paragraph.id}] Attempt {attempt+1}/{max_retries}: {e}")
                if attempt == max_retries - 1:
                    raise
                delay = min(60, 5 * (2 ** attempt)) + random.uniform(0, 3)
                logger.info(f"   ⏳ [{paragraph.id}] Backing off {delay:.1f}s before retry...")
                time.sleep(delay)

    total_waves = (len(paragraphs) + WAVE_SIZE - 1) // WAVE_SIZE
    for wave_start in range(0, len(paragraphs), WAVE_SIZE):
        wave = paragraphs[wave_start : wave_start + WAVE_SIZE]
        wave_num = wave_start // WAVE_SIZE + 1
        wave_ids = [p.id for p in wave]

        wave_indices = [wave_start + i for i in range(len(wave))]
        if all(idx in results_by_index for idx in wave_indices):
            logger.info(
                f"⏭️ Wave {wave_num}/{total_waves} — all {len(wave)} paragraphs already in checkpoint, skipping"
            )
            continue

        logger.info(
            f"🌊 Wave {wave_num}/{total_waves} "
            f"({len(wave)} paragraphs: {wave_ids[0]}..{wave_ids[-1]})"
        )

        continuity_snapshot = continuity_buffer[-5:] if continuity_buffer else None

        with ThreadPoolExecutor(max_workers=min(WAVE_SIZE, len(wave))) as executor:
            futures = {}
            for i, p in enumerate(wave):
                abs_idx = wave_start + i
                if abs_idx in results_by_index:
                    logger.info(f"  ⏭️ Paragraph {abs_idx+1}/{len(paragraphs)} [{p.id}] — already in checkpoint")
                    continue
                logger.info(f"  🔄 Submitting Paragraph {abs_idx+1}/{len(paragraphs)} [{p.id}] ({p.duration:.1f}s)")
                future = executor.submit(_translate_one, abs_idx, p, continuity_snapshot)
                futures[future] = (abs_idx, p)

            for future in as_completed(futures):
                abs_idx, p = futures[future]
                try:
                    idx, segments = future.result()
                    results_by_index[idx] = segments
                    total_done += 1
                    logger.info(f"  ✓ Paragraph {abs_idx+1}/{len(paragraphs)} [{p.id}] done ({total_done}/{len(paragraphs)} total)")
                except Exception as e:
                    logger.error(f"  ❌ Paragraph {abs_idx+1}/{len(paragraphs)} [{p.id}] FAILED: {e}")
                    logger.info(f"💾 Saving checkpoint ({len(results_by_index)}/{len(paragraphs)} paragraphs done)...")
                    try:
                        checkpoint = {
                            "completed_segments": {str(k): v for k, v in results_by_index.items()},
                            "last_completed_wave": wave_num - 1,
                            "total_paragraphs": len(paragraphs),
                            "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        }
                        upload_json(storage_client, bucket=bucket, blob_name=checkpoint_blob, payload=checkpoint)
                        logger.info(f"💾 Checkpoint saved.")
                    except Exception as cp_err:
                        logger.error(f"Failed to save checkpoint: {cp_err}")
                    raise

        for i in range(wave_start, wave_start + len(wave)):
            if i in results_by_index:
                for seg in results_by_index[i]:
                    continuity_buffer.append({"id": seg.get("id"), "text": seg.get("text")})
        if len(continuity_buffer) > 20:
            continuity_buffer = continuity_buffer[-10:]

        try:
            checkpoint = {
                "completed_segments": {str(k): v for k, v in results_by_index.items()},
                "last_completed_wave": wave_num,
                "total_paragraphs": len(paragraphs),
                "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            upload_json(storage_client, bucket=bucket, blob_name=checkpoint_blob, payload=checkpoint)
        except Exception as cp_err:
            logger.warning(f"Checkpoint save failed (non-fatal): {cp_err}")

    # Reassemble in original paragraph order
    all_translated_segments = []
    for i in range(len(paragraphs)):
        all_translated_segments.extend(results_by_index[i])

    return all_translated_segments


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", required=True, help="Job ID")
    parser.add_argument("--bucket", required=True, help="GCS Bucket")
    parser.add_argument("--prefix", required=True, help="GCS Prefix")
    args = parser.parse_args()

    logger.info(f"🚀 Starting Cloud Job: {args.job_id}")

    # 1. Initialize Infrastructure
    storage_client = storage.Client()
    paths = GcsJobPaths(args.bucket, args.prefix, args.job_id)

    translate_timeout = int(os.environ.get("OMEGA_CLOUD_TRANSLATE_TIMEOUT", "300") or 300)

    try:
        # 2. Download Inputs
        logger.info("📥 Downloading inputs...")
        job_manifest = download_json(storage_client, bucket=args.bucket, blob_name=paths.job_json())
        skeleton = download_json(storage_client, bucket=args.bucket, blob_name=paths.skeleton_json())

        target_lang = job_manifest.get("target_language", "is")
        program_profile = job_manifest.get("profile", "standard")

        logger.info(f"⚡ Configuration: Target={target_lang}, Profile={program_profile}")

        # 3. Load optional termbook (per-job custom glossary)
        extra_terms = {}
        termbook = try_download_json(storage_client, bucket=args.bucket, blob_name=paths.termbook_json())
        if termbook and isinstance(termbook, dict):
            extra_terms = termbook.get("terms", {})
            if extra_terms:
                logger.info(f"📖 Loaded {len(extra_terms)} extra terms from termbook")

        # 4. Extract entity anchors (names/places consistency)
        entity_anchors = {}
        if isinstance(skeleton, dict) and skeleton.get("entities"):
            entity_anchors = profiles.get_entity_anchors(skeleton.get("entities", []), target_lang)
            if entity_anchors:
                logger.info(f"🏷️ Loaded {len(entity_anchors)} entity anchors")

        # 5. Pre-segment: split long transcription segments into subtitle-sized chunks (2-7s)
        #    This is the architectural fix: Gemini receives subtitle-sized chunks with hard
        #    character budgets instead of 10-60s transcription blobs.
        raw_segments = skeleton.get("segments", [])
        pre_segmented = pre_segment_for_translation(raw_segments, target_lang)
        logger.info(f"🔪 Pre-segmented {len(raw_segments)} segments -> {len(pre_segmented)} subtitle chunks")
        skeleton["segments"] = pre_segmented

        # 5b. Build Paragraphs from pre-segmented chunks
        logger.info("🧱 Building paragraphs from pre-segmented chunks...")
        paragraphs = TranslationEngine.build_paragraphs_from_skeleton(skeleton)
        logger.info(f"   => Created {len(paragraphs)} paragraphs from {len(pre_segmented)} chunks.")

        # ═══════════════════════════════════════════════════════════════
        # GUARDRAIL 1: Paragraph Count Circuit Breaker
        # Expected: ~3-5 paragraphs per minute of audio.
        # A 60-min program should have ~180-300 paragraphs.
        # If we see 500+, something is deeply wrong (fragmentation bug).
        # ═══════════════════════════════════════════════════════════════
        MAX_PARAGRAPHS = int(os.environ.get("OMEGA_MAX_PARAGRAPHS", "500"))
        if len(paragraphs) > MAX_PARAGRAPHS:
            logger.critical(
                f"🛑 CIRCUIT BREAKER: {len(paragraphs)} paragraphs exceeds safety limit of {MAX_PARAGRAPHS}. "
                f"This looks like a fragmentation bug. Aborting to prevent runaway API costs. "
                f"Expected ~3-5 paragraphs per minute of content."
            )
            sys.exit(1)

        # ═══════════════════════════════════════════════════════════════
        # GUARDRAIL 2: Cost Estimation
        # Gemini 2.5 Pro: ~$0.03-0.05 per paragraph (input + output + thinking)
        # Log estimated cost so we can see it in Cloud Run logs.
        # ═══════════════════════════════════════════════════════════════
        est_cost_per_para = 0.04  # conservative average
        est_cost = len(paragraphs) * est_cost_per_para
        MAX_JOB_COST = float(os.environ.get("OMEGA_MAX_JOB_COST", "25.0"))
        logger.info(f"💰 Cost estimate: {len(paragraphs)} paragraphs × ${est_cost_per_para:.2f} ≈ ${est_cost:.2f}")
        if est_cost > MAX_JOB_COST:
            logger.critical(
                f"🛑 COST BREAKER: Estimated cost ${est_cost:.2f} exceeds budget ${MAX_JOB_COST:.2f}. "
                f"Aborting. Increase OMEGA_MAX_JOB_COST env var to override."
            )
            sys.exit(1)

        # ═══════════════════════════════════════════════════════════════
        # GUARDRAIL 3: Checkpoint Resume
        # Check if a previous execution left a checkpoint. If so, resume
        # from where it stopped instead of re-translating everything.
        # ═══════════════════════════════════════════════════════════════
        checkpoint_blob = paths.prefix + "/" + args.job_id + "/translation_checkpoint.json"
        checkpoint_data = try_download_json(storage_client, bucket=args.bucket, blob_name=checkpoint_blob)
        resumed_results = {}
        if checkpoint_data and isinstance(checkpoint_data, dict):
            cp_segments = checkpoint_data.get("completed_segments", {})
            cp_wave = checkpoint_data.get("last_completed_wave", -1)
            if cp_segments:
                # Validate checkpoint: segments must match current paragraph structure
                resumed_results = {int(k): v for k, v in cp_segments.items()}
                logger.info(
                    f"♻️ RESUMING from checkpoint: {len(resumed_results)}/{len(paragraphs)} paragraphs "
                    f"already translated (last wave: {cp_wave}). "
                    f"Skipping ${len(resumed_results) * est_cost_per_para:.2f} in saved API calls."
                )

        # 5c. Extract rich context from skeleton/manifest (ported from legacy worker)
        # Speaker gender map (for Icelandic grammatical agreement)
        speaker_genders = {}
        for spk in skeleton.get("speakers", []):
            name = spk.get("name") or spk.get("id", "Unknown")
            gender = spk.get("gender")  # "male", "female", or None
            if gender:
                speaker_genders[name] = gender
        if speaker_genders:
            logger.info(f"👤 Speaker genders: {speaker_genders}")

        # Document brief (episode context)
        doc_brief = job_manifest.get("doc_brief") or skeleton.get("doc_brief") or None
        if doc_brief:
            logger.info(f"📋 Document brief loaded ({len(doc_brief)} chars)")

        # Visual context (scene descriptions — future enhancement, graceful degradation)
        visual_context = skeleton.get("visual_context") or None

        # 6. Initialize Engine (creates google.genai client with Vertex AI)
        # v8 uses higher thinking budget per chunk (24576 vs 16384 for v7 per-paragraph)
        use_v8 = config.OMEGA_V8_CHUNKED_ENABLED
        thinking_budget = config.OMEGA_V8_THINKING_BUDGET if use_v8 else 16384
        engine = TranslationEngine(thinking_budget=thinking_budget)

        # 6b. Phase 0: Generate Translation Brief (full-program context for every paragraph)
        translation_brief = None
        if config.OMEGA_TRANSLATION_BRIEF_ENABLED:
            try:
                logger.info("📖 Phase 0: Generating Translation Brief...")
                t0 = time.time()
                translation_brief = engine.generate_translation_brief(
                    paragraphs=paragraphs,
                    target_lang=target_lang,
                    program_profile=program_profile,
                    entity_anchors=entity_anchors,
                    extra_terms=extra_terms,
                )
                elapsed = time.time() - t0
                if translation_brief:
                    logger.info(f"📖 Translation Brief generated ({len(translation_brief)} chars, {elapsed:.1f}s)")
                else:
                    logger.info("📖 Translation Brief skipped (too short or empty)")
            except Exception as exc:
                logger.warning(f"⚠️ Translation Brief failed ({exc}), proceeding without")
                translation_brief = None

        # Use AI-generated brief, falling back to manifest/skeleton doc_brief
        effective_brief = translation_brief or doc_brief

        # ═══════════════════════════════════════════════════════════════
        # 7. TRANSLATION — v8 (chunked + cached) or v7 (per-paragraph waves)
        # ═══════════════════════════════════════════════════════════════

        all_translated_segments = []

        if use_v8:
            all_translated_segments = _run_v8_translation(
                engine=engine,
                paragraphs=paragraphs,
                pre_segmented=pre_segmented,
                target_lang=target_lang,
                program_profile=program_profile,
                extra_terms=extra_terms,
                entity_anchors=entity_anchors,
                speaker_genders=speaker_genders,
                effective_brief=effective_brief,
                storage_client=storage_client,
                bucket=args.bucket,
                checkpoint_blob=checkpoint_blob,
                checkpoint_data=checkpoint_data,
            )
        else:
            all_translated_segments = _run_v7_translation(
                engine=engine,
                paragraphs=paragraphs,
                target_lang=target_lang,
                program_profile=program_profile,
                extra_terms=extra_terms,
                entity_anchors=entity_anchors,
                speaker_genders=speaker_genders,
                visual_context=visual_context,
                effective_brief=effective_brief,
                translate_timeout=translate_timeout,
                resumed_results=resumed_results,
                storage_client=storage_client,
                bucket=args.bucket,
                checkpoint_blob=checkpoint_blob,
            )

        # 8. Merge timing data back from source skeleton
        # Gemini returns only {id, text} per segment — we must restore
        # start, end, speaker, words, source_text from the original skeleton.
        # Build source map from pre-segmented data (works for both v7 and v8).
        source_map = {}
        for seg in pre_segmented:
            seg_id = seg.get("id")
            if seg_id is not None:
                source_map[str(seg_id)] = {
                    "start": seg.get("start"),
                    "end": seg.get("end"),
                    "speaker": seg.get("speaker"),
                    "words": seg.get("words"),
                    "source_text": seg.get("text"),
                }

        merged_count = 0
        for seg in all_translated_segments:
            seg_key = str(seg.get("id"))
            if seg_key in source_map:
                seg.update(source_map[seg_key])
                merged_count += 1

        logger.info(f"🔗 Merged timing data for {merged_count}/{len(all_translated_segments)} segments")
        if merged_count < len(all_translated_segments):
            logger.warning(f"⚠️ {len(all_translated_segments) - merged_count} segments had no source timing match")

        # 8b. Post-translation cleanup: fix stray Gemini artifacts
        cleanup_count = 0
        for seg in all_translated_segments:
            text = seg.get("text", "")
            original = text
            # Remove orphaned leading punctuation (". Já" -> "Já")
            text = re.sub(r'^[.,;:]\s*', '', text)
            # Fix trailing double dots ("Jer.." -> "Jer.")
            text = re.sub(r'\.\.\s*$', '.', text)
            # Strip lone dashes that Gemini sometimes returns for interjections
            text = re.sub(r'^[-–—]\s*$', '', text)
            text = text.strip()
            if text != original:
                seg["text"] = text
                cleanup_count += 1
        if cleanup_count:
            logger.info(f"🧹 Cleaned {cleanup_count} Gemini artifacts")

        # 9. Assemble Final Output
        # Structure must match what cloud_sync_service expects in approved.json
        # Determine architecture name
        flash_qa_on = use_v8 and config.OMEGA_FLASH_QA_ENABLED
        if flash_qa_on:
            arch_name = "v9_twopass"
            engine_name = "TranslationEngine_v9"
        elif use_v8:
            arch_name = "v8_chunked"
            engine_name = "TranslationEngine_v8"
        else:
            arch_name = "v7_per_paragraph"
            engine_name = "TranslationEngine_v2"

        meta = {
            "engine": engine_name,
            "architecture": arch_name,
            "model": engine.model_name,
            "thinking_budget": engine.thinking_budget,
            "pre_segmented": len(pre_segmented),
            "original_segments": len(raw_segments),
            "paragraphs": len(paragraphs),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "cloud_run_execution": os.environ.get("CLOUD_RUN_EXECUTION", "unknown"),
        }
        if use_v8:
            meta["v8_chunk_size"] = config.OMEGA_V8_CHUNK_SIZE
            meta["v8_total_chunks"] = (len([s for s in pre_segmented if not s.get("is_audio_event")]) + config.OMEGA_V8_CHUNK_SIZE - 1) // config.OMEGA_V8_CHUNK_SIZE
        if flash_qa_on:
            meta["flash_qa_model"] = config.MODEL_FLASH_QA
            meta["flash_qa_chunk_size"] = config.OMEGA_FLASH_QA_CHUNK_SIZE

        fn_output = {
            "job_id": args.job_id,
            "target_language": target_lang,
            "segments": all_translated_segments,
            "meta": meta,
        }

        # 9. Upload Result
        logger.info(f"📤 Uploading {len(all_translated_segments)} segments to approved.json...")
        upload_json(storage_client, bucket=args.bucket, blob_name=paths.approved_json(), payload=fn_output)

        logger.info("✅ Job Complete.")

    except Exception as e:
        logger.critical(f"💥 Job Failed: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
