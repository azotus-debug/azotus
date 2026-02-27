import json
import logging
import shutil
import time
import asyncio
import threading
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

import config
from db import get_db
from models import Track, Program, MasterScript, TrackDelivery
from routers import admin_required
from routers._transition_helpers import validate_and_set_stage

logger = logging.getLogger("OmegaFastAPI")

router = APIRouter(prefix="/api/v2", tags=["Tracks"])


def _track_to_dict(track: Track) -> dict:
    d = {c.name: getattr(track, c.name) for c in track.__table__.columns}
    if isinstance(d.get("meta"), str):
        try:
            d["meta"] = json.loads(d["meta"])
        except Exception:
            d["meta"] = {}
    for dtf in ("created_at", "updated_at", "override_timestamp", "locked_at"):
        if isinstance(d.get(dtf), datetime):
            d[dtf] = d[dtf].isoformat()
    return d


def _program_to_dict(p: Program) -> dict:
    d = {c.name: getattr(p, c.name) for c in p.__table__.columns}
    if isinstance(d.get("meta"), str):
        try:
            d["meta"] = json.loads(d["meta"])
        except Exception:
            d["meta"] = {}
    for dtf in ("created_at", "updated_at", "deleted_at"):
        if isinstance(d.get(dtf), datetime):
            d[dtf] = d[dtf].isoformat()
    return d


def _hydrate_file_paths(tracks: list[dict]) -> None:
    for track in tracks:
        job_id = track.get("job_id")
        if job_id:
            srt = config.SRT_DIR / f"DONE_{job_id}.srt"
            if not srt.exists():
                srt = config.SRT_DIR / f"{job_id}.srt"
            vid = config.VIDEO_DIR / f"{job_id}_SUBBED.mp4"
            track["srt_path"] = str(srt) if srt.exists() else None
            track["video_path"] = str(vid) if vid.exists() else None
            track["files_ready"] = srt.exists() and vid.exists()
        else:
            track["srt_path"] = track["video_path"] = None
            track["files_ready"] = False


def _bump_output_version(version: str) -> str:
    try:
        if not version:
            return "1.1"
        parts = str(version).split(".")
        major = int(parts[0]) if parts[0].isdigit() else 1
        minor = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        return f"{major}.{minor + 1}"
    except Exception:
        return "1.1"


# ---- routes ----

@router.post("/tracks")
async def create_track(data: dict, db: AsyncSession = Depends(get_db)):
    """Create a new track (fork language)."""
    program_id = data.get("program_id")
    language_code = data.get("language_code")

    if not program_id or not language_code:
        raise HTTPException(status_code=400, detail="Missing program_id or language_code")

    result = await db.execute(
        select(Program).where(Program.id == program_id, Program.status != "DELETED")
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Program not found")

    track_key = f"{program_id}|{language_code}|subtitle"
    track_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, track_key))

    existing = await db.execute(select(Track).where(Track.id == track_id))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Track already exists")

    track = Track(
        id=track_id,
        program_id=program_id,
        language_code=language_code,
        type="subtitle",
        stage="QUEUED",
        status="Created",
    )
    db.add(track)
    await db.commit()

    return {"track_id": track_id, "status": "QUEUED", "message": f"Created {language_code} track"}


@router.post("/fork-language")
async def fork_language(
    data: dict,
    db: AsyncSession = Depends(get_db),
    _admin=Depends(admin_required),
):
    """Add a new language track to an existing program (recommended endpoint)."""
    import omega_db

    program_id = data.get("program_id")
    language_code = data.get("language_code")

    if not program_id:
        raise HTTPException(status_code=400, detail="program_id is required")
    if not language_code:
        raise HTTPException(status_code=400, detail="language_code is required")

    try:
        result = await asyncio.to_thread(omega_db.fork_language, program_id, language_code)
        return {"success": True, **result}
    except ValueError as e:
        msg = str(e)
        if "not found" in msg.lower():
            raise HTTPException(status_code=404, detail=msg)
        elif "already exists" in msg.lower():
            raise HTTPException(status_code=409, detail=msg)
        raise HTTPException(status_code=400, detail=msg)
    except Exception as e:
        logger.error(f"fork_language failed: {e}")
        raise HTTPException(status_code=500, detail=f"Internal error: {e}")


