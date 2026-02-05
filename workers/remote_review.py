"""
Remote Review Handler
=====================
On-demand workflow for sending translations for external review.

Flow:
1. User clicks "Send for Remote Review" in dashboard
2. Generate 480p proxy video
3. Upload to Bunny Stream
4. Send review email with magic link
5. Reviewer sees video + subtitles in review portal

Usage:
    from workers import remote_review
    review_url = remote_review.send_for_remote_review(job_id, "reviewer@cbn.org")
"""

import os
import time
import subprocess
import logging
import requests
from pathlib import Path
from typing import Optional, Tuple

import config
import omega_db
from workers import review_notifier

logger = logging.getLogger(__name__)

# Bunny Stream Configuration
BUNNY_LIBRARY_ID = os.environ.get("BUNNY_LIBRARY_ID", "576409")
BUNNY_API_KEY = os.environ.get("BUNNY_API_KEY", "")
BUNNY_CDN_HOSTNAME = os.environ.get("BUNNY_CDN_HOSTNAME", "vz-5303b4c4-db0.b-cdn.net")
BUNNY_API_BASE = "https://video.bunnycdn.com/library"

# Proxy settings
PROXY_DIR = config.BASE_DIR / "4_DELIVERY" / "PROXY"
PROXY_RESOLUTION = "854:480"
PROXY_CRF = "28"
PROXY_AUDIO_BITRATE = "96k"


def ensure_proxy_dir():
    """Ensure proxy directory exists."""
    PROXY_DIR.mkdir(parents=True, exist_ok=True)


