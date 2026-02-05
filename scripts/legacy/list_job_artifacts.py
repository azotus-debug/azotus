
import logging
import google.auth
from google.cloud import storage

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SA_PATH = "service_account.json"
BUCKET_NAME = "omega-jobs-subtitle-project"
PREFIX = "jobs/new_system-20260204T174454520018Z/"

def list_gcs():
    logger.info(f"Listing GCS files in {BUCKET_NAME}/{PREFIX}...")

    try:
        credentials, project = google.auth.load_credentials_from_file(SA_PATH)
        client = storage.Client(credentials=credentials, project=project)
        bucket = client.bucket(BUCKET_NAME)
        blobs = list(bucket.list_blobs(prefix=PREFIX))
        
        if not blobs:
            logger.info("No blobs found.")
            return

        print("-" * 60)
        for blob in blobs:
            print(f"{blob.name} ({blob.size} bytes) - {blob.updated}")
        print("-" * 60)
        
    except Exception as e:
        logger.error(f"Failed to list GCS: {e}")

if __name__ == "__main__":
    list_gcs()