@router.get("/tracks/active")
async def get_active_tracks(
    limit: int = Query(50),
    db: AsyncSession = Depends(get_db),
):
    """Get all tracks currently in progress."""
    result = await db.execute(
        select(Track)
        .where(Track.stage.notin_(["COMPLETED", "COMPLETE", "DELIVERED", "DELETED"]))
        .order_by(Track.updated_at.desc())
        .limit(limit)
    )
    tracks = [_track_to_dict(t) for t in result.scalars().all()]
    _hydrate_file_paths(tracks)
    return tracks


@router.get("/tracks/{track_id}")
async def get_track(track_id: str, db: AsyncSession = Depends(get_db)):
    """Get a single track with details."""
    result = await db.execute(select(Track).where(Track.id == track_id))
    track = result.scalar_one_or_none()
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")

    d = _track_to_dict(track)

    # Inherit job_id from depends_on if missing
    if not d.get("job_id") and track.depends_on:
        source = await db.execute(select(Track).where(Track.id == track.depends_on))
        src = source.scalar_one_or_none()
        if src and src.job_id:
            d["job_id"] = src.job_id

    # Program info
    prog_result = await db.execute(select(Program).where(Program.id == track.program_id))
    prog = prog_result.scalar_one_or_none()
    d["program"] = _program_to_dict(prog) if prog else None

    # Deliveries
    del_result = await db.execute(select(TrackDelivery).where(TrackDelivery.track_id == track_id))
    d["deliveries"] = [
        {"id": dl.id, "track_id": dl.track_id, "destination": dl.destination,
         "recipient": dl.recipient,
         "delivered_at": dl.delivered_at.isoformat() if dl.delivered_at else None,
         "notes": dl.notes}
        for dl in del_result.scalars().all()
    ]

    _hydrate_file_paths([d])

    # Auto-correct stuck BURNING
    job_id = d.get("job_id")
    if job_id and d.get("stage") == "BURNING":
        video = config.VIDEO_DIR / f"{job_id}_SUBBED.mp4"
        if video.exists():
            current_stage = track.stage
            await validate_and_set_stage(
                track, db,
                from_stage=current_stage,
                to_stage="COMPLETED",
                status="Done",
                progress=100.0,
                worker_id="api:get_track",
                reason="Auto-correct stuck BURNING: video already exists",
                skip_validation=True,
            )
            d["stage"] = "COMPLETED"
            d["status"] = "Done"
            d["progress"] = 100.0

    return d


@router.put("/tracks/{track_id}")
async def update_track(
    track_id: str,
    data: dict,
    db: AsyncSession = Depends(get_db),
    _admin=Depends(admin_required),
):
    """Update track fields."""
    result = await db.execute(select(Track).where(Track.id == track_id))
    track = result.scalar_one_or_none()
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")

    new_stage = data.pop("stage", None)
    new_status = data.pop("status", None)
    new_progress = data.pop("progress", None)

    allowed = {"rating", "voice_id", "output_path", "meta"}
    for k, v in data.items():
        if k in allowed:
            setattr(track, k, v)

    if new_stage and new_stage != track.stage:
        current_stage = track.stage
        await validate_and_set_stage(
            track, db,
            from_stage=current_stage,
            to_stage=new_stage,
            status=new_status,
            progress=float(new_progress) if new_progress is not None else None,
            worker_id="api:update_track",
            reason=f"Admin update: {current_stage} -> {new_stage}",
            skip_validation=True,
        )
    else:
        if new_status is not None:
            track.status = new_status
        if new_progress is not None:
            track.progress = new_progress
        track.updated_at = datetime.now()
        await db.commit()

    return {"success": True}


@router.get("/jobs/{job_id}/logs")
async def get_job_logs(
    job_id: str,
    lines: int = Query(100),
    _admin=Depends(admin_required),
):
    """Tail the latest log lines for a job."""
    from job_logs import tail_job_log
    log_lines = await asyncio.to_thread(tail_job_log, job_id, lines=lines)
    return {"job_id": job_id, "lines": log_lines}


