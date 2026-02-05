import sys
import os
import logging

# Setup logging to stdout
logging.basicConfig(level=logging.INFO)
sys.path.append(os.getcwd())

import config
import omega_db
from omega_manager import _run_translate_cloud, GcsJobPaths, upload_json

# Configure correct environment
os.environ["OMEGA_CLOUD_PIPELINE"] = "true"

def trigger():
    stem = "i2605_iceland-20260126T141234775082Z"
    target_lang = "is"
    
    print(f"🚀 Manually Triggering Cloud Translation for {stem}...")
    
    # 1. Simulate the Audio Upload (since we are bypassing Ingest's loop where I added it)
    # I need to do this manually here because _run_translate_cloud assumes it's done.
    # Actually, I should inspect _run_translate_cloud again. 
    # Attempting to re-use the _run_ingest logic is hard. 
    # I will just do the upload here to be safe.
    
    bucket_name = config.OMEGA_JOBS_BUCKET
    audio_path = config.VAULT_DIR / "Audio" / f"{stem}.wav"
    if not audio_path.exists():
        print(f"❌ Audio not found: {audio_path}")
        return

    print(f"📤 Uploading Audio: {audio_path.name}")
    from google.cloud import storage
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(f"audio_cache/{audio_path.name}")
    if not blob.exists():
        blob.upload_from_filename(str(audio_path))
        print("   ✅ Audio Uploaded.")
    else:
        print("   ✅ Audio already exists.")

    # 2. Trigger Cloud Run
    try:
        # We need to ensure job.json has 'audio_file' set.
        # The manager might not have set it if we skipped ingest update.
        # Let's patch job.json in GCS.
        paths = GcsJobPaths(bucket=bucket_name, prefix=config.OMEGA_JOBS_PREFIX, job_id=stem)
        job_blob = bucket.blob(paths.job_blob)
        if job_blob.exists():
             import json
             payload = json.loads(job_blob.download_as_text())
             if not payload.get("audio_file"):
                 payload["audio_file"] = audio_path.name
                 job_blob.upload_from_string(json.dumps(payload))
                 print("   ✅ Patched job.json with audio_file.")
        
        # Now run the function
        # We need a skeleton path dummy? _run_translate_cloud takes skel path.
        skel_path = config.VAULT_DATA / f"{stem}_SKELETON.json"
        
        _run_translate_cloud(skel_path, stem, target_lang)
        print("🎉 Trigger Function Completed Successfully.")
        
    except Exception as e:
        print(f"❌ Failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    trigger()
