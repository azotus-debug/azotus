"""
ElevenLabs Scribe v2 Transcription Module

Provides exceptional transcription via ElevenLabs Scribe v2 API with:
- Word-level timestamps with logprob confidence scores
- Keyterm prompting (100 contextual religious terms)
- Audio event tagging (music, laughter, applause)
- Speaker diarization (up to 32 speakers)
- Multi-language detection and code-switching support
- Entity detection (optional - 56 PII/PHI/PCI categories)
- Skeleton output compatible with the Omega pipeline
"""

import os
import json
import logging
import time
from pathlib import Path
from typing import Optional, List, Dict

from elevenlabs import ElevenLabs

import config
import omega_db
from utils.circuit_breaker import get_breaker

logger = logging.getLogger("OmegaManager.Transcriber.ElevenLabs")

# Religious and broadcast vocabulary for keyterm prompting (100 terms max)
# These terms are biased in transcription for better accuracy
RELIGIOUS_KEYTERMS = [
    # Divine Names/Titles (15)
    "Jesus", "Christ", "Jesus Christ", "Holy Spirit", "God", "Lord",
    "Father", "Messiah", "Savior", "Redeemer", "Emmanuel", "Almighty",
    "Son of God", "Lamb of God", "I AM",

    # Biblical Places (12)
    "Jerusalem", "Bethlehem", "Galilee", "Nazareth", "Israel",
    "Jordan", "Judea", "Capernaum", "Gethsemane",
    "Calvary", "Mount Sinai", "Egypt",

    # Theological Terms (18)
    "Hallelujah", "Amen", "Scripture", "Gospel", "Bible",
    "Resurrection", "Redemption", "Salvation", "Covenant", "Grace",
    "Faith", "Baptism", "Communion", "Crucifixion", "Atonement",
    "Repentance", "Righteousness", "Rapture",

    # Biblical Figures (10)
    "Prophet", "Apostle", "Disciple", "Moses", "Abraham",
    "David", "Isaiah", "Elijah", "Paul", "Peter",

    # Ministry/Program Names (12)
    "CBN", "700 Club", "Christian World News", "Jerusalem Dateline",
    "Billy Graham", "Charles Stanley", "In Touch",
    "Joyce Meyer", "TBN", "Daystar", "Superbook", "Operation Blessing",

    # Common Religious Phrases (8)
    "Praise the Lord", "Glory to God", "In Jesus Name",
    "Word of God", "Body of Christ", "Great Commission",
    "Good News", "Kingdom of God",

    # News/Broadcast Terms (15) - Critical for news programs
    "features", "correspondent", "bureau", "anchor", "newsroom",
    "breaking news", "exclusive", "interview", "report", "coverage",
    "persecution", "protesters", "intervention", "ransom", "hostage",

    # Geopolitical Terms (10) - Common in news coverage
    "Iran", "Venezuela", "Maduro", "regime", "sanctions",
    "embassy", "administration", "humanitarian", "crisis", "refugees",
]

# Audio event types that indicate music (for filtering in finalizer)
MUSIC_AUDIO_EVENTS = {"music", "singing", "song", "choir", "instrumental", "background_music"}
# Audio events to preserve in subtitles (shown to viewers)
# NOTE: Empty set = filter ALL audio events. This is subtitles, not closed captioning.
PRESERVE_AUDIO_EVENTS = set()  # Was: {"laughter", "applause", "cheering", "crying", "gasping"}

# Entity categories for PII detection (optional)
DEFAULT_ENTITY_CATEGORIES = [
    "credit_card_number", "phone_number", "email_address",
    "date_of_birth", "ssn", "address", "person_name"
]


