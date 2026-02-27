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

# CORS configuration (single canonical frontend origin list)
_cors_raw = os.environ.get("OMEGA_CORS_ORIGINS", "http://127.0.0.1:3000,http://localhost:3000")
_cors_origins = [origin.strip() for origin in _cors_raw.split(",") if origin.strip()]
_allow_credentials = "*" not in _cors_origins

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins or ["http://127.0.0.1:3000"],
    allow_credentials=_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- MOUNT ROUTERS ---
app.include_router(health_router)       # /health, /healthz, /ready, /api/health, /api/v2/health/*, /metrics
app.include_router(programs_router)     # /api/v2/programs/*, /api/v2/thumbnails/*
app.include_router(tracks_router)       # /api/v2/tracks/*, /api/v2/fork-language, /api/v2/jobs/*
app.include_router(settings_router)     # /api/v2/ministries/*, /api/v2/deliveries/profiles/*, /api/v2/dropzones/*, /api/v2/pipeline/stats, /api/v2/languages, /api/v2/voices, /api/v2/settings
app.include_router(ops_router)          # /api/v2/ops/*, /api/v2/cloud/status, /api/v2/deliveries
app.include_router(editor_router)       # /api/surgical/*, /api/assistant/*, /api/editor/*, /api/stream/*

# Mount Socket.IO to FastAPI via ASGIApp
# This exposes the socket endpoints at /socket.io/ (default)
socket_app = socketio.ASGIApp(sio, other_asgi_app=app)


# --- SOCKET.IO EVENT HANDLERS ---
@sio.event
async def connect(sid, environ):
    logger.info(f"🟢 Socket Client Connected: {sid}")
    await sio.emit('system_status', {'message': 'Connected to Omega Core Real-Time Channel', 'timestamp': datetime.now().isoformat()}, room=sid)

@sio.event
async def disconnect(sid):
    logger.info(f"🔴 Socket Client Disconnected: {sid}")

@sio.event
async def subscribe_to_job(sid, data):
    """Clients can emit this to join a specific job's update room."""
    job_id = data.get('job_id')
    if job_id:
        sio.enter_room(sid, f"job_{job_id}")
        logger.info(f"Client {sid} subscribed to updates for job: {job_id}")
        await sio.emit('subscribed', {'job_id': job_id}, room=sid)


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
