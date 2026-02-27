import datetime
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from lock_manager import ProcessLock

BASE_DIR = Path(__file__).resolve().parent
WATCHDOG_PID_FILE = Path("/tmp/omega_watchdog.pid")
SUPERVISOR_PID_FILE = Path("/tmp/omega_watchdog_supervisor.pid")
SUPERVISOR_LOG_FILE = BASE_DIR / "logs" / "watchdog_supervisor.log"
WATCHDOG_LOG_FILE = BASE_DIR / "logs" / "watchdog.log"

PYTHON_BIN = os.environ.get("OMEGA_PYTHON") or sys.executable
WATCHDOG_CMD = [PYTHON_BIN, "-u", str(BASE_DIR / "process_watchdog.py")]

CHECK_INTERVAL_SECONDS = 10
RESTART_BASE_BACKOFF = 5
RESTART_MAX_BACKOFF = 120

_stop_requested = False
_restart_failures = 0
_last_start_attempt = 0.0


def _log(message: str) -> None:
    SUPERVISOR_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {message}"
    print(line)
    with open(SUPERVISOR_LOG_FILE, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _read_pid(path: Path) -> Optional[int]:
    try:
        raw = path.read_text(encoding="utf-8").strip()
        if not raw:
            return None
        return int(raw)
    except Exception:
        return None


def _find_running_watchdog() -> Optional[int]:
    pid = _read_pid(WATCHDOG_PID_FILE)
    if pid and _pid_alive(pid):
        return pid
    if pid and not _pid_alive(pid):
        try:
            WATCHDOG_PID_FILE.unlink()
        except Exception:
            pass

    try:
        proc = subprocess.run(
            ["pgrep", "-f", "process_watchdog.py"],
            check=False,
            capture_output=True,
            text=True,
        )
        for line in (proc.stdout or "").splitlines():
            try:
                candidate = int(line.strip())
            except Exception:
                continue
            if candidate and _pid_alive(candidate):
                WATCHDOG_PID_FILE.write_text(str(candidate), encoding="utf-8")
                return candidate
    except Exception:
        return None
    return None


def _start_watchdog() -> bool:
    global _restart_failures, _last_start_attempt
    now = time.time()
    backoff = min(RESTART_BASE_BACKOFF * (2 ** _restart_failures), RESTART_MAX_BACKOFF)
    if _last_start_attempt and now - _last_start_attempt < backoff:
        return False
    _last_start_attempt = now

    WATCHDOG_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(WATCHDOG_LOG_FILE, "a", encoding="utf-8") as log_handle:
            proc = subprocess.Popen(WATCHDOG_CMD, stdout=log_handle, stderr=log_handle, cwd=str(BASE_DIR))
        WATCHDOG_PID_FILE.write_text(str(proc.pid), encoding="utf-8")
        _restart_failures += 1
        _log(
            f"Started watchdog PID {proc.pid} "
            f"(attempt {_restart_failures}, next backoff {min(RESTART_BASE_BACKOFF * (2 ** _restart_failures), RESTART_MAX_BACKOFF)}s)"
        )
        return True
    except Exception as exc:
        _log(f"Failed to start watchdog: {exc}")
        return False


def _handle_signal(signum, frame) -> None:
    global _stop_requested
    _stop_requested = True
    _log(f"Received signal {signum}; stopping watchdog supervisor")


def monitor() -> None:
    global _restart_failures
    _log("Watchdog supervisor active")
    SUPERVISOR_PID_FILE.write_text(str(os.getpid()), encoding="utf-8")

    while not _stop_requested:
        pid = _find_running_watchdog()
        if pid:
            if _restart_failures:
                _log(f"Watchdog healthy (PID {pid}); reset restart counter")
                _restart_failures = 0
        else:
            _start_watchdog()
        time.sleep(CHECK_INTERVAL_SECONDS)

    try:
        SUPERVISOR_PID_FILE.unlink()
    except Exception:
        pass


if __name__ == "__main__":
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    with ProcessLock("omega_watchdog_supervisor"):
        monitor()
