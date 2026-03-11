import json
import logging
import re
from collections import Counter
from pathlib import Path
from typing import List, Tuple, Optional

import config
from subtitle_standards import MAX_CHARS_PER_LINE
from .models import SubtitleEvent
from .formatting import strip_metadata_tags, split_into_balanced_lines, add_speaker_dashes
from .timing import process_timing_pipeline
from .qa import generate_qa_report
from .exporters import generate_srt, generate_vtt, generate_ttml
from workers.text_sanitizer import detect_likely_t_escape_corruption

logger = logging.getLogger("omega_finalizer.main")

def normalize_segments_for_review(segments: list, target_language: str = "is") -> list:
    """
    Transforms raw translation JSON segments into pre-timed, broadcast-safe dictionaries
    for human review in the frontend.
    """
    _INTERJECTIONS = {
        "amen", "já", "halelúja", "hallelujah", "halleluja", "praise god",
        "lof sé guði", "ó", "oh", "wow", "yes", "come on", "that's right",
        "glory", "dýrð", "jesús", "jesus",
    }
    normalized = []

    # 1. Parse into strict models
    events = [SubtitleEvent.from_dict(seg) for seg in segments]
    
    # 1b. Add speaker dashes if needed
    events = add_speaker_dashes(events)

    # 2. Extract formatting logic (strip metadata, break lines)
    for event in events:
        if event.is_audio_event and not event.preserve_in_subtitle:
            # Drop pure metadata audio events like [MUSIC]
            continue

        clean_text = strip_metadata_tags(event.text)
        if not clean_text.strip():
            continue

        # Filter standalone interjections (audience reactions)
        stripped = re.sub(r'[.!?,;:\s]+', ' ', clean_text).strip().lower()
        if stripped in _INTERJECTIONS:
            continue

        # Break lines according to Broadcast standards (42 chars max)
        event.lines = split_into_balanced_lines(clean_text, target_language)
        event.text = clean_text  # Store the flat cleaned text

        normalized.append(event)
        
    # 3. Apply timing mathematics (enforce minimums, fix overlaps)
    # We do not apply strict CPS extensions here so the reviewer sees raw translation timings,
    # but we DO fix overlaps so the web player doesn't crash.
    from .timing import fix_overlaps, enforce_minimum_duration
    normalized = enforce_minimum_duration(normalized)
    normalized = fix_overlaps(normalized)
    
    # 4. Return as generic dicts for the frontend
    return [ev.to_dict() for ev in normalized]


