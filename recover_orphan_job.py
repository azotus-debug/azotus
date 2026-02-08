#!/usr/bin/env python3
"""
Quick script to recover an orphaned job by creating its DB record.
The job completed translation in Cloud Run but lost its local DB record.
"""
import sys
import os
import json

sys.path.append(os.getcwd())

import omega_db
import config
from google.cloud import storage

JOB_ID = "test_cbnjd020326cc-20260206T013135881987Z"

def main():
    print(f"Recovering orphaned job: {JOB_ID}")
    
    # Check if record exists
    existing = omega_db.get_track_by_job(JOB_ID)
    if existing:
        print(f"Job already exists in DB with status: {existing.get('status')}")
        return
    
    print("No DB record found. Fetching metadata from GCS...")
    
    # Get job metadata from GCS
    client = storage.Client()
    bucket = client.bucket(config.GCS_BUCKET)
    
    # Get job.json
    job_blob = bucket.blob(f"jobs/{JOB_ID}/job.json")
    if not job_blob.exists():
        print(f"ERROR: job.json not found in GCS for {JOB_ID}")
        return
    
    job_data = json.loads(job_blob.download_as_string())
    print(f"Found job.json: {job_data.get('source_basename', 'unknown')}")
    
    # Get progress.json to determine current state
    progress_blob = bucket.blob(f"jobs/{JOB_ID}/progress.json")
    progress_data = {}
    if progress_blob.exists():
        progress_data = json.loads(progress_blob.download_as_string())
        print(f"Progress: stage={progress_data.get('stage')}, status={progress_data.get('status')}")
    
    # Check if approved.json exists
    approved_blob = bucket.blob(f"jobs/{JOB_ID}/approved.json")
    has_approved = approved_blob.exists()
    print(f"Has approved.json: {has_approved}")
    
    # Determine status - if we have approved.json, set to REVIEWED so it can finalize
    if has_approved:
        status = "REVIEWED"  # Will advance to FINALIZING in main loop
    elif progress_data.get('stage') == 'CLOUD_DONE':
        status = "REVIEWED"
    else:
        status = "CLOUD_TRANSLATING"  # Needs more work
    
    print(f"Creating track with status: {status}")
    
    # Create the track record
    track = omega_db.create_track(
        job_id=JOB_ID,
        source_path=job_data.get('source_path', f"/Volumes/Extreme SSD/test_{JOB_ID.split('-')[0]}.mp4"),
        source_language=job_data.get('source_language', 'en'),
        target_language=job_data.get('target_language', 'is'),
        channel_id=job_data.get('channel_id', 'CBNJD'),
        status=status,
        station_id=os.getenv('OMEGA_STATION_ID', 'local')
    )
    
    if track:
        print(f"✅ Track created successfully!")
        print(f"   ID: {track.get('id')}")
        print(f"   Status: {track.get('status')}")
        print(f"   Cloud sync will pick it up and advance to FINALIZING")
    else:
        print("❌ Failed to create track")

if __name__ == "__main__":
    main()
