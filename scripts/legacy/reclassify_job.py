
import sys
import json
import logging
import argparse
from pathlib import Path

# Setup paths
sys.path.append(str(Path(__file__).parent.absolute()))

import config
from workers import audio_classifier
import omega_db

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("ReclassifyTool")

def reclassify_job(job_id):
    job = omega_db.get_job_via_track(job_id)
    if not job:
        logger.error(f"Job {job_id} not found.")
        return

    logger.info(f"Processing Job: {job_id}")
    
    # Locate Skeleton
    skeleton_path = config.VAULT_DATA / f"{job_id}_SKELETON_DONE.json"
    if not skeleton_path.exists():
        # Try fallback
        skeleton_path = config.VAULT_DATA / f"{job_id}_SKELETON.json"
    
    if not skeleton_path.exists():
        logger.error(f"Skeleton file not found for {job_id}")
        return

    # Load Skeleton
    with open(skeleton_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    segments = data.get("segments", [])
    if not segments:
        logger.warning("No segments found in skeleton.")
        return

    # Reset Text (Undo previous music masking if original_text exists)
    logger.info("Resetting segments to original text...")
    for seg in segments:
        if "original_text" in seg:
            # If original_text exists, restore it (unless it was legitimately empty)
            if seg["original_text"]:
                seg["text"] = seg["original_text"]
        else:
            # Create original_text if missing (first run logic usually does this)
            seg["original_text"] = seg.get("text", "")

    # Locate Audio
    meta = job.get("meta", {})
    audio_path = meta.get("audio_path")
    
    
    # Check VAULT_AUDIO fallback
    if not audio_path or not Path(audio_path).exists():
        stem = job.get("file_stem")
        VAULT_AUDIO = config.VAULT_DIR / "Audio"
        candidates = [
            VAULT_AUDIO / f"{stem}.wav",
            VAULT_AUDIO / f"{stem}.mp3",
            VAULT_AUDIO / f"{stem}.m4a"
        ]
        for c in candidates:
            if c.exists():
                audio_path = str(c)
                break
                
    # Fallback to source video (Classifier handles video via ffmpeg)
    if not audio_path or not Path(audio_path).exists():
        video_path = meta.get("vault_path")
        if not video_path:
             video_path = meta.get("source_path")
        
        if video_path and Path(video_path).exists():
            audio_path = video_path
        else:
             # Last ditch: check existing video file in vault by stem
             possible_vid = config.VAULT_VIDEOS / f"{stem}.mp4" # approximation
             if possible_vid.exists():
                 audio_path = str(possible_vid)

    if not audio_path or not Path(audio_path).exists():
        logger.error(f"Media file not found. Meta audio: {meta.get('audio_path')}, Meta video: {meta.get('vault_path')}")
        return

    logger.info(f"Using audio source: {audio_path}")

    # Run Classification
    logger.info("Running Multi-Signal Audio Classification...")
    try:
        # mark_music_segments runs classification and modifies segments
        # Returns (segments, count)
        segments, count = audio_classifier.mark_music_segments(segments, Path(audio_path))
        data["segments"] = segments
        
        # Save back
        with open(skeleton_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            
        logger.info(f"Successfully reclassified segments. Marked {count} as music. Updated {skeleton_path}")
        print(f"SUCCESS: Job {job_id} reclassified. {count} segments marked as music.")
        
    except Exception as e:
        logger.error(f"Classification failed: {e}", exc_info=True)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Re-run audio classification for a job")
    parser.add_argument("job_id", help="The Job ID to process")
    args = parser.parse_args()
    
    reclassify_job(args.job_id)
