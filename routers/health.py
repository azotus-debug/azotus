import os
import time
import shutil
import logging
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text, func

import config
from db import get_db
from models import Track, Program, ErrorLog
from routers import admin_required
from transition_service import execute_transition

logger = logging.getLogger("OmegaFastAPI")

router = APIRouter(tags=["Health"])


# ---- helpers (ported from dashboard.py) ----

def _heartbeat_age_seconds(process_name: str):
    try:
        beat = config.BASE_DIR / "heartbeats" / f"{process_name}.beat"
        if not beat.exists():
            return None
        return max(0.0, time.time() - beat.stat().st_mtime)
    except Exception:
        return None


def _disk_free_gb(path: Path):
    try:
        if not path.exists():
            return None
        total, used, free = shutil.disk_usage(str(path))
        return free / (2**30)
    except Exception:
        return None


# ---- routes ----

@router.get("/health")
async def health_check(db: AsyncSession = Depends(get_db)):
    """Liveness probe (lightweight)."""
    try:
        await db.execute(text("SELECT 1"))
        return {"status": "healthy"}
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return Response(
            content='{"status":"unhealthy","error":' + f'"{str(e)[:80]}"' + '}',
            status_code=503,
            media_type="application/json",
        )


@router.get("/healthz")
async def healthz(db: AsyncSession = Depends(get_db)):
    """Cloud Run health alias."""
    return await health_check(db)


@router.get("/ready")
async def readiness_check(db: AsyncSession = Depends(get_db)):
    """Startup probe."""
    try:
        # Verify tables exist by querying system_state
        await db.execute(text("SELECT 1 FROM system_state LIMIT 1"))
        return {"status": "ready"}
    except Exception as e:
        logger.error(f"Readiness check failed: {e}")
        return Response(
            content='{"status":"not_ready","error":' + f'"{str(e)[:80]}"' + '}',
            status_code=503,
            media_type="application/json",
        )


@router.get("/api/v2/health")
async def api_health(db: AsyncSession = Depends(get_db)):
    """Comprehensive health check endpoint."""
    # Track stage counts
    result = await db.execute(
        select(Track.stage, func.count(Track.id))
        .where(Track.stage.notin_(["DELETED"]))
        .group_by(Track.stage)
    )
    stage_counts = {row[0]: row[1] for row in result.all()}

    total_tracks = sum(stage_counts.values())
    dead_count = stage_counts.get("DEAD", 0)
    completed_count = stage_counts.get("COMPLETED", 0) + stage_counts.get("COMPLETE", 0)

    # DB check
    db_status = "ok"
    db_type = os.getenv("DB_TYPE", "postgres")
    try:
        await db.execute(text("SELECT 1"))
    except Exception as e:
        db_status = f"error: {str(e)[:50]}"

    # Storage
    storage_ready = False
    try:
        storage_ready = bool(config.critical_paths_ready(require_write=True))
    except Exception:
        pass

    # GCS
    gcs_status = "unknown"
    try:
        from google.cloud import storage as gcs_storage
        client = gcs_storage.Client()
        bucket = client.bucket(config.OMEGA_JOBS_BUCKET)
        bucket.reload()
        gcs_status = "ok"
    except ImportError:
        gcs_status = "sdk_not_installed"
    except Exception as e:
        gcs_status = f"error: {str(e)[:50]}"

    # ElevenLabs
    elevenlabs_status = "configured" if os.getenv("ELEVENLABS_API_KEY") else "not_configured"

    # Heartbeats
    manager_age = _heartbeat_age_seconds("omega_manager")
    dashboard_age = _heartbeat_age_seconds("dashboard")

    # Recent errors
    recent_errors = []
    errors_24h_count = 0
    try:
        result = await db.execute(
            select(ErrorLog)
            .where(ErrorLog.created_at >= text("NOW() - INTERVAL '24 hours'"))
            .order_by(ErrorLog.created_at.desc())
            .limit(10)
        )
        error_rows = result.scalars().all()
        errors_24h_count = len(error_rows)
        recent_errors = [
            {
                "time": str(e.created_at or ""),
                "job_id": e.job_id,
                "type": e.error_type,
                "message": (e.message or "")[:200],
                "worker": e.worker,
            }
            for e in error_rows[:5]
        ]
    except Exception:
        pass

    # Overall status
    overall_status = "healthy"
    if db_status != "ok":
        overall_status = "unhealthy"
    elif not storage_ready:
        overall_status = "degraded"
    elif gcs_status != "ok":
        overall_status = "degraded"
    elif dead_count > 0:
        overall_status = "degraded"
    elif manager_age is None or manager_age > 120:
        overall_status = "degraded"
    elif errors_24h_count > 5:
        overall_status = "degraded"

    return {
        "status": overall_status,
        "time": datetime.now().isoformat(),
        "checks": {
            "database": {"status": db_status, "type": db_type},
            "storage": {"status": "ok" if storage_ready else "error"},
            "gcs": {"status": gcs_status, "bucket": config.OMEGA_JOBS_BUCKET},
            "elevenlabs": {"status": elevenlabs_status},
            "disk_space": {
                "status": "ok" if (_disk_free_gb(config.DELIVERY_DIR) or 0) > 20 else "low",
                "free_gb": round(_disk_free_gb(config.DELIVERY_DIR) or 0, 1),
            },
        },
        "heartbeats": {
            "omega_manager_age_seconds": manager_age,
            "dashboard_age_seconds": dashboard_age,
            "manager_alive": manager_age is not None and manager_age < 120,
        },
        "jobs": {
            "total": total_tracks,
            "active": total_tracks - dead_count - completed_count,
            "stages": stage_counts,
            "dead": dead_count,
        },
        "errors_24h": {
            "count": errors_24h_count,
            "recent": recent_errors,
        },
        "config": {
            "db_type": db_type,
            "cloud_pipeline": os.getenv("OMEGA_CLOUD_PIPELINE", "0"),
            "transcriber": os.getenv("OMEGA_TRANSCRIBER", "elevenlabs"),
            "delivery_profile": getattr(config, "DEFAULT_DELIVERY_PROFILE", "broadcast_hevc"),
        },
    }