@router.post("/tracks/{track_id}/reveal")
async def reveal_track_file(
    track_id: str,
    data: dict = None,
    db: AsyncSession = Depends(get_db),
    _admin=Depends(admin_required),
):
    """Open track's output file in Finder (macOS)."""
    import subprocess

    data = data or {}
    file_type = data.get("type", "video")

    result = await db.execute(select(Track).where(Track.id == track_id))
    track = result.scalar_one_or_none()
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")

    job_id = track.job_id
    if not job_id:
        raise HTTPException(status_code=400, detail="No job_id for track")

    if file_type == "srt":
        file_path = config.SRT_DIR / f"DONE_{job_id}.srt"
        if not file_path.exists():
            file_path = config.SRT_DIR / f"{job_id}.srt"
    else:
        file_path = config.VIDEO_DIR / f"{job_id}_SUBBED.mp4"

    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {file_path.name}")

    await asyncio.to_thread(subprocess.run, ["open", "-R", str(file_path)], check=False)
    return {"success": True, "path": str(file_path)}


@router.post("/tracks/{track_id}/deliver")
async def deliver_track(
    track_id: str,
    data: dict,
    db: AsyncSession = Depends(get_db),
    _admin=Depends(admin_required),
):
    """Record that a track was delivered."""
    result = await db.execute(select(Track).where(Track.id == track_id))
    track = result.scalar_one_or_none()
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")

    delivery = TrackDelivery(
        id=str(uuid.uuid4()),
        track_id=track_id,
        destination=data.get("destination", "Unknown"),
        recipient=data.get("recipient"),
        notes=data.get("notes"),
    )
    db.add(delivery)
    await db.commit()

    return {"success": True, "delivery_id": delivery.id}


@router.post("/tracks/{track_id}/send-to-review")
async def send_to_review(
    track_id: str,
    db: AsyncSession = Depends(get_db),
    _admin=Depends(admin_required),
):
    """Send a track to review stage."""
    result = await db.execute(select(Track).where(Track.id == track_id))
    track = result.scalar_one_or_none()
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")

    current_stage = track.stage
    await validate_and_set_stage(
        track, db,
        from_stage=current_stage,
        to_stage="AWAITING_REVIEW",
        status="Sent for review",
        worker_id="api:send_to_review",
        reason="User sent track for review",
    )

    return {"success": True, "stage": "AWAITING_REVIEW"}


@router.post("/tracks/{track_id}/approve")
async def approve_track(
    track_id: str,
    db: AsyncSession = Depends(get_db),
    _admin=Depends(admin_required),
):
    """Approve a track and move to next stage."""
    result = await db.execute(select(Track).where(Track.id == track_id))
    track = result.scalar_one_or_none()
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")

    meta = track.meta or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            meta = {}

    if track.type == "dub":
        next_stage, next_status = "DUBBING", "Generating audio"
    else:
        # Keep state "ready-to-burn" and let manager transition to BURNING.
        next_stage, next_status = "FINALIZED", "Approved for Burn"
        meta["burn_approved"] = True
        meta["burn_approved_at"] = datetime.now().isoformat()

    # Set meta before transition (validate_and_set_stage will commit)
    track.meta = meta

    # Reset files for re-burn
    if next_stage in {"FINALIZED", "BURNING"} and track.job_id:
        stem = track.job_id
        srt_path = config.SRT_DIR / f"{stem}.srt"
        done_srt = config.SRT_DIR / f"DONE_{stem}.srt"
        if not srt_path.exists() and done_srt.exists():
            shutil.move(str(done_srt), str(srt_path))
        video_path = config.VIDEO_DIR / f"{stem}_SUBBED.mp4"
        if video_path.exists():
            backup = config.VIDEO_DIR / f"{stem}_SUBBED_BACKUP_{int(time.time())}.mp4"
            shutil.move(str(video_path), str(backup))

    current_stage = track.stage
    await validate_and_set_stage(
        track, db,
        from_stage=current_stage,
        to_stage=next_stage,
        status=next_status,
        progress=90.0,
        worker_id="api:approve",
        reason="User approved track",
    )
    return {"success": True, "stage": next_stage}


