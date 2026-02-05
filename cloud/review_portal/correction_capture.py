"""
Correction Capture System
=========================
Captures and analyzes reviewer corrections to improve future translations.

This module:
1. Detects what reviewers changed vs. original AI translation
2. Categorizes corrections (terminology, accuracy, style, CPS fixes)
3. Stores corrections in GCS for learning
4. Builds termbook entries from consistent corrections
"""

import os
import json
import logging
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, asdict
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)

# GCS Configuration
GCS_BUCKET = os.environ.get("OMEGA_JOBS_BUCKET", "omega-jobs-subtitle-project")
GCS_PREFIX = os.environ.get("OMEGA_JOBS_PREFIX", "jobs")
CORRECTIONS_PREFIX = os.environ.get("OMEGA_CORRECTIONS_PREFIX", "corrections")


@dataclass
class Correction:
    """A single reviewer correction"""
    segment_index: int
    source_text: str           # Original English
    original_translation: str  # AI-generated translation
    corrected_text: str        # What reviewer changed it to
    correction_type: str       # 'terminology', 'accuracy', 'style', 'cps', 'timing', 'other'
    confidence: float          # How confident we are in the categorization
    context: Dict              # Additional context (prev/next segments, job metadata)


@dataclass
class CorrectionReport:
    """Summary of all corrections in a job"""
    job_id: str
    reviewer_name: str
    target_language: str
    program_profile: str
    total_segments: int
    corrected_count: int
    correction_rate: float     # % of segments corrected
    corrections: List[Correction]
    term_candidates: List[Dict]  # Potential termbook entries
    captured_at: str


def compute_similarity(a: str, b: str) -> float:
    """Compute string similarity ratio (0.0-1.0)"""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def detect_correction_type(
    source: str,
    original: str,
    corrected: str,
    context: Dict = None
) -> Tuple[str, float]:
    """
    Detect what type of correction this is.

    Returns: (correction_type, confidence)
    """
    # Quick check: if identical, no correction
    if original.strip() == corrected.strip():
        return ("none", 1.0)

    # Compute similarity
    similarity = compute_similarity(original, corrected)

    # Check for CPS/length fix (similar meaning but shorter)
    len_ratio = len(corrected) / len(original) if len(original) > 0 else 1.0
    if similarity > 0.7 and len_ratio < 0.85:
        return ("cps", 0.8)

    # Check for terminology change (single word swapped)
    original_words = set(original.lower().split())
    corrected_words = set(corrected.lower().split())

    # Words added/removed
    added = corrected_words - original_words
    removed = original_words - corrected_words

    if len(added) == 1 and len(removed) == 1 and len(original_words) < 10:
        # Single word swap - likely terminology
        return ("terminology", 0.85)

    if len(added) <= 2 and len(removed) <= 2 and similarity > 0.6:
        # Minor word changes - likely style
        return ("style", 0.7)

    # Check for accuracy fix (major meaning change)
    if similarity < 0.5:
        return ("accuracy", 0.75)

    # Default to style for moderate changes
    if similarity > 0.5:
        return ("style", 0.6)

    return ("other", 0.5)


def extract_term_candidates(corrections: List[Correction]) -> List[Dict]:
    """
    Extract potential termbook entries from corrections.

    Looks for:
    - Consistent single-word swaps across multiple segments
    - Terminology corrections with high confidence
    """
    term_map = {}  # {(source_word, wrong_word): corrected_word, count}

    for correction in corrections:
        if correction.correction_type != "terminology":
            continue

        # Find the swapped words
        original_words = correction.original_translation.lower().split()
        corrected_words = correction.corrected_text.lower().split()

        original_set = set(original_words)
        corrected_set = set(corrected_words)

        removed = original_set - corrected_set
        added = corrected_set - original_set

        if len(removed) == 1 and len(added) == 1:
            wrong_word = list(removed)[0]
            right_word = list(added)[0]

            # Try to find source context
            source_lower = correction.source_text.lower()

            key = (wrong_word, right_word)
            if key not in term_map:
                term_map[key] = {
                    "wrong_term": wrong_word,
                    "correct_term": right_word,
                    "source_contexts": [],
                    "count": 0
                }

            term_map[key]["count"] += 1
            term_map[key]["source_contexts"].append(correction.source_text[:100])

    # Filter to terms that appear multiple times
    candidates = []
    for key, data in term_map.items():
        if data["count"] >= 2:  # At least 2 occurrences
            candidates.append({
                "wrong_term": data["wrong_term"],
                "correct_term": data["correct_term"],
                "occurrences": data["count"],
                "contexts": data["source_contexts"][:3],  # First 3 examples
                "confidence": min(0.9, 0.5 + data["count"] * 0.1)
            })

    return sorted(candidates, key=lambda x: -x["occurrences"])


