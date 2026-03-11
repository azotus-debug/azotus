#!/usr/bin/env python3
"""
Station heartbeat daemon — runs on every Omega Mac Mini.

On startup:
  1. Detects station identity from config (OMEGA_STATION_ID / hostname)
  2. Optionally detects Tailscale IP
  3. Registers with the local FastAPI server
  4. Sends heartbeat every INTERVAL seconds

Designed to run under PM2 as a long-lived process.
"""

import json
import logging
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib import request as urllib_request
from urllib.error import URLError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [StationHeartbeat] %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("StationHeartbeat")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

HEARTBEAT_INTERVAL = int(os.environ.get("OMEGA_HEARTBEAT_INTERVAL", "30"))
API_BASE = os.environ.get("OMEGA_API_URL", "http://127.0.0.1:8001")

# Station identity — mirrors config.py logic
_raw_station = os.environ.get("OMEGA_STATION_ID", "") or socket.gethostname()
import re
STATION_ID = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(_raw_station).strip()).strip("-").lower() or "station"
STATION_NAME = os.environ.get("OMEGA_STATION_NAME", "").strip() or STATION_ID

# Heartbeat file for local watchdog compatibility
HEARTBEAT_DIR = Path(os.environ.get("OMEGA_BASE_DIR", Path(__file__).parent)) / "heartbeats"

_running = True


def _signal_handler(sig, frame):
    global _running
    logger.info("Received signal %s, shutting down gracefully...", sig)
    _running = False


signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _detect_tailscale_ip() -> str | None:
    """Try to detect this machine's Tailscale IP."""
    try:
        result = subprocess.run(
            ["tailscale", "ip", "-4"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            ip = result.stdout.strip().split("\n")[0].strip()
            if ip:
                return ip
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def _disk_free_gb() -> float | None:
    """Get free disk space on the delivery volume."""
    for candidate in ["/Volumes/Extreme SSD", "/Volumes/OmegaSSD", os.path.expanduser("~")]:
        if os.path.exists(candidate):
            try:
                total, used, free = shutil.disk_usage(candidate)
                return round(free / (2 ** 30), 1)
            except Exception:
                pass
    return None


def _active_job_count() -> int:
    """Count active pipeline jobs by checking heartbeat files or stage directories."""
    try:
        base = Path(os.environ.get("OMEGA_BASE_DIR", Path(__file__).parent))
        stage_dir = base / "0_STAGE"
        if stage_dir.exists():
            return len([d for d in stage_dir.iterdir() if d.is_dir()])
    except Exception:
        pass
    return 0


def _api_post(endpoint: str, payload: dict) -> dict | None:
    """POST JSON to the local FastAPI server."""
    url = f"{API_BASE}{endpoint}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib_request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib_request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except URLError as e:
        logger.warning("API call to %s failed: %s", endpoint, e)
        return None
    except Exception as e:
        logger.warning("API call to %s error: %s", endpoint, e)
        return None


def _write_local_beatfile():
    """Write a local .beat file for the process watchdog."""
    HEARTBEAT_DIR.mkdir(parents=True, exist_ok=True)
    beat_file = HEARTBEAT_DIR / "station_heartbeat.beat"
    beat_file.write_text(str(time.time()))


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main():
    logger.info("Station heartbeat daemon starting")
    logger.info("  Station ID:   %s", STATION_ID)
    logger.info("  Station Name: %s", STATION_NAME)
    logger.info("  API Base:     %s", API_BASE)
    logger.info("  Interval:     %ds", HEARTBEAT_INTERVAL)

    tailscale_ip = _detect_tailscale_ip()
    if tailscale_ip:
        logger.info("  Tailscale IP: %s", tailscale_ip)
    else:
        logger.info("  Tailscale:    not detected (will retry)")

    # --- Initial registration ---
    registered = False
    while _running and not registered:
        result = _api_post("/api/v2/stations/register", {
            "station_id": STATION_ID,
            "display_name": STATION_NAME,
            "tailscale_ip": tailscale_ip,
        })
        if result and result.get("ok"):
            logger.info("Registered with API: %s", result.get("message", "ok"))
            registered = True
        else:
            logger.warning("Registration failed, retrying in 10s... (is FastAPI running?)")
            for _ in range(10):
                if not _running:
                    return
                time.sleep(1)

    # --- Heartbeat loop ---
    consecutive_failures = 0
    tailscale_retry_at = 0

    while _running:
        # Retry Tailscale detection periodically if not found
        if not tailscale_ip and time.time() > tailscale_retry_at:
            tailscale_ip = _detect_tailscale_ip()
            if tailscale_ip:
                logger.info("Tailscale IP detected: %s", tailscale_ip)
                # Re-register with the IP
                _api_post("/api/v2/stations/register", {
                    "station_id": STATION_ID,
                    "display_name": STATION_NAME,
                    "tailscale_ip": tailscale_ip,
                })
            else:
                tailscale_retry_at = time.time() + 300  # retry every 5 min

        # Send heartbeat
        result = _api_post("/api/v2/stations/heartbeat", {
            "station_id": STATION_ID,
            "status": "online",
            "active_jobs": _active_job_count(),
            "disk_free_gb": _disk_free_gb(),
        })

        if result and result.get("ok"):
            consecutive_failures = 0
        else:
            consecutive_failures += 1
            if consecutive_failures % 10 == 1:
                logger.warning("Heartbeat failed (%d consecutive)", consecutive_failures)

        # Write local .beat file regardless of API success
        _write_local_beatfile()

        # Sleep in 1s increments so we respond to signals quickly
        for _ in range(HEARTBEAT_INTERVAL):
            if not _running:
                break
            time.sleep(1)

    logger.info("Station heartbeat daemon stopped")


if __name__ == "__main__":
    main()
