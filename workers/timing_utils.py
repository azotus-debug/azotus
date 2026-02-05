"""
Timing Utilities for Broadcast-Quality Subtitles

This module provides video-aware timing functions:
1. Frame quantization - snap timestamps to frame boundaries
2. Scene detection - find visual cuts in video
3. Speech-aware scene snapping - only snap when speech allows
4. Alass integration - fix timing drift from transcription

These utilities transform "good enough" timing into broadcast-quality timing.
"""

import json
import logging
import subprocess
import shutil
from pathlib import Path
from typing import Optional

import config
from subtitle_standards import (
    DEFAULT_FRAMERATE,
    GAP_SECONDS,
    SCENE_THRESHOLD,
    SCENE_SNAP_WINDOW,
    SPEECH_GAP_THRESHOLD,
)

logger = logging.getLogger("OmegaManager.TimingUtils")


# =============================================================================
# FRAME QUANTIZATION
# =============================================================================

def snap_to_frame(seconds: float, fps: float = DEFAULT_FRAMERATE) -> float:
    """
    Snap a timestamp to the nearest frame boundary.

    Professional subtitles align to exact frame boundaries. This prevents
    the "jitter" that occurs when subtitle timing falls between frames.

    Args:
        seconds: Timestamp in seconds
        fps: Target framerate (default 23.976 for film)

    Returns:
        Timestamp snapped to nearest frame boundary
    """
    if seconds <= 0:
        return 0.0
    frame = round(seconds * fps)
    return frame / fps


def quantize_events(events: list[dict], fps: float = DEFAULT_FRAMERATE) -> list[dict]:
    """
    Snap all subtitle timestamps to frame boundaries.

    Args:
        events: List of subtitle events with 'start' and 'end' keys
        fps: Target framerate

    Returns:
        Events with quantized timestamps
    """
    result = []
    for event in events:
        new_event = event.copy()
        new_event['start'] = snap_to_frame(event['start'], fps)
        new_event['end'] = snap_to_frame(event['end'], fps)

        # Ensure minimum gap after quantization
        if new_event['end'] <= new_event['start']:
            new_event['end'] = new_event['start'] + (2 / fps)  # 2 frames minimum

        result.append(new_event)

    # Enforce minimum gaps between subtitles
    for i in range(len(result) - 1):
        gap = result[i + 1]['start'] - result[i]['end']
        if gap < GAP_SECONDS:
            # Shorten current subtitle to create minimum gap
            result[i]['end'] = snap_to_frame(result[i + 1]['start'] - GAP_SECONDS, fps)

    return result


# =============================================================================
# SCENE DETECTION
# =============================================================================

def detect_scene_cuts(
    video_path: Path,
    threshold: float = SCENE_THRESHOLD,
    cache: bool = True,
    external_cuts: Optional[list[float]] = None
) -> list[float]:
    """
    Detect visual scene cuts in a video. 
    
    Can use FFmpeg OR accept pre-computed cuts (e.g. from Gemini Vision).

    Args:
        video_path: Path to the video file
        threshold: Detection sensitivity (0.0-1.0, lower = more sensitive)
        cache: If True, cache results to a JSON file
        external_cuts: List of pre-computed cut timestamps (from Vision API)

    Returns:
        List of timestamps (in seconds) where scene cuts occur
    """
    if external_cuts is not None:
        logger.info(f"Using {len(external_cuts)} external scene cuts (Gemini Vision)")
        return external_cuts

    if not video_path or not video_path.exists():
        logger.warning(f"Video not found for scene detection: {video_path}")
        return []

    # Check for cached scene cuts
    cache_path = config.VAULT_DATA / f"{video_path.stem}_CUTS.json"
    if cache and cache_path.exists():
        try:
            with open(cache_path, 'r') as f:
                cached = json.load(f)
                if cached.get('threshold') == threshold:
                    logger.info(f"Using cached scene cuts: {len(cached['cuts'])} cuts")
                    return cached['cuts']
        except Exception as e:
            logger.warning(f"Failed to read scene cache: {e}")

    logger.info(f"Detecting scene cuts in {video_path.name} (threshold={threshold})")

    # FFmpeg scene detection filter
    # This analyzes frame differences and outputs timestamps where cuts occur
    cmd = [
        config.FFPROBE_BIN,
        "-v", "quiet",
        "-show_entries", "frame=pts_time",
        "-select_streams", "v:0",
        "-of", "csv=p=0",
        "-f", "lavfi",
        f"movie='{video_path}',select='gt(scene,{threshold})'"
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300  # 5 minute timeout
        )

        if result.returncode != 0:
            logger.error(f"FFprobe scene detection failed: {result.stderr}")
            return []

        # Parse timestamps from output
        cuts = []
        for line in result.stdout.strip().split('\n'):
            line = line.strip()
            if line:
                try:
                    timestamp = float(line)
                    cuts.append(timestamp)
                except ValueError:
                    continue

        logger.info(f"Detected {len(cuts)} scene cuts")

        # Cache the results
        if cache:
            try:
                with open(cache_path, 'w') as f:
                    json.dump({
                        'threshold': threshold,
                        'video': str(video_path),
                        'cuts': cuts
                    }, f)
            except Exception as e:
                logger.warning(f"Failed to cache scene cuts: {e}")

        return cuts

    except subprocess.TimeoutExpired:
        logger.error("Scene detection timed out")
        return []
    except Exception as e:
        logger.error(f"Scene detection failed: {e}")
        return []