def capture_corrections(
    job_id: str,
    original_segments: List[Dict],
    reviewed_segments: List[Dict],
    reviewer_name: str = "Unknown",
    job_metadata: Dict = None
) -> CorrectionReport:
    """
    Capture all corrections made by reviewer.

    Args:
        job_id: The job identifier
        original_segments: Original AI translation segments
        reviewed_segments: Reviewer-approved segments
        reviewer_name: Who made the corrections
        job_metadata: Job context (language, profile, etc.)

    Returns:
        CorrectionReport with all corrections and term candidates
    """
    corrections = []
    job_metadata = job_metadata or {}

    # Ensure same length (pad if needed)
    max_len = max(len(original_segments), len(reviewed_segments))

    for i in range(min(len(original_segments), len(reviewed_segments))):
        orig = original_segments[i]
        reviewed = reviewed_segments[i]

        orig_text = orig.get("text", "").strip()
        reviewed_text = reviewed.get("text", "").strip()
        source_text = orig.get("source_text", "") or reviewed.get("source_text", "")

        # Skip if no change
        if orig_text == reviewed_text:
            continue

        # Skip if (MUSIC) or placeholder
        if orig_text == "(MUSIC)" or reviewed_text == "(MUSIC)":
            continue

        # Detect correction type
        correction_type, confidence = detect_correction_type(
            source=source_text,
            original=orig_text,
            corrected=reviewed_text
        )

        if correction_type == "none":
            continue

        # Build context
        context = {
            "start": orig.get("start", ""),
            "end": orig.get("end", ""),
            "prev_segment": original_segments[i-1].get("text", "")[:100] if i > 0 else "",
            "next_segment": original_segments[i+1].get("text", "")[:100] if i < len(original_segments)-1 else "",
        }

        corrections.append(Correction(
            segment_index=i,
            source_text=source_text,
            original_translation=orig_text,
            corrected_text=reviewed_text,
            correction_type=correction_type,
            confidence=confidence,
            context=context
        ))

    # Extract termbook candidates
    term_candidates = extract_term_candidates(corrections)

    # Build report
    total = len(original_segments)
    corrected_count = len(corrections)
    correction_rate = corrected_count / total if total > 0 else 0.0

    return CorrectionReport(
        job_id=job_id,
        reviewer_name=reviewer_name,
        target_language=job_metadata.get("target_lang", job_metadata.get("target_language_code", "unknown")),
        program_profile=job_metadata.get("program_profile", "standard"),
        total_segments=total,
        corrected_count=corrected_count,
        correction_rate=correction_rate,
        corrections=corrections,
        term_candidates=term_candidates,
        captured_at=datetime.utcnow().isoformat()
    )


def save_corrections_to_gcs(report: CorrectionReport, bucket=None) -> bool:
    """
    Save correction report to GCS.

    Structure:
    - corrections/{lang}/{job_id}_corrections.json - per-job corrections
    - corrections/{lang}/termbook_candidates.json - aggregated term candidates
    """
    try:
        if bucket is None:
            from google.cloud import storage
            client = storage.Client()
            bucket = client.bucket(GCS_BUCKET)

        lang = report.target_language

        # Save per-job corrections
        job_path = f"{CORRECTIONS_PREFIX}/{lang}/{report.job_id}_corrections.json"
        job_blob = bucket.blob(job_path)

        # Convert to serializable dict
        report_dict = {
            "job_id": report.job_id,
            "reviewer_name": report.reviewer_name,
            "target_language": report.target_language,
            "program_profile": report.program_profile,
            "total_segments": report.total_segments,
            "corrected_count": report.corrected_count,
            "correction_rate": report.correction_rate,
            "captured_at": report.captured_at,
            "corrections": [asdict(c) for c in report.corrections],
            "term_candidates": report.term_candidates
        }

        job_blob.upload_from_string(
            json.dumps(report_dict, indent=2, ensure_ascii=False),
            content_type="application/json"
        )
        logger.info(f"Saved corrections for {report.job_id} to {job_path}")

        # Update aggregated termbook candidates
        if report.term_candidates:
            _update_aggregated_termbook(bucket, lang, report.term_candidates)

        return True

    except Exception as e:
        logger.error(f"Failed to save corrections: {e}")
        return False


