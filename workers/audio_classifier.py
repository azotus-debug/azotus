"""
Audio Classifier - Multi-Signal Fusion Architecture

Provides broadcast-grade speech vs music/singing detection for subtitle gating.
Uses a multi-signal approach for reliability:

    Signal 1: webrtcvad (Voice Activity Detection) - fast, always available
    Signal 2: ElevenLabs word confidence - leverages transcription metadata
    Signal 3: inaSpeechSegmenter (CNN) - speech vs singing vs music (gold standard)

The decision engine combines these signals to correctly handle:
    - Shofar/organ intros (no subtitle)
    - Pastor speaking over intro music (subtitle)
    - Worship songs (no subtitle)
    - Regular sermon speech (subtitle)

Usage:
    from workers.audio_classifier import classify_segments, get_music_ranges
    
    # For segment-level classification
    segments = classify_segments(segments, audio_path)
    
    # For time-range queries
    music_ranges = get_music_ranges(audio_path)
"""

import os
import logging
from pathlib import Path
from typing import List, Tuple, Optional, Dict
import config

logger = logging.getLogger("OmegaManager.AudioClassifier")

# --- Classification Mode (from config) ---
# "full" = All 3 signals (default)
# "light" = webrtcvad + confidence only (no TensorFlow)
# "off" = No classification (subtitle everything)
CLASSIFICATION_MODE = os.environ.get("OMEGA_AUDIO_CLASSIFICATION_MODE", "full").strip().lower()

# --- Confidence Threshold for Music Detection ---
# Lower = more aggressive (catches more singing, risk of hiding soft speech)
# Higher = more conservative (may miss some singing)
# Default 0.45 is balanced for mixed content (news, sermons, worship)
def _get_confidence_threshold() -> float:
    try:
        return float(os.environ.get("OMEGA_MUSIC_CONFIDENCE_THRESHOLD", "0.45"))
    except (ValueError, TypeError):
        return 0.45

CONFIDENCE_THRESHOLD = _get_confidence_threshold()

# --- Signal 1: webrtcvad (Voice Activity Detection) ---
_vad = None
_vad_available = None

def _check_vad_available() -> bool:
    """Check if webrtcvad is available."""
    global _vad_available
    if _vad_available is not None:
        return _vad_available
    
    try:
        import webrtcvad
        _vad_available = True
        return True
    except ImportError:
        _vad_available = False
        logger.warning("⚠️ webrtcvad not installed. VAD fallback disabled.")
        return False


def get_vad(aggressiveness: int = 3):
    """
    Get webrtcvad instance.
    
    Args:
        aggressiveness: 0-3 (higher = more aggressive filtering of non-speech)
    """
    global _vad
    
    if not _check_vad_available():
        return None
    
    if _vad is None:
        import webrtcvad
        _vad = webrtcvad.Vad(aggressiveness)
        logger.info(f"🎤 webrtcvad initialized (aggressiveness={aggressiveness})")
    
    return _vad


def vad_has_speech(audio_path: Path, start: float, end: float, threshold: float = 0.5) -> Optional[bool]:
    """
    Check if a time range contains speech using webrtcvad.
    
    Args:
        audio_path: Path to audio file (must be 16kHz mono PCM)
        start: Start time in seconds
        end: End time in seconds
        threshold: Fraction of frames that must be speech (0.0-1.0)
    
    Returns:
        True if speech detected, False if not, None if VAD unavailable
    """
    vad = get_vad()
    if vad is None:
        return None
    
    try:
        import soundfile as sf
        
        # Read the specific segment
        # webrtcvad requires 16kHz mono PCM
        audio, sample_rate = sf.read(str(audio_path), start=int(start * 16000), stop=int(end * 16000))
        
        if sample_rate != 16000:
            logger.warning(f"VAD requires 16kHz audio, got {sample_rate}Hz. Skipping VAD.")
            return None
        
        # Process in 30ms frames (480 samples at 16kHz)
        frame_size = 480
        speech_frames = 0
        total_frames = 0
        
        for i in range(0, len(audio) - frame_size, frame_size):
            frame = audio[i:i + frame_size]
            # Convert to 16-bit PCM bytes
            frame_bytes = (frame * 32767).astype('int16').tobytes()
            if vad.is_speech(frame_bytes, sample_rate):
                speech_frames += 1
            total_frames += 1
        
        if total_frames == 0:
            return None
        
        speech_ratio = speech_frames / total_frames
        return speech_ratio >= threshold
        
    except Exception as e:
        logger.debug(f"VAD check failed: {e}")
        return None


# --- Signal 3: inaSpeechSegmenter ---
_segmenter = None
_segmenter_available = None
_segmenter_cache: Dict[str, List[Tuple[str, float, float]]] = {}