def _get_keyterms() -> List[str]:
    """
    Returns combined keyterms from defaults + config (max 100).

    Custom keyterms can be added via ELEVENLABS_KEYTERMS env var.
    """
    terms = list(RELIGIOUS_KEYTERMS)

    # Add custom terms from config (comma-separated)
    custom = getattr(config, "ELEVENLABS_KEYTERMS", "") or os.environ.get("ELEVENLABS_KEYTERMS", "")
    if custom:
        custom_terms = [t.strip() for t in custom.split(",") if t.strip()]
        for term in custom_terms:
            # Scribe v2 limit: 5 words per term, 50 chars max
            if term not in terms and len(term) <= 50 and len(term.split()) <= 5:
                terms.append(term)

    # ElevenLabs allows up to 100 keyterms
    return terms[:100]


def _get_entity_categories() -> Optional[str]:
    """
    Returns entity detection setting if enabled.

    For broadcast quality, we use "all" to catch every name, place, and organization.
    This ensures consistency checking can validate that "Dr. Stanley" is always
    transcribed the same way, and that place names like "Jerusalem" are correct.
    """
    enabled = getattr(config, "ELEVENLABS_ENTITY_DETECTION", True) or \
              os.environ.get("ELEVENLABS_ENTITY_DETECTION", "1").lower() in {"1", "true", "yes"}
    if not enabled:
        return None

    custom = getattr(config, "ELEVENLABS_ENTITY_CATEGORIES", "") or \
             os.environ.get("ELEVENLABS_ENTITY_CATEGORIES", "")
    if custom:
        # Allow comma-separated categories or "all"
        if custom.lower() == "all":
            return "all"
        return [c.strip() for c in custom.split(",") if c.strip()]
    # Default: detect all entities for maximum broadcast quality
    return "all"


def _logprob_to_confidence(logprob: float) -> float:
    """
    Convert log probability to confidence score (0-1).

    Logprob is negative (closer to 0 = higher confidence).
    -0.1 = very confident, -2.0 = low confidence
    """
    if logprob is None:
        return 0.95  # Default high confidence if not provided
    # Clamp to reasonable range and convert
    return min(1.0, max(0.0, 1.0 + logprob))


