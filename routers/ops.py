import json
import uuid
import logging
import asyncio
from datetime import datetime
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text

import config
import omega_db
from db import get_db
from models import Track, Program, TrackDelivery
from routers import admin_required
from state_machine import InvalidTransitionError
from transition_service import execute_transition

logger = logging.getLogger("OmegaFastAPI")

router = APIRouter(tags=["Operations"])


# ---- helpers (ported from dashboard.py) ----

def _normalize_meta(meta) -> dict:
    if isinstance(meta, dict):
        return meta
    if isinstance(meta, str):
        try:
            return json.loads(meta)
        except Exception:
            return {}
    return {}


# =========================================================================
# Ops Summary
# =========================================================================

@router.get("/api/v2/ops/summary")
async def ops_summary(db: AsyncSession = Depends(get_db)):
    try:
        active_result = await db.execute(
            select(Track, Program.title, Program.due_date, Program.client)
            .join(Program, Track.program_id == Program.id)
            .where(Track.stage.notin_(["COMPLETE", "COMPLETED", "DELIVERED"]))
            .order_by(Track.updated_at.desc())
        )
        rows = active_result.all()

        completed_result = await db.execute(
            select(Track)
            .where(Track.stage.in_(["COMPLETE", "COMPLETED"]))
            .where((Track.delivery_status.is_(None)) | (Track.delivery_status != "DELIVERED"))
        )
        ready_delivery_rows = completed_result.scalars().all()

        stage_counts: dict[str, int] = {}
        now = datetime.now()
        urgent_count = 0
        overdue_count = 0

        for track, program_title, due_date, client in rows:
            stage = (track.stage or "UNKNOWN").upper()
            stage_counts[stage] = stage_counts.get(stage, 0) + 1

            if due_date:
                try:
                    dd = datetime.fromisoformat(str(due_date).replace("Z", "+00:00").split("+")[0])
                    hours = (dd - now).total_seconds() / 3600
                    if hours < 0:
                        overdue_count += 1
                    elif hours <= 24:
                        urgent_count += 1
                except Exception:
                    pass

        bottlenecks = []
        if stage_counts:
            max_count = max(stage_counts.values())
            threshold = max(3, max_count * 0.5)
            for stage, count in stage_counts.items():
                if count >= threshold and count > 2:
                    bottlenecks.append({"stage": stage, "count": count})

        ready_for_delivery = len(ready_delivery_rows)
        ready_for_burn = stage_counts.get("FINALIZING", 0) + stage_counts.get("REVIEWED", 0)
        awaiting_review = stage_counts.get("AWAITING_REVIEW", 0) + stage_counts.get("CLOUD_REVIEWING", 0)
        failed_count = stage_counts.get("FAILED", 0) + stage_counts.get("DEAD", 0)

        return {
            "total_active": len(rows),
            "stage_counts": stage_counts,
            "bottlenecks": bottlenecks,
            "urgent": urgent_count,
            "overdue": overdue_count,
            "ready_for_delivery": ready_for_delivery,
            "ready_for_burn": ready_for_burn,
            "awaiting_review": awaiting_review,
            "failed": failed_count,
            "timestamp": now.isoformat(),
        }
    except Exception as e:
        logger.error(f"Ops Summary API Error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


# =========================================================================
# Ops Queue
# =========================================================================

@router.get("/api/v2/ops/queue")
async def ops_queue(
    stage: str = Query("", description="Comma-separated stage filter"),
    due_before: str = Query("", description="ISO date string"),
    limit: int = Query(100, ge=1, le=500),
    include_completed: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    try:
        query = (
            select(
                Track,
                Program.title.label("program_title"),
                Program.due_date,
                Program.client,
                Program.video_path,
            )
            .join(Program, Track.program_id == Program.id)
        )

        if not include_completed:
            query = query.where(Track.stage.notin_(["COMPLETE", "COMPLETED", "DELIVERED"]))

        if stage.strip():
            stages = [s.strip() for s in stage.split(",")]
            query = query.where(Track.stage.in_(stages))

        if due_before.strip():
            query = query.where(Program.due_date <= due_before)

        query = query.order_by(Program.due_date.asc(), Track.updated_at.desc()).limit(limit)

        result = await db.execute(query)
        rows = result.all()

        now = datetime.now()
        results = []
        for track, program_title, due_date_str, client, video_path in rows:
            urgency = "normal"
            hours_until_due = None
            if due_date_str:
                try:
                    dd = datetime.fromisoformat(str(due_date_str).replace("Z", "+00:00").split("+")[0])
                    hours_until_due = (dd - now).total_seconds() / 3600
                    if hours_until_due < 0:
                        urgency = "overdue"
                    elif hours_until_due <= 24:
                        urgency = "urgent"
                    elif hours_until_due <= 72:
                        urgency = "soon"
                except Exception:
                    pass

            meta = _normalize_meta(track.meta)

            results.append({
                "id": track.id,
                "program_id": track.program_id,
                "program_title": program_title,
                "client": client,
                "language_code": track.language_code,
                "stage": track.stage,
                "status": track.status,
                "progress": track.progress,
                "due_date": due_date_str,
                "urgency": urgency,
                "hours_until_due": hours_until_due,
                "updated_at": track.updated_at.isoformat() if track.updated_at else None,
                "meta": meta,
            })

        return {
            "tracks": results,
            "count": len(results),
            "timestamp": now.isoformat(),
        }
    except Exception as e:
        logger.error(f"Ops Queue API Error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


# =========================================================================
# Ops Batch Actions
# =========================================================================

@router.post("/api/v2/ops/actions")
async def ops_batch_actions(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _admin=Depends(admin_required),
):
    try:
        data = await request.json()
        action = (data.get("action") or "").strip()
        track_ids = data.get("track_ids", [])
        idempotency_key = data.get("idempotency_key")

        if not action:
            return JSONResponse({"error": "Missing action"}, status_code=400)
        if not track_ids or not isinstance(track_ids, list):
            return JSONResponse({"error": "Missing or invalid track_ids"}, status_code=400)

        valid_actions = {"deliver", "burn", "approve", "retry", "finalize"}
        if action not in valid_actions:
            return JSONResponse({"error": f"Invalid action. Must be one of: {valid_actions}"}, status_code=400)

        eligibility = {
            "deliver": {"COMPLETE", "COMPLETED", "BURNING"},
            "burn": {"FINALIZED"},
            "finalize": {"REVIEWED"},
            "approve": {"AWAITING_REVIEW", "CLOUD_REVIEWING", "AWAITING_APPROVAL"},
            "retry": {"FAILED", "DEAD", "ERROR"},
        }
        eligible_stages = eligibility.get(action, set())

        results = []
        success_count = 0
        skip_count = 0
        error_count = 0

        for track_id in track_ids:
            result = await db.execute(select(Track).where(Track.id == track_id))
            track = result.scalar_one_or_none()

            if not track:
                results.append({"track_id": track_id, "status": "error", "message": "Track not found"})
                error_count += 1
                continue

            stage = (track.stage or "").upper()

            if stage not in eligible_stages:
                if action == "deliver" and track.output_path:
                    pass  # Allow
                else:
                    results.append({
                        "track_id": track_id,
                        "status": "skipped",
                        "message": f"Not eligible: stage is {stage}, need one of {eligible_stages}",
                    })
                    skip_count += 1
                    continue

            try:
                now = datetime.utcnow()
                if action == "deliver":
                    track.delivery_status = "DELIVERED"
                    track.updated_at = now
                    delivery_id = str(uuid.uuid4())
                    db.add(TrackDelivery(
                        id=delivery_id,
                        track_id=track_id,
                        destination="batch_delivery",
                        delivered_at=now,
                    ))
                    results.append({"track_id": track_id, "status": "success", "message": "Marked as delivered"})
                    success_count += 1

                elif action == "burn":
                    if not track.job_id:
                        raise ValueError("Track has no job_id")
                    srt_path = config.SRT_DIR / f"{track.job_id}.srt"
                    done_srt = config.SRT_DIR / f"DONE_{track.job_id}.srt"
                    if not srt_path.exists() and done_srt.exists():
                        done_srt.rename(srt_path)
                    if not srt_path.exists():
                        raise ValueError("No SRT found; finalize first")
                    # Queue burn by keeping stage FINALIZED; manager will transition to BURNING.
                    current_stage = track.stage
                    execute_transition(
                        job_id=track.job_id or str(track.id), job_stem=track.job_id or str(track.id),
                        from_stage=current_stage, to_stage="FINALIZED",
                        worker_id="api:ops/batch", reason="Batch: queued for burn",
                        skip_validation=True,
                    )
                    track.stage = "FINALIZED"
                    track.status = "Queued for burn"
                    track.progress = max(float(track.progress or 0), 85.0)
                    track.updated_at = now
                    results.append({"track_id": track_id, "status": "success", "message": "Queued for burning"})
                    success_count += 1

                elif action == "finalize":
                    if not track.job_id:
                        raise ValueError("Track has no job_id")
                    approved = config.TRANSLATED_DONE_DIR / f"{track.job_id}_APPROVED.json"
                    if not approved.exists():
                        raise ValueError("No approved translation found")
                    # Queue finalize by keeping stage REVIEWED; manager will transition to FINALIZING.
                    current_stage = track.stage
                    execute_transition(
                        job_id=track.job_id or str(track.id), job_stem=track.job_id or str(track.id),
                        from_stage=current_stage, to_stage="REVIEWED",
                        worker_id="api:ops/batch", reason="Batch: queued for finalize",
                        skip_validation=True,
                    )
                    track.stage = "REVIEWED"
                    track.status = "Queued for finalize"
                    track.progress = max(float(track.progress or 0), 70.0)
                    track.updated_at = now
                    results.append({"track_id": track_id, "status": "success", "message": "Queued for finalization"})
                    success_count += 1

                elif action == "approve":
                    current_stage = track.stage
                    execute_transition(
                        job_id=track.job_id or str(track.id), job_stem=track.job_id or str(track.id),
                        from_stage=current_stage, to_stage="REVIEWED",
                        worker_id="api:ops/batch", reason="Batch: approved for finalization",
                        skip_validation=True,
                    )
                    track.stage = "REVIEWED"
                    track.updated_at = now
                    results.append({"track_id": track_id, "status": "success", "message": "Approved for finalization"})
                    success_count += 1

                elif action == "retry":
                    current_stage = track.stage
                    execute_transition(
                        job_id=track.job_id or str(track.id), job_stem=track.job_id or str(track.id),
                        from_stage=current_stage, to_stage="TRANSCRIBED",
                        worker_id="api:ops/batch", reason="Batch: retry",
                        skip_validation=True,
                    )
                    track.stage = "TRANSCRIBED"
                    track.status = "Retrying"
                    track.updated_at = now
                    results.append({"track_id": track_id, "status": "success", "message": "Reset for retry"})
                    success_count += 1

            except Exception as action_err:
                results.append({"track_id": track_id, "status": "error", "message": str(action_err)})
                error_count += 1

        await db.commit()

        return {
            "action": action,
            "total": len(track_ids),
            "success": success_count,
            "skipped": skip_count,
            "errors": error_count,
            "results": results,
            "idempotency_key": idempotency_key,
        }
    except Exception as e:
        logger.error(f"Ops Batch Actions API Error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


# =========================================================================
# Ops Deliveries
# =========================================================================

@router.get("/api/v2/ops/deliveries")
async def ops_deliveries(
    status: str = Query("", description="Filter: pending, delivered, failed"),
    days: int = Query(7, ge=1, le=365),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    try:
        status_filter = status.strip().lower()

        pending = []
        if not status_filter or status_filter == "pending":
            result = await db.execute(
                select(Track, Program.title.label("program_title"), Program.client, Program.due_date)
                .join(Program, Track.program_id == Program.id)
                .where(Track.stage.in_(["COMPLETE", "COMPLETED"]))
                .where((Track.delivery_status.is_(None)) | (Track.delivery_status != "DELIVERED"))
                .order_by(Program.due_date.asc(), Track.updated_at.desc())
                .limit(limit)
            )
            for track, program_title, client, due_date in result.all():
                pending.append({
                    "id": track.id,
                    "program_id": track.program_id,
                    "program_title": program_title,
                    "client": client,
                    "due_date": due_date,
                    "language_code": track.language_code,
                    "stage": track.stage,
                    "delivery_status": track.delivery_status,
                    "updated_at": track.updated_at.isoformat() if track.updated_at else None,
                })

        delivered = []
        if not status_filter or status_filter == "delivered":
            result = await db.execute(
                select(TrackDelivery, Track.language_code, Track.type, Program.title.label("program_title"), Program.client)
                .join(Track, TrackDelivery.track_id == Track.id)
                .join(Program, Track.program_id == Program.id)
                .where(TrackDelivery.delivered_at >= text(f"NOW() - INTERVAL '{days} days'"))
                .order_by(TrackDelivery.delivered_at.desc())
                .limit(limit)
            )
            for td, language_code, track_type, program_title, client in result.all():
                delivered.append({
                    "id": td.id,
                    "track_id": td.track_id,
                    "destination": td.destination,
                    "recipient": td.recipient,
                    "delivered_at": td.delivered_at.isoformat() if td.delivered_at else None,
                    "notes": td.notes,
                    "language_code": language_code,
                    "track_type": track_type,
                    "program_title": program_title,
                    "client": client,
                })

        return {
            "pending": pending,
            "pending_count": len(pending),
            "delivered": delivered,
            "delivered_count": len(delivered),
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        logger.error(f"Ops Deliveries API Error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


# =========================================================================
# Cloud Worker Monitoring
# =========================================================================

CLOUD_STAGES = {
    "TRANSLATING_CLOUD_SUBMITTED", "CLOUD_STARTING",
    "CLOUD_TRANSLATING", "CLOUD_REVIEWING", "CLOUD_EDITING",
    "CLOUD_DETECTING_MUSIC",
    "CLOUD_DONE", "CLOUD_ERROR",
}


@router.get("/api/v2/cloud/status")
async def cloud_status(db: AsyncSession = Depends(get_db)):
    try:
        result = await db.execute(select(Track))
        all_tracks = result.scalars().all()

        deadman_minutes = int(getattr(config, "OMEGA_CLOUD_DEADMAN_MINUTES", 60) or 60)
        timeout_seconds = deadman_minutes * 60
        now = datetime.now()

        cloud_tracks = []
        for track in all_tracks:
            stage = (track.stage or "").upper()
            status_str = track.status or ""

            is_cloud = (
                stage in CLOUD_STAGES
                or stage.startswith("CLOUD_")
                or status_str.upper().startswith("CLOUD_")
            )
            if not is_cloud:
                continue

            meta = _normalize_meta(track.meta)
            cloud_progress = meta.get("cloud_progress", {})
            if not isinstance(cloud_progress, dict):
                cloud_progress = {}

            cloud_status_text = cloud_progress.get("status", "")
            cloud_stage = cloud_progress.get("stage", stage)

            elapsed_seconds = None
            cloud_started_at = None

            stage_timeline = meta.get("stage_timeline")
            if isinstance(stage_timeline, list):
                for entry in stage_timeline:
                    entry_stage = (entry.get("stage") or "").upper()
                    if entry_stage in CLOUD_STAGES or entry_stage.startswith("CLOUD") or entry_stage == "TRANSLATING_CLOUD_SUBMITTED":
                        cloud_started_at = entry.get("started_at") or entry.get("timestamp")
                        break

            if not cloud_started_at:
                cloud_started_at = track.updated_at.isoformat() if track.updated_at else None

            if cloud_started_at:
                try:
                    if isinstance(cloud_started_at, str):
                        started = datetime.fromisoformat(cloud_started_at.replace("Z", "+00:00").split("+")[0])
                    else:
                        started = cloud_started_at
                    if hasattr(started, "tzinfo") and started.tzinfo:
                        started = started.replace(tzinfo=None)
                    elapsed_seconds = max(0, (now - started).total_seconds())
                except Exception:
                    pass

            remaining_seconds = None
            if elapsed_seconds is not None and timeout_seconds > 0:
                remaining_seconds = max(0, timeout_seconds - elapsed_seconds)

            retry_count = 0
            rc = meta.get("retry_count")
            if rc is not None:
                try:
                    retry_count = int(rc)
                except (ValueError, TypeError):
                    pass

            # Get program title
            program_title = ""
            prog_result = await db.execute(select(Program.title).where(Program.id == track.program_id))
            prog_row = prog_result.scalar_one_or_none()
            if prog_row:
                program_title = prog_row

            cloud_tracks.append({
                "track_id": track.id,
                "program_id": track.program_id,
                "program_title": program_title,
                "language_code": track.language_code,
                "language_name": track.language_name or "",
                "stage": cloud_stage,
                "status": status_str,
                "cloud_status": cloud_status_text,
                "progress": float(track.progress or 0),
                "elapsed_seconds": round(elapsed_seconds, 1) if elapsed_seconds is not None else None,
                "timeout_seconds": timeout_seconds,
                "remaining_seconds": round(remaining_seconds, 1) if remaining_seconds is not None else None,
                "retry_count": retry_count,
                "cloud_started_at": cloud_started_at if isinstance(cloud_started_at, str) else None,
                "updated_at": track.updated_at.isoformat() if track.updated_at else None,
            })

        return {
            "cloud_tracks": cloud_tracks,
            "total_in_cloud": len(cloud_tracks),
            "deadman_minutes": deadman_minutes,
            "timestamp": now.isoformat(),
        }
    except Exception as e:
        logger.error(f"Cloud Status API Error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


# =========================================================================
# Deliveries (create)
# =========================================================================

@router.get("/api/v2/deliveries")
async def get_deliveries(
    days: int = Query(7, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
):
    """GET /api/v2/deliveries — lightweight delivery list for the programs store."""
    try:
        result = await db.execute(
            select(TrackDelivery, Track.language_code, Track.program_id, Program.title.label("program_title"))
            .join(Track, TrackDelivery.track_id == Track.id)
            .join(Program, Track.program_id == Program.id)
            .where(TrackDelivery.delivered_at >= text(f"NOW() - INTERVAL '{days} days'"))
            .order_by(TrackDelivery.delivered_at.desc())
            .limit(100)
        )
        deliveries = []
        for td, language_code, program_id, program_title in result.all():
            deliveries.append({
                "id": td.id,
                "track_id": td.track_id,
                "program_id": program_id,
                "program_title": program_title,
                "language_code": language_code,
                "destination": td.destination,
                "recipient": td.recipient,
                "notes": td.notes,
                "delivered_at": td.delivered_at.isoformat() if td.delivered_at else None,
            })
        return deliveries
    except Exception as e:
        logger.error(f"GET /api/v2/deliveries error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/v2/deliveries")
async def create_delivery(
    request: Request,
    _admin=Depends(admin_required),
):
    """Create a delivery for a track. Uses omega_db for complex business logic."""
    data = await request.json()
    track_id = data.get("track_id")
    method = data.get("method", "folder")
    recipient = data.get("recipient", "")
    notes = data.get("notes", "")
    provisional = bool(data.get("provisional", False))
    lock_master = bool(data.get("lock_master", True))
    clear_pending_resync = bool(data.get("clear_pending_resync", True))
    locked_by = data.get("locked_by") or "system"

    if not track_id:
        return JSONResponse({"error": "Missing track_id"}, status_code=400)

    try:
        track = await asyncio.to_thread(omega_db.get_track, track_id)
        if not track:
            return JSONResponse({"error": "Track not found"}, status_code=404)

        if provisional:
            lock_master = False
            clear_pending_resync = False
            if not notes:
                notes = "Provisional delivery"

        delivery_id = await asyncio.to_thread(
            omega_db.record_track_delivery,
            track_id=track_id,
            destination=method,
            recipient=recipient,
            notes=notes,
            lock_master=lock_master,
            clear_pending_resync=clear_pending_resync,
            locked_by=locked_by,
        )

        status_msg = f"Provisional delivery via {method}" if provisional else f"Delivered via {method}"
        await asyncio.to_thread(omega_db.update_track, track_id, status=status_msg)

        if track.get("job_id"):
            await asyncio.to_thread(
                omega_db.log_delivery, track["job_id"], "Manual",
                datetime.now().isoformat(), method, notes,
            )

        return {
            "success": True,
            "delivery_id": delivery_id,
            "provisional": provisional,
            "message": f"Track {track_id} delivered",
        }
    except Exception as e:
        logger.error(f"Create delivery error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)
