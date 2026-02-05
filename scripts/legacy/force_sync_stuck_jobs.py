
import os
import sys
import logging
from google.cloud import storage
import config
import omega_manager
import omega_db

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("ForceSync")

def recover_jobs():
    """
    Manually triggers _sync_cloud_approved_idempotent for all jobs 
    that are in 'TRANSLATING_CLOUD_SUBMITTED' or 'CLOUD_TRANSLATING' stage.
    """
    logger.info("🚀 Starting Force Sync Recovery...")
    
    # 1. Ensure DB connection
    if not config.DB_PATH.exists():
        logger.error(f"❌ Database not found at {config.DB_PATH}")
        return

    # 2. Get GCS Client
    try:
        storage_client = storage.Client()
        logger.info("✅ Authenticated with Google Cloud")
    except Exception as e:
        logger.error(f"❌ Failed to auth with GCS: {e}")
        return

    # 3. Find Candidate Jobs
    # We look for jobs that are "stuck" in cloud stages locally, but might be done in the cloud.
    jobs = omega_db.get_all_jobs_via_tracks()
    target_stages = {"TRANSLATING_CLOUD_SUBMITTED", "CLOUD_TRANSLATING", "CLOUD_REVIEWING"}
    
    candidates = [j for j in jobs if (j.get("stage") or "").upper() in target_stages]
    logger.info(f"🔍 Found {len(candidates)} jobs in cloud stages locally.")

    bucket_name = config.OMEGA_JOBS_BUCKET
    prefix = config.OMEGA_JOBS_PREFIX

    recover_count = 0
    
    for job in candidates:
        stem = job.get("file_stem")
        if not stem: continue
        
        logger.info(f"⚡️ Checking {stem}...")
        
        try:
            # Force run the sync logic
            # This logic checks if 'approved.json' exists in GCS. 
            # If yes -> Download -> Update DB -> Mark as REVIEWED/COMPLETED.
            success = omega_manager._sync_cloud_approved_idempotent(
                stem, 
                storage_client, 
                bucket_name=bucket_name, 
                prefix=prefix
            )
            
            if success:
                logger.info(f"   ✅ RECOVERED: {stem}")
                recover_count += 1
            else:
                logger.info(f"   ⏳ Not ready: {stem} (approved.json not found in cloud)")
                
        except Exception as e:
            logger.error(f"   ❌ Error syncing {stem}: {e}")

    logger.info("---------------------------------------------------")
    logger.info(f"🎉 Recovery Complete. Recovered {recover_count} jobs.")
    logger.info("   These jobs should now appear in your Dashboard as 'REVIEWED' or 'COMPLETED'.")

if __name__ == "__main__":
    recover_jobs()
