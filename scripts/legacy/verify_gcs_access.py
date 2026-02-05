
import os
import logging
from google.cloud import storage
from google.oauth2 import service_account

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# Constants
BUCKET_NAME = "omega-jobs-subtitle-project"
SA_PATH = "service_account.json"
TEST_BLOB_NAME = "gcs_access_test.txt"

def verify_gcs_access():
    logger.info("Starting GCS Access Verification...")

    # 1. Authenticate
    if not os.path.exists(SA_PATH):
        logger.error(f"❌ Service account file not found: {SA_PATH}")
        return False

    try:
        credentials = service_account.Credentials.from_service_account_file(SA_PATH)
        client = storage.Client(credentials=credentials)
        logger.info("✅ Authenticated with service account.")
    except Exception as e:
        logger.error(f"❌ Authentication failed: {e}")
        return False

    # 2. Check Bucket Access
    try:
        bucket = client.bucket(BUCKET_NAME)
        if not bucket.exists():
             logger.error(f"❌ Bucket '{BUCKET_NAME}' not found or not accessible.")
             return False
        logger.info(f"✅ Bucket '{BUCKET_NAME}' found.")
    except Exception as e:
        logger.error(f"❌ Failed to access bucket: {e}")
        return False

    # 3. Test Write
    try:
        blob = bucket.blob(TEST_BLOB_NAME)
        blob.upload_from_string("This is a test file to verify write access.")
        logger.info(f"✅ Successfully wrote to gs://{BUCKET_NAME}/{TEST_BLOB_NAME}")
    except Exception as e:
        logger.error(f"❌ Write permission failed: {e}")
        return False

    # 4. Test Read
    try:
        content = blob.download_as_text()
        if content == "This is a test file to verify write access.":
             logger.info("✅ Successfully read back the test file.")
        else:
             logger.warning("⚠️ Read content mismatch.")
    except Exception as e:
        logger.error(f"❌ Read permission failed: {e}")
        return False

    # 5. Cleanup
    try:
        blob.delete()
        logger.info("✅ Successfully deleted test file.")
    except Exception as e:
        logger.warning(f"⚠️ Failed to delete test file: {e}")
    
    return True

if __name__ == "__main__":
    if verify_gcs_access():
        logger.info("\n🎉 GCS Access Verified: Read/Write OK")
    else:
        logger.error("\n❌ GCS Access Verification Failed")
