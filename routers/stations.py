"""Station management API — register, heartbeat, list remote processing nodes."""

import json
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text

import config
from db import get_db
from models import Station, Track
from routers import admin_required

logger = logging.getLogger("OmegaStations")

router = APIRouter(tags=["Stations"])

DEAD_STATION_TIMEOUT_MINUTES = 5


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class StationRegisterRequest(BaseModel):
    station_id: str
    display_name: Optional[str] = None
    tailscale_ip: Optional[str] = None
    config: Optional[dict] = None


class StationHeartbeatRequest(BaseModel):
    station_id: str
    status: str = "online"
    # Optional payload so station can report current load
    active_jobs: Optional[int] = None
    disk_free_gb: Optional[float] = None


class JobAssignRequest(BaseModel):
    """Assign one or more jobs (by track ID or job_id) to a target station."""
    target_station: str
    track_ids: Optional[list[str]] = None
    job_ids: Optional[list[str]] = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/api/v2/stations/register")
async def register_station(body: StationRegisterRequest, db: AsyncSession = Depends(get_db)):
    """
    Register or update a processing station.
    Called on station startup. Idempotent — safe to call repeatedly.
    """
    now = datetime.utcnow()
    config_json = json.dumps(body.config) if body.config else None

    existing = await db.get(Station, body.station_id)
    if existing:
        if body.display_name:
            existing.display_name = body.display_name
        if body.tailscale_ip:
            existing.tailscale_ip = body.tailscale_ip
        if config_json:
            existing.config = config_json
        existing.last_heartbeat = now
        existing.status = "online"
        existing.updated_at = now
    else:
        station = Station(
            id=body.station_id,
            display_name=body.display_name or body.station_id,
            tailscale_ip=body.tailscale_ip,
            config=config_json,
            last_heartbeat=now,
            status="online",
            jobs_processed=0,
            created_at=now,
            updated_at=now,
        )
        db.add(station)

    await db.commit()

    logger.info("Station registered: %s (%s)", body.station_id, body.tailscale_ip or "no IP")
    return {
        "ok": True,
        "station_id": body.station_id,
        "message": "registered" if not existing else "updated",
    }


@router.post("/api/v2/stations/heartbeat")
async def station_heartbeat(body: StationHeartbeatRequest, db: AsyncSession = Depends(get_db)):
    """
    Periodic heartbeat from a station (every 30s).
    Updates liveness timestamp so dead-station cleanup doesn't release its jobs.
    """
    station = await db.get(Station, body.station_id)
    if not station:
        raise HTTPException(status_code=404, detail=f"Station '{body.station_id}' not registered. Call /register first.")

    now = datetime.utcnow()
    station.last_heartbeat = now
    station.status = body.status
    station.updated_at = now

    # Store optional load info in config JSON
    if body.active_jobs is not None or body.disk_free_gb is not None:
        existing_config = {}
        if station.config:
            try:
                existing_config = json.loads(station.config)
            except Exception:
                pass
        if body.active_jobs is not None:
            existing_config["active_jobs"] = body.active_jobs
        if body.disk_free_gb is not None:
            existing_config["disk_free_gb"] = body.disk_free_gb
        station.config = json.dumps(existing_config)

    await db.commit()
    return {"ok": True, "station_id": body.station_id}


@router.get("/api/v2/stations")
async def list_stations(db: AsyncSession = Depends(get_db)):
    """List all registered stations with their current status."""
    result = await db.execute(select(Station).order_by(Station.display_name))
    stations = result.scalars().all()

    now = datetime.utcnow()
    out = []
    for s in stations:
        age_seconds = None
        if s.last_heartbeat:
            age_seconds = (now - s.last_heartbeat).total_seconds()

        parsed_config = None
        if s.config:
            try:
                parsed_config = json.loads(s.config)
            except Exception:
                pass

        alive = age_seconds is not None and age_seconds < (DEAD_STATION_TIMEOUT_MINUTES * 60)

        out.append({
            "id": s.id,
            "display_name": s.display_name,
            "tailscale_ip": s.tailscale_ip,
            "status": s.status if alive else "offline",
            "alive": alive,
            "last_heartbeat": s.last_heartbeat.isoformat() if s.last_heartbeat else None,
            "heartbeat_age_seconds": round(age_seconds, 1) if age_seconds is not None else None,
            "jobs_processed": s.jobs_processed,
            "config": parsed_config,
            "created_at": s.created_at.isoformat() if s.created_at else None,
        })

    return {"stations": out, "count": len(out)}


