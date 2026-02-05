import os
import shutil
import subprocess
import logging
from pathlib import Path
import config
import omega_db

logger = logging.getLogger("OmegaManager.Transcriber")

def get_audio_duration(audio_path: Path) -> float:
    """
    Get the duration of an audio file in seconds using ffprobe.
    """
    cmd = [
        config.FFPROBE_BIN, "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(audio_path)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return float(result.stdout.strip())
    except ValueError:
        logger.warning(f"Could not get duration for {audio_path}, defaulting to 0")
        return 0.0

def ingest(file_path: Path):
    """
    Moves video to Vault, extracts audio, and generates thumbnail.
    Returns (video_path, audio_path, thumbnail_path).
    """
    stem = file_path.stem
    
    # 1. Move to Vault
    vault_video_path = config.VAULT_VIDEOS / file_path.name
    
    if file_path.resolve() != vault_video_path.resolve():
        if vault_video_path.exists():
            os.remove(vault_video_path)
        shutil.move(str(file_path), str(vault_video_path))
        logger.info(f"📦 Moved to Vault: {vault_video_path.name}")
    
    # 2. Extract Audio
    audio_dir = config.VAULT_DIR / "Audio"
    audio_dir.mkdir(exist_ok=True)
    audio_path = audio_dir / f"{stem}.wav"

    if not audio_path.exists():
        logger.info(f"🔊 Extracting Audio: {audio_path.name}")
        cmd = [
            config.FFMPEG_BIN, "-y",
            "-i", str(vault_video_path),
            "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
            str(audio_path)
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
    
    # 3. Generate Thumbnail (for Library view)
    thumbnail_dir = config.VAULT_DIR / "Thumbnails"
    thumbnail_dir.mkdir(exist_ok=True)
    thumbnail_path = thumbnail_dir / f"{stem}.jpg"
    
    if not thumbnail_path.exists():
        logger.info(f"🖼️ Generating Thumbnail: {thumbnail_path.name}")
        thumbnail_path = generate_thumbnail(vault_video_path, thumbnail_path)

    # 4. Generate Proxy (for Dashboard Playback)
    proxies_dir = config.VAULT_DIR / "Proxies"
    proxies_dir.mkdir(exist_ok=True)
    proxy_path = proxies_dir / f"{stem}_PROXY.mp4"
    
    if not proxy_path.exists():
        logger.info(f"🎞️ Generating Proxy: {proxy_path.name}")
        generate_proxy(vault_video_path, proxy_path)
    
    return vault_video_path, audio_path, thumbnail_path


def generate_thumbnail(video_path: Path, output_path: Path, seek_seconds: float = 10) -> Path:
    """
    Extract a frame from video to use as thumbnail.
    Falls back to 25% of duration if seek_seconds exceeds video length.
    """
    try:
        # Try extracting at specified time
        cmd = [
            config.FFMPEG_BIN, "-y",
            "-ss", str(seek_seconds),
            "-i", str(video_path),
            "-vframes", "1",
            "-vf", "scale=320:-1",
            "-q:v", "2",
            str(output_path)
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=30, stdin=subprocess.DEVNULL)
        
        if output_path.exists() and output_path.stat().st_size > 0:
            return output_path
        
        # Fallback: try at 1 second
        cmd[3] = "1"
        subprocess.run(cmd, capture_output=True, timeout=30, stdin=subprocess.DEVNULL)
        
        if output_path.exists() and output_path.stat().st_size > 0:
            return output_path
            
    except Exception as e:
        logger.warning(f"Thumbnail generation failed: {e}")
    
    return None

def generate_proxy(video_path: Path, output_path: Path) -> Path:
    """
    Generates a web-optimised proxy (H.264, 480p) for the dashboard.
    """
    try:
        cmd = [
            config.FFMPEG_BIN, "-y",
            "-i", str(video_path),
            "-c:v", "libx264",
            "-vf", "scale=-2:480",
            "-c:a", "aac",
            "-b:a", "128k",
            str(output_path)
        ]
        # Run asynchronously or block? Ingest is already async task, so blocking is fine.
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
        
        if output_path.exists() and output_path.stat().st_size > 0:
            return output_path
            
    except Exception as e:
        logger.warning(f"Proxy generation failed: {e}")
    
    return None

def transcribe(audio_path: Path, job_id: str = None):
    """
    Transcribes audio to skeleton JSON.

    Uses ElevenLabs Scribe v2 only.

    If OMEGA_DEMUCS_ENABLED=1, first extracts vocals to remove background music.
    """
    stem = job_id if job_id else audio_path.stem

    # Step 1: Demucs vocal extraction (optional, removes background music)
    transcription_audio = audio_path

    if getattr(config, "OMEGA_DEMUCS_ENABLED", False):
        try:
            from workers.vocal_extractor import extract_vocals, is_demucs_available

            if is_demucs_available():
                logger.info(f"🎵 Extracting vocals (removing background music)...")
                omega_db.update_job_via_track(stem, status="Extracting vocals (Demucs)", progress=8.0)

                vocals_path = extract_vocals(
                    audio_path,
                    output_dir=audio_path.parent,
                    model=getattr(config, "OMEGA_DEMUCS_MODEL", "htdemucs_ft"),
                    device=getattr(config, "OMEGA_DEMUCS_DEVICE", "mps"),
                )

                if vocals_path and vocals_path.exists():
                    logger.info(f"✅ Using vocals track: {vocals_path.name}")
                    transcription_audio = vocals_path
                else:
                    logger.warning("⚠️ Vocal extraction failed, using original audio")
            else:
                logger.debug("Demucs not available, skipping vocal extraction")

        except Exception as e:
            logger.warning(f"⚠️ Demucs error, using original audio: {e}")

    # Step 2: Transcription - ElevenLabs Scribe v2 (required)
    selected = getattr(config, "OMEGA_TRANSCRIBER", "elevenlabs") or "elevenlabs"
    selected = str(selected).strip().lower()
    if selected != "elevenlabs":
        message = f"OMEGA_TRANSCRIBER={selected} is not supported. ElevenLabs is required."
        logger.error(message)
        omega_db.update_job_via_track(stem, status="Transcription blocked: ElevenLabs only", progress=0.0)
        raise RuntimeError(message)

    if not config.ELEVENLABS_API_KEY:
        message = "ELEVENLABS_API_KEY not configured. Transcription disabled."
        logger.error(message)
        omega_db.update_job_via_track(stem, status="Missing ELEVENLABS_API_KEY", progress=0.0)
        raise RuntimeError(message)

    try:
        from workers import transcriber_elevenlabs
        return transcriber_elevenlabs.transcribe_elevenlabs(transcription_audio, job_id=job_id)
    except Exception as e:
        logger.error(f"❌ ElevenLabs failed: {e}")
        omega_db.update_job_via_track(stem, status="ElevenLabs transcription failed", progress=0.0)
        raise

def run(file_path: Path, job_id: str = None):
    """
    Full Ingest -> Transcribe pipeline.
    """
    video_path, audio_path, _ = ingest(file_path)
    # If job_id provided, rename extracted audio to match job_id?
    # omega_manager passed job_id to run(). 
    # ingest() puts audio at {file_path.stem}.wav in VAULT_DATA/Audio
    
    # Correction: We should rename the audio to match job_id if provided
    # so that subsequent steps find it easily.
    if job_id:
        original_audio = audio_path
        new_audio_path = audio_path.with_name(f"{job_id}.wav")
        if original_audio != new_audio_path:
             if new_audio_path.exists():
                 new_audio_path.unlink()
             original_audio.rename(new_audio_path)
             audio_path = new_audio_path
             logger.info(f"🔊 Renamed audio to Job ID: {audio_path.name}")

    skeleton_path = transcribe(audio_path, job_id=job_id)
    return skeleton_path