@router.get("/api/health")
async def api_health_compat(db: AsyncSession = Depends(get_db)):
    """Compatibility alias for older health checks."""
    return await api_health(db)


@router.get("/api/v2/health/diagnose")
async def api_health_diagnose(db: AsyncSession = Depends(get_db)):
    """Identify fixable issues (stage/file misalignment)."""
    import json as _json

    result = await db.execute(
        select(Track).where(Track.stage.notin_(["DELETED"]))
    )
    tracks = result.scalars().all()

    problems = []
    for track in tracks:
        job_id = track.job_id
        if not job_id:
            continue

        stage = (track.stage or "").upper()
        meta = track.meta or {}
        if isinstance(meta, str):
            try:
                meta = _json.loads(meta)
            except Exception:
                meta = {}

        if meta.get("halted"):
            continue

        srt_path = config.SRT_DIR / f"{job_id}.srt"
        approved_path = config.TRANSLATED_DONE_DIR / f"{job_id}_APPROVED.json"
        skeleton_path = config.find_skeleton(job_id)

        final_path = None
        delivery_profile = meta.get("delivery_profile") or "broadcast_hevc"
        profile_info = config.DELIVERY_PROFILES.get(delivery_profile, {})
        output_subdir = profile_info.get("output_subdir", "VIDEO")
        for ext in [".mp4", ".mov"]:
            candidate = config.DELIVERY_DIR / output_subdir / f"{job_id}_SUBBED{ext}"
            if candidate.exists():
                final_path = candidate
                break

        if final_path and stage not in {"COMPLETED", "DELIVERED"}:
            problems.append({
                "stem": job_id, "issue": "video_exists_wrong_stage",
                "description": f"Burned video exists but stage is {stage}",
                "current_stage": stage, "suggested_stage": "COMPLETED", "auto_fixable": True,
            })
        elif srt_path.exists() and stage not in {"FINALIZED", "BURNING", "COMPLETED", "DELIVERED"}:
            problems.append({
                "stem": job_id, "issue": "srt_exists_wrong_stage",
                "description": f"SRT file exists but stage is {stage}",
                "current_stage": stage, "suggested_stage": "FINALIZED", "auto_fixable": True,
            })
        elif approved_path.exists() and stage not in {"REVIEWED", "FINALIZING", "FINALIZED", "BURNING", "COMPLETED", "DELIVERED"}:
            problems.append({
                "stem": job_id, "issue": "approved_exists_wrong_stage",
                "description": f"Approved translation exists but stage is {stage}",
                "current_stage": stage, "suggested_stage": "REVIEWED", "auto_fixable": True,
            })
        elif skeleton_path and stage in {"QUEUED", "INGEST", ""}:
            problems.append({
                "stem": job_id, "issue": "skeleton_exists_wrong_stage",
                "description": f"Skeleton exists but stage is {stage or 'empty'}",
                "current_stage": stage, "suggested_stage": "TRANSCRIBED", "auto_fixable": True,
            })

        if stage == "DEAD":
            if final_path:
                problems.append({"stem": job_id, "issue": "dead_but_video_exists",
                    "current_stage": "DEAD", "suggested_stage": "COMPLETED", "auto_fixable": True,
                    "description": "Job is DEAD but burned video exists"})
            elif srt_path.exists():
                problems.append({"stem": job_id, "issue": "dead_but_srt_exists",
                    "current_stage": "DEAD", "suggested_stage": "FINALIZED", "auto_fixable": True,
                    "description": "Job is DEAD but SRT file exists"})
            elif approved_path.exists():
                problems.append({"stem": job_id, "issue": "dead_but_approved_exists",
                    "current_stage": "DEAD", "suggested_stage": "REVIEWED", "auto_fixable": True,
                    "description": "Job is DEAD but approved translation exists"})
            elif skeleton_path:
                problems.append({"stem": job_id, "issue": "dead_but_skeleton_exists",
                    "current_stage": "DEAD", "suggested_stage": "TRANSCRIBED", "auto_fixable": True,
                    "description": "Job is DEAD but skeleton exists"})

    return {
        "time": datetime.now().isoformat(),
        "total_problems": len(problems),
        "auto_fixable": sum(1 for p in problems if p.get("auto_fixable")),
        "problems": problems,
    }


