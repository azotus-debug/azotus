
import time
import logging
import sys
import google.auth
from google.cloud import storage
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s', datefmt='%H:%M:%S')
logger = logging.getLogger("GCSMonitor")

SA_PATH = "service_account.json"
BUCKET_NAME = "omega-jobs-subtitle-project"
JOB_ID = "new_system-20260204T174454520018Z"
PREFIX = f"jobs/{JOB_ID}/"

def monitor_gcs():
    logger.info(f"👀 Monitoring GCS Bucket: {BUCKET_NAME}/{PREFIX}")
    
    try:
        credentials, project = google.auth.load_credentials_from_file(SA_PATH)
        client = storage.Client(credentials=credentials, project=project)
        bucket = client.bucket(BUCKET_NAME)
    except Exception as e:
        logger.error(f"Failed to auth: {e}")
        return

    known_blobs = {}

    while True:
        try:
            blobs = list(bucket.list_blobs(prefix=PREFIX))
            current_state = {b.name: b.size for b in blobs}
            
            # Check for changes
            for name, size in current_state.items():
                short_name = name.replace(PREFIX, "")
                if name not in known_blobs:
                    logger.info(f"🆕 NEW FILE: {short_name} ({size} bytes)")
                    # If it's the progress file, read it!
                    if "progress.json" in short_name:
                        _read_progress(bucket, name)
                elif known_blobs[name] != size:
                    logger.info(f"📝 UPDATED:  {short_name} ({known_blobs[name]} -> {size} bytes)")
                    if "progress.json" in short_name:
                        _read_progress(bucket, name)
            
            known_blobs = current_state
            
            # Pulse
            sys.stdout.write(".")
            sys.stdout.flush()
            time.sleep(5)
            
        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.error(f"Error polling GCS: {e}")
            time.sleep(5)

def _read_progress(bucket, blob_name):
    try:
        blob = bucket.blob(blob_name)
        content = blob.download_as_text()
        print(f"\n📊 PROGRESS UPDATE:\n{content}\n")
    except Exception as e:
        print(f" (Failed to read progress: {e})")

if __name__ == "__main__":
    monitor_gcs()