def _update_aggregated_termbook(bucket, lang: str, new_candidates: List[Dict]):
    """
    Update the aggregated termbook candidates for a language.
    Merges new candidates with existing ones.
    """
    termbook_path = f"{CORRECTIONS_PREFIX}/{lang}/termbook_candidates.json"
    termbook_blob = bucket.blob(termbook_path)

    # Load existing
    existing = {}
    if termbook_blob.exists():
        try:
            existing_data = json.loads(termbook_blob.download_as_text())
            for entry in existing_data.get("candidates", []):
                key = (entry["wrong_term"], entry["correct_term"])
                existing[key] = entry
        except Exception:
            pass

    # Merge new candidates
    for candidate in new_candidates:
        key = (candidate["wrong_term"], candidate["correct_term"])
        if key in existing:
            # Increment count
            existing[key]["occurrences"] += candidate["occurrences"]
            existing[key]["confidence"] = min(0.95, existing[key]["confidence"] + 0.05)
            # Add new contexts (up to 5 total)
            existing[key]["contexts"] = (existing[key]["contexts"] + candidate["contexts"])[:5]
        else:
            existing[key] = candidate

    # Sort by occurrences and save
    candidates_list = sorted(existing.values(), key=lambda x: -x["occurrences"])

    termbook_blob.upload_from_string(
        json.dumps({
            "language": lang,
            "updated_at": datetime.utcnow().isoformat(),
            "total_candidates": len(candidates_list),
            "candidates": candidates_list
        }, indent=2, ensure_ascii=False),
        content_type="application/json"
    )
    logger.info(f"Updated termbook candidates for {lang}: {len(candidates_list)} entries")


def get_correction_stats(bucket=None, lang: str = None) -> Dict:
    """
    Get correction statistics across all jobs.

    Args:
        bucket: GCS bucket (optional, will create if not provided)
        lang: Filter by language (optional)

    Returns:
        Dict with correction statistics
    """
    try:
        if bucket is None:
            from google.cloud import storage
            client = storage.Client()
            bucket = client.bucket(GCS_BUCKET)

        prefix = f"{CORRECTIONS_PREFIX}/"
        if lang:
            prefix = f"{CORRECTIONS_PREFIX}/{lang}/"

        stats = {
            "total_jobs": 0,
            "total_corrections": 0,
            "avg_correction_rate": 0.0,
            "by_type": {},
            "by_language": {},
            "top_term_candidates": []
        }

        correction_rates = []

        # Iterate through correction files
        for blob in bucket.list_blobs(prefix=prefix):
            if not blob.name.endswith("_corrections.json"):
                continue

            try:
                data = json.loads(blob.download_as_text())
                stats["total_jobs"] += 1
                stats["total_corrections"] += data.get("corrected_count", 0)
                correction_rates.append(data.get("correction_rate", 0))

                # Track by language
                job_lang = data.get("target_language", "unknown")
                if job_lang not in stats["by_language"]:
                    stats["by_language"][job_lang] = {"jobs": 0, "corrections": 0}
                stats["by_language"][job_lang]["jobs"] += 1
                stats["by_language"][job_lang]["corrections"] += data.get("corrected_count", 0)

                # Track by type
                for correction in data.get("corrections", []):
                    ctype = correction.get("correction_type", "other")
                    stats["by_type"][ctype] = stats["by_type"].get(ctype, 0) + 1

            except Exception as e:
                logger.debug(f"Error reading {blob.name}: {e}")

        if correction_rates:
            stats["avg_correction_rate"] = sum(correction_rates) / len(correction_rates)

        # Load top term candidates
        if lang:
            termbook_path = f"{CORRECTIONS_PREFIX}/{lang}/termbook_candidates.json"
            termbook_blob = bucket.blob(termbook_path)
            if termbook_blob.exists():
                termbook_data = json.loads(termbook_blob.download_as_text())
                stats["top_term_candidates"] = termbook_data.get("candidates", [])[:10]

        return stats

    except Exception as e:
        logger.error(f"Failed to get correction stats: {e}")
        return {}
