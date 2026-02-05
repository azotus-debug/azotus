"""
Demucs Vocal Extractor

Extracts clean vocal track from audio/video files using Facebook's Demucs.
Runs on Apple Silicon M2 Max using MPS acceleration.

This removes background music BEFORE transcription, preventing
ElevenLabs from hallucinating background lyrics.

Usage:
    from workers.vocal_extractor import extract_vocals
    
    vocals_path = extract_vocals(source_path)
    # Now send vocals_path to ElevenLabs instead of original
"""

import logging
import subprocess
import shutil
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("OmegaManager.VocalExtractor")

# Demucs model - htdemucs is fast (5-8x realtime)
# htdemucs_ft is slower but slightly higher quality
DEFAULT_MODEL = "htdemucs"

# Use MPS (Metal Performance Shaders) for Apple Silicon GPU
DEFAULT_DEVICE = "mps"


def is_demucs_available() -> bool:
    """Check if demucs is installed and accessible."""
    result = subprocess.run(["which", "demucs"], capture_output=True)
    return result.returncode == 0


def extract_vocals(
    source_path: Path,
    output_dir: Optional[Path] = None,
    model: str = DEFAULT_MODEL,
    device: str = DEFAULT_DEVICE,
    keep_no_vocals: bool = False,
) -> Optional[Path]:
    """
    Extract vocal track from audio/video file using Demucs.
    
    Args:
        source_path: Path to source audio or video file
        output_dir: Where to save extracted vocals (default: same as source)
        model: Demucs model to use (htdemucs_ft, htdemucs)
        device: Device to use (mps for Apple Silicon, cpu, cuda)
        keep_no_vocals: Also extract "no_vocals" (instrumental) track
        
    Returns:
        Path to extracted vocals.wav file, or None if extraction failed.
    """
    source_path = Path(source_path)
    if not source_path.exists():
        logger.error(f"❌ Source file not found: {source_path}")
        return None
    
    if output_dir is None:
        output_dir = source_path.parent
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Temp dir for demucs output
    temp_dir = output_dir / "_demucs_temp"
    temp_dir.mkdir(exist_ok=True)
    
    stem = source_path.stem
    
    logger.info(f"🎵 Extracting vocals from: {source_path.name}")
    logger.info(f"   Model: {model}, Device: {device}")
    
    start_time = time.time()
    
    # Build demucs command
    cmd = [
        "demucs",
        "-n", model,
        "--two-stems", "vocals",  # Only vocals vs everything else
        "-d", device,
        "-o", str(temp_dir),
        str(source_path)
    ]
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=7200  # 2-hour timeout for very long files
        )
        
        if result.returncode != 0:
            logger.error(f"❌ Demucs failed: {result.stderr[-500:]}")
            return None
            
    except subprocess.TimeoutExpired:
        logger.error("❌ Demucs timed out")
        return None
    except Exception as e:
        logger.error(f"❌ Demucs error: {e}")
        return None
    
    elapsed = time.time() - start_time
    
    # Find output vocals file
    vocals_source = temp_dir / model / stem / "vocals.wav"
    if not vocals_source.exists():
        # Try with different extensions based on input
        for ext in [".wav", ".mp3", ".m4a", ".flac"]:
            alt_stem = source_path.stem
            vocals_source = temp_dir / model / alt_stem / "vocals.wav"
            if vocals_source.exists():
                break
    
    if not vocals_source.exists():
        logger.error(f"❌ Vocals output not found")
        logger.error(f"   Expected at: {temp_dir / model / stem / 'vocals.wav'}")
        # List what's in the temp dir for debugging
        for p in temp_dir.rglob("*"):
            logger.debug(f"   Found: {p}")
        return None
    
    # Move vocals to final location
    final_vocals = output_dir / f"{stem}_VOCALS.wav"
    shutil.move(str(vocals_source), str(final_vocals))
    
    # Clean up temp directory
    shutil.rmtree(str(temp_dir), ignore_errors=True)
    
    # Log performance
    logger.info(f"✅ Vocals extracted in {elapsed:.1f}s: {final_vocals.name}")
    logger.info(f"   Size: {final_vocals.stat().st_size / 1024 / 1024:.1f} MB")
    
    return final_vocals


def extract_vocals_for_transcription(
    video_path: Path,
    audio_output_dir: Path
) -> Path:
    """
    Convenience function for transcription pipeline.
    
    If Demucs is available, extracts vocals.
    Otherwise, just extracts audio normally.
    
    Args:
        video_path: Source video file
        audio_output_dir: Where to save audio
        
    Returns:
        Path to audio file ready for transcription
    """
    if not is_demucs_available():
        logger.warning("⚠️ Demucs not available, using raw audio")
        # Fall back to simple audio extraction
        audio_path = audio_output_dir / f"{video_path.stem}.wav"
        subprocess.run([
            "ffmpeg", "-y", "-i", str(video_path),
            "-vn", "-acodec", "pcm_s16le", "-ar", "44100",
            str(audio_path)
        ], capture_output=True)
        return audio_path
    
    # Extract vocals (removes background music)
    vocals = extract_vocals(video_path, audio_output_dir)
    
    if vocals and vocals.exists():
        return vocals
    else:
        # Fall back to raw audio
        logger.warning("⚠️ Vocal extraction failed, using raw audio")
        audio_path = audio_output_dir / f"{video_path.stem}.wav"
        subprocess.run([
            "ffmpeg", "-y", "-i", str(video_path),
            "-vn", "-acodec", "pcm_s16le", "-ar", "44100",
            str(audio_path)
        ], capture_output=True)
        return audio_path
