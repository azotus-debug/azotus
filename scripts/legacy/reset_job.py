
import omega_db

stem = "I2252_IntlUK_h264-1080p2997-aac"
print(f"Resetting {stem}...")
omega_db.update_job_via_track(stem, stage="REVIEWED", status="Reset for Re-burn", progress=75.0, meta={"halted": False, "burn_approved": True})
print("Done.")
