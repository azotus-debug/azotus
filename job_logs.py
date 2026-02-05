from __future__ import annotations

import logging
import threading
from collections import deque
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, List

import config


def _sanitize_job_id(job_id: str) -> str:
    value = (job_id or "").strip()
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in value)
    safe = safe.strip("._")
    return safe or "job"


def job_logs_dir() -> Path:
    log_dir = config.BASE_DIR / "logs" / "jobs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return log_dir


def job_log_path(job_id: str) -> Path:
    return job_logs_dir() / f"{_sanitize_job_id(job_id)}.log"


def tail_job_log(job_id: str, lines: int = 100) -> List[str]:
    path = job_log_path(job_id)
    if not path.exists():
        return []
    try:
        limit = max(1, min(int(lines), 2000))
    except Exception:
        limit = 100
    buffer = deque(maxlen=limit)
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            buffer.append(line.rstrip("\n"))
    return list(buffer)


class _ThreadFilter(logging.Filter):
    def __init__(self, thread_id: int) -> None:
        super().__init__()
        self._thread_id = thread_id

    def filter(self, record: logging.LogRecord) -> bool:
        return record.thread == self._thread_id


@contextmanager
def job_log_context(job_id: str) -> Iterable[None]:
    if not job_id:
        yield
        return

    handler = None
    root_logger = logging.getLogger()
    try:
        handler = logging.FileHandler(job_log_path(job_id), encoding="utf-8")
        handler.setLevel(logging.INFO)
        handler.setFormatter(
            logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")
        )
        handler.addFilter(_ThreadFilter(threading.get_ident()))
        root_logger.addHandler(handler)
    except Exception:
        handler = None

    try:
        yield
    finally:
        if handler:
            try:
                root_logger.removeHandler(handler)
            finally:
                handler.close()
