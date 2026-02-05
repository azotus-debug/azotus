import sys
import logging
from workers import assistant

# Setup logging
logging.basicConfig(level=logging.INFO)

job_id = "cbnjd010126cc_hdmpeg2-20260105T211434455897Z"
print(f"Checking job: {job_id}")

try:
    path, data = assistant._load_job_file(job_id)
    print(f"Loaded Path: {path}")
    if data:
        meta = data.get("meta", {})
        print(f"Meta: {meta}")
        
    # Also check DB record for this job
    import omega_db
    job_rec = omega_db.get_job_via_track(job_id)
    if job_rec:
        print(f"DB Job: {job_rec}")
    else:
        print("Job not found in DB")
except Exception as e:
    print(f"Error: {e}")
