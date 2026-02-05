"""
Audio Clip Generator for Review Portal
=======================================
Generates short audio clips for each subtitle line to enable
reviewers to hear the original audio while reviewing translations.
"""

import subprocess
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def generate_audio_clips(
    video_path: Path,
    segments: list[dict],
    output_dir: Path,
    padding_ms: int = 200,
    format: str = "mp3"
) -> list[Path]:
    """
    Generate audio clips for each subtitle segment.
    
    Args:
        video_path: Path to source video/audio file
        segments: List of subtitle segments with 'start' and 'end' times
        output_dir: Directory to save clips
        padding_ms: Extra time before/after each clip (ms)
        format: Output format (mp3 recommended for web)
    
    Returns:
        List of paths to generated clips
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    clips = []
    
    logger.info(f"🎵 Generating {len(segments)} audio clips for review portal...")
    
    for i, segment in enumerate(segments):
        try:
            # Parse start/end times (support both seconds and HH:MM:SS.ms)
            start = _parse_time(segment.get("start", 0))
            end = _parse_time(segment.get("end", start + 3))
            
            # Add padding
            start_padded = max(0, start - padding_ms / 1000)
            duration = (end - start) + (padding_ms * 2 / 1000)
            
            # Cap duration at 10 seconds
            duration = min(duration, 10.0)
            
            clip_path = output_dir / f"clip_{i:04d}.{format}"
            
            cmd = [
                "ffmpeg", "-y",
                "-ss", str(start_padded),
                "-i", str(video_path),
                "-t", str(duration),
                "-vn",  # No video
                "-acodec", "libmp3lame" if format == "mp3" else "aac",
                "-ab", "128k",
                "-ar", "44100",
                "-ac", "1",  # Mono for smaller files
                str(clip_path)
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode == 0 and clip_path.exists():
                clips.append(clip_path)
            else:
                logger.warning(f"   ⚠️ Failed to generate clip {i}: {result.stderr[:100]}")
                
        except Exception as e:
            logger.warning(f"   ⚠️ Error generating clip {i}: {e}")
    
    logger.info(f"   ✅ Generated {len(clips)} of {len(segments)} clips")
    return clips


def _parse_time(time_value) -> float:
    """Parse time value to seconds."""
    if isinstance(time_value, (int, float)):
        return float(time_value)
    
    if isinstance(time_value, str):
        # Handle HH:MM:SS.ms format
        if ":" in time_value:
            parts = time_value.replace(",", ".").split(":")
            if len(parts) == 3:
                h, m, s = parts
                return int(h) * 3600 + int(m) * 60 + float(s)
            elif len(parts) == 2:
                m, s = parts
                return int(m) * 60 + float(s)
        return float(time_value)
    
    return 0.0


def upload_clips_to_gcs(
    clips: list[Path],
    bucket_name: str,
    job_prefix: str,
    job_id: str
) -> list[str]:
    """
    Upload generated clips to GCS.
    
    Returns list of GCS URIs.
    """
    from google.cloud import storage
    
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    
    uploaded = []
    gcs_dir = f"{job_prefix}/{job_id}/audio_clips"
    
    logger.info(f"   ☁️ Uploading {len(clips)} clips to GCS...")
    
    for clip_path in clips:
        blob_name = f"{gcs_dir}/{clip_path.name}"
        blob = bucket.blob(blob_name)
        blob.upload_from_filename(str(clip_path))
        uploaded.append(f"gs://{bucket_name}/{blob_name}")
    
    logger.info(f"   ✅ Uploaded to gs://{bucket_name}/{gcs_dir}/")
    return uploaded


def cleanup_local_clips(clips: list[Path]):
    """Remove local clip files after upload."""
    for clip in clips:
        try:
            clip.unlink()
        except Exception:
            pass


# Convenience function for omega_manager
def prepare_review_clips(
    video_path: Path,
    skeleton_path: Path,
    bucket_name: str,
    job_prefix: str,
    job_id: str
) -> bool:
    """
    Full workflow: Generate clips from skeleton and upload to GCS.
    
    Args:
        video_path: Source video
        skeleton_path: Path to skeleton JSON with segments
        bucket_name: GCS bucket
        job_prefix: GCS prefix
        job_id: Job identifier
    
    Returns:
        True if successful
    """
    import json
    import tempfile
    
    try:
        # Load skeleton
        with open(skeleton_path) as f:
            skeleton = json.load(f)
        
        segments = skeleton.get("segments", [])
        if not segments:
            logger.warning("   ⚠️ No segments found in skeleton")
            return False
        
        # Generate clips to temp dir
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            clips = generate_audio_clips(video_path, segments, temp_path)
            
            if clips:
                upload_clips_to_gcs(clips, bucket_name, job_prefix, job_id)
                return True
            
        return False
        
    except Exception as e:
        logger.error(f"   ❌ Failed to prepare review clips: {e}")
        return False
