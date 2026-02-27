import omega_db
import sys
import json

# Force connect to the same DB
print("Checking for i2609 in get_all_jobs_via_tracks()...")

try:
    jobs = omega_db.get_all_jobs_via_tracks(limit=100)
    print(f"Fetched {len(jobs)} jobs.")
    
    found = False
    for job in jobs:
        stem = job.get("file_stem") or job.get("job_id") # handle potential key name diffs
        if stem and "i2609" in stem:
            print("FOUND i2609!")
            print(json.dumps(job, indent=2, default=str))
            found = True
            break
            
    if not found:
        print("i2609 NOT FOUND in the list.")
        # Print top 5 to see what is there
        print("Top 5 jobs:")
        for job in jobs[:5]:
            print(f"- {job.get('file_stem') or job.get('job_id')}")

except Exception as e:
    print(f"Error: {e}")
