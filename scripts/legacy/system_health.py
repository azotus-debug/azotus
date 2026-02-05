import shutil
import os
import sys
from pathlib import Path

def check_disk_space(path=".", min_gb=10):
    """
    Checks if there is enough free disk space.
    Returns True if space is sufficient, False otherwise.
    """
    try:
        total, used, free = shutil.disk_usage(path)
        free_gb = free / (2**30)
        
        if free_gb < min_gb:
            print(f"   ❌ CRITICAL WARNING: Low Disk Space! {free_gb:.2f} GB free (Min: {min_gb} GB)")
            return False
        return True
    except Exception as e:
        print(f"   ⚠️ Error checking disk space: {e}")
        return True # Fail open if check fails

def update_heartbeat(process_name):
    """
    Touches a heartbeat file to indicate the process is alive.
    """
    try:
        beat_dir = Path("heartbeats")
        beat_dir.mkdir(exist_ok=True)
        beat_file = beat_dir / f"{process_name}.beat"
        beat_file.touch()
    except Exception:
        pass


def check_memory(min_mb=500):
    """
    Best-effort memory check. Returns True if available memory is above threshold.
    Fails open if system APIs are unavailable.
    """
    try:
        import psutil  # Optional dependency
    except Exception:
        return True
    try:
        avail_mb = psutil.virtual_memory().available / (1024 * 1024)
        if avail_mb < min_mb:
            print(f"   ❌ CRITICAL WARNING: Low Memory! {avail_mb:.0f} MB free (Min: {min_mb} MB)")
            return False
        return True
    except Exception:
        return True