def _segment_words_enhanced(words: List[Dict], entities: List[Dict] = None) -> List[Dict]:
    """
    Groups word-level timestamps into sentence segments with enhanced metadata.

    Handles:
    - Audio events (music, laughter, applause) as separate segments
    - Speaker changes for diarization (new segment on speaker change)
    - Word confidence scores (logprob → 0-1 confidence)
    - Language detection per segment (for code-switching)
    - Entity annotations for names/places

    Args:
        words: List of word dicts from ElevenLabs API
        entities: Optional list of detected entities

    Returns:
        List of segment dicts in enhanced skeleton format
    """
    if not words:
        return []

    # Build entity lookup by character position (for future use)
    entity_map = {}
    if entities:
        for entity in entities:
            start_char = entity.get("start_char", 0)
            entity_map[start_char] = {
                "type": entity.get("entity_type"),
                "text": entity.get("text"),
                "end_char": entity.get("end_char")
            }

    segments = []
    current_words = []
    current_word_data = []
    current_start = None
    current_speaker = None
    current_confidence_sum = 0.0
    segment_id = 1

    for word in words:
        word_text = word.get("text", "")
        word_start = word.get("start", 0)
        word_end = word.get("end", 0)
        word_speaker = word.get("speaker_id")
        word_type = word.get("type", "word")
        word_logprob = word.get("logprob")

        # Handle audio events (laughter, music, etc.) - create separate segments
        if word_type == "audio_event":
            # Flush current segment if any
            if current_words:
                avg_confidence = current_confidence_sum / len(current_word_data) if current_word_data else 1.0
                segment = _create_segment(
                    segment_id, current_start, current_word_data[-1]["end"],
                    current_words, current_word_data, current_speaker, avg_confidence
                )
                segments.append(segment)
                segment_id += 1
                current_words = []
                current_word_data = []
                current_start = None
                current_speaker = None
                current_confidence_sum = 0.0

            # Add audio event as its own segment
            event_text = word_text.lower().strip("()[]")
            is_music = event_text in MUSIC_AUDIO_EVENTS
            should_preserve = event_text in PRESERVE_AUDIO_EVENTS

            event_segment = {
                "id": segment_id,
                "start": round(word_start, 3),
                "end": round(word_end, 3),
                "text": f"({event_text.upper()})",
                "is_audio_event": True,
                "audio_event_type": event_text,
                "is_music": is_music,
                "preserve_in_subtitle": should_preserve
            }
            segments.append(event_segment)
            segment_id += 1
            continue

        # Handle spacing/punctuation-only tokens
        if word_type == "spacing" or not word_text.strip():
            continue

        # Check for speaker change → force new segment for clear speaker attribution
        if word_speaker and current_speaker and word_speaker != current_speaker:
            if current_words:
                avg_confidence = current_confidence_sum / len(current_word_data) if current_word_data else 1.0
                segment = _create_segment(
                    segment_id, current_start, current_word_data[-1]["end"],
                    current_words, current_word_data, current_speaker, avg_confidence
                )
                segments.append(segment)
                segment_id += 1
                current_words = []
                current_word_data = []
                current_start = None
                current_confidence_sum = 0.0

        # Initialize new segment
        if current_start is None:
            current_start = word_start
            current_speaker = word_speaker

        # Track word confidence
        confidence = _logprob_to_confidence(word_logprob)
        current_confidence_sum += confidence

        current_words.append(word_text)
        word_data = {
            "text": word_text,
            "start": round(word_start, 3),
            "end": round(word_end, 3),
            "confidence": round(confidence, 3)
        }
        if word_speaker:
            word_data["speaker"] = word_speaker
        current_word_data.append(word_data)

        # End segment on sentence-ending punctuation
        if word_text.rstrip().endswith(('.', '?', '!')):
            avg_confidence = current_confidence_sum / len(current_word_data) if current_word_data else 1.0
            segment = _create_segment(
                segment_id, current_start, word_end,
                current_words, current_word_data, current_speaker, avg_confidence
            )
            segments.append(segment)
            segment_id += 1
            current_words = []
            current_word_data = []
            current_start = None
            current_speaker = None
            current_confidence_sum = 0.0

    # Handle remaining words (no sentence-ender at end)
    if current_words:
        avg_confidence = current_confidence_sum / len(current_word_data) if current_word_data else 1.0
        segment = _create_segment(
            segment_id, current_start, current_word_data[-1]["end"],
            current_words, current_word_data, current_speaker, avg_confidence
        )
        segments.append(segment)

    return segments


def _create_segment(
    segment_id: int, start: float, end: float,
    words: List[str], word_data: List[Dict],
    speaker: Optional[str], avg_confidence: float
) -> Dict:
    """Helper to create a segment dict with all metadata."""
    segment = {
        "id": segment_id,
        "start": round(start, 3),
        "end": round(end, 3),
        "text": " ".join(words).strip(),
        "words": word_data,
        "confidence": round(avg_confidence, 3)
    }
    if speaker:
        segment["speaker"] = speaker
    return segment