@router.post("/tracks/{track_id}/override")
async def override_track(
    track_id: str,
    data: dict = None,
    db: AsyncSession = Depends(get_db),
    _admin=Depends(admin_required),
):
    """Apply an output-only override to a track."""
    data = data or {}
    result = await db.execute(select(Track).where(Track.id == track_id))
    track = result.scalar_one_or_none()
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")

    reason = data.get("reason") or data.get("override_reason") or "Output-only fix"
    author = data.get("author") or data.get("override_author") or "operator"
    new_version = _bump_output_version(track.output_version or "1.0")

    track.output_version = new_version
    track.output_override = 1
    track.override_reason = reason
    track.override_author = author
    track.override_timestamp = datetime.now()
    track.updated_at = datetime.now()
    await db.commit()

    return {
        "success": True, "output_version": new_version,
        "override_reason": reason, "override_author": author, "message": "Override applied",
    }


@router.post("/tracks/{track_id}/lock")
async def lock_track(
    track_id: str,
    data: dict = None,
    db: AsyncSession = Depends(get_db),
    _admin=Depends(admin_required),
):
    """Lock a track."""
    data = data or {}
    result = await db.execute(select(Track).where(Track.id == track_id))
    track = result.scalar_one_or_none()
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")

    now = datetime.now()
    locked_by = data.get("locked_by") or "operator"
    track.locked_at = now
    track.locked_by = locked_by
    track.updated_at = now

    if data.get("lock_master") and track.master_script_id:
        ms_result = await db.execute(select(MasterScript).where(MasterScript.id == track.master_script_id))
        ms = ms_result.scalar_one_or_none()
        if ms and not ms.locked_at:
            ms.state = "locked"
            ms.locked_at = now
            ms.locked_by = locked_by

    await db.commit()
    return {"success": True, "locked_at": now.isoformat(), "locked_by": locked_by}


@router.post("/tracks/{track_id}/unlock")
async def unlock_track(
    track_id: str,
    data: dict = None,
    db: AsyncSession = Depends(get_db),
    _admin=Depends(admin_required),
):
    """Unlock a track."""
    data = data or {}
    result = await db.execute(select(Track).where(Track.id == track_id))
    track = result.scalar_one_or_none()
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")

    track.locked_at = None
    track.locked_by = None
    track.updated_at = datetime.now()
    await db.commit()

    return {"success": True, "unlocked_by": data.get("unlocked_by", "operator")}


@router.post("/tracks/{track_id}/send-review")
async def send_for_review(
    track_id: str,
    data: dict,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    _admin=Depends(admin_required),
):
    """Send a track for remote review (proxy upload + email)."""
    reviewer_email = data.get("email")
    if not reviewer_email:
        raise HTTPException(status_code=400, detail="Email address required")

    result = await db.execute(select(Track).where(Track.id == track_id))
    track = result.scalar_one_or_none()
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")

    if not track.job_id:
        raise HTTPException(status_code=400, detail="No linked job for this track")

    from workers import remote_review
    background_tasks.add_task(remote_review.send_for_remote_review, track.job_id, reviewer_email)

    return {"success": True, "message": "Review being prepared. Email will be sent when ready."}


@router.get("/tracks/{track_id}/review-status")
async def get_review_status(
    track_id: str,
    db: AsyncSession = Depends(get_db),
    _admin=Depends(admin_required),
):
    """Get remote review status."""
    result = await db.execute(select(Track).where(Track.id == track_id))
    track = result.scalar_one_or_none()
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")
    if not track.job_id:
        raise HTTPException(status_code=400, detail="No linked job")

    from workers import remote_review
    status = await asyncio.to_thread(remote_review.get_review_status, track.job_id)
    return status


@router.get("/tracks/{track_id}/open-editor")
async def open_editor(track_id: str, db: AsyncSession = Depends(get_db)):
    """Get the editor URL for a track."""
    result = await db.execute(select(Track).where(Track.id == track_id))
    track = result.scalar_one_or_none()
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")
    if not track.job_id:
        raise HTTPException(status_code=400, detail="No linked job for this track")

    return {"editor_url": f"/editor/{track.job_id}", "job_id": track.job_id}


