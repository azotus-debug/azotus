import os
import omega_db
from pathlib import Path

job_id = "timessquarechurch_20260215-20260221T205201457054Z"

# Kill ffmpeg so it lets go of the file locks
os.system("pkill -9 ffmpeg")

# Force delete all intermediate files to enforce a brand new Finalizer run
base = Path("/Users/haukurhauksson/Azotus")
for ext in ["srt", "ass", "vtt", "ttml"]:
    for p in base.rglob(f"*{job_id}*.{ext}"):
        try: p.unlink()
        except: pass

# Delete the broken (subtitle-less) MP4 files
for p in base.rglob(f"*{job_id}*.mp4"):
    if "SUBBED" in p.name or "temp_render" in str(p) or "._" in p.name:
        try: p.unlink()
        except: pass

# Reset via the active track/program pipeline contract (not legacy jobs table).
omega_db.update_job_via_track(
    job_id,
    stage="REVIEWED",
    status="Ready to Burn",
    progress=90.0,
    subtitle_style="RUV_BOX",
    meta={
        "burn_approved": True,
        "reset_by_script": True,
    },
)

print("Cleaned up failing artifacts and reset track-based DB state.")