def generate_proxy(source_video: Path, job_id: str) -> Path:
    """
    Generate a 480p proxy video for remote review.
    
    Args:
        source_video: Path to the source video file
        job_id: Job identifier for naming
    
    Returns:
        Path to the generated proxy file
    """
    ensure_proxy_dir()
    output_path = PROXY_DIR / f"{job_id}_proxy.mp4"
    
    # Skip if already exists and is recent
    if output_path.exists():
        age_hours = (time.time() - output_path.stat().st_mtime) / 3600
        if age_hours < 24:
            logger.info(f"   ♻️ Using existing proxy: {output_path.name}")
            return output_path
    
    logger.info(f"   🎬 Generating 480p proxy for: {job_id}")
    
    cmd = [
        "ffmpeg", "-y",
        "-i", str(source_video),
        "-vf", f"scale={PROXY_RESOLUTION}:force_original_aspect_ratio=decrease,pad={PROXY_RESOLUTION}:(ow-iw)/2:(oh-ih)/2",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", PROXY_CRF,
        "-profile:v", "main",
        "-level", "3.1",
        "-c:a", "aac",
        "-b:a", PROXY_AUDIO_BITRATE,
        "-ac", "2",
        "-movflags", "+faststart",
        str(output_path)
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        logger.info(f"   ✅ Proxy generated: {output_path.name} ({output_path.stat().st_size / 1024 / 1024:.1f} MB)")
        return output_path
    except subprocess.CalledProcessError as e:
        logger.error(f"   ❌ Proxy generation failed: {e.stderr}")
        raise


def create_bunny_video(title: str) -> Tuple[str, str]:
    """
    Create a new video entry in Bunny Stream.
    
    Returns:
        Tuple of (video_id, upload_url)
    """
    if not BUNNY_API_KEY:
        raise ValueError("BUNNY_API_KEY not set")
    
    url = f"{BUNNY_API_BASE}/{BUNNY_LIBRARY_ID}/videos"
    headers = {
        "AccessKey": BUNNY_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {"title": title}
    
    response = requests.post(url, json=payload, headers=headers)
    response.raise_for_status()
    
    data = response.json()
    video_id = data["guid"]
    
    logger.info(f"   📦 Created Bunny video entry: {video_id}")
    return video_id


def upload_to_bunny(video_path: Path, video_id: str) -> bool:
    """
    Upload video file to Bunny Stream.
    
    Args:
        video_path: Path to video file
        video_id: Bunny video GUID
    
    Returns:
        True if upload successful
    """
    url = f"{BUNNY_API_BASE}/{BUNNY_LIBRARY_ID}/videos/{video_id}"
    headers = {
        "AccessKey": BUNNY_API_KEY,
        "Content-Type": "application/octet-stream"
    }
    
    file_size = video_path.stat().st_size
    logger.info(f"   📤 Uploading to Bunny: {file_size / 1024 / 1024:.1f} MB")
    
    with open(video_path, "rb") as f:
        response = requests.put(url, data=f, headers=headers)
    
    response.raise_for_status()
    logger.info(f"   ✅ Upload complete")
    return True


def wait_for_encoding(video_id: str, timeout: int = 300) -> bool:
    """
    Wait for Bunny to finish encoding the video.
    
    Args:
        video_id: Bunny video GUID
        timeout: Max seconds to wait
    
    Returns:
        True if encoding completed
    """
    url = f"{BUNNY_API_BASE}/{BUNNY_LIBRARY_ID}/videos/{video_id}"
    headers = {"AccessKey": BUNNY_API_KEY}
    
    start_time = time.time()
    while time.time() - start_time < timeout:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        
        data = response.json()
        status = data.get("status", 0)
        
        # Status codes: 0=created, 1=uploaded, 2=processing, 3=transcoding, 4=finished, 5=error
        if status == 4:
            logger.info(f"   ✅ Bunny encoding complete")
            return True
        elif status == 5:
            logger.error(f"   ❌ Bunny encoding failed")
            return False
        
        logger.debug(f"   ⏳ Encoding status: {status}")
        time.sleep(5)
    
    logger.warning(f"   ⚠️ Encoding timeout after {timeout}s")
    return False


def get_embed_url(video_id: str) -> str:
    """Get the embed URL for a Bunny video."""
    return f"https://iframe.mediadelivery.net/embed/{BUNNY_LIBRARY_ID}/{video_id}"


def get_direct_play_url(video_id: str) -> str:
    """Get the direct play URL for a Bunny video."""
    return f"https://{BUNNY_CDN_HOSTNAME}/{video_id}/play_480p.mp4"


def send_for_remote_review(
    job_id: str,
    reviewer_email: str,
    wait_for_encoding: bool = False
) -> Optional[str]:
    """
    Full remote review flow:
    1. Generate proxy
    2. Upload to Bunny
    3. Send review email
    
    Args:
        job_id: The job identifier
        reviewer_email: Email address to send review link to
        wait_for_encoding: If True, wait for Bunny to finish encoding
    
    Returns:
        Review URL on success, None on failure
    """
    logger.info(f"🚀 Starting remote review for: {job_id}")
    
    try:
        # Get job info
        job = omega_db.get_job_via_track(job_id)
        if not job:
            logger.error(f"   ❌ Job not found: {job_id}")
            return None
        
        meta = job.get("meta", {})
        
        # Find source video
        vault_path = meta.get("vault_path")
        if vault_path:
            source_video = Path(vault_path)
        else:
            original_stem = meta.get("original_stem", job_id)
            source_video = config.VAULT_VIDEOS / f"{original_stem}.mp4"
            if not source_video.exists():
                # Try other extensions
                for ext in [".mpg", ".mov", ".avi", ".mxf"]:
                    candidate = config.VAULT_VIDEOS / f"{original_stem}{ext}"
                    if candidate.exists():
                        source_video = candidate
                        break
        
        if not source_video.exists():
            logger.error(f"   ❌ Source video not found for: {job_id}")
            return None
        
        # Update status
        omega_db.update_job_via_track(job_id, meta={
            "remote_review_status": "generating_proxy",
            "remote_review_email": reviewer_email,
            "remote_review_started": time.strftime("%Y-%m-%dT%H:%M:%SZ")
        })
        
        # Generate proxy
        proxy_path = generate_proxy(source_video, job_id)
        
        # Update status
        omega_db.update_job_via_track(job_id, meta={"remote_review_status": "uploading"})
        
        # Create Bunny video entry
        program_name = meta.get("original_filename", job_id)
        video_id = create_bunny_video(f"Review: {program_name}")
        
        # Upload to Bunny
        upload_to_bunny(proxy_path, video_id)
        
        # Optionally wait for encoding
        if wait_for_encoding:
            omega_db.update_job_via_track(job_id, meta={"remote_review_status": "encoding"})
            if not wait_for_encoding(video_id):
                logger.warning("   ⚠️ Encoding not complete, link may show processing state")
        
        # Get embed URL
        embed_url = get_embed_url(video_id)
        
        # Update job metadata in database
        omega_db.update_job_via_track(job_id, meta={
            "remote_review_status": "sent",
            "bunny_video_id": video_id,
            "bunny_embed_url": embed_url,
            "remote_review_requested": True
        })

        # Also sync to GCS for Review Portal access
        try:
            from gcs_jobs import download_json, upload_json
            job_json = download_json(job_id, "job.json") or {}
            if "meta" not in job_json:
                job_json["meta"] = {}
            job_json["meta"]["bunny_video_id"] = video_id
            job_json["meta"]["bunny_embed_url"] = embed_url
            upload_json(job_id, "job.json", job_json)
            logger.info(f"   📦 Synced Bunny info to GCS")
        except Exception as e:
            logger.warning(f"   ⚠️ Failed to sync Bunny info to GCS: {e}")
        
        # Generate review URL
        review_url = review_notifier.build_review_url(job_id)
        
        # Send email
        target_language = job.get("target_language", "Icelandic")
        review_notifier.send_review_notification(
            job_id=job_id,
            program_name=program_name,
            target_language=target_language,
            reviewer_email=reviewer_email
        )
        
        logger.info(f"✅ Remote review sent: {review_url}")
        return review_url
        
    except Exception as e:
        logger.error(f"❌ Remote review failed: {e}")
        omega_db.update_job_via_track(job_id, meta={
            "remote_review_status": "failed",
            "remote_review_error": str(e)
        })
        return None


def get_review_status(job_id: str) -> dict:
    """Get the current remote review status for a job."""
    job = omega_db.get_job_via_track(job_id)
    if not job:
        return {"status": "not_found"}
    
    meta = job.get("meta", {})
    return {
        "status": meta.get("remote_review_status", "not_requested"),
        "email": meta.get("remote_review_email"),
        "bunny_video_id": meta.get("bunny_video_id"),
        "bunny_embed_url": meta.get("bunny_embed_url"),
        "started": meta.get("remote_review_started"),
        "error": meta.get("remote_review_error")
    }
