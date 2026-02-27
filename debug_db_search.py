import sys
import os
import json
import logging

sys.path.append(os.getcwd())

try:
    import omega_db
    import config
    
    target = sys.argv[1] if len(sys.argv) > 1 else "CBNJD020326CC"
    print(f"--- SEARCHING FOR {target} ---")
    
    # helper to print track
    def print_track(t):
        # omega_db._job_dict_from_track_row returns 'file_stem', not 'job_id'
        job_id = t.get('file_stem') or t.get('job_id')
        print(f"FOUND: ID={job_id} | Stage={t.get('stage')} | Status={t.get('status')} | ProgID={t.get('program_id')}")

    # 1. Try get_track_by_job with variations
    vars = [target, target.lower(), target.upper()]
    
    found = False
    for v in vars:
        t = omega_db.get_job(v)  # try get_job first as it's the main accessor
        if t: 
            print_track(t)
            found = True
            break
            
    if not found:
        print(f"No job found for {target} via get_job()")
        
    print(f"DB Config: {getattr(config, 'OMEGA_DB_PATH', 'Unknown')}")
    
except Exception as e:
    print(f"Error: {e}")