def get_speech_gaps(events: list[dict]) -> list[tuple[float, float]]:
    """
    Find gaps between speech (potential scene snap points).

    A speech gap is a period of silence between subtitles. These are
    natural points where scene cuts should snap, because the viewer
    isn't in the middle of reading.

    Args:
        events: List of subtitle events with 'start', 'end', and optionally 'words'

    Returns:
        List of (gap_start, gap_end) tuples where speech gaps occur
    """
    gaps = []

    for i in range(len(events) - 1):
        current_end = events[i]['end']
        next_start = events[i + 1]['start']

        # Check word-level timing if available for more precision
        words = events[i].get('words')
        if isinstance(words, list) and words:
            last_word = words[-1]
            if 'end' in last_word:
                current_end = last_word['end']

        next_words = events[i + 1].get('words')
        if isinstance(next_words, list) and next_words:
            first_word = next_words[0]
            if 'start' in first_word:
                next_start = first_word['start']

        gap_duration = next_start - current_end

        if gap_duration >= SPEECH_GAP_THRESHOLD:
            gaps.append((current_end, next_start))

    return gaps


def is_speech_active(timestamp: float, events: list[dict]) -> bool:
    """
    Check if speech is active at a given timestamp.

    Uses word-level timing when available for precision.

    Args:
        timestamp: The timestamp to check
        events: List of subtitle events

    Returns:
        True if someone is speaking at that timestamp
    """
    for event in events:
        # Check word-level timing first
        words = event.get('words')
        if isinstance(words, list) and words:
            for word in words:
                word_start = word.get('start', event['start'])
                word_end = word.get('end', event['end'])
                if word_start <= timestamp <= word_end:
                    return True
        else:
            # Fall back to segment timing
            if event['start'] <= timestamp <= event['end']:
                return True

    return False


def snap_to_scene_cuts(
    events: list[dict],
    scene_cuts: list[float],
    fps: float = DEFAULT_FRAMERATE
) -> list[dict]:
    """
    Snap subtitle boundaries to nearby scene cuts, but only when speech allows.

    This is the key function for broadcast-quality timing. It ensures subtitles
    don't span visual cuts (which is jarring), but respects speech flow.

    Critical insight: Camera cuts mid-sentence (e.g., wide to close-up of same
    speaker) should NOT break the subtitle. Only snap when there's a speech gap.

    Args:
        events: List of subtitle events
        scene_cuts: List of scene cut timestamps from detect_scene_cuts()
        fps: Target framerate for quantization

    Returns:
        Events with scene-aware timing adjustments
    """
    if not scene_cuts:
        return events

    # Pre-compute speech gaps
    speech_gaps = get_speech_gaps(events)

    def is_in_speech_gap(timestamp: float) -> bool:
        """Check if timestamp falls within a speech gap."""
        for gap_start, gap_end in speech_gaps:
            if gap_start <= timestamp <= gap_end:
                return True
        return False

    result = []
    for event in events:
        new_event = event.copy()

        # Check if any scene cut is near the start of this subtitle
        for cut in scene_cuts:
            distance_to_start = cut - event['start']

            # Cut is near the start and within snap window
            if 0 < distance_to_start < SCENE_SNAP_WINDOW:
                # Only snap if the cut happens during a speech gap
                # (i.e., we're not mid-sentence when the cut happens)
                if is_in_speech_gap(cut) or not is_speech_active(cut, events):
                    new_event['start'] = snap_to_frame(cut, fps)
                    logger.debug(f"Snapped subtitle start to scene cut at {cut:.3f}s")
                    break

            # Cut is just before the start - delay slightly
            elif -0.2 < distance_to_start <= 0:
                if is_in_speech_gap(cut) or not is_speech_active(cut, events):
                    new_event['start'] = snap_to_frame(cut + (2 / fps), fps)  # Start 2 frames after cut
                    break

        # Check if any scene cut is near the end of this subtitle
        for cut in scene_cuts:
            distance_to_end = event['end'] - cut

            # Cut is near the end and within snap window
            if 0 < distance_to_end < SCENE_SNAP_WINDOW:
                # Only snap if the cut happens during a speech gap
                if is_in_speech_gap(cut) or not is_speech_active(cut, events):
                    new_event['end'] = snap_to_frame(cut - (1 / fps), fps)  # End 1 frame before cut
                    logger.debug(f"Snapped subtitle end to scene cut at {cut:.3f}s")
                    break

        # Ensure we didn't create an invalid event
        if new_event['end'] <= new_event['start']:
            new_event['end'] = new_event['start'] + (2 / fps)

        result.append(new_event)

    return result


