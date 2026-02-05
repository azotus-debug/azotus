import datetime
import config
from pathlib import Path
import omega_db

DB_PATH = config.DB_PATH

def cleanup_stale_jobs():
    if not DB_PATH.exists():
        print("Database not found.")
        return

    # Fetch active jobs via tracks/programs
    jobs = [
        job for job in omega_db.get_all_jobs_via_tracks()
        if job.get("stage") != "COMPLETED" and "Error" not in str(job.get("status", ""))
    ]

    now = datetime.datetime.now()
    
    stale_count = 0
    
    print(f"Checking {len(jobs)} active jobs for staleness...")
    
    for job in jobs:
        stem = job.get("file_stem")
        updated_at = job.get("updated_at")
        try:
            # Parse ISO format (might have microseconds or not)
            last_update = datetime.datetime.fromisoformat(updated_at)
            diff = now - last_update
            
            # If older than 2 hours
            if diff.total_seconds() > 7200:
                print(f"⚠️ Stale Job Found: {stem} (Last updated: {updated_at}, {diff})")
                
                # Mark as Stalled
                omega_db.update_job_via_track(
                    stem,
                    status=f"Stalled (Last active: {updated_at})",
                    stage="FAILED",
                )
                stale_count += 1
                
        except Exception as e:
            print(f"Error parsing date for {stem}: {e}")
            continue
    
    print(f"✅ Cleanup Complete. Marked {stale_count} jobs as Stalled/Failed.")

if __name__ == "__main__":
    cleanup_stale_jobs()
