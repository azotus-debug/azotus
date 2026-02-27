import time
import os
import subprocess
import sys
from pathlib import Path
import datetime
from lock_manager import ProcessLock

# --- CONFIGURATION ---
BASE_DIR = Path(__file__).resolve().parent
HEARTBEAT_DIR = BASE_DIR / "heartbeats"
MAX_SILENCE_SECONDS = 300  # 5 minutes
LOG_FILE = BASE_DIR / "logs" / "watchdog.log"
LOG_DIR = BASE_DIR / "logs"
MAX_RESTARTS = 10  # Stop trying after this many consecutive restarts
BASE_BACKOFF = 30  # Base backoff seconds between restarts
MAX_BACKOFF = 600  # Maximum backoff (10 minutes)
CONSECUTIVE_STALE_BEFORE_KILL = 3

# Process Name -> Restart Command
PYTHON_BIN = os.environ.get("OMEGA_PYTHON") or sys.executable
PROCESS_MAP = {
    "omega_manager": [PYTHON_BIN, "-u", "omega_manager.py"],
    "fastapi": [PYTHON_BIN, "-m", "uvicorn", "api_main:socket_app", "--host", "0.0.0.0", "--port", "8001", "--workers", "1", "--log-level", "info"],
}
PROCESS_MATCHERS = {
    "omega_manager": ["omega_manager.py"],
    "fastapi": ["uvicorn api_main:socket_app"],
    "cloud_sync_service": ["cloud_sync_service.py"],
}
PROCESS_WITHOUT_HEARTBEAT = {"fastapi"}

if os.environ.get("OMEGA_CLOUD_SYNC_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}:
    PROCESS_MAP["cloud_sync_service"] = [PYTHON_BIN, "-u", "cloud_sync_service.py"]

# Per-process restart tracking: {name: {"count": int, "last_restart": float}}
_restart_state = {}
_stale_state = {}

def log(msg):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{timestamp}] {msg}"
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(entry + "\n")


def _is_process_running(name: str) -> bool:
    patterns = PROCESS_MATCHERS.get(name, [f"{name}.py"])
    for pattern in patterns:
        try:
            proc = subprocess.run(
                ["pgrep", "-f", pattern],
                check=False,
                capture_output=True,
                text=True,
            )
        except Exception:
            continue

        for line in (proc.stdout or "").splitlines():
            try:
                pid = int(line.strip())
            except Exception:
                continue
            if pid and pid != os.getpid():
                return True
    return False


def restart_process(name, command):
    state = _restart_state.setdefault(name, {"count": 0, "last_restart": 0.0})

    # Check max restart limit
    if state["count"] >= MAX_RESTARTS:
        log(f"🛑 GIVING UP on {name}: {state['count']} consecutive restarts reached limit ({MAX_RESTARTS}). Manual intervention required.")
        return

    # Check backoff
    backoff = min(BASE_BACKOFF * (2 ** state["count"]), MAX_BACKOFF)
    elapsed = time.time() - state["last_restart"]
    if elapsed < backoff:
        # Still in backoff period — skip this cycle
        return

    log(f"🚨 DEAD PROCESS DETECTED: {name}. Restarting (attempt {state['count'] + 1}/{MAX_RESTARTS})...")

    # 1. Kill existing (if hung) - Be careful not to kill the watchdog itself if names overlap
    if _is_process_running(name):
        try:
            for pattern in PROCESS_MATCHERS.get(name, [f"{name}.py"]):
                subprocess.run(["pkill", "-f", pattern], check=False)
        except Exception as e:
            log(f"⚠️ Failed to kill {name}: {e}")
    time.sleep(1)

    # 2. Restart
    if name == "omega_manager":
        log_name = LOG_DIR / "manager.log"
    elif name == "fastapi":
        log_name = LOG_DIR / "fastapi.log"
    elif name == "cloud_sync_service":
        log_name = LOG_DIR / "cloud_sync.log"
    else:
        log_name = LOG_DIR / f"{name}.log"

    with open(log_name, "a", encoding="utf-8") as out:
        subprocess.Popen(command, stdout=out, stderr=out, cwd=str(BASE_DIR))

    state["count"] += 1
    state["last_restart"] = time.time()
    log(f"✅ Restarted {name} (attempt {state['count']}/{MAX_RESTARTS}, next backoff: {min(BASE_BACKOFF * (2 ** state['count']), MAX_BACKOFF)}s)")

    # Touch heartbeat to give it time to boot
    (HEARTBEAT_DIR / f"{name}.beat").touch()

def monitor():
    log("🐶 Watchdog Active. Monitoring heartbeats...")
    HEARTBEAT_DIR.mkdir(parents=True, exist_ok=True)

    while True:
        now = time.time()
        for name, command in PROCESS_MAP.items():
            try:
                beat_file = HEARTBEAT_DIR / f"{name}.beat"
                last_beat = beat_file.stat().st_mtime if beat_file.exists() else 0.0
                silence = now - last_beat
                running = _is_process_running(name)

                # Some services (FastAPI) do not emit heartbeat files yet.
                # For these, process liveness alone is authoritative.
                if name in PROCESS_WITHOUT_HEARTBEAT:
                    if running:
                        _stale_state[name] = 0
                        state = _restart_state.get(name)
                        if state and state["count"] > 0:
                            log(f"💚 {name} recovered after {state['count']} restart(s). Resetting counter.")
                            state["count"] = 0
                            state["last_restart"] = 0.0
                    else:
                        restart_process(name, command)
                    continue

                if silence > MAX_SILENCE_SECONDS:
                    stale_count = _stale_state.get(name, 0) + 1
                    _stale_state[name] = stale_count

                    # If process is still present, require repeated stale checks before kill/restart.
                    if running and stale_count < CONSECUTIVE_STALE_BEFORE_KILL:
                        if stale_count == 1:
                            log(
                                f"⚠️ {name} heartbeat stale ({silence:.0f}s) but process is running. "
                                f"Waiting for {CONSECUTIVE_STALE_BEFORE_KILL} consecutive stale checks."
                            )
                        continue

                    restart_process(name, command)
                else:
                    _stale_state[name] = 0
                    # Process is alive — reset restart counter
                    state = _restart_state.get(name)
                    if state and state["count"] > 0:
                        log(f"💚 {name} recovered after {state['count']} restart(s). Resetting counter.")
                        state["count"] = 0
                        state["last_restart"] = 0.0
            except Exception as e:
                log(f"❌ Watchdog loop error for {name}: {e}")
        time.sleep(10)

if __name__ == "__main__":
    try:
        with ProcessLock("omega_watchdog"):
            monitor()
    except BaseException as exc:
        log(f"🔥 Watchdog fatal error: {exc!r}")
        raise
