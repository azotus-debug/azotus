import time
import os
import subprocess
from pathlib import Path
import datetime
from lock_manager import ProcessLock

# --- CONFIGURATION ---
HEARTBEAT_DIR = Path("heartbeats")
MAX_SILENCE_SECONDS = 300  # 5 minutes
LOG_FILE = Path("logs/watchdog.log")
MAX_RESTARTS = 10  # Stop trying after this many consecutive restarts
BASE_BACKOFF = 30  # Base backoff seconds between restarts
MAX_BACKOFF = 600  # Maximum backoff (10 minutes)

# Process Name -> Restart Command
PYTHON_BIN = os.environ.get("OMEGA_PYTHON", "python3")
PROCESS_MAP = {
    "omega_manager": [PYTHON_BIN, "-u", "omega_manager.py"],
    "dashboard": [PYTHON_BIN, "-u", "dashboard.py"],
}

if os.environ.get("OMEGA_CLOUD_SYNC_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}:
    PROCESS_MAP["cloud_sync_service"] = [PYTHON_BIN, "-u", "cloud_sync_service.py"]

# Per-process restart tracking: {name: {"count": int, "last_restart": float}}
_restart_state = {}

def log(msg):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{timestamp}] {msg}"
    print(entry)
    with open(LOG_FILE, "a") as f:
        f.write(entry + "\n")

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
    try:
        subprocess.run(["pkill", "-f", f"python.*{name}.py"], check=False)
    except Exception as e:
        log(f"⚠️ Failed to kill {name}: {e}")
    time.sleep(1)

    # 2. Restart
    if name == "omega_manager":
        log_name = "logs/manager.log"
    elif name == "dashboard":
        log_name = "logs/dashboard.log"
    elif name == "cloud_sync_service":
        log_name = "logs/cloud_sync.log"
    else:
        log_name = f"logs/{name}.log"

    with open(log_name, "a") as out:
        subprocess.Popen(command, stdout=out, stderr=out)

    state["count"] += 1
    state["last_restart"] = time.time()
    log(f"✅ Restarted {name} (attempt {state['count']}/{MAX_RESTARTS}, next backoff: {min(BASE_BACKOFF * (2 ** state['count']), MAX_BACKOFF)}s)")

    # Touch heartbeat to give it time to boot
    (HEARTBEAT_DIR / f"{name}.beat").touch()

def monitor():
    log("🐶 Watchdog Active. Monitoring heartbeats...")
    HEARTBEAT_DIR.mkdir(exist_ok=True)

    while True:
        now = time.time()

        for name, command in PROCESS_MAP.items():
            beat_file = HEARTBEAT_DIR / f"{name}.beat"

            last_beat = beat_file.stat().st_mtime if beat_file.exists() else 0
            silence = now - last_beat

            # If silence > MAX and we expect it to be running
            if silence > MAX_SILENCE_SECONDS:
                restart_process(name, command)
            else:
                # Process is alive — reset restart counter
                state = _restart_state.get(name)
                if state and state["count"] > 0:
                    log(f"💚 {name} recovered after {state['count']} restart(s). Resetting counter.")
                    state["count"] = 0
                    state["last_restart"] = 0.0

        time.sleep(10)

if __name__ == "__main__":
    with ProcessLock("omega_watchdog"):
        monitor()
