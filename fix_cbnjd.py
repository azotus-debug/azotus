import sys
import os
sys.path.append(os.getcwd())
import omega_db

stem = "cbnjd022026cc-20260220T223657666491Z"
job = omega_db.get_job_via_track(stem)
if job:
    print(f"Found job: {job.get('status')} - {job.get('stage')}")
    # Reset to REVIEWED so finalizer runs again and creates fresh SRT, then it burns
    omega_db.update_job_via_track(stem, stage="REVIEWED", status="Editor Approved", progress=70.0)
    print("Reset to REVIEWED successfully.")
else:
    print("Job not found.")
