"""
Termbook Storage System
=======================
Manages per-language and per-ministry terminology for consistent translations.

Structure in GCS:
- termbooks/{lang}/global.json       - Language-wide terminology
- termbooks/{lang}/{ministry}.json   - Ministry-specific overrides
- termbooks/{lang}/corrections.json  - Auto-learned from reviewer corrections

Usage:
1. Manual termbook entries set via API
2. Auto-learned entries from correction_capture.py
3. Get_termbook() merges all sources for translation prompts
"""

import os
import json
import logging
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)

# GCS Configuration
GCS_BUCKET = os.environ.get("OMEGA_JOBS_BUCKET", "omega-jobs-subtitle-project")
TERMBOOK_PREFIX = os.environ.get("OMEGA_TERMBOOK_PREFIX", "termbooks")


@dataclass
class TermEntry:
    """A single terminology entry"""
    source_term: str          # English term (case-insensitive match)
    target_term: str          # Translated term
    source: str               # Where this came from: 'manual', 'corrections', 'import'
    confidence: float         # 0.0-1.0
    notes: Optional[str]      # Usage notes (e.g., "Use for religious context")
    created_at: str
    updated_at: str
    examples: List[str]       # Example usages


@dataclass
class Termbook:
    """A collection of terminology entries"""
    language: str
    ministry: str             # 'global' for language-wide, else ministry slug
    entries: Dict[str, TermEntry]  # {source_term_lower: TermEntry}
    updated_at: str


def _get_bucket():
    """Get GCS bucket lazily"""
    from google.cloud import storage
    client = storage.Client()
    return client.bucket(GCS_BUCKET)


def load_termbook(lang: str, ministry: str = "global", bucket=None) -> Optional[Termbook]:
    """
    Load a termbook from GCS.

    Args:
        lang: Language code (e.g., 'is', 'nl', 'es')
        ministry: Ministry slug or 'global' for language-wide
        bucket: GCS bucket (optional, lazy-loaded if not provided)

    Returns:
        Termbook or None if not found
    """
    try:
        if bucket is None:
            bucket = _get_bucket()

        path = f"{TERMBOOK_PREFIX}/{lang}/{ministry}.json"
        blob = bucket.blob(path)

        if not blob.exists():
            return None

        data = json.loads(blob.download_as_text())

        # Reconstruct termbook
        entries = {}
        for key, entry_data in data.get("entries", {}).items():
            entries[key] = TermEntry(
                source_term=entry_data["source_term"],
                target_term=entry_data["target_term"],
                source=entry_data.get("source", "import"),
                confidence=entry_data.get("confidence", 1.0),
                notes=entry_data.get("notes"),
                created_at=entry_data.get("created_at", ""),
                updated_at=entry_data.get("updated_at", ""),
                examples=entry_data.get("examples", [])
            )

        return Termbook(
            language=lang,
            ministry=ministry,
            entries=entries,
            updated_at=data.get("updated_at", "")
        )

    except Exception as e:
        logger.error(f"Failed to load termbook {lang}/{ministry}: {e}")
        return None


def save_termbook(termbook: Termbook, bucket=None) -> bool:
    """
    Save a termbook to GCS.

    Args:
        termbook: Termbook to save
        bucket: GCS bucket (optional)

    Returns:
        True if saved successfully
    """
    try:
        if bucket is None:
            bucket = _get_bucket()

        path = f"{TERMBOOK_PREFIX}/{termbook.language}/{termbook.ministry}.json"
        blob = bucket.blob(path)

        data = {
            "language": termbook.language,
            "ministry": termbook.ministry,
            "updated_at": datetime.utcnow().isoformat(),
            "entries": {
                key: asdict(entry)
                for key, entry in termbook.entries.items()
            }
        }

        blob.upload_from_string(
            json.dumps(data, indent=2, ensure_ascii=False),
            content_type="application/json"
        )

        logger.info(f"Saved termbook {termbook.language}/{termbook.ministry}: {len(termbook.entries)} entries")
        return True

    except Exception as e:
        logger.error(f"Failed to save termbook: {e}")
        return False


def get_merged_termbook(lang: str, ministry: str = None, bucket=None) -> Dict[str, str]:
    """
    Get merged terminology for a language + ministry.

    Merges in order (later sources override earlier):
    1. corrections (auto-learned)
    2. global (language-wide manual)
    3. ministry-specific (if provided)

    Args:
        lang: Language code
        ministry: Ministry slug (optional)
        bucket: GCS bucket (optional)

    Returns:
        Dict of {source_term: target_term} for use in translation prompts
    """
    if bucket is None:
        bucket = _get_bucket()

    merged = {}

    # 1. Auto-learned corrections (lowest priority)
    corrections_book = load_termbook(lang, "corrections", bucket)
    if corrections_book:
        for key, entry in corrections_book.entries.items():
            if entry.confidence >= 0.7:  # Only use high-confidence corrections
                merged[entry.source_term] = entry.target_term

    # 2. Global language termbook
    global_book = load_termbook(lang, "global", bucket)
    if global_book:
        for key, entry in global_book.entries.items():
            merged[entry.source_term] = entry.target_term

    # 3. Ministry-specific (highest priority)
    if ministry and ministry != "global":
        ministry_book = load_termbook(lang, ministry, bucket)
        if ministry_book:
            for key, entry in ministry_book.entries.items():
                merged[entry.source_term] = entry.target_term

    return merged