# =============================================================================
# ALASS INTEGRATION
# =============================================================================

def run_alass(
    video_path: Path,
    srt_path: Path,
    output_path: Optional[Path] = None
) -> Optional[Path]:
    """
    Run Alass to correct subtitle timing drift.

    Alass (Automatic Language-Agnostic Subtitle Synchronization) uses audio
    fingerprinting to align subtitles to the actual speech in the video.
    This fixes drift that accumulates over long content.

    Args:
        video_path: Path to the video file
        srt_path: Path to the input SRT file
        output_path: Path for the aligned output (default: input_aligned.srt)

    Returns:
        Path to the aligned SRT, or None if Alass is unavailable/failed
    """
    # Check if Alass is installed (Homebrew installs it as 'alass-cli')
    alass_bin = shutil.which('alass-cli') or shutil.which('alass')
    if not alass_bin:
        logger.warning("Alass not installed - skipping drift correction")
        return None

    if output_path is None:
        output_path = srt_path.with_suffix('.aligned.srt')

    logger.info(f"Running Alass to correct timing drift...")

    cmd = [
        alass_bin,
        str(video_path),
        str(srt_path),
        str(output_path)
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120  # 2 minute timeout
        )

        if result.returncode == 0 and output_path.exists():
            logger.info(f"Alass alignment complete: {output_path.name}")
            return output_path
        else:
            logger.error(f"Alass failed: {result.stderr}")
            return None

    except subprocess.TimeoutExpired:
        logger.error("Alass timed out")
        return None
    except Exception as e:
        logger.error(f"Alass failed: {e}")
        return None


# =============================================================================
# COMBINED TIMING PIPELINE
# =============================================================================

def apply_broadcast_timing(
    events: list[dict],
    video_path: Optional[Path] = None,
    fps: float = DEFAULT_FRAMERATE,
    scene_snap: bool = True,
    external_cuts: Optional[list[float]] = None
) -> list[dict]:
    """
    Apply full broadcast-quality timing adjustments.

    This is the main entry point for timing optimization. It applies:
    1. Scene-aware snapping (if video provided OR external cuts available)
    2. Frame quantization
    3. Gap enforcement

    Args:
        events: List of subtitle events
        video_path: Optional video path for scene detection
        fps: Target framerate
        scene_snap: Whether to apply scene snapping 
        external_cuts: Optional list of pre-computed cuts (Genetic Shot Detection)

    Returns:
        Events with broadcast-quality timing
    """
    result = events

    # Step 1: Scene-aware snapping (if video available or external cuts provided)
    if scene_snap and ((video_path and video_path.exists()) or external_cuts):
        # Pass external_cuts to detect_scene_cuts (which handles the priority logic)
        scene_cuts = detect_scene_cuts(video_path, external_cuts=external_cuts)
        if scene_cuts:
            result = snap_to_scene_cuts(result, scene_cuts, fps)
            source = "Gemini" if external_cuts else "FFmpeg"
            logger.info(f"Applied scene snapping ({len(scene_cuts)} cuts via {source})")

    # Step 2: Frame quantization
    result = quantize_events(result, fps)
    logger.info(f"Applied frame quantization at {fps} fps")

    return result


def get_video_framerate(video_path: Path) -> float:
    """
    Detect the framerate of a video file.

    Args:
        video_path: Path to the video file

    Returns:
        Framerate in fps, or DEFAULT_FRAMERATE if detection fails
    """
    if not video_path or not video_path.exists():
        return DEFAULT_FRAMERATE

    cmd = [
        config.FFPROBE_BIN,
        "-v", "quiet",
        "-select_streams", "v:0",
        "-show_entries", "stream=r_frame_rate",
        "-of", "csv=p=0",
        str(video_path)
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            # Output is like "24000/1001" or "25/1"
            rate_str = result.stdout.strip()
            if '/' in rate_str:
                num, den = rate_str.split('/')
                fps = float(num) / float(den)
                logger.debug(f"Detected framerate: {fps:.3f} fps")
                return fps
    except Exception as e:
        logger.warning(f"Could not detect framerate: {e}")

    return DEFAULT_FRAMERATE
