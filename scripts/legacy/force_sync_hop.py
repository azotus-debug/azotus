
import os
import json
import logging
from google.cloud import storage
import config
import omega_db

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ForceSync")

STEM = "hop_2912_int57-20260113T134336303802Z"
BUCKET = "omega-jobs-subtitle-project"
PROJECT = "sermon-translator-system"

def force_sync():
    print(f"Force syncing {STEM}...")
    
    # 1. Download approved.json
    client = storage.Client(project=PROJECT)
    bucket = client.bucket(BUCKET)
    blob_name = f"jobs/{STEM}/approved.json"
    blob = bucket.blob(blob_name)
    
    if not blob.exists():
        print(f"❌ Blob {blob_name} does not exist!")
        return

    json_str = blob.download_as_text()
    data = json.loads(json_str)
    
    # 2. Save locally
    local_path = config.TRANSLATED_DONE_DIR / f"{STEM}_APPROVED.json"
    with open(local_path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"✅ Saved to {local_path}")
    
    # 3. Update DB
    omega_db.update_job_via_track(STEM, stage="REVIEWED", status="Human Review Complete", progress=100.0)
    print("✅ DB Updated to REVIEWED")

if __name__ == "__main__":
    force_sync()
