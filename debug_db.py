import sys
import os
import json
import logging

# Setup path
sys.path.append(os.getcwd())

try:
    import omega_db
    import config
    
    # Init DB
    # omega_db.ensure_schema() # might not need this if reading
    
    job_id = "new_system-20260204T174454520018Z"
    print(f"Checking Job: {job_id}")
    
    track = omega_db.get_track_by_job(job_id)
    if track:
        print("--- TRACK FOUND ---")
        print(json.dumps(dict(track), indent=2, default=str))
    else:
        print("--- TRACK NOT FOUND ---")
        
        # List recent tracks
        print("Listing recent tracks instead:")
        # This function might not exist, checking raw sql
        # But let's rely on get_track_by_job first
        
except Exception as e:
    print(f"Error: {e}")