def _check_segmenter_available() -> bool:
    """Check if inaSpeechSegmenter is installed."""
    global _segmenter_available
    if _segmenter_available is not None:
        return _segmenter_available
    
    if CLASSIFICATION_MODE == "off":
        _segmenter_available = False
        logger.info("🔇 Audio classification disabled (OMEGA_AUDIO_CLASSIFICATION_MODE=off)")
        return False
    
    if CLASSIFICATION_MODE == "light":
        _segmenter_available = False
        logger.info("⚡ Using light classification mode (no TensorFlow)")
        return False
    
    try:
        from inaSpeechSegmenter import Segmenter
        _segmenter_available = True
        return True
    except ImportError:
        _segmenter_available = False
        logger.warning("⚠️ inaSpeechSegmenter not installed. Falling back to VAD-only mode.")
        logger.warning("   Install with: pip install inaSpeechSegmenter")
        return False


def get_segmenter():
    """
    Get or create the inaSpeechSegmenter instance.
    
    Uses 'smn' engine for speech/music/noise classification.
    Gender detection disabled for speed.
    """
    global _segmenter
    
    if not _check_segmenter_available():
        return None
    
    if _segmenter is None:
        from inaSpeechSegmenter import Segmenter
        logger.info("🎵 Initializing inaSpeechSegmenter (CNN-based audio classifier)...")
        _segmenter = Segmenter(vad_engine='smn', detect_gender=False)
        logger.info("✅ inaSpeechSegmenter ready")
    
    return _segmenter


def classify_audio(audio_path: Path) -> List[Tuple[str, float, float]]:
    """
    Classify audio into speech/music/noise segments using inaSpeechSegmenter.
    
    Args:
        audio_path: Path to audio file (any format supported by ffmpeg)
    
    Returns:
        List of (label, start_seconds, end_seconds) tuples.
        Labels: 'speech', 'music', 'noise'
        
    Raises:
        RuntimeError: If inaSpeechSegmenter is not available
    """
    # Check cache first
    cache_key = str(audio_path)
    if cache_key in _segmenter_cache:
        return _segmenter_cache[cache_key]
    
    segmenter = get_segmenter()
    if segmenter is None:
        raise RuntimeError("inaSpeechSegmenter not available")
    
    logger.info(f"🔊 Classifying audio: {audio_path.name}")
    result = segmenter(str(audio_path))
    
    # Count segments by type
    counts = {}
    for label, start, end in result:
        counts[label] = counts.get(label, 0) + 1
    
    logger.info(f"✅ Classification complete: {counts}")
    
    # Cache the result
    _segmenter_cache[cache_key] = result
    
    return result


def get_music_ranges(audio_path: Path) -> List[Tuple[float, float]]:
    """
    Get time ranges that are music (not speech).
    
    Args:
        audio_path: Path to audio file
        
    Returns:
        List of (start_seconds, end_seconds) tuples for music segments.
        Returns empty list if classifier not available.
    """
    try:
        segments = classify_audio(audio_path)
        return [(start, end) for label, start, end in segments if label == 'music']
    except Exception as e:
        logger.warning(f"⚠️ Audio classification failed: {e}")
        return []


def get_speech_ranges(audio_path: Path) -> List[Tuple[float, float]]:
    """
    Get time ranges that are speech.
    
    Args:
        audio_path: Path to audio file
        
    Returns:
        List of (start_seconds, end_seconds) tuples for speech segments.
    """
    try:
        segments = classify_audio(audio_path)
        return [(start, end) for label, start, end in segments if label in ('speech', 'male', 'female')]
    except Exception as e:
        logger.warning(f"⚠️ Audio classification failed: {e}")
        return []


def is_music_timestamp(timestamp: float, music_ranges: List[Tuple[float, float]]) -> bool:
    """
    Check if a timestamp falls within a music range.
    """
    for start, end in music_ranges:
        if start <= timestamp <= end:
            return True
    return False


def is_speech_timestamp(timestamp: float, speech_ranges: List[Tuple[float, float]]) -> bool:
    """
    Check if a timestamp falls within a speech range.
    """
    for start, end in speech_ranges:
        if start <= timestamp <= end:
            return True
    return False


# --- Decision Engine: Multi-Signal Fusion ---