@router.post("/tracks/{track_id}/start-dub")
async def start_dub(
    track_id: str,
    db: AsyncSession = Depends(get_db),
    _admin=Depends(admin_required),
):
    """Start dubbing for a dub track."""
    result = await db.execute(select(Track).where(Track.id == track_id))
    track = result.scalar_one_or_none()
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")
    if track.type != "dub":
        raise HTTPException(status_code=400, detail="Track is not a dub type")

    voice_id = track.voice_id or "alloy"
    source_track_id = track.depends_on

    if not source_track_id:
        sub_result = await db.execute(
            select(Track).where(
                Track.program_id == track.program_id,
                Track.type == "subtitle",
                Track.stage.in_(["COMPLETE", "COMPLETED", "DELIVERED"]),
            )
        )
        subtitle_track = sub_result.scalars().first()
        if subtitle_track:
            source_track_id = subtitle_track.id

    if not source_track_id:
        raise HTTPException(status_code=400, detail="No source subtitle track available")

    source_result = await db.execute(select(Track).where(Track.id == source_track_id))
    source_track = source_result.scalar_one_or_none()
    if not source_track or not source_track.job_id:
        raise HTTPException(status_code=400, detail="Source track missing job data")

    job_id = source_track.job_id
    if not track.job_id:
        track.job_id = job_id

    import omega_db

    def run_dubbing():
        try:
            from workers.dubber import Dubber
            omega_db.update_track(track_id, stage="DUBBING", status=f"Generating audio ({voice_id})", progress=10)
            job_dir = (Path("jobs") / job_id).resolve()
            dubber = Dubber(job_id, job_dir)
            dubber.run()
            omega_db.update_track(track_id, stage="COMPLETE", status="Dubbing complete", progress=100)
        except Exception as e:
            logger.error(f"Dubbing failed for {track_id}: {e}")
            omega_db.update_track(track_id, stage="FAILED", status=f"Dubbing failed: {str(e)[:50]}")

    thread = threading.Thread(target=run_dubbing, daemon=True)
    thread.start()

    await db.commit()
    return {"success": True, "message": "Dubbing started", "track_id": track_id}


@router.post("/tracks/{track_id}/reject")
async def reject_track(
    track_id: str,
    data: dict = None,
    db: AsyncSession = Depends(get_db),
    _admin=Depends(admin_required),
):
    """Reject a track and send back for rework."""
    data = data or {}
    reason = data.get("reason", "Needs rework")

    result = await db.execute(select(Track).where(Track.id == track_id))
    track = result.scalar_one_or_none()
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")

    current_stage = track.stage
    await validate_and_set_stage(
        track, db,
        from_stage=current_stage,
        to_stage="TRANSLATING",
        status=f"Rejected: {reason}",
        worker_id="api:reject",
        reason=f"User rejected track: {reason}",
        skip_validation=True,
    )

    return {"success": True, "stage": "TRANSLATING"}


