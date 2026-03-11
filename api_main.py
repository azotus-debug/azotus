import logging
import os
import asyncio
from datetime import datetime
from fastapi import FastAPI, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import socketio

import config
from routers import admin_required

# Configure Logger for the new API
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("OmegaFastAPI")

# --- ROUTER IMPORTS ---
from routers.health import router as health_router
from routers.programs import router as programs_router
from routers.tracks import router as tracks_router
from routers.settings import router as settings_router
from routers.ops import router as ops_router
from routers.editor import router as editor_router
from routers.auth import router as auth_router
from routers.stations import router as stations_router

# --- SOCKET.IO SETUP ---
# We use AsyncServer for FastAPI compatibility
sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins='*')

# The ASGIApp wraps both the FastAPI app and the Socket.IO server into a single mountable unit
# but we will wrap the fastAPI app inside it later.

# --- FASTAPI SETUP ---
app = FastAPI(
    title="Omega Literati Core API",
    description="The high-performance async backend for Omega Literati video processing.",
    version="2.0.0",
)

# CORS configuration — allow all local origins.
# This is a local-network service, not exposed to the internet.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- SECURITY HEADERS MIDDLEWARE ---
@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    return response


# --- MOUNT ROUTERS ---
app.include_router(auth_router)            # /api/auth/*
app.include_router(health_router)          # /health, /healthz, /ready, /api/health, /api/v2/health/*, /metrics
app.include_router(programs_router)        # /api/v2/programs/*, /api/v2/thumbnails/*
app.include_router(tracks_router)          # /api/v2/tracks/*, /api/v2/fork-language, /api/v2/jobs/*
app.include_router(settings_router)        # /api/v2/ministries/*, /api/v2/deliveries/profiles/*, /api/v2/dropzones/*, /api/v2/pipeline/stats, /api/v2/languages, /api/v2/voices, /api/v2/settings
app.include_router(ops_router)             # /api/v2/ops/*, /api/v2/cloud/status, /api/v2/deliveries
app.include_router(editor_router)          # /api/surgical/*, /api/assistant/*, /api/editor/*, /api/stream/*
app.include_router(stations_router)       # /api/v2/stations/*

# Mount Socket.IO to FastAPI via ASGIApp
# This exposes the socket endpoints at /socket.io/ (default)
socket_app = socketio.ASGIApp(sio, other_asgi_app=app)


# --- SOCKET.IO EVENT HANDLERS ---
@sio.event
async def connect(sid, environ, auth=None):
    """Authenticate Socket.IO connections via JWT when available."""
    # Extract token from auth dict (sent by client) or query string
    token = None
    if auth and isinstance(auth, dict):
        token = auth.get("token")

    if not token:
        # Try query string fallback (e.g. ?token=...)
        query = environ.get("QUERY_STRING", "")
        for part in query.split("&"):
            if part.startswith("token="):
                token = part[6:]
                break

    # If a token is provided, verify it
    user_info = {"sub": "anonymous", "role": "viewer"}
    if token:
        from routers.auth import verify_jwt
        claims = verify_jwt(token)
        if claims:
            user_info = {"sub": claims.get("sub", "unknown"), "role": claims.get("role", "viewer")}

    await sio.save_session(sid, {"user": user_info})
    logger.info(f"Socket connected: {sid} (user={user_info['sub']}, role={user_info['role']})")
    await sio.emit('system_status', {
        'message': 'Connected to Omega Core Real-Time Channel',
        'timestamp': datetime.now().isoformat(),
        'user': user_info['sub'],
    }, room=sid)

@sio.event
async def disconnect(sid):
    logger.info(f"Socket disconnected: {sid}")
    # Release any editor locks held by this session
    try:
        session = await sio.get_session(sid)
        user = session.get("user", {}) if session else {}
        username = user.get("sub")
        lock_job = session.get("editing_job")
        if username and lock_job:
            import omega_db
            track = omega_db.get_track_by_job(lock_job)
            if track and track.get("locked_by") == username:
                omega_db.update_track(track["id"], locked_by=None, locked_at=None)
                await sio.emit("lock_changed", {
                    "job_id": lock_job,
                    "locked_by": None,
                    "locked_at": None,
                }, room=f"job_{lock_job}")
                logger.info(f"Auto-released lock on {lock_job} for {username}")
    except Exception as e:
        logger.warning(f"Lock cleanup on disconnect failed: {e}")

@sio.event
async def subscribe_to_job(sid, data):
    """Clients can emit this to join a specific job's update room."""
    job_id = data.get('job_id')
    if job_id:
        sio.enter_room(sid, f"job_{job_id}")
        logger.info(f"Client {sid} subscribed to updates for job: {job_id}")
        await sio.emit('subscribed', {'job_id': job_id}, room=sid)

@sio.event
async def editing_job(sid, data):
    """Client reports which job it's actively editing (for lock cleanup on disconnect)."""
    job_id = data.get('job_id') if isinstance(data, dict) else None
    if job_id:
        session = await sio.get_session(sid)
        session["editing_job"] = job_id
        await sio.save_session(sid, session)


# --- FASTAPI REST ROUTES (Phase 1 Stub) ---
from pydantic import BaseModel
from typing import Any, Dict

class TrackEventPayload(BaseModel):
    event: str
    track_id: str
    payload: Dict[str, Any]

@app.post("/api/v2/internal/publish")
async def publish_internal_event(
    data: TrackEventPayload,
    _admin=Depends(admin_required),
):
    """
    Internal-only route. Realtimebridge calls this when the database updates.
    We push the event to connected WebSockets.
    """
    logger.info(f"Broadcast triggered for track {data.track_id}: {data.event}")
    # Broadcast to anyone listening to this specific job room
    # (Clients join rooms based on the job_id, assuming `data.payload['job_id']` exists)
    job_id = data.payload.get('job_id')
    if job_id:
        await sio.emit('track_updated', data.dict(), room=f"job_{job_id}")
    else:
        # If no job_id, broadcast globally
        await sio.emit('track_updated', data.dict())

    return {"status": "broadcasted"}


# Optional: Start block if running directly, though `uvicorn api_main:socket_app` is preferred.
if __name__ == "__main__":
    import uvicorn
    # Start on port 8001 so it doesn't conflict with Flask on 8000 during the phase-out
    uvicorn.run("api_main:socket_app", host="0.0.0.0", port=8001, reload=True)