def should_subtitle_segment(
    segment: dict,
    audio_path: Optional[Path] = None,
    music_ranges: Optional[List[Tuple[float, float]]] = None,
    speech_ranges: Optional[List[Tuple[float, float]]] = None,
    confidence_threshold: float = None
) -> Tuple[bool, str]:
    """
    Determine if a segment should be subtitled using multi-signal fusion.
    
    Decision Logic:
        1. If inaSpeechSegmenter says "speech" → subtitle
        2. If inaSpeechSegmenter says "music" AND confidence < threshold → skip (singing)
        3. If inaSpeechSegmenter says "music" AND confidence >= threshold → subtitle (speech over music)
        4. If classifier unavailable, fallback to confidence-only
    
    Args:
        segment: Segment dict with 'start', 'end', 'text', optional 'words' with 'confidence'
        audio_path: Path to audio file (for VAD fallback)
        music_ranges: Pre-computed music ranges (avoids re-classification)
        speech_ranges: Pre-computed speech ranges
        confidence_threshold: Min average word confidence to subtitle during music
    
    Returns:
        Tuple of (should_subtitle: bool, reason: str)
    """
    # Use configured threshold if not provided
    if confidence_threshold is None:
        confidence_threshold = CONFIDENCE_THRESHOLD

    start = segment.get('start', 0)
    end = segment.get('end', 0)
    mid = (start + end) / 2

    # Signal 2: Word Confidence (if available)
    words = segment.get('words', [])
    avg_confidence = 1.0  # Default high confidence
    if words and isinstance(words, list):
        confidences = [w.get('confidence', 1.0) for w in words if isinstance(w, dict)]
        if confidences:
            avg_confidence = sum(confidences) / len(confidences)

    # Signal 3: inaSpeechSegmenter classification
    if speech_ranges is not None:
        if is_speech_timestamp(mid, speech_ranges):
            return True, "speech_detected"

    if music_ranges is not None:
        if is_music_timestamp(mid, music_ranges):
            # In a music region - check confidence
            if avg_confidence < confidence_threshold:
                return False, f"low_confidence_singing ({avg_confidence:.2f})"
            else:
                return True, f"high_confidence_speech_over_music ({avg_confidence:.2f})"

    # If no classification data, use confidence alone (light mode)
    # Use a slightly lower threshold for the fallback to be more conservative
    fallback_threshold = confidence_threshold - 0.05
    if avg_confidence < fallback_threshold:
        return False, f"very_low_confidence ({avg_confidence:.2f})"
    
    # Default: subtitle
    return True, "default_subtitle"


def mark_music_segments(
    segments: List[dict],
    audio_path: Path,
    music_marker: str = "(MUSIC)",
    confidence_threshold: float = None
) -> Tuple[List[dict], int]:
    """
    Mark segments that should be hidden/replaced based on multi-signal analysis.

    Uses the full classification pipeline to determine which segments are:
    - Pure music (mark as MUSIC)
    - Singing (mark as MUSIC)
    - Speech (keep original text)
    - Speech over music (keep original text)

    Args:
        segments: List of segment dicts with 'start', 'end', 'text'
        audio_path: Path to the source audio file
        music_marker: Text to replace segment content with
        confidence_threshold: Min word confidence to keep during music (default from env)

    Returns:
        Tuple of (modified segments, count of marked segments)
    """
    if not segments:
        return segments, 0

    if CLASSIFICATION_MODE == "off":
        return segments, 0

    # Use configured threshold if not provided
    if confidence_threshold is None:
        confidence_threshold = CONFIDENCE_THRESHOLD

    logger.info(f"🎵 Music detection: mode={CLASSIFICATION_MODE}, threshold={confidence_threshold}")

    # Get classification data (cached after first call)
    music_ranges = []
    speech_ranges = []

    try:
        music_ranges = get_music_ranges(audio_path)
        speech_ranges = get_speech_ranges(audio_path)
    except Exception as e:
        logger.warning(f"⚠️ Full classification failed: {e}. Using confidence-only mode.")

    marked_count = 0

    for segment in segments:
        should_sub, reason = should_subtitle_segment(
            segment,
            audio_path=audio_path,
            music_ranges=music_ranges,
            speech_ranges=speech_ranges,
            confidence_threshold=confidence_threshold
        )
        
        if not should_sub:
            segment['original_text'] = segment.get('text', '')
            segment['text'] = music_marker
            segment['is_music'] = True
            segment['music_source'] = 'multi_signal_fusion'
            segment['skip_reason'] = reason
            marked_count += 1
            logger.debug(f"🎵 Marked as music: [{segment.get('id', '?')}] {reason}")
    
    if marked_count > 0:
        logger.info(f"🎵 Multi-signal classifier: Marked {marked_count} segments as (MUSIC)")
    
    return segments, marked_count


# --- Public API ---

def is_available() -> bool:
    """Check if any audio classification is available."""
    if CLASSIFICATION_MODE == "off":
        return False
    if CLASSIFICATION_MODE == "light":
        # Light mode uses confidence scores from transcription, which is always available
        # VAD is just a bonus fallback - confidence-based detection works without it
        return True
    return _check_segmenter_available() or _check_vad_available()


