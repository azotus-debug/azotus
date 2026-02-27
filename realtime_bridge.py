import logging
import json
import os
import urllib.request
import urllib.error

logger = logging.getLogger("OmegaEventStream")

FASTAPI_HOST = os.environ.get("OMEGA_FASTAPI_HOST", "127.0.0.1").strip() or "127.0.0.1"
FASTAPI_PORT = os.environ.get("OMEGA_FASTAPI_PORT", "8001").strip() or "8001"
FASTAPI_URL = f"http://{FASTAPI_HOST}:{FASTAPI_PORT}/api/v2/internal/publish"

def broadcast_track_update(track_id: str, payload: dict):
    """
    Sends a fire-and-forget HTTP POST to the local FastAPI server.
    This bridges the synchronous SQLite/PostgreSQL workers with the async Real-Time server.
    """
    try:
        data = json.dumps({
            "event": "track_updated",
            "track_id": track_id,
            "payload": payload
        }).encode("utf-8")
        
        req = urllib.request.Request(
            FASTAPI_URL, 
            data=data, 
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        # Keep timeout short because this is local-only fire-and-forget.
        # If the FastAPI server is down, we immediately swallow the error and move on
        # so we never stall the database thread.
        try:
            with urllib.request.urlopen(req, timeout=0.2) as response:
                pass
        except urllib.error.URLError:
            pass # FastAPI server isn't running yet, ignore safely.
            
    except Exception as e:
        logger.debug(f"Failed to broadcast event for track {track_id}: {e}")
