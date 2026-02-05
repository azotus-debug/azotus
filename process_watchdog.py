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

# Process Name -> Restart Command
PYTHON_BIN = os.environ.get("OMEGA_PYTHON", "python3")
PROCESS_MAP = {
    "omega_manager": [PYTHON_BIN, "-u", "omega_manager.py"],
    "dashboard": [PYTHON_BIN, "-u", "dashboard.py"],
}

if os.environ.get("OMEGA_CLOUD_SYNC_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}:
    PROCESS_MAP["cloud_sync_service"] = [PYTHON_BIN, "-u", "cloud_sync_service.py"]

def log(msg):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{timestamp}] {msg}"
    print(entry)
    with open(LOG_FILE, "a") as f:
        f.write(entry + "\n")

def restart_process(name, command):
    log(f"🚨 DEAD PROCESS DETECTED: {name}. Restarting...")
    
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
    else:
        log_name = "logs/unknown.log"

    with open(log_name, "a") as out:
        subprocess.Popen(command, stdout=out, stderr=out)
    
    log(f"✅ Restarted {name}")
    
    # Touch heartbeat to give it time to boot
    (HEARTBEAT_DIR / f"{name}.beat").touch()

def monitor():
    log("🐶 Watchdog Active. Monitoring heartbeats...")
    HEARTBEAT_DIR.mkdir(exist_ok=True)
    
    while True:
        now = time.time()
        
        for name, command in PROCESS_MAP.items():
            beat_file = HEARTBEAT_DIR / f"{name}.beat"
            
            if not beat_file.exists():
                # First run or deleted? Give it a pass if it's running?
                # For now, assume if file missing, it's dead.
                # But wait, if we just started, file might not exist yet.
                # Let's check if process is running via ps?
                # Simpler: Just restart if missing for > 60s (logic below handles missing file as old time 0)
                pass
            
            last_beat = beat_file.stat().st_mtime if beat_file.exists() else 0
            silence = now - last_beat
            
            # If silence > MAX and we expect it to be running
            if silence > MAX_SILENCE_SECONDS:
                # Double check if it's actually running? 
                # No, if it's running but not beating, it's HUNG. Kill it.
                restart_process(name, command)
        
        time.sleep(10)

if __name__ == "__main__":
    with ProcessLock("omega_watchdog"):
        monitor()