def add_term(
    lang: str,
    source_term: str,
    target_term: str,
    ministry: str = "global",
    source: str = "manual",
    notes: str = None,
    examples: List[str] = None,
    bucket=None
) -> bool:
    """
    Add or update a terminology entry.

    Args:
        lang: Language code
        source_term: English term
        target_term: Translated term
        ministry: Ministry slug or 'global'
        source: Origin of entry ('manual', 'corrections', 'import')
        notes: Usage notes
        examples: Example sentences
        bucket: GCS bucket (optional)

    Returns:
        True if saved successfully
    """
    if bucket is None:
        bucket = _get_bucket()

    # Load existing termbook or create new
    termbook = load_termbook(lang, ministry, bucket)
    if termbook is None:
        termbook = Termbook(
            language=lang,
            ministry=ministry,
            entries={},
            updated_at=""
        )

    key = source_term.lower().strip()
    now = datetime.utcnow().isoformat()

    # Check if updating existing
    existing = termbook.entries.get(key)
    created_at = existing.created_at if existing else now

    termbook.entries[key] = TermEntry(
        source_term=source_term.strip(),
        target_term=target_term.strip(),
        source=source,
        confidence=1.0 if source == "manual" else 0.8,
        notes=notes,
        created_at=created_at,
        updated_at=now,
        examples=examples or []
    )

    return save_termbook(termbook, bucket)


def remove_term(
    lang: str,
    source_term: str,
    ministry: str = "global",
    bucket=None
) -> bool:
    """
    Remove a terminology entry.

    Args:
        lang: Language code
        source_term: English term to remove
        ministry: Ministry slug or 'global'
        bucket: GCS bucket (optional)

    Returns:
        True if removed successfully
    """
    if bucket is None:
        bucket = _get_bucket()

    termbook = load_termbook(lang, ministry, bucket)
    if termbook is None:
        return False

    key = source_term.lower().strip()
    if key not in termbook.entries:
        return False

    del termbook.entries[key]
    return save_termbook(termbook, bucket)


def import_corrections_to_termbook(lang: str, min_confidence: float = 0.8, bucket=None) -> int:
    """
    Import high-confidence corrections into the corrections termbook.

    This promotes auto-learned terminology from the correction_capture system
    into the termbook for use in future translations.

    Args:
        lang: Language code
        min_confidence: Minimum confidence threshold
        bucket: GCS bucket (optional)

    Returns:
        Number of terms imported
    """
    if bucket is None:
        bucket = _get_bucket()

    # Load termbook candidates from corrections
    candidates_path = f"corrections/{lang}/termbook_candidates.json"
    candidates_blob = bucket.blob(candidates_path)

    if not candidates_blob.exists():
        logger.info(f"No termbook candidates for {lang}")
        return 0

    candidates_data = json.loads(candidates_blob.download_as_text())
    candidates = candidates_data.get("candidates", [])

    # Load or create corrections termbook
    termbook = load_termbook(lang, "corrections", bucket)
    if termbook is None:
        termbook = Termbook(
            language=lang,
            ministry="corrections",
            entries={},
            updated_at=""
        )

    imported_count = 0
    now = datetime.utcnow().isoformat()

    for candidate in candidates:
        if candidate.get("confidence", 0) < min_confidence:
            continue

        # These candidates are "wrong_term" -> "correct_term" (both in target language)
        # We need source context to make this useful
        # For now, store as target->target corrections for reference
        wrong = candidate["wrong_term"]
        correct = candidate["correct_term"]
        key = wrong.lower().strip()

        termbook.entries[key] = TermEntry(
            source_term=wrong,  # The wrong translation (to avoid)
            target_term=correct,  # The correct translation
            source="corrections",
            confidence=candidate.get("confidence", 0.8),
            notes=f"Auto-learned: avoid '{wrong}', use '{correct}' instead",
            created_at=now,
            updated_at=now,
            examples=candidate.get("contexts", [])[:3]
        )
        imported_count += 1

    if imported_count > 0:
        save_termbook(termbook, bucket)
        logger.info(f"Imported {imported_count} correction terms for {lang}")

    return imported_count


def list_termbooks(bucket=None) -> Dict[str, List[str]]:
    """
    List all available termbooks by language.

    Returns:
        Dict of {lang: [ministry1, ministry2, ...]}
    """
    if bucket is None:
        bucket = _get_bucket()

    result = {}

    for blob in bucket.list_blobs(prefix=f"{TERMBOOK_PREFIX}/"):
        # Parse path: termbooks/{lang}/{ministry}.json
        parts = blob.name.replace(f"{TERMBOOK_PREFIX}/", "").split("/")
        if len(parts) == 2 and parts[1].endswith(".json"):
            lang = parts[0]
            ministry = parts[1].replace(".json", "")

            if lang not in result:
                result[lang] = []
            result[lang].append(ministry)

    return result