def finalize(
    approved_path: Path,
    target_language: str = "is",
    video_path: Optional[Path] = None,
    apply_scene_snap: bool = True,
    apply_alass: bool = False,
) -> Tuple[Path, Path]:
    """
    The main orchestrator. Takes an approved JSON, runs final broadcast math, 
    and generates SRT, VTT, and TTML.
    """
    approved_path = Path(approved_path)
    logger.info(f"🎬 V2 Finalizer Processing: {approved_path.name}")
    if apply_alass:
        logger.info("ℹ️ ALASS requested but not enabled in V2 runtime; continuing with native timing pipeline.")
    
    stem = approved_path.name
    for suffix in [".json", "_ICELANDIC", "_APPROVED", "_normalized", "_SKELETON", "_SKELETON_DONE"]:
        stem = stem.replace(suffix, "")
        
    # 1. Load data
    with open(approved_path, "r", encoding="utf-8") as f:
        data_wrapper = json.load(f)
        
    raw_segments = data_wrapper.get("segments", []) if isinstance(data_wrapper, dict) else data_wrapper
    integrity_report = detect_likely_t_escape_corruption(
        raw_segments if isinstance(raw_segments, list) else [],
        target_language=target_language,
    )
    if integrity_report.get("suspicious"):
        details = integrity_report.get("reason") or "likely tab-escape corruption"
        raise RuntimeError(
            f"Subtitle text integrity gate failed for {approved_path.name}: {details}"
        )

    # 2. Build Models
    # Standalone interjections (audience "Amen", etc.) — visual clutter on broadcast
    _INTERJECTIONS = {
        "amen", "já", "halelúja", "hallelujah", "halleluja", "praise god",
        "lof sé guði", "ó", "oh", "wow", "yes", "come on", "that's right",
        "glory", "dýrð", "jesús", "jesus",
    }
    interjection_count = 0
    events = []
    for seg in raw_segments:
        ev = SubtitleEvent.from_dict(seg)
        if ev.is_audio_event and not ev.preserve_in_subtitle:
            continue
        # Apply strict formatting again just in case reviewing altered line lengths
        clean_text = strip_metadata_tags(ev.text)
        if not clean_text:
            continue
        # Filter standalone interjections (audience reactions)
        stripped = re.sub(r'[.!?,;:\s]+', ' ', clean_text).strip().lower()
        if stripped in _INTERJECTIONS:
            interjection_count += 1
            continue
        ev.lines = split_into_balanced_lines(clean_text, target_language)
        ev.text = clean_text
        events.append(ev)

    events = add_speaker_dashes(events)

    if interjection_count:
        logger.info(f"   🗣️ Filtered {interjection_count} standalone interjections (audience reactions)")

    # 3. Full Timing Pipeline (anchoring CPS, overlap fixes, frame quantization)
    events = process_timing_pipeline(
        events,
        fps=29.97,
        video_path=video_path,
        apply_scene_snap=apply_scene_snap,
    )
    
    # 3b. Final line enforcement — re-split any lines that still exceed 42 chars
    #     (timing pipeline may merge events, altering text that was previously split)
    resplit_count = 0
    for ev in events:
        needs_resplit = False
        if ev.lines:
            for line in ev.lines:
                if len(line) > MAX_CHARS_PER_LINE:
                    needs_resplit = True
                    break
        if needs_resplit:
            ev.lines = split_into_balanced_lines(ev.text, target_language)
            resplit_count += 1
    if resplit_count:
        logger.info(f"   📏 Re-split {resplit_count} subtitles for 42-char enforcement")

    # 3c. Double-dot cleanup — runs LAST before export to catch any artifacts
    #     that earlier processing may have introduced
    dd_fixes = 0
    for ev in events:
        if ev.lines:
            new_lines = []
            for line in ev.lines:
                fixed = re.sub(r'(?<!\.)\.\.(?!\.)', '...', line)
                if fixed != line:
                    dd_fixes += 1
                new_lines.append(fixed)
            ev.lines = new_lines
        if ev.text:
            ev.text = re.sub(r'(?<!\.)\.\.(?!\.)', '...', ev.text)
    if dd_fixes:
        logger.info(f"   🔧 Fixed {dd_fixes} double-dot artifacts → ellipsis")

    # QA Check (Passive warning generation)
    qa_report = generate_qa_report(events)
    if qa_report.get("high_cps_violations", 0) > 0:
        logger.warning(f"   ⚠️ QA Warning: {qa_report['high_cps_violations']} subtitles exceed 20 CPS.")

    # 4. Exports
    srt_path = config.SRT_DIR / f"{stem}.srt"
    vtt_path = config.SRT_DIR / f"{stem}.vtt"
    ttml_path = config.SRT_DIR / f"{stem}.ttml"
    norm_path = config.SRT_DIR / f"{stem}_normalized.json"
    
    generate_srt(events, srt_path)
    generate_vtt(events, vtt_path)
    generate_ttml(events, ttml_path, lang_code=target_language)
    
    with open(norm_path, "w", encoding="utf-8") as f:
        json.dump({"events": [ev.to_dict() for ev in events], "qa": qa_report}, f, ensure_ascii=False, indent=2)
        
    return srt_path, norm_path


def _load_approved_segments(approved_path: Path) -> List[dict]:
    with open(approved_path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, dict):
        segments = payload.get("segments", [])
        return segments if isinstance(segments, list) else []
    return payload if isinstance(payload, list) else []


def _extract_srt_text(srt_path: Path) -> str:
    with open(srt_path, "r", encoding="utf-8") as handle:
        content = handle.read()
    blocks = [block for block in content.strip().split("\n\n") if block.strip()]
    text_parts = []
    for block in blocks:
        lines = block.splitlines()
        if len(lines) < 3:
            continue
        text_lines = [line.replace("{\\an8}", "").strip() for line in lines[2:]]
        text = " ".join([line for line in text_lines if line]).strip()
        if text:
            text_parts.append(text)
    return " ".join(text_parts)


def _tokenize_text(text: str) -> List[str]:
    lowered = (text or "").lower()
    return re.findall(r"\w+", lowered, flags=re.UNICODE)


def compute_srt_text_coverage(approved_path: Path, srt_path: Path, min_ratio: float = 0.995) -> dict:
    """
    Compare approved JSON text against generated SRT text.
    Returns token-level coverage so burn can fail fast on omissions.
    """
    approved_segments = _load_approved_segments(Path(approved_path))
    approved_text = " ".join(str(seg.get("text", "")).strip() for seg in approved_segments if isinstance(seg, dict))
    srt_text = _extract_srt_text(Path(srt_path))

    approved_tokens = _tokenize_text(approved_text)
    srt_tokens = _tokenize_text(srt_text)

    approved_counter = Counter(approved_tokens)
    srt_counter = Counter(srt_tokens)

    total_tokens = sum(approved_counter.values())
    missing_counter = Counter()
    for token, required in approved_counter.items():
        available = srt_counter.get(token, 0)
        if available < required:
            missing_counter[token] = required - available

    missing_tokens = sum(missing_counter.values())
    coverage_ratio = 1.0
    if total_tokens > 0:
        coverage_ratio = 1.0 - (missing_tokens / total_tokens)

    missing_samples = [token for token, _count in missing_counter.most_common(20)]
    return {
        "passed": coverage_ratio >= float(min_ratio),
        "min_ratio": round(float(min_ratio), 6),
        "coverage_ratio": round(float(coverage_ratio), 6),
        "total_tokens": int(total_tokens),
        "missing_tokens": int(missing_tokens),
        "missing_samples": missing_samples,
    }