@router.get("/api/v2/stations/{station_id}")
async def get_station(station_id: str, db: AsyncSession = Depends(get_db)):
    """Get details for a specific station."""
    station = await db.get(Station, station_id)
    if not station:
        raise HTTPException(status_code=404, detail=f"Station '{station_id}' not found")

    now = datetime.utcnow()
    age_seconds = None
    if station.last_heartbeat:
        age_seconds = (now - station.last_heartbeat).total_seconds()

    parsed_config = None
    if station.config:
        try:
            parsed_config = json.loads(station.config)
        except Exception:
            pass

    alive = age_seconds is not None and age_seconds < (DEAD_STATION_TIMEOUT_MINUTES * 60)

    return {
        "id": station.id,
        "display_name": station.display_name,
        "tailscale_ip": station.tailscale_ip,
        "status": station.status if alive else "offline",
        "alive": alive,
        "last_heartbeat": station.last_heartbeat.isoformat() if station.last_heartbeat else None,
        "heartbeat_age_seconds": round(age_seconds, 1) if age_seconds is not None else None,
        "jobs_processed": station.jobs_processed,
        "config": parsed_config,
        "created_at": station.created_at.isoformat() if station.created_at else None,
    }


@router.delete("/api/v2/stations/{station_id}")
async def remove_station(
    station_id: str,
    db: AsyncSession = Depends(get_db),
    _admin=Depends(admin_required),
):
    """Remove a station registration (admin only)."""
    station = await db.get(Station, station_id)
    if not station:
        raise HTTPException(status_code=404, detail=f"Station '{station_id}' not found")

    await db.delete(station)
    await db.commit()
    logger.info("Station removed: %s", station_id)
    return {"ok": True, "station_id": station_id, "message": "removed"}


@router.post("/api/v2/stations/assign-jobs")
async def assign_jobs(
    body: JobAssignRequest,
    db: AsyncSession = Depends(get_db),
    _admin=Depends(admin_required),
):
    """
    Reassign jobs to a different station (admin only).
    Accepts track IDs and/or job_ids (file stems). Updates meta.station_id
    so the target station's manager and cloud_sync will pick them up.
    """
    target = body.target_station.strip().lower()

    # Validate target station exists (or is a known station ID)
    station = await db.get(Station, target)
    if not station:
        # Allow assigning to a station that hasn't registered yet
        # (e.g., preparing jobs before Iceland Mac comes online)
        logger.warning("Assigning jobs to unregistered station '%s'", target)

    # Collect tracks to update
    tracks = []
    if body.track_ids:
        result = await db.execute(select(Track).where(Track.id.in_(body.track_ids)))
        tracks.extend(result.scalars().all())
    if body.job_ids:
        result = await db.execute(select(Track).where(Track.job_id.in_(body.job_ids)))
        tracks.extend(result.scalars().all())

    if not tracks:
        raise HTTPException(status_code=404, detail="No matching tracks found")

    # Deduplicate
    seen = set()
    unique_tracks = []
    for t in tracks:
        if t.id not in seen:
            seen.add(t.id)
            unique_tracks.append(t)

    updated = []
    for track in unique_tracks:
        meta = track.meta or {}
        old_station = meta.get("station_id", "")
        meta["station_id"] = target
        track.meta = meta
        track.updated_at = datetime.utcnow()
        updated.append({
            "track_id": track.id,
            "job_id": track.job_id,
            "old_station": old_station,
            "new_station": target,
        })

    await db.commit()

    logger.info("Reassigned %d job(s) to station '%s'", len(updated), target)
    return {
        "ok": True,
        "reassigned_count": len(updated),
        "target_station": target,
        "tracks": updated,
    }
