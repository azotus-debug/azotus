import datetime
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Optional

from google.cloud import storage

logger = logging.getLogger("OmegaManager.GcsJobs")
from google.api_core import retry

from utils.circuit_breaker import get_breaker


def slugify(value: str) -> str:
    value = (value or "").strip().lower()
    if not value:
        return "job"
    value = re.sub(r"[^a-z0-9._-]+", "-", value)
    value = re.sub(r"-{2,}", "-", value)
    return value.strip("-._") or "job"


def new_job_id(stem: str) -> str:
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{slugify(stem)}-{ts}"


@dataclass(frozen=True)
class GcsJobPaths:
    bucket: str
    prefix: str
    job_id: str

    def _base(self) -> str:
        pfx = (self.prefix or "").strip("/ ")
        jid = (self.job_id or "").strip("/ ")
        return f"{pfx}/{jid}" if pfx else jid

    @property
    def job_blob(self) -> str:
        return f"{self._base()}/job.json"

    @property
    def skeleton_blob(self) -> str:
        return f"{self._base()}/skeleton.json"

    def job_json(self) -> str:
        return self.job_blob

    def skeleton_json(self) -> str:
        return self.skeleton_blob

    def termbook_json(self) -> str:
        return f"{self._base()}/termbook.json"

    def translation_checkpoint_json(self) -> str:
        return f"{self._base()}/translation_checkpoint.json"

    def translation_draft_json(self) -> str:
        return f"{self._base()}/translation_draft.json"

    def editor_report_json(self) -> str:
        return f"{self._base()}/editor_report.json"

    def approved_json(self) -> str:
        return f"{self._base()}/approved.json"

    def review_json(self) -> str:
        return f"{self._base()}/review.json"

    def review_token_json(self) -> str:
        return f"{self._base()}/review_token.json"

    def review_corrections_json(self) -> str:
        return f"{self._base()}/review_corrections.json"
    
    def reviewed_json(self) -> str:
        """Pattern used by review portal: {job_id}_REVIEWED.json"""
        return f"{self._base()}/{self.job_id}_REVIEWED.json"
        
    def review_status_json(self) -> str:
        """Status file written by review portal after approval"""
        return f"{self._base()}/review_status.json"

    def progress_json(self) -> str:
        return f"{self._base()}/progress.json"

    # =========================================================================
    # Multimodal (Azotus) Pipeline Paths
    # =========================================================================

    def proxy_blob(self) -> str:
        """360p vision proxy for Gemini analysis."""
        return f"{self._base()}/proxy_360p.mp4"

    def audio_blob(self) -> str:
        """Audio file for transcription/context caching."""
        return f"{self._base()}/audio.wav"

    def vision_scan_json(self) -> str:
        """Gemini Flash video analysis results."""
        return f"{self._base()}/vision_scan.json"

    def danger_zones_json(self) -> str:
        """Danger zones for subtitle positioning."""
        return f"{self._base()}/danger_zones.json"


def gcs_uri(bucket: str, blob_name: str) -> str:
    name = (blob_name or "").lstrip("/")
    return f"gs://{bucket}/{name}"


def blob_exists(client: storage.Client, bucket: str, blob_name: str) -> bool:
    breaker = get_breaker("gcs")
    if breaker.is_open():
        raise RuntimeError("GCS circuit breaker is open")
    try:
        exists = client.bucket(bucket).blob(blob_name).exists(client)
        breaker.record_success()
        return exists
    except Exception:
        breaker.record_failure()
        raise


from google.cloud.exceptions import NotFound

def _is_retryable(exc: Exception) -> bool:
    """Retry on all errors except 404 NotFound."""
    if isinstance(exc, NotFound):
        return False
    return True

@retry.Retry(predicate=_is_retryable)
def upload_json(
    client: storage.Client,
    *,
    bucket: str,
    blob_name: str,
    payload: Any,
    content_type: str = "application/json; charset=utf-8",
) -> None:
    breaker = get_breaker("gcs")
    if breaker.is_open():
        raise RuntimeError("GCS circuit breaker is open")
    data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    try:
        client.bucket(bucket).blob(blob_name).upload_from_string(data, content_type=content_type)
        breaker.record_success()
    except Exception:
        breaker.record_failure()
        raise


@retry.Retry(predicate=_is_retryable)
def download_json(client: storage.Client, *, bucket: str, blob_name: str) -> Any:
    breaker = get_breaker("gcs")
    if breaker.is_open():
        raise RuntimeError("GCS circuit breaker is open")
    try:
        raw = client.bucket(bucket).blob(blob_name).download_as_bytes()
        payload = json.loads(raw.decode("utf-8"))
        breaker.record_success()
        return payload
    except Exception:
        breaker.record_failure()
        raise


def try_download_json(client: storage.Client, *, bucket: str, blob_name: str) -> Optional[Any]:
    """Download JSON, returning None on failure (with logging)."""
    try:
        return download_json(client, bucket=bucket, blob_name=blob_name)
    except Exception as e:
        if "404" not in str(e):
             logger.warning(f"Failed to download gs://{bucket}/{blob_name}: {e}")
        return None


def upload_text(
    client: storage.Client,
    *,
    bucket: str,
    blob_name: str,
    text: str,
    content_type: str = "text/plain; charset=utf-8",
) -> None:
    breaker = get_breaker("gcs")
    if breaker.is_open():
        raise RuntimeError("GCS circuit breaker is open")
    try:
        client.bucket(bucket).blob(blob_name).upload_from_string(text or "", content_type=content_type)
        breaker.record_success()
    except Exception:
        breaker.record_failure()
        raise


def utc_iso_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def backoff_sleep(attempt: int, *, base_seconds: float = 1.7, cap_seconds: float = 45.0) -> None:
    """Standard exponential backoff for transient failures."""
    delay = min(cap_seconds, base_seconds ** max(1, attempt))
    time.sleep(delay)


def is_rate_limit_error(exc: Exception) -> bool:
    """
    Check if an exception is a rate limit / quota error.
    
    Vertex AI returns google.api_core.exceptions.ResourceExhausted for 429.
    """
    exc_str = str(exc).lower()
    exc_type = type(exc).__name__.lower()
    
    # Check for common rate limit indicators
    if "429" in exc_str or "resourceexhausted" in exc_type:
        return True
    if "quota" in exc_str or "rate" in exc_str:
        return True
    if "too many requests" in exc_str:
        return True
    return False


def rate_limit_backoff(attempt: int, *, base_seconds: float = 15.0, cap_seconds: float = 120.0) -> None:
    """
    Extended backoff for rate limit / quota errors.
    
    Uses longer delays (15s base, 120s cap) to let quota reset.
    For batch processing of 10+ programs, this prevents quota exhaustion.
    """
    delay = min(cap_seconds, base_seconds * max(1, attempt))
    time.sleep(delay)