def get_classification_mode() -> str:
    """Return the current classification mode."""
    return CLASSIFICATION_MODE


# --- Layer 2: Text Repetition Detection (Worship Lyrics) ---

import re
import unicodedata
from collections import Counter

def _normalize_text(text: str) -> str:
    """Normalize text for phrase comparison: lowercase, strip punctuation, collapse whitespace."""
    if not text:
        return ""
    t = text.lower().strip()
    # Remove dashes at start (dialogue markers)
    t = re.sub(r'^-\s*', '', t)
    # Remove all punctuation except apostrophes
    t = re.sub(r"[^\w\s']", '', t)
    # Collapse whitespace
    t = re.sub(r'\s+', ' ', t).strip()
    return t


def detect_worship_by_repetition(
    segments: List[dict],
    window_size: int = 60,
    min_occurrences: int = 3,
    min_cluster_size: int = 5,
) -> Tuple[List[dict], int]:
    """
    Detect worship/singing segments by text repetition patterns.

    Worship lyrics repeat the same phrases many times within a short window.
    Sermon speech almost never repeats identical phrases 3+ times.

    Algorithm:
        1. Normalize each segment's text
        2. For each segment, count how many times its normalized text appears
           within a window of ±window_size/2 segments
        3. If a phrase appears >= min_occurrences times, mark those segments
        4. Expand to contiguous clusters (bridge gaps of ≤2 unmarked segments)
        5. Only keep clusters with >= min_cluster_size segments

    Args:
        segments: List of segment dicts with 'text', 'start', 'end'
        window_size: Sliding window size in segments
        min_occurrences: Minimum phrase repetitions to flag
        min_cluster_size: Minimum consecutive segments to form a worship cluster

    Returns:
        Tuple of (modified segments with is_worship=True, count of flagged segments)
    """
    if not segments:
        return segments, 0

    # Skip segments already marked as audio events
    n = len(segments)
    normalized = []
    for seg in segments:
        if seg.get("is_audio_event"):
            normalized.append("")
        else:
            normalized.append(_normalize_text(seg.get("text", "")))

    # Phase 1: Mark segments whose normalized text repeats >= min_occurrences in window
    raw_marks = [False] * n
    half_w = window_size // 2

    for i in range(n):
        if not normalized[i] or len(normalized[i]) < 8:
            # Skip very short phrases (Amen, Já, etc. — handled by interjection filter)
            continue

        # Count occurrences in window
        w_start = max(0, i - half_w)
        w_end = min(n, i + half_w + 1)
        count = 0
        for j in range(w_start, w_end):
            if normalized[j] == normalized[i]:
                count += 1

        if count >= min_occurrences:
            # Mark ALL segments with this phrase in the window
            for j in range(w_start, w_end):
                if normalized[j] == normalized[i]:
                    raw_marks[j] = True

    # Phase 2: Bridge small gaps (≤2 unmarked segments between marked ones)
    bridged = list(raw_marks)
    for i in range(n):
        if not bridged[i]:
            # Check if this is a small gap between marked segments
            prev_marked = -1
            next_marked = -1
            for j in range(i - 1, max(-1, i - 4), -1):
                if bridged[j]:
                    prev_marked = j
                    break
            for j in range(i + 1, min(n, i + 4)):
                if bridged[j]:
                    next_marked = j
                    break
            if prev_marked >= 0 and next_marked >= 0 and (i - prev_marked) <= 3 and (next_marked - i) <= 3:
                bridged[i] = True

    # Phase 3: Identify contiguous clusters and filter by min_cluster_size
    clusters = []
    cluster_start = None
    for i in range(n):
        if bridged[i]:
            if cluster_start is None:
                cluster_start = i
        else:
            if cluster_start is not None:
                clusters.append((cluster_start, i - 1))
                cluster_start = None
    if cluster_start is not None:
        clusters.append((cluster_start, n - 1))

    # Apply min_cluster_size filter
    marked_count = 0
    for c_start, c_end in clusters:
        cluster_len = c_end - c_start + 1
        if cluster_len >= min_cluster_size:
            for i in range(c_start, c_end + 1):
                seg = segments[i]
                if not seg.get("is_audio_event") and not seg.get("is_worship"):
                    seg["is_worship"] = True
                    seg["worship_source"] = "text_repetition"
                    seg["worship_phrase"] = normalized[i][:60] if normalized[i] else ""
                    marked_count += 1

    if marked_count > 0:
        logger.info(f"🎵 Text repetition: {marked_count} segments in {len([c for c in clusters if c[1]-c[0]+1 >= min_cluster_size])} worship clusters")

    return segments, marked_count
