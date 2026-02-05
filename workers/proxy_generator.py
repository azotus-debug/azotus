"""
Proxy Generator for Multimodal (Azotus) Pipeline

Generates 360p smart proxies optimized for Gemini multimodal analysis.
Features:
- 360p resolution (~4MB/min, efficient for vision AI)
- 1 fps sampling (Gemini default sampling rate, lowest token cost)
- Low-bitrate mono audio for prosody and tone
- Fast preset (speed over quality for analysis)
"""

import subprocess
import logging
from pathlib import Path
from typing import Optional, Tuple

import config
import omega_db

logger = logging.getLogger("OmegaManager.ProxyGenerator")


def get_video_duration(video_path: Path) -> float:
    """Get video duration in seconds using ffprobe."""
    cmd = [
        config.FFPROBE_BIN, "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(video_path)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return float(result.stdout.strip())
    except ValueError:
        logger.warning(f"Could not get duration for {video_path}, defaulting to 0")
        return 0.0


def get_video_resolution(video_path: Path) -> Tuple[int, int]:
    """Get video resolution (width, height) using ffprobe."""
    cmd = [
        config.FFPROBE_BIN, "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=p=0",
        str(video_path)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    try:
        parts = result.stdout.strip().split(",")
        return int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        logger.warning(f"Could not get resolution for {video_path}, defaulting to 1920x1080")
        return 1920, 1080


def get_video_fps(video_path: Path) -> float:
    """Get video frame rate (fps) using ffprobe."""
    cmd = [
        config.FFPROBE_BIN, "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=r_frame_rate",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(video_path)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    rate = result.stdout.strip()
    try:
        if "/" in rate:
            num, den = rate.split("/", 1)
            return float(num) / float(den)
        return float(rate)
    except (ValueError, ZeroDivisionError):
        logger.warning(f"Could not get fps for {video_path}, defaulting to 30")
        return 30.0


def generate_vision_proxy(
    video_path: Path,
    job_id: str,
    output_dir: Optional[Path] = None,
    target_height: int = 360,
    target_fps: int = 1,
    keyframe_interval: int = 1,
    crf: int = 28,
) -> Path:
    """
    Generate a 360p proxy optimized for Gemini vision analysis.

    Args:
        video_path: Path to source video
        job_id: Job identifier for naming and progress updates
        output_dir: Output directory (defaults to PROXIES_DIR)
        target_height: Target resolution height (default 360p)
        target_fps: Target frame rate (default 1 fps)
        keyframe_interval: Seconds between keyframes (default 1)
        crf: Constant Rate Factor for quality (higher = smaller file)

    Returns:
        Path to generated vision proxy

    Features:
        - 360p for efficient Gemini analysis (~4MB/min)
        - 1 fps sampling + extra frames on scene changes
        - Low-bitrate audio for prosody/tone (Gemini multimodal)
        - libx264 with fast preset
    """
    output_dir = output_dir or config.PROXIES_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = output_dir / f"{job_id}_VISION.mp4"

    # Update progress
    omega_db.update_job_via_track(job_id, status="Generating vision proxy", progress=0.05)
    logger.info(f"🎬 Generating vision proxy for {job_id}: {video_path.name}")

    # Get source info
    width, height = get_video_resolution(video_path)
    duration = get_video_duration(video_path)

    logger.info(f"   Source: {width}x{height}, {duration:.1f}s")

    # Calculate scale filter (maintain aspect ratio, target height)
    # -2 ensures width is divisible by 2 (required for h264)
    input_fps = get_video_fps(video_path)
    frames_per_second = max(1, int(round(input_fps)))
    scene_threshold = 0.2

    # Select 1 fps baseline + extra frames on scene changes, then scale.
    select_filter = (
        f"select='gt(scene,{scene_threshold})+not(mod(n\\,{frames_per_second}))',"
        f"setpts=N/{input_fps}/TB,scale=-2:{target_height}"
    )

    cmd = [
        config.FFMPEG_BIN, "-y",
        "-i", str(video_path),
        # Video settings
        "-c:v", "libx264",
        "-vf", select_filter,
        "-vsync", "vfr",
        "-g", "1",  # 1 fps baseline => keyframe every frame
        "-keyint_min", "1",
        "-force_key_frames", f"expr:gte(t,n_forced*{keyframe_interval})",
        "-preset", "fast",
        "-crf", str(crf),
        # Low-bitrate mono audio (prosody/tone)
        "-c:a", "aac",
        "-b:a", "64k",
        "-ac", "1",
        "-ar", "16000",
        # Output
        str(output_path)
    ]

    try:
        result = subprocess.run(
            cmd,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            timeout=duration * 2 + 60  # Allow 2x realtime + 60s buffer
        )

        if output_path.exists() and output_path.stat().st_size > 0:
            file_size_mb = output_path.stat().st_size / (1024 * 1024)
            logger.info(f"   ✅ Vision proxy generated: {output_path.name} ({file_size_mb:.1f}MB)")
            omega_db.update_job_via_track(job_id, status="Vision proxy ready", progress=0.1)
            return output_path
        else:
            raise RuntimeError(f"Vision proxy generation produced empty file: {output_path}")

    except subprocess.TimeoutExpired:
        logger.error(f"   ❌ Vision proxy generation timed out for {job_id}")
        raise RuntimeError(f"Vision proxy generation timed out after {duration * 2 + 60}s")

    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode() if e.stderr else "Unknown error"
        logger.error(f"   ❌ Vision proxy generation failed: {stderr[:500]}")
        raise RuntimeError(f"Vision proxy generation failed: {stderr[:200]}")


def upload_vision_proxy_to_gcs(
    proxy_path: Path,
    bucket: str,
    blob_path: str,
) -> str:
    """
    Upload vision proxy to GCS for cloud processing.

    Args:
        proxy_path: Local path to vision proxy
        bucket: GCS bucket name
        blob_path: Destination blob path (e.g., "jobs/{job_id}/proxy_360p.mp4")

    Returns:
        GCS URI (gs://bucket/blob_path)
    """
    from google.cloud import storage

    logger.info(f"☁️ Uploading vision proxy to gs://{bucket}/{blob_path}")

    client = storage.Client()
    bucket_obj = client.bucket(bucket)
    blob = bucket_obj.blob(blob_path)

    blob.upload_from_filename(
        str(proxy_path),
        content_type="video/mp4",
        timeout=300,  # 5 min timeout for upload
    )

    gcs_uri = f"gs://{bucket}/{blob_path}"
    logger.info(f"   ✅ Uploaded: {gcs_uri}")
    return gcs_uri


# =============================================================================
# INTEGRATION HELPERS
# =============================================================================

def should_generate_vision_proxy(job_id: str) -> bool:
    """
    Check if multimodal is enabled and proxy doesn't already exist.

    Returns True if:
    - OMEGA_MULTIMODAL_ENABLED=1
    - Vision proxy doesn't already exist locally
    """
    import os

    if os.environ.get("OMEGA_MULTIMODAL_ENABLED", "0") != "1":
        return False

    proxy_path = config.PROXIES_DIR / f"{job_id}_VISION.mp4"
    return not proxy_path.exists()


def get_vision_proxy_path(job_id: str) -> Optional[Path]:
    """Get path to existing vision proxy, or None if not generated."""
    proxy_path = config.PROXIES_DIR / f"{job_id}_VISION.mp4"
    if proxy_path.exists() and proxy_path.stat().st_size > 0:
        return proxy_path
    return None
