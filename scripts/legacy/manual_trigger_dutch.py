
import sys
import os
from pathlib import Path
import json
import logging
import datetime
from google.cloud import storage

# Add project root to path
sys.path.append(os.getcwd())

import config
import omega_db
from gcs_jobs import upload_json, GcsJobPaths
from cloud_run_jobs import run_cloud_run_job

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ManualTrigger")

def manual_trigger(job_id):
    logger.info(f"🚀 Manually triggering cloud translation for {job_id}")
    
    # 1. Fetch metadata from DB
    jobs = omega_db.get_all_jobs_via_tracks()
    job = next((j for j in jobs if j.get("file_stem") == job_id), None)
    if not job:
        logger.error(f"Job {job_id} not found in DB")
        return

    stem = job.get("file_stem")
    lang_code = job.get("target_language", "nl")
    
    # 2. Upload artifacts to GCS (fixing what failed before)
    bucket_name = config.OMEGA_JOBS_BUCKET
    prefix = config.OMEGA_JOBS_PREFIX
    paths = GcsJobPaths(bucket=bucket_name, prefix=prefix, job_id=job_id)
    
    storage_client = storage.Client()
    
    # Upload Skeleton
    skeleton_path = config.VAULT_DATA / f"{job_id}_SKELETON.json"
    if skeleton_path.exists():
        logger.info(f"   Uploading Skeleton: {skeleton_path.name}")
        with open(skeleton_path, "r") as f:
            skeleton_data = json.load(f)
        upload_json(storage_client, bucket=bucket_name, blob_name=paths.skeleton_blob, payload=skeleton_data)
    else:
        logger.warning(f"   Skeleton not found at {skeleton_path}")

    # Upload Job Metadata
    audio_path = config.VAULT_DIR / "Audio" / f"{stem}.wav"
    job_payload = {
        "id": job_id,
        "file_stem": stem,
        "target_language": lang_code,
        "program_profile": job.get("program_profile") or "standard",
        "glossary_terms": [],
        "audio_file": audio_path.name if audio_path.exists() else f"{stem}.wav",
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
    }
    logger.info(f"   Uploading Job Metadata: job.json")
    upload_json(storage_client, bucket=bucket_name, blob_name=paths.job_blob, payload=job_payload)
    
    # 3. Trigger Cloud Run
    job_name = os.environ.get("OMEGA_CLOUD_JOB_NAME", "omega-cloud-worker")
    region = os.environ.get("OMEGA_CLOUD_REGION", "us-central1")
    args = ["--job-id", job_id]
    
    logger.info(f"   Triggering Cloud Run: {job_name} in {region} with args {args}")
    execution = run_cloud_run_job(job_name=job_name, args=args, region=region, project=None)
    execution_name = execution.name if hasattr(execution, 'name') else "unknown"
    logger.info(f"✅ Triggered Cloud Run: {execution_name}")
    
    # 4. Update DB
    omega_db.update_job_via_track(
        job_id, 
        stage="TRANSLATING_CLOUD_SUBMITTED", 
        status="Submitted to Cloud (Manual)", 
        progress=40.0, 
        meta={"cloud_run_execution": execution_name}
    )
    logger.info("✅ DB Updated.")

if __name__ == "__main__":
    job_id = "cbnjd012326cc-20260126T200619268517Z"
    manual_trigger(job_id)