@router.post("/api/v2/health/fix")
async def api_health_fix(
    request_body: dict,
    db: AsyncSession = Depends(get_db),
    _admin=Depends(admin_required),
):
    """Auto-fix detected stage/file misalignment problems."""
    import json as _json

    stems_to_fix = request_body.get("stems", [])
    fix_all = request_body.get("fix_all", False)

    result = await db.execute(select(Track).where(Track.stage.notin_(["DELETED"])))
    tracks = result.scalars().all()

    fixes = []
    for track in tracks:
        job_id = track.job_id
        if not job_id:
            continue
        if not fix_all and job_id not in stems_to_fix:
            continue

        stage = (track.stage or "").upper()
        meta = track.meta or {}
        if isinstance(meta, str):
            try:
                meta = _json.loads(meta)
            except Exception:
                meta = {}

        srt_path = config.SRT_DIR / f"{job_id}.srt"
        approved_path = config.TRANSLATED_DONE_DIR / f"{job_id}_APPROVED.json"
        skeleton_path = config.find_skeleton(job_id)
        final_path = None
        delivery_profile = meta.get("delivery_profile") or "broadcast_hevc"
        profile_info = config.DELIVERY_PROFILES.get(delivery_profile, {})
        output_subdir = profile_info.get("output_subdir", "VIDEO")
        for ext in [".mp4", ".mov"]:
            candidate = config.DELIVERY_DIR / output_subdir / f"{job_id}_SUBBED{ext}"
            if candidate.exists():
                final_path = candidate
                break

        fixed = False
        now_iso = datetime.now().isoformat()

        def _heal(to_stage, status_text, progress_val, action_msg):
            nonlocal fixed
            try:
                execute_transition(
                    job_id=job_id, job_stem=job_id,
                    from_stage=stage, to_stage=to_stage,
                    worker_id="api:health/fix", reason=f"Self-heal: {action_msg}",
                    skip_validation=True,
                )
            except Exception as te:
                logger.warning(f"Transition audit failed for {job_id}: {te}")
            track.stage = to_stage
            track.status = status_text
            track.progress = progress_val
            track.updated_at = datetime.now()
            fixes.append({"stem": job_id, "fixed": True, "action": action_msg})
            fixed = True

        if final_path and stage not in {"COMPLETED", "DELIVERED"}:
            _heal("COMPLETED", "Done", 100.0, "Stage corrected to COMPLETED")
        elif srt_path.exists() and stage not in {"FINALIZED", "BURNING", "COMPLETED", "DELIVERED"}:
            _heal("FINALIZED", "Ready to burn", 85.0, "Stage corrected to FINALIZED")
        elif approved_path.exists() and stage not in {"REVIEWED", "FINALIZING", "FINALIZED", "BURNING", "COMPLETED", "DELIVERED"}:
            _heal("REVIEWED", "Ready to finalize", 70.0, "Stage corrected to REVIEWED")
        elif skeleton_path and stage in {"QUEUED", "INGEST", ""}:
            _heal("TRANSCRIBED", "Ready for translation", 30.0, "Stage corrected to TRANSCRIBED")

        if stage == "DEAD" and not fixed:
            if final_path:
                _heal("COMPLETED", "Recovered", 100.0, "Recovered from DEAD to COMPLETED")
            elif srt_path.exists():
                _heal("FINALIZED", "Recovered", 85.0, "Recovered from DEAD to FINALIZED")

    await db.commit()

    return {
        "time": datetime.now().isoformat(),
        "fixed_count": len([f for f in fixes if f.get("fixed")]),
        "fixes": fixes,
    }


