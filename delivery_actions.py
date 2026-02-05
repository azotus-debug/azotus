"""
Mark Delivered Action

Handles the "Mark Delivered" workflow:
1. Render delivery filename from template
2. Copy files to delivery location
3. Log delivery in database
"""
import shutil
import json
from pathlib import Path
from datetime import datetime
import config
import omega_db
from delivery_templates import render_template


def mark_delivered(job_stem: str, notes: str = "") -> dict:
    """
    Mark a job as delivered.
    
    Returns dict with:
        - success: bool
        - delivered_filename: str
        - delivery_path: str
        - error: str (if failed)
    """
    # 1. Get job info
    job = omega_db.get_job_via_track(job_stem)
    if not job:
        return {"success": False, "error": "Job not found"}
    
    client = job.get("client", "unknown")
    meta = job.get("meta", {})
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except:
            meta = {}
    
    original_filename = meta.get("original_filename", job_stem)
    
    # 2. Get client delivery config
    client_defaults = getattr(config, "CLIENT_DEFAULTS", {})
    client_config = client_defaults.get(client, client_defaults.get("unknown", {}))
    
    template = client_config.get("delivery_template", "{title}_{date_YYYY_MM_DD}")
    delivery_target = client_config.get("delivery_target", "4_DELIVERY")
    
    # 3. Render delivery filename
    delivery_filename_base = render_template(template, client, original_filename)
    
    # 4. Find completed files (SRT, MP4)
    base_dir = Path(config.BASE_DIR)
    delivery_dir = base_dir / delivery_target
    delivery_dir.mkdir(parents=True, exist_ok=True)
    
    # Look for output files
    srt_dir = base_dir / "4_DELIVERY" / "SRT"
    srt_candidates = list(srt_dir.glob(f"{job_stem}*.srt"))
    srt_candidates.extend(srt_dir.glob(f"DONE_{job_stem}*.srt"))
    video_candidates = list((base_dir / "4_DELIVERY" / "VIDEO").glob(f"{job_stem}*.mp4"))
    
    delivered_files = []
    
    # Copy SRT
    for srt_file in srt_candidates:
        dest_filename = f"{delivery_filename_base}.srt"
        dest_path = delivery_dir / dest_filename
        shutil.copy2(srt_file, dest_path)
        delivered_files.append(str(dest_path))
    
    # Copy Video (if exists)
    for video_file in video_candidates:
        dest_filename = f"{delivery_filename_base}.mp4"
        dest_path = delivery_dir / dest_filename
        shutil.copy2(video_file, dest_path)
        delivered_files.append(str(dest_path))
    
    if not delivered_files:
        return {"success": False, "error": "No output files found to deliver"}
    
    # 5. Log delivery
    delivered_at = datetime.now().isoformat()
    omega_db.log_delivery(
        job_stem=job_stem,
        client=client,
        delivered_at=delivered_at,
        method=client_config.get("delivery_method", "folder"),
        notes=f"Files: {', '.join([Path(f).name for f in delivered_files])}. {notes}".strip()
    )

    omega_db.update_job_via_track(
        job_stem,
        stage="DELIVERED",
        status="Delivered",
        progress=100.0,
        meta={
            "delivered_at": delivered_at,
            "delivery_path": str(delivery_dir),
            "delivery_files": [Path(f).name for f in delivered_files],
        },
    )
    
    return {
        "success": True,
        "delivered_filename": delivery_filename_base,
        "delivery_path": str(delivery_dir),
        "files": delivered_files
    }