@router.post("/tracks/{track_id}/retry")
async def retry_track(
    track_id: str,
    force: str = Query(""),
    db: AsyncSession = Depends(get_db),
    _admin=Depends(admin_required),
):
    """Retry a failed track by resetting to the best checkpoint."""
    result = await db.execute(select(Track).where(Track.id == track_id))
    track = result.scalar_one_or_none()
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")

    job_id = track.job_id
    if not job_id:
        raise HTTPException(status_code=400, detail="Track has no job_id")

    meta = track.meta or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            meta = {}

    retry_count = int(meta.get("retry_count") or 0)
    if retry_count >= 3 and force.lower() not in ("1", "true", "yes"):
        raise HTTPException(status_code=409, detail=f"Retry limit reached (3). Use ?force=1 to override.")

    srt_path = config.SRT_DIR / f"{job_id}.srt"
    approved_path = config.TRANSLATED_DONE_DIR / f"{job_id}_APPROVED.json"
    skeleton_path = config.find_skeleton(job_id)
    vault_path = meta.get("vault_path")
    vault_exists = False
    if vault_path:
        try:
            vault_exists = Path(str(vault_path)).exists()
        except Exception:
            vault_exists = False

    if srt_path.exists():
        reset_stage, reset_status, progress = "FINALIZING", "Re-burning video", 80
    elif approved_path.exists():
        reset_stage, reset_status, progress = "REVIEWED", "Re-finalizing subtitles", 70
    elif skeleton_path:
        reset_stage, reset_status, progress = "TRANSCRIBED", "Re-translating", 30
    elif vault_exists:
        reset_stage, reset_status, progress = "INGEST", "Retrying ingest from Vault", 10
    else:
        reset_stage, reset_status, progress = "QUEUED", "Starting over", 0

    now_iso = datetime.now().isoformat()
    meta["retry_requested_at"] = now_iso
    meta["retry_count"] = retry_count + 1
    meta["last_error"] = ""
    meta["failed_at"] = ""
    meta["retry_force_ingest_recovery"] = bool(reset_stage == "INGEST")
    meta["halted"] = False
    meta["halted_at"] = ""
    meta["halt_reason"] = ""
    meta["stall_restart_count"] = 0
    meta["stall_stage"] = ""
    meta["stall_detected_at"] = ""
    meta["cloud_stall_detected_at"] = ""
    meta["cloud_trigger_last_attempt"] = 0
    meta["cloud_trigger_attempts"] = 0
    meta["cloud_run_execution"] = ""
    meta["deadman_timeout"] = False
    meta["deadman_at"] = ""
    meta["stage_timeline"] = [{"stage": reset_stage, "started_at": now_iso, "ended_at": None}]
    status_timeline = meta.get("status_timeline")
    if not isinstance(status_timeline, list):
        status_timeline = []
    status_timeline = [entry for entry in status_timeline if isinstance(entry, dict)][-49:]
    status_timeline.append({"status": reset_status, "at": now_iso})
    meta["status_timeline"] = status_timeline

    # Set meta before transition (validate_and_set_stage will commit)
    track.meta = meta

    current_stage = track.stage
    await validate_and_set_stage(
        track, db,
        from_stage=current_stage,
        to_stage=reset_stage,
        status=reset_status,
        progress=float(progress),
        worker_id="api:retry",
        reason=f"Retry #{retry_count + 1}: resetting to {reset_stage}",
        skip_validation=True,
    )

    return {
        "success": True, "track_id": track_id, "job_id": job_id,
        "reset_stage": reset_stage, "status": reset_status, "retry_count": retry_count + 1,
    }