def transcribe_elevenlabs(
    audio_path: Path,
    max_retries: int = 3,
    job_id: str = None,
    language_code: str = "en",
    seed: int = None
) -> Path:
    """
    Transcribes audio via ElevenLabs Scribe v2 API with all advanced features.

    Features enabled:
    - Word-level timestamps with confidence scores
    - Keyterm prompting (100 religious vocabulary terms)
    - Audio event tagging (music, laughter, applause)
    - Speaker diarization (up to 32 speakers)
    - Entity detection (optional PII/names)

    Args:
        audio_path: Path to audio file (WAV, MP3, M4A, FLAC, etc.)
        max_retries: Number of retry attempts on failure
        job_id: Optional job identifier for tracking/naming
        language_code: ISO-639-1 language code (default: "en")

    Returns:
        Path to skeleton JSON in enhanced pipeline format

    Raises:
        ValueError: If API key not configured
        RuntimeError: If transcription fails after retries
    """
    api_key = getattr(config, "ELEVENLABS_API_KEY", "") or os.environ.get("ELEVENLABS_API_KEY", "")
    if not api_key:
        raise ValueError("ELEVENLABS_API_KEY not configured. Get one at https://elevenlabs.io")

    stem = job_id or audio_path.stem
    # Strip _VOCALS suffix if Demucs was used (maintain original job name)
    if not job_id and stem.endswith("_VOCALS"):
        stem = stem[:-7]

    output_dir = config.VAULT_DATA
    skeleton_path = output_dir / f"{stem}_SKELETON.json"

    logger.info(f"📤 ElevenLabs Scribe v2: Submitting {audio_path.name}")
    omega_db.update_job_via_track(stem, status="Submitting to ElevenLabs Scribe v2", progress=12.0)

    # Initialize SDK client
    client = ElevenLabs(api_key=api_key)

    # Gather configuration for broadcast-quality transcription
    keyterms = _get_keyterms()
    entity_categories = _get_entity_categories()
    enable_diarization = getattr(config, "ELEVENLABS_SPEAKER_DIARIZATION", True)
    if isinstance(enable_diarization, str):
        enable_diarization = enable_diarization.lower() in {"1", "true", "yes", "on"}
    tag_audio_events = getattr(config, "ELEVENLABS_TAG_AUDIO_EVENTS", True)
    if isinstance(tag_audio_events, str):
        tag_audio_events = tag_audio_events.lower() in {"1", "true", "yes", "on"}
    # Diarization threshold: 0.0-0.4 (lower = more sensitive to speaker changes)
    # Using threshold instead of num_speakers for automatic speaker detection
    diarization_threshold = getattr(config, "ELEVENLABS_DIARIZATION_THRESHOLD", 0.3)

    # Language detection: auto-detect or specific code
    auto_detect_language = getattr(config, "ELEVENLABS_AUTO_LANGUAGE", False)
    if isinstance(auto_detect_language, str):
        auto_detect_language = auto_detect_language.lower() in {"1", "true", "yes", "on"}

    if keyterms:
        logger.info(f"🔤 Using {len(keyterms)} keyterms for transcription accuracy")
    if entity_categories:
        logger.info(f"🏷️ Entity detection enabled: {entity_categories}")

    breaker = get_breaker("elevenlabs")
    if breaker.is_open():
        raise RuntimeError("ElevenLabs circuit breaker is open")

    last_error = None
    for attempt in range(max_retries):
        if breaker.is_open():
            raise RuntimeError("ElevenLabs circuit breaker is open")
        try:
            omega_db.update_job_via_track(
                stem,
                status=f"Transcribing via ElevenLabs Scribe v2 (attempt {attempt + 1})",
                progress=15.0
            )

            # Use SDK for clean API integration - broadcast quality settings
            # Note: diarization_threshold and num_speakers are mutually exclusive
            # We prefer threshold for automatic speaker detection with sensitivity control
            with open(audio_path, "rb") as audio_file:
                result = client.speech_to_text.convert(
                    model_id="scribe_v2",  # v2 model for keyterms and full features
                    file=audio_file,
                    language_code=language_code if not auto_detect_language else None,
                    diarize=enable_diarization,
                    # Use threshold for auto speaker detection (preferred for broadcast)
                    diarization_threshold=diarization_threshold if enable_diarization else None,
                    tag_audio_events=tag_audio_events,
                    timestamps_granularity="word",  # Essential for subtitle timing
                    keyterms=keyterms if keyterms else None,
                    entity_detection=entity_categories if entity_categories else None,
                    seed=seed,  # For reproducible results in QA
                )

            breaker.record_success()

            # Convert SDK response to dict-like access
            words = [
                {
                    "text": w.text,
                    "start": w.start,
                    "end": w.end,
                    "type": w.type,
                    "speaker_id": getattr(w, "speaker_id", None),
                    "logprob": getattr(w, "logprob", None),
                }
                for w in result.words
            ] if result.words else []

            entities = [
                {
                    "text": e.text,
                    "type": e.entity_type,  # SDK uses entity_type not type
                    "start_char": e.start_char,
                    "end_char": e.end_char,
                }
                for e in result.entities
            ] if hasattr(result, "entities") and result.entities else []

            detected_language = result.language_code or language_code
            language_confidence = getattr(result, "language_probability", 1.0) or 1.0
            transcription_id = getattr(result, "transcription_id", "") or ""

            # Count words vs audio events
            word_count = len([w for w in words if w.get("type") == "word"])
            audio_events_count = len([w for w in words if w.get("type") == "audio_event"])

            logger.info(f"✅ ElevenLabs: Transcription complete ({word_count} words, {audio_events_count} audio events)")

            # Count unique speakers
            speakers = set()
            for word in words:
                speaker_id = word.get("speaker_id")
                if speaker_id:
                    speakers.add(speaker_id)
            speaker_count = len(speakers)
            if speaker_count > 1:
                logger.info(f"🎙️ Speaker diarization: {speaker_count} speakers detected")

            omega_db.update_job_via_track(stem, status=f"Transcribed ({word_count} words)", progress=25.0)

            # Build enhanced skeleton with segmentation
            segments = _segment_words_enhanced(words, entities)

            # Count audio event segments by type
            music_count = sum(1 for s in segments if s.get("is_music"))
            preserved_events = sum(1 for s in segments if s.get("preserve_in_subtitle"))

            if music_count > 0:
                logger.info(f"🎵 Audio events: {music_count} music segments detected (will be filtered)")
            if preserved_events > 0:
                logger.info(f"😄 Preserved events: {preserved_events} (laughter, applause shown in subtitles)")

            # Build skeleton with full metadata
            skeleton = {
                "file": stem,
                "transcriber": "elevenlabs_scribe_v2",
                "transcription_id": transcription_id,
                "language_code": detected_language,
                "language_confidence": round(language_confidence, 3),
                "speaker_count": speaker_count,
                "segments": segments,
                "metadata": {
                    "word_count": word_count,
                    "audio_events_count": audio_events_count,
                    "music_segments": music_count,
                    "preserved_events": preserved_events,
                    "keyterms_used": len(keyterms),
                    "diarization_enabled": enable_diarization,
                    "entity_detection_enabled": bool(entity_categories),
                    "api_model": "scribe_v2"
                }
            }

            # Include entities if detected (for name consistency validation)
            if entities:
                skeleton["entities"] = entities
                logger.info(f"🏷️ Entities detected: {len(entities)} items")

            # Store transcription metadata in job record
            omega_db.update_job_via_track(
                stem,
                meta={
                    "transcriber": "elevenlabs_scribe_v2",
                    "language_detected": detected_language,
                    "speaker_count": speaker_count,
                    "word_count": word_count,
                    "audio_events": audio_events_count,
                }
            )

            # Save skeleton to disk
            with open(skeleton_path, "w", encoding="utf-8") as f:
                json.dump(skeleton, f, indent=2, ensure_ascii=False)

            logger.info(f"✅ Skeleton saved: {skeleton_path.name} ({len(segments)} segments)")
            return skeleton_path

        except Exception as e:
            breaker.record_failure()
            last_error = str(e)
            logger.warning(f"⚠️ ElevenLabs attempt {attempt + 1} failed: {e}")

        if attempt < max_retries - 1:
            wait_time = 2 ** attempt  # Exponential backoff: 1s, 2s, 4s
            logger.info(f"⏳ Retrying in {wait_time}s...")
            time.sleep(wait_time)

    raise RuntimeError(f"ElevenLabs transcription failed after {max_retries} attempts: {last_error}")
