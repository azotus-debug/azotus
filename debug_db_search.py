import sys
import os
import json
import logging

sys.path.append(os.getcwd())

try:
    import omega_db
    
    print("--- SEARCHING FOR CBNJD020326CC ---")
    
    # helper to print track
    def print_track(t):
        print(f"FOUND: ID={t.get('job_id')} | Stage={t.get('stage')} | Status={t.get('status')} | ProgID={t.get('program_id')}")

    # 1. Try get_track_by_job with variations
    target = "CBNJD020326CC"
    vars = [target, target.lower(), target.upper()]
    
    for v in vars:
        t = omega_db.get_track_by_job(v)
        if t: 
            print_track(t)
            
    # 2. List all programs and check titles
    # Assuming get_all_programs or similar exists. 
    # If not, we might have to rely on get_program_by_original_filename if we knew it.
    
    # Let's try to list recent via raw connection if module allows, OR just try to find based on title
    p = omega_db.get_program_by_title(target)
    if p:
        print(f"FOUND PROGRAM: {p['id']} - {p.get('title')}")
        tracks = omega_db.get_tracks_for_program(p['id'])
        for t in tracks:
            print_track(t)
            
    # 3. Last ditch: try to list *all* recent tracks by guessing IDs? No.
    # Let's try to query the DB file referenced in defaults.py/config.py if we can find it.
    # Actually, let's just inspect what `omega_db` uses.
    
    print("\n--- CONFIG CHECK ---")
    import config
    print(f"DB Config: {getattr(config, 'OMEGA_DB_PATH', 'Unknown')}")
    
except Exception as e:
    print(f"Error: {e}")