@router.post("/tracks/{track_id}/finalize")
async def finalize_track(
    track_id: str,
    db: AsyncSession = Depends(get_db),
    _admin=Depends(admin_required),
):
    """Trigger finalization (SRT generation) for a track."""
    from workers import finalizer

    result = await db.execute(select(Track).where(Track.id == track_id))
    track = result.scalar_one_or_none()
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")
    if not track.job_id:
        raise HTTPException(status_code=400, detail="Track has no job_id")

    approved_path = config.TRANSLATED_DONE_DIR / f"{track.job_id}_APPROVED.json"
    if not approved_path.exists():
        approved_path = config.VAULT_DATA / f"{track.job_id}_APPROVED.json"
    if not approved_path.exists():
        raise HTTPException(status_code=400, detail="No approved translation found.")

    language_code = track.language_code or "is"

    current_stage = track.stage
    await validate_and_set_stage(
        track, db,
        from_stage=current_stage,
        to_stage="FINALIZING",
        status="Generating subtitles",
        progress=75.0,
        worker_id="api:finalize",
        reason="User triggered finalization",
    )

    try:
        srt_path, norm_path = await asyncio.to_thread(
            finalizer.finalize,
            approved_path,
            language_code,
        )
        meta = track.meta or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                meta = {}
        track.meta = {
            **meta,
            "srt_path": str(srt_path),
            "normalized_path": str(norm_path),
            "finalized_at": datetime.now().isoformat(),
        }
        current_stage = track.stage
        await validate_and_set_stage(
            track, db,
            from_stage=current_stage,
            to_stage="FINALIZED",
            status="Ready to burn",
            progress=85.0,
            worker_id="api:finalize",
            reason="Finalization completed successfully",
        )
        return {"success": True, "track_id": track_id, "srt_path": str(srt_path), "status": "Ready to burn"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Finalize failed for {track_id}: {e}")
        meta = track.meta or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                meta = {}
        track.meta = {**meta, "finalize_error": str(e), "failed_at": datetime.now().isoformat()}
        current_stage = track.stage
        await validate_and_set_stage(
            track, db,
            from_stage=current_stage,
            to_stage="FAILED",
            status=f"Finalize failed: {e}",
            worker_id="api:finalize",
            reason=f"Finalization error: {e}",
            skip_validation=True,
        )
        raise HTTPException(status_code=500, detail=f"Finalization failed: {e}")


@router.post("/tracks/{track_id}/burn")
async def burn_track(
    track_id: str,
    db: AsyncSession = Depends(get_db),
    _admin=Depends(admin_required),
):
    """Trigger video burning for a track."""
    import omega_db

    result = await db.execute(select(Track).where(Track.id == track_id))
    track = result.scalar_one_or_none()
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")
    if not track.job_id:
        raise HTTPException(status_code=400, detail="Track has no job_id")

    srt_path = config.SRT_DIR / f"{track.job_id}.srt"
    if not srt_path.exists():
        raise HTTPException(status_code=400, detail="No SRT found. Run finalize first.")

    meta = track.meta or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            meta = {}

    track.meta = {**meta, "burn_started_at": datetime.now().isoformat()}

    current_stage = track.stage
    await validate_and_set_stage(
        track, db,
        from_stage=current_stage,
        to_stage="BURNING",
        status="Burning subtitles into video",
        progress=90.0,
        worker_id="api:burn",
        reason="User triggered video burn",
    )

    # Run burn in background (uses omega_db directly as it has complex logic)
    def _run_burn():
        try:
            from workers import publisher
            logger.info(f"Burn started: {track.job_id}")
            # Get job info
            job = omega_db.get_job_via_track(track.job_id) or {}
            meta = job.get("meta", {}) or {}
            vault_path = job.get("vault_path") or meta.get("vault_path") or meta.get("source_path")

            video_path = None
            if vault_path:
                vault_candidate = Path(vault_path)
                # Support both legacy metadata (directory) and newer metadata (full file path).
                if vault_candidate.exists() and vault_candidate.is_file() and vault_candidate.suffix.lower() in {".mp4", ".mov", ".mkv"}:
                    video_path = vault_candidate
                elif vault_candidate.exists() and vault_candidate.is_dir():
                    for ext in [".mp4", ".mov", ".mkv"]:
                        candidate = vault_candidate / f"{track.job_id}{ext}"
                        if candidate.exists():
                            video_path = candidate
                            break

            if not video_path:
                original_filename = (
                    job.get("original_filename")
                    or meta.get("original_filename")
                )
                if original_filename:
                    by_original = config.VAULT_VIDEOS / str(original_filename)
                    if by_original.exists() and by_original.suffix.lower() in {".mp4", ".mov", ".mkv"}:
                        video_path = by_original

            if not video_path:
                for candidate in config.VAULT_VIDEOS.glob(f"{track.job_id}.*"):
                    if candidate.suffix.lower() in {".mp4", ".mov", ".mkv"}:
                        video_path = candidate
                        break

            if not video_path:
                raise FileNotFoundError(f"Video not found for {track.job_id}")

            subtitle_style = job.get("subtitle_style") or meta.get("subtitle_style") or "RUV_BOX"
            output_path = publisher.publish(video_path, srt_path, subtitle_style=subtitle_style)

            omega_db.update_job_via_track(
                track.job_id, stage="COMPLETED", status="Done", progress=100.0,
                meta={"burn_completed_at": datetime.now().isoformat(), "final_output": str(output_path)},
            )
        except Exception as e:
            logger.error(f"Burn failed for {track.job_id}: {e}")
            omega_db.update_job_via_track(
                track.job_id, stage="FINALIZED", status=f"Burn Failed: {e}", progress=90.0,
            )

    thread = threading.Thread(target=_run_burn, daemon=True, name=f"burn_{track.job_id}")
    thread.start()

    return {"success": True, "track_id": track_id, "job_id": track.job_id, "status": "Burning started"}
