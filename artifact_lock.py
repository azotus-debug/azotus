import re
import time
import fcntl
from contextlib import contextmanager
from pathlib import Path


LOCK_DIR = Path("/tmp")
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _lock_path(job_id: str) -> Path:
    safe = _SAFE_NAME_RE.sub("_", str(job_id or "unknown"))
    return LOCK_DIR / f"omega_artifacts_{safe}.lock"


@contextmanager
def job_artifact_lock(job_id: str, timeout_seconds: float = 30.0, poll_seconds: float = 0.1):
    """
    Cross-process file lock for per-job artifact writes (approved JSON/SRT/video).
    Uses timeout to avoid dead-waiting when another worker is active.
    """
    lock_path = _lock_path(job_id)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "a+")
    deadline = time.time() + max(0.1, float(timeout_seconds))
    poll = max(0.01, float(poll_seconds))
    locked = False

    try:
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except BlockingIOError:
                if time.time() >= deadline:
                    raise TimeoutError(f"Timed out waiting for artifact lock: {lock_path.name}")
                time.sleep(poll)
        yield
    finally:
        try:
            if locked:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
