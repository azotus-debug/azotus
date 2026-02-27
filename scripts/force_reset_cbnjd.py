import sys
import os
sys.path.append(os.getcwd())
import omega_db

stem = "cbnjd022026cc-20260220T223657666491Z"
# Reset the job state and clear all failure-related meta fields
omega_db.update_job_via_track(
    stem, 
    stage="REVIEWED", 
    status="Editor Approved", 
    progress=70.0,
    meta={
        "halted": False,
        "failed_count": 0,
        "failed_at": None,
        "last_error": None,
        "burn_approved": True,
    }
)
print("Job completely reset.")