@router.get("/api/v2/health/stuck")
async def api_health_stuck(
    notify: str = Query("0"),
    db: AsyncSession = Depends(get_db),
):
    """Detect jobs stuck in intermediate stages."""
    import json as _json

    STALL_THRESHOLDS = {
        "INGEST": 600, "TRANSCRIBED": 300, "TRANSLATING": 1800,
        "TRANSLATING_CLOUD_SUBMITTED": 1800, "CLOUD_TRANSLATING": 2700,
        "CLOUD_REVIEWING": 3600, "REVIEWED": 300, "FINALIZING": 600,
        "FINALIZED": 300, "BURNING": 7200,
    }

    result = await db.execute(select(Track).where(Track.stage.notin_(["DELETED"])))
    tracks = result.scalars().all()

    now = datetime.now()
    stuck_jobs = []

    for track in tracks:
        stage = (track.stage or "").upper()
        threshold = STALL_THRESHOLDS.get(stage)
        if not threshold:
            continue

        meta = track.meta or {}
        if isinstance(meta, str):
            try:
                meta = _json.loads(meta)
            except Exception:
                meta = {}
        if meta.get("halted"):
            continue

        updated_at = track.updated_at
        if not updated_at:
            continue

        elapsed = (now - updated_at).total_seconds()
        if elapsed < threshold:
            continue

        if elapsed < 3600:
            duration_str = f"{int(elapsed / 60)} minutes"
        else:
            duration_str = f"{elapsed / 3600:.1f} hours"

        stuck_jobs.append({
            "stem": track.job_id or track.id,
            "stage": stage,
            "stuck_duration": duration_str,
            "elapsed_seconds": int(elapsed),
            "threshold_seconds": threshold,
            "updated_at": track.updated_at.isoformat() if track.updated_at else None,
            "status": track.status or "",
        })

    stuck_jobs.sort(key=lambda x: x["elapsed_seconds"], reverse=True)

    notification_sent = False
    if notify == "1" and stuck_jobs:
        try:
            from notification_manager import NotificationManager
            notification_sent = NotificationManager.notify_stuck_jobs(stuck_jobs)
        except Exception:
            pass

    return {
        "time": datetime.now().isoformat(),
        "stuck_count": len(stuck_jobs),
        "notification_sent": notification_sent,
        "stuck_jobs": stuck_jobs,
    }


@router.post("/api/v2/health/notify-dead")
async def api_health_notify_dead(
    db: AsyncSession = Depends(get_db),
    _admin=Depends(admin_required),
):
    """Send notification about DEAD jobs."""
    import json as _json

    result = await db.execute(select(Track).where(Track.stage == "DEAD"))
    dead_tracks = result.scalars().all()

    dead_jobs = []
    for track in dead_tracks:
        meta = track.meta or {}
        if isinstance(meta, str):
            try:
                meta = _json.loads(meta)
            except Exception:
                meta = {}
        dead_jobs.append({
            "stem": track.job_id or track.id,
            "error": meta.get("halt_reason") or meta.get("last_error") or track.status or "Unknown error",
            "failed_at": meta.get("failed_at") or (track.updated_at.isoformat() if track.updated_at else None),
        })

    if not dead_jobs:
        return {"message": "No DEAD jobs found", "notification_sent": False}

    notification_sent = False
    try:
        from notification_manager import NotificationManager
        notification_sent = NotificationManager.notify_dead_jobs(dead_jobs)
    except Exception:
        pass

    return {
        "time": datetime.now().isoformat(),
        "dead_count": len(dead_jobs),
        "notification_sent": notification_sent,
        "dead_jobs": dead_jobs,
    }


@router.get("/metrics")
async def metrics(db: AsyncSession = Depends(get_db)):
    """Prometheus-compatible metrics."""
    result = await db.execute(
        select(Track.stage, func.count(Track.id)).group_by(Track.stage)
    )
    stage_counts = {row[0]: row[1] for row in result.all()}
    total = sum(stage_counts.values())

    manager_age = _heartbeat_age_seconds("omega_manager")
    storage_ready = 0
    try:
        storage_ready = 1 if config.critical_paths_ready(require_write=True) else 0
    except Exception:
        pass

    lines = [
        "# HELP omega_storage_ready Storage paths ready/writable (1/0)",
        "# TYPE omega_storage_ready gauge",
        f"omega_storage_ready {storage_ready}",
        "# HELP omega_jobs_total Total jobs in DB",
        "# TYPE omega_jobs_total gauge",
        f"omega_jobs_total {total}",
        "# HELP omega_jobs_stage_total Jobs by stage",
        "# TYPE omega_jobs_stage_total gauge",
    ]
    for stage, count in sorted(stage_counts.items()):
        lines.append(f'omega_jobs_stage_total{{stage="{stage}"}} {count}')
    if manager_age is not None:
        lines.append("# HELP omega_manager_heartbeat_age_seconds Seconds since manager heartbeat")
        lines.append("# TYPE omega_manager_heartbeat_age_seconds gauge")
        lines.append(f"omega_manager_heartbeat_age_seconds {manager_age:.3f}")

    return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")
