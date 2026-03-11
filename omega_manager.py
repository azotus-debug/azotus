import time
import os
import sys
import json
import logging
import traceback
import shutil
import subprocess
import secrets
from contextlib import nullcontext
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, List
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

import config
import omega_db
import system_health
from gcp_auth import ensure_google_application_credentials
from gcs_jobs import GcsJobPaths, new_job_id, upload_json, download_json, blob_exists
from email_utils import send_email
from cloud_run_jobs import run_cloud_run_job
from lock_manager import ProcessLock
from concurrent.futures import ThreadPoolExecutor
from google.cloud import storage
from job_logs import job_log_context
from transition_service import execute_transition, apply_transition
from state_machine import legacy_to_new, can_transition
from artifact_lock import job_artifact_lock
from workers.text_sanitizer import detect_likely_t_escape_corruption

# --- Circuit Breaker for DB Errors ---
_DB_ERROR_PATTERNS = ("connection", "timeout", "operational", "closed", "reset by peer")
_MANAGER_DB_FAILURE_THRESHOLD = int(os.environ.get("OMEGA_MANAGER_DB_FAILURE_THRESHOLD", "3"))
_MANAGER_DB_COOLDOWN_SECONDS = float(os.environ.get("OMEGA_MANAGER_DB_COOLDOWN_SECONDS", "300"))


def _is_db_error(exc: Exception) -> bool:
    msg = str(exc or "").lower()
    if any(p in msg for p in _DB_ERROR_PATTERNS):
        return True
    cause = getattr(exc, "__cause__", None) or getattr(exc, "__context__", None)
    if cause and cause is not exc:
        return _is_db_error(cause)
    return False


# Import Workers
from workers import transcriber, editor, finalizer, publisher
from workers import audio_clipper, review_notifier

# Multimodal (Azotus) workers - imported lazily when enabled
# from workers import proxy_generator, vision_scanner, ocr_layout

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("logs/manager.log"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("OmegaManager")

# Active Task Registry to prevent duplicate submissions
active_tasks = set()

# Failure tracking for backoff
failure_counts = {}

# Thread lock for concurrent access to active_tasks and failure_counts
import threading
_task_lock = threading.Lock()

# Graceful shutdown event — set by signal handler, checked by main loop
_shutdown_event = threading.Event()

MAX_TASK_FAILURES = 3  # Reduced from 5 to limit runaway costs (was allowing 5 full restarts = 5x cost)

# --- Thread-safe helpers for active_tasks ---
def _is_task_active(stem: str) -> bool:
    """Thread-safe check if a task is currently active."""
    with _task_lock:
        return stem in active_tasks

def _add_task(stem: str) -> bool:
    """Thread-safe add to active_tasks. Returns True if added, False if already present."""
    with _task_lock:
        if stem in active_tasks:
            return False
        active_tasks.add(stem)
        return True

def _remove_task(stem: str):
    """Thread-safe remove from active_tasks."""
    with _task_lock:
        active_tasks.discard(stem)

def _is_in_cooldown(stem: str) -> bool:
    """Thread-safe cooldown check."""
    with _task_lock:
        if stem not in failure_counts:
            return False
        count, last_fail = failure_counts[stem]
        backoff = min(2 ** count, 60)
        return time.time() - last_fail < backoff

def _safe_float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except Exception:
        return default


def _load_approved_segments(approved_path: Path) -> list:
    with open(approved_path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, dict):
        segments = payload.get("segments", [])
        return segments if isinstance(segments, list) else []
    return payload if isinstance(payload, list) else []


def _ensure_text_integrity(
    *,
    stem: str,
    approved_path: Path,
    target_language: str,
) -> dict:
    if not approved_path.exists():
        return {"enabled": False, "suspicious": False, "reason": "approved-missing"}

    segments = _load_approved_segments(approved_path)
    report = detect_likely_t_escape_corruption(
        segments,
        target_language=target_language,
    )
    if report.get("suspicious"):
        text_stats = report.get("text", {})
        t_ratio = float(text_stats.get("t_ratio") or 0.0)
        letters = int(text_stats.get("letter_count") or 0)
        reason = report.get("reason") or "likely tab-escape corruption"
        raise RuntimeError(
            f"Text integrity gate failed for {stem}: {reason} "
            f"(t_ratio={t_ratio:.6f}, letters={letters})."
        )
    return report

INGEST_STALL_SECONDS = _safe_float_env("OMEGA_INGEST_STALL_SECONDS", 1800.0)
INGEST_STABILITY_CHECKS = int(os.environ.get("OMEGA_INGEST_STABILITY_CHECKS", "3") or 3)
INGEST_STABILITY_DELAY = _safe_float_env("OMEGA_INGEST_STABILITY_DELAY", 1.0)
INGEST_MIN_AGE_SECONDS = _safe_float_env("OMEGA_INGEST_MIN_AGE", 3.0)
INGEST_RETRY_RECOVERY_WINDOW_SECONDS = _safe_float_env("OMEGA_INGEST_RETRY_RECOVERY_WINDOW_SECONDS", 21600.0)
RESTART_FLAG = config.BASE_DIR / "heartbeats" / "omega_manager.restart"
RESTART_FORCE_FLAG = config.BASE_DIR / "heartbeats" / "omega_manager.restart.force"

STAGE_STALL_THRESHOLDS = {
    "TRANSCRIBED": _safe_float_env("OMEGA_STALL_TRANSCRIBED", 7200.0),
    "TRANSLATING": _safe_float_env("OMEGA_STALL_TRANSLATING", 5400.0),
    "TRANSLATING_CLOUD_SUBMITTED": _safe_float_env("OMEGA_STALL_CLOUD_SUBMITTED", 5400.0),
    "CLOUD_TRANSLATING": _safe_float_env("OMEGA_STALL_CLOUD", 1800.0),
    "CLOUD_REVIEWING": _safe_float_env("OMEGA_STALL_CLOUD_REVIEWING", 7200.0),
    "REVIEWED": _safe_float_env("OMEGA_STALL_REVIEWED", 10800.0),
    "REVIEWING": _safe_float_env("OMEGA_STALL_REVIEWING", 10800.0),
    "FINALIZING": _safe_float_env("OMEGA_STALL_FINALIZING", 10800.0),
    "BURNING": _safe_float_env("OMEGA_STALL_BURNING", 21600.0),
}
CLOUD_STALL_RETRY_COOLDOWN_SECONDS = _safe_float_env("OMEGA_CLOUD_STALL_RETRY_COOLDOWN_SECONDS", 300.0)


def _cloud_pipeline_enabled() -> bool:
    return str(os.environ.get("OMEGA_CLOUD_PIPELINE", "1")).strip().lower() in {"1", "true", "yes", "on"}

def _is_truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _requires_validation_bypass(from_stage: Optional[str], to_stage: str) -> bool:
    """Return True when a transition is outside the enforced state machine."""
    from_raw = from_stage if str(from_stage or "").strip() else "QUEUED"
    try:
        from_enum, _ = legacy_to_new(str(from_raw))
        to_enum, _ = legacy_to_new(str(to_stage))
        return not can_transition(from_enum, to_enum)
    except Exception:
        return True


def _multimodal_enabled() -> bool:
    """Check if multimodal (Azotus) pipeline is enabled."""
    return config.OMEGA_MULTIMODAL_ENABLED


def _proxy_required() -> bool:
    """Generate video proxy when cloud pipeline or multimodal features are enabled."""
    return _cloud_pipeline_enabled() or _multimodal_enabled()

def _run_multimodal_pipeline(video_path: Path, job_id: str, bucket_name: str, prefix: str, original_stem: str = None, job_ids: Optional[List[str]] = None) -> dict:
    """
    Run the multimodal (Azotus) pipeline if enabled.

    Steps:
    1. Generate 360p vision proxy
    2. Upload proxy to GCS
    3. Run Gemini Flash vision scan
    4. Detect danger zones
    5. Upload results to GCS

    Args:
        video_path: Path to the video file
        job_id: Full job ID (with timestamp)
        bucket_name: GCS bucket name
        prefix: GCS prefix for job artifacts
        original_stem: Original video file stem (without timestamp). Used for danger zones
                      file so all language tracks for the same video share the same file.
        job_ids: Optional list of job IDs to upload the proxy under (multi-language tracks).

    Returns dict with paths/URIs to generated artifacts, or empty dict if disabled.
    """
    # Use original_stem for local files (shared across language tracks)
    # Fall back to job_id if original_stem not provided
    local_file_stem = original_stem or job_id
    if not _proxy_required():
        return {}

    run_vision = _multimodal_enabled()

    try:
        from workers import proxy_generator, vision_scanner, ocr_layout
        from gcs_jobs import GcsJobPaths

        logger.info(f"🎬 Multimodal pipeline starting for {job_id}")

        # Step 1: Generate vision proxy
        omega_db.update_job_via_track(job_id, status="Generating vision proxy", progress=5.0)
        vision_proxy_path = proxy_generator.generate_vision_proxy(video_path, job_id)

        # Step 2: Upload proxy to GCS
        paths = GcsJobPaths(bucket=bucket_name, prefix=prefix, job_id=job_id)
        proxy_gcs_uri = proxy_generator.upload_vision_proxy_to_gcs(
            vision_proxy_path, bucket_name, paths.proxy_blob()
        )

        upload_job_ids = job_ids or []
        for extra_job_id in upload_job_ids:
            if extra_job_id == job_id:
                continue
            extra_paths = GcsJobPaths(bucket=bucket_name, prefix=prefix, job_id=extra_job_id)
            proxy_generator.upload_vision_proxy_to_gcs(
                vision_proxy_path, bucket_name, extra_paths.proxy_blob()
            )

        if not run_vision:
            logger.info("👁️ Multimodal disabled: skipping vision scan and danger zones")
            return {
                "vision_proxy_path": str(vision_proxy_path),
                "vision_proxy_gcs": proxy_gcs_uri,
                "original_stem": local_file_stem,
            }

        # Step 3: Run vision scan
        omega_db.update_job_via_track(job_id, status="Running vision scan", progress=10.0)
        # Pass job_id for DB updates, local_file_stem for local file naming (shared across language tracks)
        vision_scan = vision_scanner.scan_video(
            proxy_gcs_uri, job_id, output_dir=config.VAULT_DATA, file_stem=local_file_stem
        )

        # Upload vision scan to GCS
        vision_scanner.upload_vision_scan_to_gcs(vision_scan, bucket_name, paths.vision_scan_json())

        # Step 4: Detect danger zones
        duration = proxy_generator.get_video_duration(video_path)
        omega_db.update_job_via_track(job_id, status="Detecting danger zones", progress=12.0)
        danger_zones = ocr_layout.detect_danger_zones(
            vision_scan, duration, job_id, output_dir=config.VAULT_DATA, file_stem=local_file_stem
        )

        # Upload danger zones to GCS
        ocr_layout.upload_danger_zones_to_gcs(danger_zones, bucket_name, paths.danger_zones_json())

        logger.info(f"✅ Multimodal pipeline complete for {job_id}")

        return {
            "vision_proxy_path": str(vision_proxy_path),
            "vision_proxy_gcs": proxy_gcs_uri,
            "vision_scan_path": str(config.VAULT_DATA / f"{local_file_stem}_VISION_SCAN.json"),
            "danger_zones_path": str(config.VAULT_DATA / f"{local_file_stem}_DANGER_ZONES.json"),
            "original_stem": local_file_stem,
            "shots": len(vision_scan.get("shots", [])),
            "danger_zones": len(danger_zones),
        }

    except Exception as e:
        logger.warning(f"⚠️ Multimodal pipeline failed for {job_id}: {e}")
        logger.warning("   Continuing without multimodal features")
        return {"error": str(e)}

def _review_portal_url() -> str:
    return str(os.environ.get("OMEGA_REVIEW_PORTAL_URL", "") or "").strip()

def _reviewer_emails(meta: dict) -> list[str]:
    if isinstance(meta, dict):
        value = meta.get("reviewer_email")
        if value:
            return [v.strip() for v in str(value).replace(";", ",").split(",") if v.strip()]
    value = os.environ.get("OMEGA_REVIEWER_EMAIL", "")
    return [v.strip() for v in str(value).replace(";", ",").split(",") if v.strip()]

def _parse_iso(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        # Convert to naive UTC for comparison with datetime.now()
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        return dt
    except Exception:
        return None

def _stage_started_at(meta: dict, stage: str) -> Optional[datetime]:
    if not isinstance(meta, dict) or not stage:
        return None
    timeline = meta.get("stage_timeline")
    if not isinstance(timeline, list):
        return None
    for item in reversed(timeline):
        if not isinstance(item, dict):
            continue
        if str(item.get("stage", "")).upper() != stage.upper():
            continue
        started_at = item.get("started_at")
        parsed = _parse_iso(started_at) if isinstance(started_at, str) else None
        if parsed:
            return parsed
    return None

def _status_is_blocked(status: str) -> bool:
    if not status:
        return False
    lowered = status.lower()
    if "waiting" in lowered:
        return True
    if "blocked" in lowered:
        return True
    if "paused" in lowered:
        return True
    return False

def _request_manager_restart(force: bool = False) -> None:
    try:
        RESTART_FLAG.parent.mkdir(exist_ok=True)
        RESTART_FLAG.touch()
        if force:
            RESTART_FORCE_FLAG.touch()
    except Exception as e:
        logger.debug(f"Failed to create restart flag: {e}")

def _find_vault_video(stem: str) -> Optional[Path]:
    try:
        candidates = sorted(config.VAULT_VIDEOS.glob(f"{stem}.*"))
    except Exception:
        candidates = []
    for candidate in candidates:
        if candidate.is_file() and not candidate.name.startswith("._"):
            return candidate
    return None


def _is_stable_file(path: Path) -> bool:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return False
    if INGEST_MIN_AGE_SECONDS and (time.time() - stat.st_mtime) < INGEST_MIN_AGE_SECONDS:
        return False
    size = stat.st_size
    checks = max(1, int(INGEST_STABILITY_CHECKS))
    for _ in range(checks - 1):
        time.sleep(max(0.1, INGEST_STABILITY_DELAY))
        try:
            if path.stat().st_size != size:
                return False
        except FileNotFoundError:
            return False
    return True

def _cloud_job_paths(meta: dict) -> tuple[Optional[GcsJobPaths], Optional[str], Optional[str]]:
    if not isinstance(meta, dict):
        return None, None, None
    cloud_job_id = meta.get("cloud_job_id") or meta.get("gcs_job_id")
    if not cloud_job_id:
        return None, None, None
    bucket_name = str(meta.get("cloud_bucket") or config.OMEGA_JOBS_BUCKET).strip()
    prefix = str(meta.get("cloud_prefix") or config.OMEGA_JOBS_PREFIX).strip()
    return GcsJobPaths(bucket=bucket_name, prefix=prefix, job_id=str(cloud_job_id)), bucket_name, prefix

def _trigger_review_portal(stem: str, meta: dict, job: dict) -> bool:
    """
    Trigger human review portal workflow if enabled.
    
    Generates audio clips, uploads to GCS, and sends email notification.
    Returns True if review was triggered (job should wait for approval).
    """
    # Check if review portal is enabled
    if not _is_truthy(os.environ.get("OMEGA_REVIEW_PORTAL_ENABLED", "0")):
        return False
    
    # Check if this job requires human review (from source path)
    source_path = str(meta.get("source_path") or "").lower()
    if "/02_human_review/" not in source_path:
        return False
    
    # Already in review?
    if meta.get("review_notification_sent"):
        return True  # Wait for approval
    
    logger.info(f"🔍 Triggering human review for: {stem}")
    
    # Get necessary paths (prefer stored vault path / original filename)
    video_path = None
    vault_path = meta.get("vault_path") if isinstance(meta, dict) else None
    if vault_path:
        candidate = Path(str(vault_path))
        if candidate.exists():
            video_path = candidate
    if video_path is None:
        original_filename = meta.get("original_filename") if isinstance(meta, dict) else None
        if original_filename:
            candidate = config.VAULT_VIDEOS / original_filename
            if candidate.exists():
                video_path = candidate
    if video_path is None:
        original_stem = meta.get("original_stem") if isinstance(meta, dict) else None
        video_path = _find_vault_video(original_stem or stem)
    skeleton_path = config.find_skeleton(stem)

    # Get cloud job info
    paths, bucket_name, prefix = _cloud_job_paths(meta)
    cloud_job_id = meta.get("cloud_job_id") or meta.get("gcs_job_id")

    if not all([video_path, skeleton_path, cloud_job_id]):
        logger.warning(f"   ⚠️ Missing files for review: video={video_path}, skeleton={bool(skeleton_path)}")
        return False
    
    try:
        # Generate and upload audio clips
        audio_clipper.prepare_review_clips(
            video_path=video_path,
            skeleton_path=skeleton_path,
            bucket_name=bucket_name,
            job_prefix=prefix,
            job_id=cloud_job_id
        )
        
        # Get reviewer email
        target_lang = str(job.get("target_language") or meta.get("target_language") or "is").upper()
        reviewer_email = review_notifier.get_reviewer_for_language(target_lang)
        
        if reviewer_email:
            # Get quality rating from editor report
            report = job.get("editor_report")
            quality_rating = None
            if report:
                try:
                    report_data = json.loads(report) if isinstance(report, str) else report
                    quality_rating = report_data.get("rating")
                except Exception as e:
                    logger.debug(f"Failed to parse editor report for quality rating: {e}")
            
            # Send notification
            review_notifier.send_review_notification(
                job_id=cloud_job_id,
                program_name=stem,
                target_language=target_lang,
                reviewer_email=reviewer_email,
                quality_rating=quality_rating
            )
        
        # Update job status
        omega_db.update_job_via_track(
            stem,
            status="Awaiting Human Review",
            meta={
                "review_notification_sent": True,
                "review_portal_job_id": cloud_job_id,
                "review_requested_at": datetime.now().isoformat(),
            }
        )
        
        logger.info(f"   ✅ Review portal triggered for {stem}")
        return True
        
    except Exception as e:
        logger.error(f"   ❌ Failed to trigger review portal: {e}")
        return False


def _build_review_payload(
    *,
    stem: str,
    approved_path: Path,
    target_language: str,
    program_profile: str,
) -> dict:
    with open(approved_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    segments = data.get("segments", data) if isinstance(data, dict) else data
    payload_segments = []
    for seg in segments or []:
        seg_id = seg.get("id")
        if seg_id is None:
            continue
        seg_id = str(seg_id)
        payload_segments.append(
            {
                "id": seg_id,
                "start": seg.get("start"),
                "end": seg.get("end"),
                "source": seg.get("source_text") or seg.get("source") or "",
                "translation": seg.get("text") or "",
            }
        )
    return {
        "stem": stem,
        "target_language": target_language,
        "program_profile": program_profile,
        "created_at": datetime.utcnow().isoformat() + "Z",
        "segments": payload_segments,
    }

def _send_review_email(*, stem: str, review_url: str, recipients: list[str]) -> bool:
    subject = f"Omega Review Needed: {stem}"
    body = (
        f"Hello!\\n\\n"
        f"A translation is ready for your review. Please open the link below, edit any lines that need fixing, and click Submit.\\n\\n"
        f"{review_url}\\n\\n"
        f"Thank you!\\n"
    )
    return send_email(subject=subject, body=body, to_addrs=recipients)

def _apply_remote_corrections(*, approved_path: Path, corrections: list[dict]) -> tuple[int, int]:
    if not corrections:
        return 0, 0
    with open(approved_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    segments = data.get("segments", data) if isinstance(data, dict) else data
    if not isinstance(segments, list):
        return 0, 0

    correction_map: dict[str, str] = {}
    comment_count = 0
    for item in corrections:
        if not isinstance(item, dict):
            continue
        seg_id = item.get("id")
        if seg_id is None:
            continue
        seg_id = str(seg_id)
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            correction_map[seg_id] = text.strip()
        comment = item.get("comment")
        if isinstance(comment, str) and comment.strip():
            comment_count += 1

    applied = 0
    for seg in segments:
        seg_id = seg.get("id")
        if seg_id is None:
            continue
        seg_id = str(seg_id)
        if seg_id in correction_map:
            seg["text"] = correction_map[seg_id]
            applied += 1

    if isinstance(data, dict):
        data["segments"] = segments

    with open(approved_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    return applied, comment_count

def _is_hidden_artifact(path: Path) -> bool:
    name = path.name
    return name.startswith("._") or name.startswith(".")

def _is_transient_error(error: Exception) -> bool:
    """
    Detect transient errors that should auto-retry with less penalty.
    These are typically network issues, rate limits, or temporary service problems.
    """
    error_str = str(error).lower()
    transient_patterns = [
        "timeout", "timed out",
        "connection reset", "connection refused", "connection error",
        "network", "socket",
        "rate limit", "ratelimit", "429", "too many requests",
        "503", "service unavailable",
        "502", "bad gateway",
        "500", "internal server error",
        "temporary", "transient",
        "retry", "retryable",
        "deadline exceeded",
        "quota exceeded",  # Often temporary
        "resource exhausted",
    ]
    for pattern in transient_patterns:
        if pattern in error_str:
            return True
    # Check exception type
    error_type = type(error).__name__.lower()
    if any(t in error_type for t in ["timeout", "connection", "network", "socket"]):
        return True
    return False


def task_wrapper(stem, task_name, func, *args, **kwargs):
    """
    Wraps a worker function to handle active_tasks cleanup, error logging, and backoff.
    Transient errors (network, rate limits) get automatic retry with minimal penalty.
    """
    log_context = nullcontext() if task_name == "Ingest" else job_log_context(stem)
    with log_context:
        try:
            logger.info(f"🚀 Starting Async Task: {task_name} for {stem}")
            func(*args, **kwargs)

            # Success: Reset failure count
            with _task_lock:
                if stem in failure_counts:
                    del failure_counts[stem]

        except Exception as e:
            is_transient = _is_transient_error(e)
            error_str = str(e)
            short_error = error_str if len(error_str) <= 180 else error_str[:177] + "..."
            traceback_str = traceback.format_exc()

            if is_transient:
                # Transient error: Log at warning level, minimal backoff, don't count toward failure limit
                logger.warning(f"⚠️ Transient error in {task_name} for {stem}: {short_error}")
                with _task_lock:
                    # Use separate counter for transient errors (doesn't count toward permanent failure)
                    transient_count = failure_counts.get(stem, (0, 0))[0]
                    # Only increment by 0.5 for transient errors (takes twice as many to reach limit)
                    failure_counts[stem] = (transient_count + 0.5, time.time())
                # Short backoff for transient errors (10-30 seconds)
                backoff = min(10 + transient_count * 5, 30)
                try:
                    omega_db.update_job_via_track(
                        stem,
                        status=f"Transient error (auto-retry in {backoff}s): {short_error}",
                        meta={
                            "last_error": short_error,
                            "error_type": "transient",
                            "failed_at": datetime.now().isoformat(),
                            "traceback": traceback_str,
                        },
                    )
                except Exception as db_err:
                    logger.warning(f"DB update failed in error handler for {stem}: {db_err}")
            else:
                # Permanent error: Full backoff and failure counting
                logger.error(f"❌ Async Task Failed ({task_name}): {e}")
                with _task_lock:
                    count = failure_counts.get(stem, (0, 0))[0] + 1
                    failure_counts[stem] = (count, time.time())

                # Calculate backoff (2^count, max 60s)
                backoff = min(2 ** int(count), 60)

                if count > MAX_TASK_FAILURES:
                    logger.error(f"🛑 Job {stem} failed {int(count)} times. Halting until manual intervention.")
                    try:
                        current_job = omega_db.get_job_via_track(stem)
                        current_stage = (current_job.get("stage") or "UNKNOWN") if current_job else "UNKNOWN"
                        apply_transition(
                            job_id=stem,
                            job_stem=stem,
                            from_stage=current_stage,
                            to_stage="DEAD",
                            worker_id="omega_manager",
                            reason=f"Permanent failure after {int(count)} retries: {short_error}",
                            skip_validation=_requires_validation_bypass(current_stage, "DEAD"),
                            status=f"DEAD: {short_error}",
                            progress=0,
                            meta={
                                "halted": True,
                                "halt_reason": short_error,
                                "last_error": short_error,
                                "error_type": "permanent",
                                "failed_at": datetime.now().isoformat(),
                                "traceback": traceback_str,
                            },
                        )
                    except Exception as te:
                        logger.warning(f"Transition to DEAD failed for {stem}: {te}")
                else:
                    logger.warning(f"⚠️ Job {stem} failed {int(count)} times. Backing off for {backoff}s...")
                    try:
                        fail_job = omega_db.get_job_via_track(stem)
                        fail_stage = (fail_job.get("stage") or "UNKNOWN") if fail_job else "UNKNOWN"
                        apply_transition(
                            job_id=stem,
                            job_stem=stem,
                            from_stage=fail_stage,
                            to_stage="FAILED",
                            worker_id="omega_manager",
                            reason=f"Error (Retry {int(count)}/{MAX_TASK_FAILURES}): {short_error}",
                            skip_validation=_requires_validation_bypass(fail_stage, "FAILED"),
                            status=f"Error (Retry {int(count)}/{MAX_TASK_FAILURES}): {short_error}",
                            progress=0,
                            meta={
                                "last_error": short_error,
                                "error_type": "permanent",
                                "failed_at": datetime.now().isoformat(),
                                "traceback": traceback_str,
                            },
                        )
                    except Exception as db_err:
                        logger.warning(f"Transition to FAILED failed for {stem}: {db_err}")

        finally:
            logger.info(f"🏁 Finished Async Task: {task_name} for {stem}")
            _remove_task(stem)

def ingest_new_files(executor):
    """
    Scans INBOX for new video files.
    """
    EXTENSIONS = {".mp3", ".wav", ".mp4", ".m4a", ".mov", ".mkv", ".mpg", ".mpeg", ".moc", ".mxf"}
    
    WATCH_MAP = {
        # Root Inbox (Default to Auto/RUV_BOX)
        config.INBOX_DIR: ("AUTO", "RUV_BOX"),
        # Auto Pilot
        config.INBOX_DIR / "01_AUTO_PILOT" / "Classic": ("AUTO", "RUV_BOX"),
        config.INBOX_DIR / "01_AUTO_PILOT" / "Modern_Look": ("AUTO", "Modern"),
        config.INBOX_DIR / "01_AUTO_PILOT" / "Apple_TV": ("AUTO", "Apple"),
        # Manual Review
        config.INBOX_DIR / "02_HUMAN_REVIEW" / "Classic": ("REVIEW", "RUV_BOX"),
        config.INBOX_DIR / "02_HUMAN_REVIEW" / "Modern_Look": ("REVIEW", "Modern"),
        config.INBOX_DIR / "02_HUMAN_REVIEW" / "Apple_TV": ("REVIEW", "Apple"),
        # Remote Review (email reviewer)
        config.INBOX_DIR / "03_REMOTE_REVIEW" / "Classic": ("REMOTE_REVIEW", "RUV_BOX"),
        config.INBOX_DIR / "03_REMOTE_REVIEW" / "Modern_Look": ("REMOTE_REVIEW", "Modern"),
        config.INBOX_DIR / "03_REMOTE_REVIEW" / "Apple_TV": ("REMOTE_REVIEW", "Apple"),
        # STAGED MODE: Transcribe only, wait for user to configure
        config.STAGE_DIR: ("STAGED", "RUV_BOX"),
    }
    
    for folder, (mode, style) in WATCH_MAP.items():
        if not folder.exists(): continue
        
        for file_path in folder.iterdir():
            if file_path.name.startswith("."): continue
            if file_path.suffix.lower() in EXTENSIONS:
                # Stability Check
                if not _is_stable_file(file_path):
                    continue

                stem = file_path.stem
                if _is_task_active(stem):
                    logger.debug(f"⚠️ Skipping {stem}: Already active")
                    continue 

                # Check for sidecar JSON (manual settings from wizard)
                sidecar_path = file_path.with_suffix(".json")
                sidecar_meta = {}
                if sidecar_path.exists() and _is_stable_file(sidecar_path):
                    try:
                        with open(sidecar_path, "r", encoding="utf-8") as f:
                            sidecar_meta = json.load(f)
                        logger.info(f"📋 Found sidecar metadata for {stem}")
                    except Exception as e:
                        logger.error(f"❌ Failed to read sidecar {sidecar_path}: {e}")

                logger.info(f"📥 Found Candidate: {file_path.name} in {folder}")

                # Validate video file before processing
                from utils.video_validator import validate_video
                is_valid, error_message = validate_video(file_path)
                if not is_valid:
                    logger.error(f"❌ Invalid video rejected: {file_path.name} - {error_message}")
                    # Move to rejected folder instead of deleting
                    rejected_dir = config.INBOX_DIR / "REJECTED"
                    rejected_dir.mkdir(exist_ok=True)
                    rejected_path = rejected_dir / f"{stem}_INVALID.txt"
                    rejected_path.write_text(f"File: {file_path.name}\nReason: {error_message}\nRejected at: {datetime.now().isoformat()}")
                    # Move video to rejected folder
                    try:
                        file_path.rename(rejected_dir / file_path.name)
                    except Exception as e:
                        logger.error(f"Could not move rejected file: {e}")
                    continue

                # Mark as active (thread-safe)
                if not _add_task(stem):
                    continue  # Already added by another thread

                # Submit to ThreadPool
                executor.submit(task_wrapper, stem, "Ingest", _run_ingest, file_path, mode, style, sidecar_meta)

    # =========================================================================
    # DROP ZONE SCANNING (Workflow Architecture Phase 4)
    # =========================================================================
    # Scan /0_DROPZONE/* subfolders for videos and apply recipe settings
    if config.DROPZONE_DIR.exists():
        for dropzone_folder in config.DROPZONE_DIR.iterdir():
            if not dropzone_folder.is_dir():
                continue
            if dropzone_folder.name.startswith("."):
                continue
            
            # Look up recipe by folder name
            recipe = omega_db.get_dropzone_recipe(folder_name=dropzone_folder.name)
            if not recipe:
                # No recipe found for this folder - skip silently
                continue
            
            # Scan for video files in this dropzone
            for file_path in dropzone_folder.iterdir():
                if file_path.name.startswith("."):
                    continue
                if file_path.suffix.lower() not in EXTENSIONS:
                    continue
                
                # Stability check
                if not _is_stable_file(file_path):
                    continue
                
                stem = file_path.stem
                if _is_task_active(stem):
                    continue
                
                logger.info(f"📦 Drop Zone: {file_path.name} in [{dropzone_folder.name}]")
                
                # Validate video
                from utils.video_validator import validate_video
                is_valid, error_message = validate_video(file_path)
                if not is_valid:
                    logger.error(f"❌ Invalid video rejected: {file_path.name} - {error_message}")
                    continue
                
                # Build recipe metadata to pass to _run_ingest
                recipe_meta = {
                    "dropzone_recipe_id": recipe.get("id"),
                    "dropzone_folder": dropzone_folder.name,
                    "ministry_id": recipe.get("ministry_id"),
                    "delivery_id": recipe.get("delivery_id"),
                    "target_languages": recipe.get("languages", []),
                    "ingest_mode": "dropzone",
                }
                
                # Mark as active
                if not _add_task(stem):
                    continue
                
                # Submit with DROPZONE mode
                executor.submit(task_wrapper, stem, "Ingest", _run_ingest, file_path, "DROPZONE", "RUV_BOX", recipe_meta)

def _detect_client(filename: str) -> str:
    """Detect client from filename using CLIENT_PATTERNS from config."""
    name_lower = filename.lower()
    for pattern, client_name in getattr(config, "CLIENT_PATTERNS", {}).items():
        if pattern in name_lower:
            return client_name
    return "unknown"



def _run_ingest(file_path, mode, style, sidecar_meta=None):
    if sidecar_meta is None:
        sidecar_meta = {}

    original_stem = file_path.stem
    # Generate unique job ID using same pattern as cloud pipeline
    # Format: {slugified_stem}-{timestamp} e.g., "episode1-20241228T121500Z"
    job_id = new_job_id(original_stem)
    
    # Client Detection: Prefer sidecar, fall back to auto-detect
    client = sidecar_meta.get("client") or _detect_client(original_stem)
    
    # Calculate due date: Prefer sidecar, fall back to client defaults
    import datetime
    if sidecar_meta.get("due_date"):
        due_date = sidecar_meta.get("due_date")
    else:
        client_defaults = getattr(config, "CLIENT_DEFAULTS", {})
        client_config = client_defaults.get(client, client_defaults.get("unknown", {}))
        due_days = client_config.get("due_date_days", 7)
        due_date = (datetime.datetime.now() + datetime.timedelta(days=due_days)).strftime("%Y-%m-%d")

    # Style Override
    if sidecar_meta.get("style"):
        style = sidecar_meta.get("style")
    
    # Determine if this is a staged ingest (transcribe only, wait for config)
    is_staged = (mode == "STAGED")
    # Determine if this is a dropzone ingest (auto-process with recipe)
    is_dropzone = (mode == "DROPZONE")
    dropzone_languages = sidecar_meta.get("target_languages", []) if is_dropzone else []
    
    with job_log_context(job_id):
        try:
            logger.info("Ingest starting for job_id=%s (source=%s, mode=%s)", job_id, file_path.name, mode)
            source_path = str(file_path)
            review_required = (mode in {"REVIEW", "REMOTE_REVIEW"}) or ("/02_human_review/" in source_path.lower()) or sidecar_meta.get("review_required")
            remote_review_required = (mode == "REMOTE_REVIEW") or ("/03_remote_review/" in source_path.lower()) or sidecar_meta.get("remote_review_required")
            trace_id = str(
                sidecar_meta.get("trace_id")
                or sidecar_meta.get("correlation_id")
                or f"ingest-{secrets.token_hex(8)}"
            ).strip()
            logger.info("Ingest trace context: job_id=%s trace_id=%s", job_id, trace_id)

            # --- 1. INGEST (Move to Vault, Extract Audio/Proxy) ---
            # This is relatively fast (compared to transcription)
            vault_video_path, audio_path, thumbnail_path = transcriber.ingest(file_path)
            
            # Rename audio to match Job ID for consistency
            if audio_path.name != f"{job_id}.wav":
                new_audio_path = audio_path.with_name(f"{job_id}.wav")
                if new_audio_path.exists():
                     new_audio_path.unlink()
                audio_path.rename(new_audio_path)
                audio_path = new_audio_path
                logger.info(f"🔊 Renamed audio to Job ID: {audio_path.name}")

            # Get duration from the audio we just extracted
            duration = transcriber.get_audio_duration(audio_path)
            
            # --- 2. CREATE PROGRAM ---
            # Cloud Metadata Prep
            bucket_name = config.OMEGA_JOBS_BUCKET
            prefix = config.OMEGA_JOBS_PREFIX
            
            meta = {
                "original_filename": file_path.name,
                "original_stem": original_stem,
                "vault_path": str(vault_video_path),
                "mode": "DROPZONE" if is_dropzone else ("STAGED" if is_staged else ("REVIEW" if review_required else "AUTO")),
                "style": style,
                "source_path": source_path,
                "review_required": review_required,
                "remote_review_required": remote_review_required,
                "ingest_time": publisher.iso_now(),
                "cloud_job_id": job_id,
                "cloud_bucket": bucket_name,
                "cloud_prefix": prefix,
                "station_id": config.OMEGA_STATION_ID,
                "trace_id": trace_id,
                **sidecar_meta 
            }
            meta["trace_id"] = str(meta.get("trace_id") or trace_id).strip() or trace_id

            # Create Program
            program_title = sidecar_meta.get("program_title") or original_stem
            existing_program = omega_db.get_program_by_video(str(vault_video_path))
            
            if existing_program:
                program_id = existing_program['id']
                logger.info(f"📚 Using existing program: {program_id}")
            else:
                program_id = omega_db.create_program(
                    title=program_title,
                    original_filename=file_path.name,
                    video_path=str(vault_video_path),
                    thumbnail_path=str(thumbnail_path) if thumbnail_path and thumbnail_path.exists() else None,
                    duration_seconds=duration,
                    client=client,
                    due_date=due_date,
                    default_style=style,
                    meta=meta
                )
                logger.info(f"📚 Created program: {program_id}")
                
                # Update ingest_mode for staged/dropzone programs
                if is_staged:
                    omega_db.update_program(program_id, ingest_mode='staged')
                    logger.info(f"📋 Marked program as STAGED (waiting for configuration)")
                elif is_dropzone:
                    # Set dropzone mode and link ministry/delivery from recipe
                    omega_db.update_program(
                        program_id,
                        ingest_mode='dropzone',
                        ministry_id=sidecar_meta.get('ministry_id'),
                        delivery_id=sidecar_meta.get('delivery_id')
                    )
                    logger.info(f"📦 Marked program as DROPZONE (auto-processing {len(dropzone_languages)} languages)")
            
            # --- 3. RUN TRANSCRIPTION (Slow) ---
            # For non-staged: create track(s) for status tracking
            # For dropzone: create tracks for ALL languages in recipe
            track_ids = []
            primary_job_id = job_id  # Keep original job_id for primary track
            
            if is_staged:
                track_id = None
                logger.info("🎭 STAGED MODE: Skipping track creation, will transcribe only")
            elif is_dropzone and dropzone_languages:
                # DROPZONE: Create tracks for each language in the recipe
                from profiles import LANGUAGES
                for i, lang_code in enumerate(dropzone_languages):
                    lang_job_id = new_job_id(f"{original_stem}_{lang_code}") if i > 0 else job_id
                    language_name = (LANGUAGES.get(lang_code) or {}).get("name")
                    
                    master_script_id = omega_db.ensure_master_script(
                        program_id=program_id,
                        language_code=lang_code,
                        language_name=language_name,
                    )
                    
                    # Record QUEUED -> INGEST transition before track creation
                    try:
                        execute_transition(
                            job_id=lang_job_id,
                            job_stem=original_stem,
                            from_stage="QUEUED",
                            to_stage="INGEST",
                            processing_step="ingest",
                            worker_id="omega_manager",
                            reason="Starting file ingest (dropzone mode)",
                        )
                    except Exception as e:
                        logger.warning(f"Transition audit failed for {lang_job_id}: {e}")

                    track_id = omega_db.create_track(
                        program_id=program_id,
                        type='subtitle',
                        language_code=lang_code,
                        language_name=language_name,
                        stage='INGEST',
                        status='Processing Audio',
                        job_id=lang_job_id,
                        master_script_id=master_script_id,
                        meta={**meta, "target_language": lang_code}
                    )
                    track_ids.append((track_id, lang_code, lang_job_id))
                    logger.info(f"📦 Created dropzone track: {track_id} ({lang_code} subtitle)")
                
                # Update primary track status
                omega_db.update_job_via_track(job_id, stage="INGEST", status="Broadcasting Audio...", progress=15.0)
            else:
                # Check if sidecar specifies multiple languages
                sidecar_languages = sidecar_meta.get("languages", [])

                if sidecar_languages and len(sidecar_languages) > 0:
                    # Multi-language mode from import modal
                    from profiles import LANGUAGES
                    for i, lang_code in enumerate(sidecar_languages):
                        lang_job_id = new_job_id(f"{original_stem}_{lang_code}") if i > 0 else job_id
                        language_name = (LANGUAGES.get(lang_code) or {}).get("name")

                        master_script_id = omega_db.ensure_master_script(
                            program_id=program_id,
                            language_code=lang_code,
                            language_name=language_name,
                        )

                        # Record QUEUED -> INGEST transition before track creation
                        try:
                            execute_transition(
                                job_id=lang_job_id,
                                job_stem=original_stem,
                                from_stage="QUEUED",
                                to_stage="INGEST",
                                processing_step="ingest",
                                worker_id="omega_manager",
                                reason="Starting file ingest (multi-language mode)",
                            )
                        except Exception as e:
                            logger.warning(f"Transition audit failed for {lang_job_id}: {e}")

                        track_id = omega_db.create_track(
                            program_id=program_id,
                            type='subtitle',
                            language_code=lang_code,
                            language_name=language_name,
                            stage='INGEST',
                            status='Processing Audio',
                            job_id=lang_job_id,
                            master_script_id=master_script_id,
                            meta={**meta, "target_language": lang_code}
                        )
                        track_ids.append((track_id, lang_code, lang_job_id))
                        logger.info(f"🎬 Created track: {track_id} ({lang_code} subtitle)")

                    # Update primary track status
                    omega_db.update_job_via_track(job_id, stage="INGEST", status="Broadcasting Audio...", progress=15.0)
                else:
                    # Normal single-language mode (legacy or direct folder drop)
                    target_lang = sidecar_meta.get("target_language") or getattr(config, "OMEGA_TARGET_LANGUAGE", "is")
                    language_name = None
                    try:
                        from profiles import LANGUAGES
                        language_name = (LANGUAGES.get(target_lang) or {}).get("name")
                    except Exception:
                        language_name = None

                    master_script_id = omega_db.ensure_master_script(
                        program_id=program_id,
                        language_code=target_lang,
                        language_name=language_name,
                    )

                    # Record QUEUED -> INGEST transition before track creation
                    try:
                        execute_transition(
                            job_id=job_id,
                            job_stem=original_stem,
                            from_stage="QUEUED",
                            to_stage="INGEST",
                            processing_step="ingest",
                            worker_id="omega_manager",
                            reason="Starting file ingest",
                        )
                    except Exception as e:
                        logger.warning(f"Transition audit failed for {job_id}: {e}")

                    track_id = omega_db.create_track(
                        program_id=program_id,
                        type='subtitle',
                        language_code=target_lang,
                        language_name=language_name,
                        stage='INGEST',
                        status='Processing Audio',
                        job_id=job_id,
                        master_script_id=master_script_id,
                        meta=meta
                    )
                    track_ids.append((track_id, target_lang, job_id))
                    logger.info(f"🎬 Created track: {track_id} ({target_lang} subtitle)")

                    # Update status via track
                    omega_db.update_job_via_track(job_id, stage="INGEST", status="Broadcasting Audio...", progress=15.0)

            # --- 2.5 MULTIMODAL PIPELINE (Optional) ---
            # Run vision analysis if OMEGA_MULTIMODAL_ENABLED=1
            # This generates danger zones for subtitle positioning
            # Pass original_stem so danger zones file is shared across all language tracks
            multimodal_job_ids = [entry[2] for entry in track_ids] if track_ids else None
            multimodal_results = _run_multimodal_pipeline(
                vault_video_path, job_id, bucket_name, prefix, original_stem=original_stem, job_ids=multimodal_job_ids
            )
            if multimodal_results and not multimodal_results.get("error"):
                meta["multimodal"] = multimodal_results
                logger.info(f"👁️ Multimodal: {multimodal_results.get('shots', 0)} shots, {multimodal_results.get('danger_zones', 0)} danger zones")

            # Run transcription (shared for all modes)
            skeleton_path = transcriber.transcribe(audio_path, job_id=job_id)

            # --- 3.5 WORSHIP / MUSIC DETECTION (pre-translation filtering) ---
            if config.OMEGA_WORSHIP_DETECTION_ENABLED and skeleton_path and skeleton_path.exists():
                try:
                    from workers.audio_classifier import (
                        mark_music_segments,
                        detect_worship_by_repetition,
                        is_available as ac_available,
                    )

                    with open(skeleton_path, "r") as _f:
                        skel_data = json.load(_f)

                    skel_segments = skel_data if isinstance(skel_data, list) else skel_data.get("segments", [])
                    worship_stats = {"audio_marked": 0, "text_marked": 0}

                    # Layer 1: CNN-based audio classification (instrumental music, melodic singing)
                    if ac_available() and audio_path and audio_path.exists():
                        logger.info("🎵 Layer 1: Running audio classification on %d segments...", len(skel_segments))
                        skel_segments, audio_marked = mark_music_segments(skel_segments, audio_path)
                        worship_stats["audio_marked"] = audio_marked
                        if audio_marked:
                            logger.info("🎵 Layer 1 complete: %d segments marked as music", audio_marked)

                    # Layer 2: Text repetition detection (sung lyrics with clear words)
                    logger.info("🎵 Layer 2: Running text repetition analysis on %d segments...", len(skel_segments))
                    skel_segments, text_marked = detect_worship_by_repetition(
                        skel_segments,
                        window_size=config.OMEGA_WORSHIP_REPETITION_WINDOW,
                        min_occurrences=config.OMEGA_WORSHIP_REPETITION_THRESHOLD,
                        min_cluster_size=config.OMEGA_WORSHIP_MIN_CLUSTER_SIZE,
                    )
                    worship_stats["text_marked"] = text_marked

                    total_marked = sum(1 for s in skel_segments if s.get("is_worship") or s.get("is_music"))
                    if total_marked > 0:
                        logger.info(
                            "🎵 TOTAL: %d/%d segments marked for worship filtering (%d audio, %d text)",
                            total_marked, len(skel_segments), worship_stats["audio_marked"], worship_stats["text_marked"],
                        )
                        # Write back modified skeleton
                        if isinstance(skel_data, list):
                            skel_data = skel_segments
                        else:
                            skel_data["segments"] = skel_segments
                            skel_data["worship_detection"] = worship_stats
                        with open(skeleton_path, "w") as _f:
                            json.dump(skel_data, _f, ensure_ascii=False, indent=2)
                        logger.info("🎵 Updated skeleton with worship flags: %s", skeleton_path.name)
                    else:
                        logger.info("🎵 No worship segments detected — all content will be subtitled")

                except Exception as e:
                    logger.warning("⚠️ Worship detection failed (non-fatal): %s", e)

            # --- 4. HANDLE STAGED vs NORMAL/DROPZONE FLOW ---
            if is_staged:
                # STAGED MODE: Stop here, don't upload to cloud or create tracks
                # Update program meta with skeleton path
                omega_db.update_program(program_id, meta={
                    **meta,
                    "skeleton_path": str(skeleton_path) if skeleton_path else None,
                    "staged_at": publisher.iso_now(),
                })
                logger.info(f"⏸️ STAGED: Program {program_id} is ready for configuration")
                logger.info(f"   Dashboard: Select Ministry, Languages, Delivery, then click START")
                return  # Exit early for staged mode
            
            # --- NORMAL/DROPZONE FLOW CONTINUES ---
            # Update all tracks with skeleton path
            for track_id, lang_code, lang_job_id in track_ids:
                omega_db.update_job_via_track(lang_job_id, meta={"skeleton_path": str(skeleton_path) if skeleton_path else None})

            # --- 5. CLOUD UPLOAD ---
            if _cloud_pipeline_enabled():
                # For DROPZONE with multiple languages, upload for EACH track
                for track_id, lang_code, lang_job_id in track_ids:
                    logger.info(f"☁️ Uploading artifacts to GCS for job {lang_job_id} ({lang_code})...")
                    paths = GcsJobPaths(bucket=bucket_name, prefix=prefix, job_id=lang_job_id)
                    
                    storage_client = storage.Client()
                    # Upload Skeleton
                    if skeleton_path and skeleton_path.exists():
                        with open(skeleton_path, "r") as f:
                            skeleton_data = json.load(f)
                        upload_json(storage_client, bucket=bucket_name, blob_name=paths.skeleton_blob, payload=skeleton_data)
                    else:
                        logger.warning(f"No skeleton found to upload for {lang_job_id}.")

                    # Upload Audio (CRITICAL for Gemini Context Cache)
                    if audio_path and audio_path.exists():
                        logger.info(f"   Upload Audio: {audio_path.name}")
                        audio_blob_name = paths.audio_blob()
                        bucket_obj = storage_client.bucket(bucket_name)
                        blob = bucket_obj.blob(audio_blob_name)
                        if not blob.exists():
                             blob.upload_from_filename(str(audio_path), content_type="audio/wav")
                    
                    # Upload Metadata (job.json)
                    job_payload = {
                        "id": lang_job_id,
                        "trace_id": trace_id,
                        "file_stem": original_stem,
                        "target_language": lang_code,
                        "program_profile": sidecar_meta.get("program_profile") or "standard",
                        "station_id": meta.get("station_id"),
                        "glossary_terms": sidecar_meta.get("glossary_terms") or [],
                        "audio_file": audio_path.name if audio_path else None,
                        "audio_gcs_uri": f"gs://{bucket_name}/{audio_blob_name}" if audio_path else None,
                        "meta": {**meta, "target_language": lang_code},
                        "created_at": publisher.iso_now()
                    }
                    temp_job_json = config.VAULT_DATA / f"{lang_job_id}_cloud_job.json"
                    with open(temp_job_json, "w") as f:
                        json.dump(job_payload, f, indent=2)
                    upload_json(storage_client, bucket=bucket_name, blob_name=paths.job_blob, payload=job_payload)
                    temp_job_json.unlink(missing_ok=True)
                    
                    # Record upload time
                    omega_db.update_job_via_track(
                        lang_job_id,
                        meta={"uploaded_at": publisher.iso_now(), "trace_id": trace_id},
                    )



            # --- 6. FINALIZE INGEST ---
            # Update all tracks to TRANSCRIBED
            for track_id, lang_code, lang_job_id in track_ids:
                try:
                    apply_transition(
                        job_id=lang_job_id,
                        job_stem=original_stem,
                        from_stage="INGEST",
                        to_stage="TRANSCRIBED",
                        processing_step="transcribe",
                        worker_id="omega_manager",
                        reason="Transcription complete",
                        status="Ready for Translation",
                        progress=30.0,
                        meta={"program_id": program_id, "track_id": track_id, "cloud_job_id": lang_job_id},
                    )
                except Exception as e:
                    logger.warning(f"Transition INGEST->TRANSCRIBED failed for {lang_job_id}: {e}")

        except Exception as e:
            raise e

def _run_ingest_recovery(stem: str, video_path: Path):
    """
    Recovers a stalled ingest job where the video is already in the Vault.
    Re-runs transcription ensuring Job ID consistency.
    """
    logger.info(f"🔄 Recovering Ingest for {stem}")
    try:
        # Re-run transcriber
        # input: video_path (in Vault)
        # job_id: stem (Critical for file naming)
        skeleton_path = transcriber.run(video_path, job_id=stem)

        # Recovery path must also restore cloud input artifacts.
        # Without this, cloud translation can be triggered with no job payload.
        if _cloud_pipeline_enabled():
            bucket_name = config.OMEGA_JOBS_BUCKET
            prefix = config.OMEGA_JOBS_PREFIX
            if not bucket_name:
                raise RuntimeError("Cloud pipeline enabled but OMEGA_JOBS_BUCKET is empty")

            job = omega_db.get_job_via_track(stem) or {}
            track = omega_db.get_track_by_job(stem) or {}
            job_meta = _job_meta(job)
            target_language = (
                str(track.get("language_code") or job.get("target_language") or "is")
                .strip()
                .lower()
                or "is"
            )
            trace_id = str(job_meta.get("trace_id") or stem).strip() or stem
            original_stem = str(job_meta.get("original_stem") or video_path.stem)
            station_id = str(job_meta.get("station_id") or config.OMEGA_STATION_ID or "").strip()
            program_profile = str(job.get("program_profile") or job_meta.get("program_profile") or "standard").strip() or "standard"
            glossary_terms = job_meta.get("glossary_terms")
            if not isinstance(glossary_terms, list):
                glossary_terms = []

            audio_path = config.VAULT_DIR / "Audio" / f"{stem}.wav"
            if not skeleton_path or not Path(skeleton_path).exists():
                raise RuntimeError(f"Missing skeleton for recovery upload: {stem}")
            if not audio_path.exists():
                raise RuntimeError(f"Missing audio for recovery upload: {audio_path}")

            logger.info("☁️ Recovery upload: %s (%s)", stem, target_language)
            ensure_google_application_credentials()
            storage_client = storage.Client()
            paths = GcsJobPaths(bucket=bucket_name, prefix=prefix, job_id=stem)

            with open(skeleton_path, "r", encoding="utf-8") as handle:
                skeleton_data = json.load(handle)
            upload_json(
                storage_client,
                bucket=bucket_name,
                blob_name=paths.skeleton_blob,
                payload=skeleton_data,
            )

            audio_blob_name = paths.audio_blob()
            audio_blob = storage_client.bucket(bucket_name).blob(audio_blob_name)
            if not audio_blob.exists():
                audio_blob.upload_from_filename(str(audio_path), content_type="audio/wav")

            job_payload = {
                "id": stem,
                "trace_id": trace_id,
                "file_stem": original_stem,
                "target_language": target_language,
                "program_profile": program_profile,
                "station_id": station_id,
                "glossary_terms": glossary_terms,
                "audio_file": audio_path.name,
                "audio_gcs_uri": f"gs://{bucket_name}/{audio_blob_name}",
                "meta": {**job_meta, "target_language": target_language},
                "created_at": publisher.iso_now(),
            }
            upload_json(
                storage_client,
                bucket=bucket_name,
                blob_name=paths.job_blob,
                payload=job_payload,
            )
            omega_db.update_job_via_track(
                stem,
                meta={
                    "uploaded_at": publisher.iso_now(),
                    "trace_id": trace_id,
                    "cloud_recovery_upload_at": publisher.iso_now(),
                },
            )

        try:
            apply_transition(
                job_id=stem,
                job_stem=stem,
                from_stage="INGEST",
                to_stage="TRANSCRIBED",
                processing_step="transcribe",
                worker_id="omega_manager",
                reason="Transcription recovery complete",
                status="Ready for Translation",
                progress=30.0,
                meta={"retry_force_ingest_recovery": False, "retry_requested_at": ""},
            )
        except Exception as e:
            logger.warning(f"Transition INGEST->TRANSCRIBED failed for {stem}: {e}")
    except Exception as e:
        logger.error(f"❌ Recovery failed for {stem}: {e}")
        try:
            apply_transition(
                job_id=stem,
                job_stem=stem,
                from_stage="INGEST",
                to_stage="FAILED",
                worker_id="omega_manager",
                reason=f"Ingest recovery failed: {str(e)}",
                skip_validation=_requires_validation_bypass("INGEST", "FAILED"),
                status=f"Recovery Failed: {str(e)}",
                progress=0.0,
            )
        except Exception as te:
            logger.warning(f"Transition INGEST->FAILED failed for {stem}: {te}")
        raise e

# =========================================================================
# CLOUD SYNC LOGIC (Phase 1)
# =========================================================================

def _sync_cloud_approved_idempotent(stem: str, storage_client, bucket_name: str, prefix: str = None) -> bool:
    """
    Idempotently download approved.json from GCS and update local database.

    Uses cloud_sync_state table to track progress through atomic states:
    PENDING → DOWNLOADING → DOWNLOADED → FILE_SAVED → SYNCED

    Args:
        stem: Job file stem identifier
        storage_client: Google Cloud Storage client
        bucket_name: GCS bucket name
        prefix: GCS prefix path (e.g., "jobs"). Defaults to config.OMEGA_JOBS_PREFIX

    Returns:
        True if sync completed successfully
        False if sync should be retried later
    """
    import json
    from pathlib import Path
    from datetime import datetime, timedelta
    import omega_db

    artifact_id = stem
    artifact_type = "approved_json"

    # Use provided prefix or fallback to config default
    if prefix is None:
        prefix = str(getattr(config, "OMEGA_JOBS_PREFIX", "jobs")).strip()

    gcs_path = f"{prefix}/{stem}/approved.json"
    local_path = config.TRANSLATED_DONE_DIR / f"{stem}_APPROVED.json"

    # Step 1: Get or create sync state
    sync_state = omega_db.get_sync_state(artifact_id, artifact_type)

    if not sync_state:
        # First time seeing this artifact - create state record
        omega_db.create_sync_state(artifact_id, artifact_type, gcs_path, str(local_path))
        sync_state = omega_db.get_sync_state(artifact_id, artifact_type)

    current_state = sync_state['state']

    # Already completed successfully
    if current_state == 'SYNCED':
        return True

    # Check for retry backoff (exponential backoff after failures)
    if sync_state.get('attempt_count', 0) > 0 and sync_state.get('last_attempt_at'):
        from datetime import datetime, timedelta
        # Handle timezone-aware vs naive datetimes if needed, but ISO format usually safe
        try:
            last_attempt = datetime.fromisoformat(sync_state['last_attempt_at'])
            if last_attempt.tzinfo is None:
                # Assume UTC if naive, or local system time if that's what DB stores
                pass 
            
            backoff_seconds = min(300, 2 ** sync_state['attempt_count'])  # Max 5 min
            # Simple check avoiding tz headaches if not strictly necessary
            if (datetime.now() - last_attempt).total_seconds() < backoff_seconds:
                return False  # Too soon to retry
        except Exception as e:
            logger.debug(f"Backoff time parsing failed, proceeding with sync: {e}")

    try:
        # Step 2: Download from GCS (if not already downloaded)
        if current_state in ['PENDING', 'DOWNLOADING']:
            omega_db.update_sync_state(artifact_id, artifact_type, 'DOWNLOADING')

            # Check if blob exists
            if not blob_exists(storage_client, bucket_name, gcs_path):
                # logger.warning(f"Approved.json not yet available for {stem}")
                return False

            # Download to memory first
            approved_payload = download_json(storage_client, bucket=bucket_name, blob_name=gcs_path)

            # Mark as downloaded (payload in memory but not on disk)
            omega_db.update_sync_state(artifact_id, artifact_type, 'DOWNLOADED')

            # Store in state for next step (in real implementation, could use temp file)
            sync_state['_payload'] = approved_payload

        # Step 3: Save to disk (atomic operation)
        if current_state in ['PENDING', 'DOWNLOADING', 'DOWNLOADED']:
            # Get payload from previous step or re-download
            if '_payload' not in sync_state:
                approved_payload = download_json(storage_client, bucket=bucket_name, blob_name=gcs_path)
            else:
                approved_payload = sync_state['_payload']

            # Write atomically using temp file + rename
            import tempfile
            import os
            temp_fd, temp_path = tempfile.mkstemp(
                dir=config.TRANSLATED_DONE_DIR,
                prefix=f"{stem}_APPROVED_",
                suffix=".json.tmp"
            )
            try:
                with os.fdopen(temp_fd, 'w') as f:
                    json.dump(approved_payload, f, indent=2, ensure_ascii=False)

                # Atomic rename
                os.rename(temp_path, local_path)

                # Mark as saved to disk
                omega_db.update_sync_state(artifact_id, artifact_type, 'FILE_SAVED', str(local_path))

            except Exception as e:
                # Clean up temp file on error
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
                raise

        # Step 4: Update database (wrapped in transaction)
        # Step 4: Update database (Sequential updates)
        if current_state in ['PENDING', 'DOWNLOADING', 'DOWNLOADED', 'FILE_SAVED']:
            try:
                # Read approved.json from disk
                with open(local_path, 'r') as f:
                    approved_payload = json.load(f)

                # NORMALIZE SEGMENTS FOR REVIEW
                # This ensures reviewers see broadcast-ready subtitles, not raw AI output
                job = omega_db.get_job_via_track(stem) or {}
                target_language = job.get("target_language", "is")
                raw_segments = approved_payload.get("segments", [])
                if raw_segments:
                    logger.info(f"📝 Normalizing {len(raw_segments)} segments before review...")
                    normalized_segments = finalizer.normalize_segments_for_review(
                        raw_segments, target_language=target_language
                    )
                    # Update the approved.json with normalized segments
                    # Preserve word-level timing data for BBC/Netflix speech anchoring in finalizer
                    approved_payload["segments"] = [
                        {
                            "start": seg["start"],
                            "end": seg["end"],
                            "text": seg["text"],
                            "speaker": seg.get("speaker"),
                            **({"words": seg["words"]} if "words" in seg else {}),
                        }
                        for seg in normalized_segments
                    ]
                    approved_payload["normalized_for_review"] = True
                    approved_payload["normalized_at"] = datetime.now().isoformat()
                    # Save normalized version back to disk
                    with open(local_path, 'w', encoding='utf-8') as f:
                        json.dump(approved_payload, f, ensure_ascii=False, indent=2)
                    logger.info(f"✅ Saved normalized segments ({len(raw_segments)} → {len(normalized_segments)})")

                # Update job record via track (Source of Truth)
                current_stage = (job.get("stage") or "CLOUD_REVIEWING").upper()
                apply_transition(
                    job_id=stem,
                    job_stem=stem,
                    from_stage=current_stage,
                    to_stage="REVIEWED",
                    worker_id="omega_manager",
                    reason="Cloud approved.json synced successfully",
                    skip_validation=_requires_validation_bypass(current_stage, "REVIEWED"),
                    status='Awaiting finalization',
                )

                # Update sync state to SYNCED
                omega_db.update_sync_state(artifact_id, artifact_type, 'SYNCED')

                logger.info(f"✅ Successfully synced approved.json for {stem}")
                return True

            except Exception as e:
                logger.error(f"❌ Failed to sync/update DB for {stem}: {e}")
                raise e

        return True

    except Exception as e:
        error_msg = str(e)
        logger.error(f"Failed to sync {stem}: {error_msg}")

        # Get the CURRENT state from DB (not the cached value from start of function)
        # This ensures we don't roll back a state that was successfully transitioned
        try:
            db_sync_state = omega_db.get_sync_state(artifact_id, artifact_type)
            db_current_state = db_sync_state.get('state') if db_sync_state else 'PENDING'
        except Exception:
            db_current_state = current_state if current_state else 'PENDING'

        omega_db.update_sync_state(
            artifact_id,
            artifact_type,
            db_current_state,
            error_message=error_msg
        )
        return False

def reconcile_orphaned_sync_states():
    """
    Clean up orphaned sync states for jobs that completed through alternate code paths.

    This handles cases where:
    1. Job completed (approved.json on disk) but sync state is still DOWNLOADING
    2. Track stage is COMPLETED but sync state wasn't updated

    Run this periodically to prevent sync state table pollution.
    """
    import omega_db

    pending_syncs = omega_db.get_pending_syncs('approved_json')
    if not pending_syncs:
        return

    reconciled_count = 0
    for sync in pending_syncs:
        job_id = sync['id']
        current_state = sync.get('state', '')

        # Check 1: Does approved.json exist on disk?
        local_approved = config.TRANSLATED_DONE_DIR / f"{job_id}_APPROVED.json"
        if local_approved.exists():
            omega_db.update_sync_state(job_id, 'approved_json', 'SYNCED')
            logger.info(f"🔄 Reconciled {job_id}: approved.json exists on disk")
            reconciled_count += 1
            continue

        # Check 2: Is the track already COMPLETED/DELIVERED?
        conn = omega_db._connect()
        try:
            c = conn.cursor()
            c.execute("SELECT stage FROM tracks WHERE job_id = ?", (job_id,))
            row = c.fetchone()
            if row:
                track_stage = (row['stage'] or '').upper()
                if track_stage in {'COMPLETED', 'DELIVERED', 'COMPLETE'}:
                    omega_db.update_sync_state(job_id, 'approved_json', 'SYNCED')
                    logger.info(f"🔄 Reconciled {job_id}: track stage is {track_stage}")
                    reconciled_count += 1
                    continue
        finally:
            conn.close()

        # Check 3: Does the final output video exist?
        final_video = config.DELIVERY_DIR / "VIDEO" / f"{job_id}_SUBBED.mp4"
        if final_video.exists():
            omega_db.update_sync_state(job_id, 'approved_json', 'SYNCED')
            logger.info(f"🔄 Reconciled {job_id}: final video exists")
            reconciled_count += 1
            continue

    if reconciled_count > 0:
        logger.info(f"✅ Reconciled {reconciled_count} orphaned sync states")


def reconcile_orphaned_deliveries():
    """
    Background service to detect and reconcile orphaned delivery files.
    Uses the same deterministic logic as scripts/reconcile_orphan_deliveries.py.
    """
    try:
        from scripts.reconcile_orphan_deliveries import reconcile
    except Exception as e:
        logger.warning(f"Orphan reconciler import failed: {e}")
        return

    archive_dir = Path(config.DELIVERY_DIR) / "VIDEO" / f"ORPHAN_ARCHIVE_{datetime.now():%Y%m%d}"
    try:
        report = reconcile(apply=True, archive_dir=archive_dir, write_manifest=True)
    except Exception as e:
        logger.error(f"Orphan reconciliation run failed: {e}")
        return

    summary = report.get("summary") or {}
    planned = int(summary.get("planned") or 0)
    applied = int(summary.get("applied") or 0)
    by_class = summary.get("by_classification") or {}
    if planned > 0:
        logger.warning(
            "🔄 Orphan reconciliation: planned=%d applied=%d classes=%s",
            planned,
            applied,
            by_class,
        )


# ============================================================================
# Self-Healing Functions (Module Level for Testing)
# ============================================================================

def _job_meta(job: dict) -> dict:
    """Extract meta dict from job, handling string JSON."""
    meta = job.get("meta") or {}
    if isinstance(meta, str):
        try:
            import json
            meta = json.loads(meta)
        except:
            meta = {}
    return meta if isinstance(meta, dict) else {}


def _job_station_id(job: dict) -> Optional[str]:
    # Return normalized station_id for a job if present.
    meta = _job_meta(job)
    station_id = meta.get("station_id")
    if station_id:
        return str(station_id).strip().lower()
    # Fallback if a column exists in DB
    if job.get("station_id"):
        return str(job.get("station_id")).strip().lower()
    return None


def _job_belongs_to_station(job: dict) -> bool:
    # Check if a job should be processed on this station.
    station_id = _job_station_id(job)
    if not config.OMEGA_STATION_ID:
        return True
    if station_id:
        return station_id == config.OMEGA_STATION_ID
    return config.OMEGA_STATION_CLAIM_UNASSIGNED


def _final_output_path(job: dict) -> Optional[Path]:
    """Get the final output path from job meta."""
    meta = _job_meta(job)
    value = meta.get("final_output")
    if not value:
        return None
    try:
        return Path(str(value))
    except Exception:
        return None


def _autocorrect_completed(stem: str, job: dict) -> bool:
    """Auto-correct job to COMPLETED if output file exists."""
    final_path = _final_output_path(job)
    if final_path and final_path.exists():
        stage_upper = (job.get("stage") or "").upper()
        if stage_upper not in {"COMPLETED", "DELIVERED"}:
            logger.info(f"✅ Auto-correcting {stem}: output exists at {final_path}")
            try:
                apply_transition(
                    job_id=stem,
                    job_stem=stem,
                    from_stage=stage_upper,
                    to_stage="COMPLETED",
                    worker_id="self_healer",
                    reason="Self-heal: output file exists",
                    skip_validation=_requires_validation_bypass(stage_upper, "COMPLETED"),
                    status="Done",
                    progress=100.0,
                    meta={"last_error": "", "failed_at": ""},
                )
            except Exception as e:
                logger.warning(f"Transition to COMPLETED failed for {stem}: {e}")
        # Reconcile orphaned sync states - mark as SYNCED if job is complete
        try:
            sync_state = omega_db.get_sync_state(stem, 'approved_json')
            if sync_state and sync_state.get('state') != 'SYNCED':
                logger.info(f"🔄 Reconciling sync state for completed job {stem}")
                omega_db.update_sync_state(stem, 'approved_json', 'SYNCED')
        except Exception as e:
            logger.debug(f"Sync state reconciliation skipped for {stem}: {e}")
        return True
    return False


def _self_heal_job(stem: str, job: dict) -> bool:
    """
    Self-healing: Detect and fix misaligned job states based on file presence.

    Returns True if job was auto-corrected, False otherwise.
    """
    # Neutered: The database is the single source of truth. Relieving the manager of file polling.
    return False

    stage = (job.get("stage") or "").upper()
    meta = _job_meta(job)

    # Skip halted jobs - require manual intervention
    if meta.get("halted"):
        return False

    # Skip if already in a terminal state
    if stage in {"COMPLETED", "DELIVERED", "DEAD"}:
        return False

    # Check what files exist
    srt_path = config.SRT_DIR / f"{stem}.srt"
    approved_path = config.TRANSLATED_DONE_DIR / f"{stem}_APPROVED.json"
    skeleton_path = config.find_skeleton(stem)
    final_path = _final_output_path(job)

    # Priority 1: Final video exists → COMPLETED
    # BUT: Only if the video file is newer than the track's last update.
    # This prevents stale videos from previous runs hijacking re-runs.
    if final_path and final_path.exists():
        if stage != "COMPLETED":
            # Check video freshness against the appropriate reference time:
            # - BURNING stage: use burn_started_at (most specific)
            # - All other stages: use track updated_at (catches reset-without-cleanup)
            if stage == "BURNING":
                reference_time_str = meta.get("burn_started_at")
            else:
                reference_time_str = job.get("updated_at")
            if reference_time_str:
                try:
                    from datetime import datetime
                    video_mtime = datetime.fromtimestamp(final_path.stat().st_mtime)
                    ref_str = str(reference_time_str)
                    # Handle various ISO format variants
                    ref_str = ref_str.replace("Z", "+00:00")
                    if "+" not in ref_str and ref_str.count(":") < 3:
                        ref_str += "+00:00"
                    reference_time = datetime.fromisoformat(ref_str.replace("+00:00", "")).replace(tzinfo=None)
                    video_mtime = video_mtime.replace(tzinfo=None)
                    if video_mtime < reference_time:
                        # Video is OLDER than track update - stale from previous run
                        logger.info(f"🔧 Self-heal {stem}: video exists but is stale (video={video_mtime}, track_updated={reference_time})")
                        return False
                except Exception as e:
                    logger.warning(f"🔧 Self-heal {stem}: Could not check video freshness: {e}")
                    if stage == "BURNING":
                        return False  # Don't auto-complete a BURNING job if we can't verify
            logger.info(f"🔧 Self-heal {stem}: video exists → COMPLETED")
            try:
                apply_transition(
                    job_id=stem,
                    job_stem=stem,
                    from_stage=stage,
                    to_stage="COMPLETED",
                    worker_id="self_healer",
                    reason="Self-heal: video file exists",
                    skip_validation=_requires_validation_bypass(stage, "COMPLETED"),
                    status="Done",
                    progress=100.0,
                )
            except Exception as e:
                logger.warning(f"🔧 Self-heal transition failed for {stem}: {e}")
                return False
            return True

    # Priority 2: SRT exists but stage is before FINALIZED
    if srt_path.exists() and stage not in {"FINALIZED", "BURNING", "COMPLETED"}:
        logger.info(f"🔧 Self-heal {stem}: SRT exists → FINALIZED")
        try:
            apply_transition(
                job_id=stem,
                job_stem=stem,
                from_stage=stage,
                to_stage="FINALIZED",
                worker_id="self_healer",
                reason="Self-heal: SRT file exists",
                skip_validation=_requires_validation_bypass(stage, "FINALIZED"),
                status="Ready to burn",
                progress=85.0,
            )
        except Exception as e:
            logger.warning(f"🔧 Self-heal transition failed for {stem}: {e}")
            return False
        return True

    # Priority 3: Approved translation exists but stage is before REVIEWED
    if approved_path.exists() and stage not in {"REVIEWED", "FINALIZING", "FINALIZED", "BURNING", "COMPLETED"}:
        logger.info(f"🔧 Self-heal {stem}: approved.json exists → REVIEWED")
        try:
            apply_transition(
                job_id=stem,
                job_stem=stem,
                from_stage=stage,
                to_stage="REVIEWED",
                worker_id="self_healer",
                reason="Self-heal: approved.json exists",
                skip_validation=_requires_validation_bypass(stage, "REVIEWED"),
                status="Ready to finalize",
                progress=70.0,
            )
        except Exception as e:
            logger.warning(f"🔧 Self-heal transition failed for {stem}: {e}")
            return False
        return True

    # Priority 4: Skeleton exists but stage is stuck at QUEUED/INGEST
    if skeleton_path and stage in {"QUEUED", "INGEST", ""}:
        logger.info(f"🔧 Self-heal {stem}: skeleton exists → TRANSCRIBED")
        try:
            apply_transition(
                job_id=stem,
                job_stem=stem,
                from_stage=stage,
                to_stage="TRANSCRIBED",
                worker_id="self_healer",
                reason="Self-heal: skeleton exists",
                skip_validation=_requires_validation_bypass(stage, "TRANSCRIBED"),
                status="Ready for translation",
                progress=30.0,
            )
        except Exception as e:
            logger.warning(f"🔧 Self-heal transition failed for {stem}: {e}")
            return False
        return True

    # Priority 5: Job stuck in DEAD but files exist for recovery
    if stage == "DEAD":
        if srt_path.exists():
            logger.info(f"🔧 Self-heal {stem}: DEAD but SRT exists → FINALIZED")
            try:
                apply_transition(
                    job_id=stem,
                    job_stem=stem,
                    from_stage=stage,
                    to_stage="FINALIZED",
                    worker_id="self_healer",
                    reason="Self-heal: DEAD but SRT exists",
                    skip_validation=_requires_validation_bypass(stage, "FINALIZED"),
                    status="Recovered from DEAD",
                    progress=85.0,
                    meta={**meta, "halted": False, "recovered_from_dead": True},
                )
            except Exception as e:
                logger.warning(f"🔧 Self-heal transition failed for {stem}: {e}")
                return False
            return True
        elif approved_path.exists():
            logger.info(f"🔧 Self-heal {stem}: DEAD but approved exists → REVIEWED")
            try:
                apply_transition(
                    job_id=stem,
                    job_stem=stem,
                    from_stage=stage,
                    to_stage="REVIEWED",
                    worker_id="self_healer",
                    reason="Self-heal: DEAD but approved.json exists",
                    skip_validation=_requires_validation_bypass(stage, "REVIEWED"),
                    status="Recovered from DEAD",
                    progress=70.0,
                    meta={**meta, "halted": False, "recovered_from_dead": True},
                )
            except Exception as e:
                logger.warning(f"🔧 Self-heal transition failed for {stem}: {e}")
                return False
            return True
        elif skeleton_path:
            logger.info(f"🔧 Self-heal {stem}: DEAD but skeleton exists → TRANSCRIBED")
            try:
                apply_transition(
                    job_id=stem,
                    job_stem=stem,
                    from_stage=stage,
                    to_stage="TRANSCRIBED",
                    worker_id="self_healer",
                    reason="Self-heal: DEAD but skeleton exists",
                    skip_validation=_requires_validation_bypass(stage, "TRANSCRIBED"),
                    status="Recovered from DEAD",
                    progress=30.0,
                    meta={**meta, "halted": False, "recovered_from_dead": True},
                )
            except Exception as e:
                logger.warning(f"🔧 Self-heal transition failed for {stem}: {e}")
                return False
            return True

    return False


# ============================================================================
# Main Processing Loop
# ============================================================================

def process_jobs(executor):
    """
    Polls DB/Files for jobs in intermediate stages.
    """
    # Use the thread-safe module-level _is_in_cooldown
    is_in_cooldown = _is_in_cooldown

    jobs = [j for j in omega_db.get_all_jobs_via_tracks() if _job_belongs_to_station(j)]
    jobs_by_stem = {j.get("file_stem"): j for j in jobs if j.get("file_stem")}

    # 0.25 Self-heal: Auto-correct job states based on file presence
    # This runs every cycle to fix any misaligned states before processing
    healed_count = 0
    for stem, job in jobs_by_stem.items():
        try:
            if _self_heal_job(stem, job):
                healed_count += 1
        except Exception as e:
            logger.warning(f"🔧 Self-heal error for {stem}: {e}")
    if healed_count > 0:
        logger.info(f"🔧 Self-healed {healed_count} job(s) this cycle")
        # Refresh jobs after healing
        jobs = [j for j in omega_db.get_all_jobs_via_tracks() if _job_belongs_to_station(j)]
        jobs_by_stem = {j.get("file_stem"): j for j in jobs if j.get("file_stem")}

    # 0.5 Detect stalled stages and trigger recovery/restart
    now = datetime.now()
    for stem, job in jobs_by_stem.items():
        if not stem:
            continue
        try:
            stage = str(job.get("stage") or "").upper()
            threshold = STAGE_STALL_THRESHOLDS.get(stage)
            if not threshold:
                continue
            status = str(job.get("status") or "")
            if _status_is_blocked(status):
                continue
            meta = _job_meta(job)
            if meta.get("halted"):
                continue

            started_at = _stage_started_at(meta, stage)
            if not started_at:
                started_at = _parse_iso(job.get("updated_at"))
            cloud_progress_at = None
            if stage in {"TRANSLATING_CLOUD_SUBMITTED", "CLOUD_TRANSLATING", "CLOUD_REVIEWING"}:
                cloud_progress = meta.get("cloud_progress") if isinstance(meta.get("cloud_progress"), dict) else {}
                cloud_progress_at = _parse_iso(cloud_progress.get("updated_at")) if isinstance(cloud_progress, dict) else None
            elapsed = None
            if cloud_progress_at:
                elapsed = (now - cloud_progress_at).total_seconds()
            elif started_at:
                elapsed = (now - started_at).total_seconds()
            if elapsed is None:
                continue
            if elapsed < threshold:
                continue

            cloud_stages = {"TRANSLATING_CLOUD_SUBMITTED", "CLOUD_TRANSLATING", "CLOUD_REVIEWING"}
            is_cloud_stage = stage in cloud_stages

            stall_count = int(meta.get("stall_restart_count") or 0)
            if stall_count >= MAX_TASK_FAILURES and not is_cloud_stage:
                try:
                    apply_transition(
                        job_id=stem,
                        job_stem=stem,
                        from_stage=stage,
                        to_stage="DEAD",
                        worker_id="omega_manager",
                        reason=f"Stall timeout in {stage} after {stall_count} restart attempts",
                        skip_validation=_requires_validation_bypass(stage, "DEAD"),
                        status=f"DEAD: stalled in {stage}",
                        progress=0,
                        meta={
                            "halted": True,
                            "halted_at": datetime.now().isoformat(),
                            "halt_reason": f"stalled in {stage}",
                            "stall_detected_at": datetime.now().isoformat(),
                        },
                    )
                except Exception as te:
                    logger.warning(f"Transition to DEAD failed for {stem}: {te}")
                continue

            if is_cloud_stage:
                # GCS progress check: if cloud worker recently updated progress, skip stall action
                gcs_still_active = False
                try:
                    cloud_job_id = meta.get("cloud_job_id") or meta.get("gcs_job_id")
                    if cloud_job_id:
                        _bucket = str(meta.get("cloud_bucket") or config.OMEGA_JOBS_BUCKET).strip()
                        _prefix = str(meta.get("cloud_prefix") or config.OMEGA_JOBS_PREFIX).strip()
                        _paths = GcsJobPaths(bucket=_bucket, prefix=_prefix, job_id=str(cloud_job_id))
                        _sc = storage.Client()
                        _bucket_obj = _sc.bucket(_bucket)
                        for check_blob_name in [_paths.progress_json(), f"{_prefix}/{cloud_job_id}/translation_checkpoint.json"]:
                            try:
                                blob_obj = _bucket_obj.get_blob(check_blob_name)
                                if blob_obj and blob_obj.updated:
                                    blob_age = (datetime.utcnow() - blob_obj.updated.replace(tzinfo=None)).total_seconds()
                                    if blob_age < 300:  # Updated within 5 minutes
                                        logger.info(f"⏳ Cloud job {stem} still active (GCS blob updated {blob_age:.0f}s ago). Skipping stall action.")
                                        gcs_still_active = True
                                        break
                            except Exception as e:
                                logger.debug(f"GCS blob check failed for {stem} ({check_blob_name}): {e}")
                except Exception as gcs_exc:
                    logger.debug(f"GCS progress check failed for {stem}: {gcs_exc}")
                if gcs_still_active:
                    continue

                # Soft stall → hard stall: first detection = warning, second = action
                soft_stall_at = _parse_iso(meta.get("soft_stall_at"))
                if not soft_stall_at:
                    logger.warning(f"⚠️ Soft stall detected for {stem} in {stage} ({elapsed:.0f}s). Will re-check next cycle.")
                    omega_db.update_job_via_track(
                        stem,
                        status=f"Warning: possible stall in {stage}",
                        meta={"soft_stall_at": datetime.now().isoformat()},
                    )
                    continue
                # Hard stall: soft_stall_at exists and we're past threshold again
                soft_elapsed = (now - soft_stall_at).total_seconds()
                if soft_elapsed < CLOUD_STALL_RETRY_COOLDOWN_SECONDS:
                    continue

                last_cloud_stall = _parse_iso(meta.get("cloud_stall_detected_at"))
                if last_cloud_stall:
                    retry_after = (now - last_cloud_stall).total_seconds()
                    if retry_after < CLOUD_STALL_RETRY_COOLDOWN_SECONDS:
                        continue

                # Duplicate prevention: check if cloud_triggered_at is recent
                cloud_triggered_at = meta.get("cloud_triggered_at")
                if cloud_triggered_at:
                    try:
                        triggered_dt = _parse_iso(cloud_triggered_at)
                        if triggered_dt and (now - triggered_dt).total_seconds() < 600:
                            logger.info(f"⏳ Cloud job for {stem} was triggered {(now - triggered_dt).total_seconds():.0f}s ago. Skipping re-trigger.")
                            omega_db.update_job_via_track(stem, meta={"soft_stall_at": ""})
                            continue
                    except Exception as e:
                        logger.debug(f"Failed to parse cloud_triggered_at for stall check on {stem}: {e}")

                try:
                    apply_transition(
                        job_id=stem,
                        job_stem=stem,
                        from_stage=stage,
                        to_stage="TRANSLATING_CLOUD_SUBMITTED",
                        processing_step="translate_submit",
                        worker_id="stall_detector",
                        reason=f"Cloud hard stall recovery - re-triggering from {stage}",
                        skip_validation=_requires_validation_bypass(stage, "TRANSLATING_CLOUD_SUBMITTED"),
                        status="Cloud stalled; re-triggering",
                        progress=40.0,
                        meta={
                            "cloud_run_execution": "",
                            "cloud_trigger_last_attempt": 0,
                            "cloud_trigger_attempts": int(meta.get("cloud_trigger_attempts") or 0) + 1,
                            "cloud_stall_detected_at": datetime.now().isoformat(),
                            "stall_restart_count": min(stall_count + 1, MAX_TASK_FAILURES),
                            "soft_stall_at": "",
                            "halted": False,
                        },
                    )
                except Exception as e:
                    logger.warning(f"Cloud stall recovery transition failed for {stem}: {e}")
                continue

            omega_db.update_job_via_track(
                stem,
                status=f"Stalled in {stage}; restarting manager",
                progress=0,
                meta={
                    "stall_stage": stage,
                    "stall_detected_at": datetime.now().isoformat(),
                    "stall_restart_count": stall_count + 1,
                },
            )
            _request_manager_restart(force=True)
        except Exception as loop_exc:
            logger.error(f"❌ Stall detection error for {stem}: {loop_exc}")

    # 1. Recover ingest jobs (stalled or explicit retry from Vault)
    now = datetime.now()
    for job in jobs:
        stem = job.get("file_stem")
        if not stem or _is_task_active(stem):
            continue
        stage = (job.get("stage") or "").upper()
        if stage not in {"INGEST", "QUEUED"}:
            continue
        if is_in_cooldown(stem):
            continue
        try:
            meta = _job_meta(job)
            if meta.get("halted"):
                continue

            retry_requested_at = _parse_iso(meta.get("retry_requested_at"))
            retry_requested_recently = False
            if retry_requested_at:
                retry_age_seconds = (now - retry_requested_at).total_seconds()
                retry_requested_recently = retry_age_seconds <= INGEST_RETRY_RECOVERY_WINDOW_SECONDS
            explicit_retry = _is_truthy(meta.get("retry_force_ingest_recovery")) or retry_requested_recently

            updated_at = _parse_iso(job.get("updated_at"))
            stalled_ingest = (
                stage == "INGEST"
                and updated_at is not None
                and (now - updated_at).total_seconds() >= INGEST_STALL_SECONDS
            )
            if not explicit_retry and not stalled_ingest:
                continue

            skel_path = config.find_skeleton(stem)
            if skel_path:
                logger.warning("⚠️ Ingest recovery for %s: skeleton already exists. Advancing stage.", stem)
                # Record transition for audit
                try:
                    apply_transition(
                        job_id=stem,
                        job_stem=stem,
                        from_stage=stage if stage else "QUEUED",
                        to_stage="TRANSCRIBED",
                        processing_step="transcribe",
                        worker_id="omega_manager",
                        reason="Ingest recovery - skeleton exists",
                        skip_validation=True,
                        status="Ready for Translation",
                        progress=30.0,
                        meta={"retry_force_ingest_recovery": False, "retry_requested_at": ""},
                    )
                except Exception as e:
                    logger.warning(f"Ingest recovery transition failed for {stem}: {e}")
                continue

            # Check if video already in vault (prefer stored vault path / original filename)
            video_vault = None
            vault_path = meta.get("vault_path")
            if vault_path:
                candidate = Path(str(vault_path))
                if candidate.exists():
                    video_vault = candidate
            if video_vault is None:
                original_filename = meta.get("original_filename")
                if original_filename:
                    candidate = config.VAULT_VIDEOS / original_filename
                    if candidate.exists():
                        video_vault = candidate
            if video_vault is None:
                original_stem = meta.get("original_stem")
                if original_stem:
                    candidate = _find_vault_video(original_stem)
                    if candidate and candidate.exists():
                        video_vault = candidate

            if video_vault and video_vault.exists():
                reason = "retry request" if explicit_retry else "stall detector"
                logger.warning(
                    "⚠️ Ingest recovery for %s (%s): video exists in Vault. Re-running transcription.",
                    stem,
                    reason,
                )
                try:
                    apply_transition(
                        job_id=stem,
                        job_stem=stem,
                        from_stage=stage if stage else "QUEUED",
                        to_stage="INGEST",
                        worker_id="omega_manager",
                        reason=f"Ingest retry from Vault ({reason})",
                        skip_validation=True,
                        status="Retrying ingest from Vault",
                        progress=10.0,
                        meta={"retry_force_ingest_recovery": False, "retry_requested_at": ""},
                    )
                except Exception as e:
                    logger.warning(f"Ingest retry transition failed for {stem}: {e}")
                if _add_task(stem):
                    executor.submit(task_wrapper, stem, "IngestRecovery", _run_ingest_recovery, stem, video_vault)
                continue

            if explicit_retry:
                omega_db.update_job_via_track(
                    stem,
                    status="Retry blocked: source video missing in Vault",
                    progress=0.0,
                    meta={"retry_force_ingest_recovery": False},
                )
        except Exception as loop_exc:
            logger.error(f"❌ Ingest recovery error for {stem}: {loop_exc}")

    # 2. TRANSCRIBED -> TRANSLATING (submit to Cloud Run or local worker)
    # Calculate initial translating count for concurrency gate
    MAX_CONCURRENT_TRANSLATIONS = int(os.environ.get("OMEGA_MAX_CONCURRENT_TRANSLATIONS", "2"))
    translating_stages = {"TRANSLATING", "TRANSLATING_CLOUD_SUBMITTED", "CLOUD_TRANSLATING", "CLOUD_REVIEWING"}
    currently_translating = sum(
        1 for _, j in jobs_by_stem.items()
        if (j.get("stage") or "").upper() in translating_stages
    )

    # 1. TRANSCRIBED -> TRANSLATING (submit to Cloud Run or local worker)
    for job in jobs:
        stem = job.get("file_stem")
        if not stem or _is_task_active(stem):
            continue
        if is_in_cooldown(stem):
            continue
        try:
            meta = _job_meta(job)
            if meta.get("halted"):
                continue

            # Check stage FIRST - database is source of truth, not file presence
            stage = (job.get("stage") or "").upper()
            if stage in {"COMPLETED", "DELIVERED", "BURNING", "FINALIZING"}:
                # Already past translation stage - skip without file manipulation
                continue

            # Auto-correct if output exists but stage is wrong
            if _autocorrect_completed(stem, job):
                continue  # Stage corrected to COMPLETED, skip

            # Skeleton check - use centralized lookup
            skel = config.find_skeleton(stem)
            if not skel:
                continue

            if stage in {"QUEUED", "INGEST", ""}:
                try:
                    apply_transition(
                        job_id=stem,
                        job_stem=stem,
                        from_stage=stage if stage else "QUEUED",
                        to_stage="TRANSCRIBED",
                        processing_step="transcribe",
                        worker_id="omega_manager",
                        reason="Stage auto-correction - skeleton exists",
                        status="Ready for Translation",
                        progress=30.0,
                    )
                except Exception as e:
                    logger.warning(f"Stage auto-correction transition failed for {stem}: {e}")
                stage = "TRANSCRIBED"
            if stage not in {"TRANSCRIBED", "TRANSLATING"}:
                continue

            if not _cloud_pipeline_enabled():
                logger.error("Local translation workflow is disabled. Set OMEGA_CLOUD_PIPELINE=1.")
                omega_db.update_job_via_track(
                    stem,
                    status="Misconfigured: OMEGA_CLOUD_PIPELINE must be enabled",
                    progress=30.0,
                    meta={"blocked_reason": "cloud_pipeline_disabled"},
                )
                continue

            target_language = job.get("target_language", "is")

            # Concurrency gate: max 2 translations at a time to prevent API quota issues
            if currently_translating >= MAX_CONCURRENT_TRANSLATIONS:
                # Skip this job for now; it will be picked up in the next cycle
                logger.debug(f"⏳ Waiting to translate {stem}: {currently_translating} jobs already translating (max {MAX_CONCURRENT_TRANSLATIONS})")
                continue

            _add_task(stem)
            currently_translating += 1 # Local increment for this loop

            executor.submit(task_wrapper, stem, "Translate (Cloud)", _run_translate_cloud, skel, stem, target_language)
        except Exception as loop_exc:
            logger.error(f"❌ Translation dispatch error for {stem}: {loop_exc}")

    # 1b. CLOUD TRANSLATION/REVIEW -> REVIEWED (download approved.json)
    if _cloud_pipeline_enabled():
        ensure_google_application_credentials()
        try:
            storage_client = storage.Client()
        except Exception as e:
            logger.error("❌ Failed to initialize GCS client: %s", e)
            storage_client = None

        if storage_client:
            for job_entry in jobs:
                stem = job_entry.get("file_stem")
                if not stem:
                    continue

                stage = (job_entry.get("stage") or "").upper()
                if stage not in {
                    "TRANSLATING_CLOUD_SUBMITTED",
                    "CLOUD_TRANSLATING",
                    "CLOUD_REVIEWING",
                }:
                    continue

                try:
                    meta = _job_meta(job_entry)
                    if meta.get("halted"):
                        continue

                    cloud_job_id = meta.get("cloud_job_id") or meta.get("gcs_job_id")
                    if not cloud_job_id:
                        continue

                    bucket_name = str(meta.get("cloud_bucket") or config.OMEGA_JOBS_BUCKET).strip()
                    prefix = str(meta.get("cloud_prefix") or config.OMEGA_JOBS_PREFIX).strip()
                    paths = GcsJobPaths(bucket=bucket_name, prefix=prefix, job_id=str(cloud_job_id))

                    # If Cloud Run auto-trigger is configured, retry triggering any submitted jobs
                    # that don't have an execution recorded yet (e.g., first-time setup).
                    cloud_run_job = getattr(config, "OMEGA_CLOUD_RUN_JOB", "").strip()
                    cloud_run_region = config.OMEGA_CLOUD_RUN_REGION
                    cloud_run_project = getattr(config, "OMEGA_CLOUD_PROJECT", "").strip() or None
                    # Fixed: Check for invalid execution values (None, empty, or "unknown" from dict access bug)
                    # Also check if recently triggered to avoid duplicate submissions
                    execution_value = meta.get("cloud_run_execution")
                    cloud_triggered_at = meta.get("cloud_triggered_at")
                    recently_triggered = False
                    if cloud_triggered_at:
                        try:
                            triggered_dt = _parse_iso(cloud_triggered_at)
                            if triggered_dt and (datetime.now() - triggered_dt).total_seconds() < 300:
                                recently_triggered = True
                        except Exception as e:
                            logger.debug(f"Failed to parse cloud_triggered_at for {stem}: {e}")
                    needs_trigger = execution_value in (None, "", "unknown") and not recently_triggered
                    # GUARDRAIL: Cap total Cloud Run trigger attempts to prevent runaway cost
                    MAX_CLOUD_TRIGGERS = 3  # Max 3 full Cloud Run executions per job
                    if (
                        cloud_run_job
                        and stage == "TRANSLATING_CLOUD_SUBMITTED"
                        and needs_trigger
                    ):
                        now = time.time()
                        attempts = int(meta.get("cloud_trigger_attempts") or 0)
                        if attempts >= MAX_CLOUD_TRIGGERS:
                            logger.error(
                                f"🛑 COST GUARDRAIL: {stem} has reached {attempts} Cloud Run trigger attempts "
                                f"(limit: {MAX_CLOUD_TRIGGERS}). Marking as DEAD to prevent runaway costs."
                            )
                            try:
                                apply_transition(
                                    job_id=stem,
                                    job_stem=stem,
                                    from_stage=stage,
                                    to_stage="DEAD",
                                    worker_id="omega_manager",
                                    reason=f"Cost guardrail: exceeded {MAX_CLOUD_TRIGGERS} Cloud Run attempts",
                                    skip_validation=_requires_validation_bypass(stage, "DEAD"),
                                    status=f"DEAD: exceeded {MAX_CLOUD_TRIGGERS} Cloud Run attempts",
                                    meta={"halted": True, "halt_reason": "cost_guardrail_max_triggers"},
                                )
                            except Exception as te:
                                logger.warning(f"Cost guardrail transition to DEAD failed for {stem}: {te}")
                            continue
                        last_attempt = float(meta.get("cloud_trigger_last_attempt") or 0.0)
                        backoff = min(2 ** max(0, attempts), 300.0)
                        if now - last_attempt >= backoff:
                            omega_db.update_job_via_track(stem, status="Triggering cloud worker…")
                            args = [
                                "--job-id",
                                str(cloud_job_id),
                                "--bucket",
                                bucket_name,
                                "--prefix",
                                prefix,
                            ]
                            try:
                                resp = run_cloud_run_job(
                                    job_name=cloud_run_job,
                                    region=cloud_run_region,
                                    project=cloud_run_project,
                                    args=args,
                                )
                                omega_db.update_job_via_track(
                                    stem,
                                    status="Cloud worker started",
                                    meta={
                                        "cloud_run_execution": resp.get("name"),
                                        "cloud_triggered_at": datetime.now().isoformat(),
                                        "cloud_trigger_attempts": attempts,
                                        "cloud_trigger_last_attempt": now,
                                    },
                                )
                            except Exception as e:
                                omega_db.update_job_via_track(
                                    stem,
                                    status=f"Cloud trigger failed: {e}",
                                    meta={
                                        "cloud_trigger_error": str(e),
                                        "cloud_trigger_failed_at": datetime.now().isoformat(),
                                        "cloud_trigger_attempts": attempts + 1,
                                        "cloud_trigger_last_attempt": now,
                                    },
                                )

                    # Optional: reflect cloud progress into the dashboard.
                    try:
                        if blob_exists(storage_client, bucket_name, paths.progress_json()):
                            progress_payload = download_json(
                                storage_client,
                                bucket=bucket_name,
                                blob_name=paths.progress_json(),
                            )
                            if isinstance(progress_payload, dict):
                                status = progress_payload.get("status")
                                progress = progress_payload.get("progress")
                                if status or progress is not None:
                                    progress_meta = progress_payload.get("meta") if isinstance(progress_payload.get("meta"), dict) else {}
                                    cloud_progress = {
                                        "stage": progress_payload.get("stage"),
                                        "status": status,
                                        "progress": progress,
                                        "updated_at": progress_payload.get("updated_at"),
                                        "segments_done": progress_meta.get("segments_done"),
                                        "segments_total": progress_meta.get("segments_total"),
                                        "meta": progress_meta,
                                    }
                                    omega_db.update_job_via_track(
                                        stem,
                                        status=str(status) if status else None,
                                        progress=float(progress) if progress is not None else None,
                                        meta={
                                            "cloud_stage": progress_payload.get("stage"),
                                            "cloud_progress": cloud_progress,
                                            "cloud_last_poll_at": datetime.now().isoformat(),
                                        },
                                    )
                    except Exception as e:
                        logger.warning(f"Failed to update cloud progress for {stem}: {e}")

                    # IMPORTANT: If cloud_sync_service is enabled, skip manager's sync logic.
                    # Let the dedicated cloud_sync_service.py handle downloads to avoid race conditions.
                    cloud_sync_enabled = os.environ.get("OMEGA_CLOUD_SYNC_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
                    if cloud_sync_enabled:
                        # Cloud sync service handles this - skip to avoid duplicate work and race conditions
                        continue

                    # Fallback: If cloud sync service is disabled, use idempotent sync (blocking but safe)
                    try:
                        # Phase 1: Use Idempotent Sync with correct GCS prefix
                        _sync_cloud_approved_idempotent(stem, storage_client, bucket_name, prefix)

                        # Check if review triggered (only if synced successfully)
                        updated_job = omega_db.get_job_via_track(stem)
                        if updated_job and updated_job.get("stage") == "REVIEWED":
                            if _trigger_review_portal(stem, _job_meta(updated_job), updated_job):
                                 continue

                    except Exception as e:
                        logger.error("❌ Failed to sync cloud approval for %s: %s", stem, e)
                except Exception as loop_exc:
                    logger.error(f"❌ Cloud poll error for {stem}: {loop_exc}")

            # Backfill editor reports for cloud-completed jobs that already advanced stages.
            for job_entry in jobs:
                stem = job_entry.get("file_stem")
                if not stem or job_entry.get("editor_report"):
                    continue
                meta = _job_meta(job_entry)
                if meta.get("cloud_stage") != "CLOUD_DONE":
                    continue
                cloud_job_id = meta.get("cloud_job_id") or meta.get("gcs_job_id")
                if not cloud_job_id:
                    continue
                bucket_name = str(meta.get("cloud_bucket") or config.OMEGA_JOBS_BUCKET).strip()
                prefix = str(meta.get("cloud_prefix") or config.OMEGA_JOBS_PREFIX).strip()
                paths = GcsJobPaths(bucket=bucket_name, prefix=prefix, job_id=str(cloud_job_id))
                try:
                    if not blob_exists(storage_client, bucket_name, paths.editor_report_json()):
                        continue
                    report_payload = download_json(
                        storage_client,
                        bucket=bucket_name,
                        blob_name=paths.editor_report_json(),
                    )
                    omega_db.update_job_via_track(stem, editor_report=json.dumps(report_payload or {}))
                    logger.info("✅ Cloud editor report backfilled: %s", paths.editor_report_json())
                except Exception as e:
                    logger.error("❌ Failed to backfill cloud editor report for %s: %s", stem, e)

            # 1c. HUMAN REVIEW PORTAL -> Check for reviewed translations
            for job_entry in jobs:
                stem = job_entry.get("file_stem")
                if not stem:
                    continue
                meta = _job_meta(job_entry)
                
                # Only check jobs waiting for human review
                if not meta.get("review_notification_sent"):
                    continue
                if meta.get("human_review_complete"):
                    continue
                
                # Check for reviewed.json in GCS
                cloud_job_id = meta.get("cloud_job_id") or meta.get("review_portal_job_id")
                if not cloud_job_id:
                    continue
                    
                bucket_name = str(meta.get("cloud_bucket") or config.OMEGA_JOBS_BUCKET).strip()
                prefix = str(meta.get("cloud_prefix") or config.OMEGA_JOBS_PREFIX).strip()
                reviewed_blob = f"{prefix}/{cloud_job_id}/{cloud_job_id}_REVIEWED.json"
                
                try:
                    if not blob_exists(storage_client, bucket_name, reviewed_blob):
                        continue
                    
                    # Download the reviewed translation
                    reviewed_payload = download_json(
                        storage_client,
                        bucket=bucket_name,
                        blob_name=reviewed_blob,
                    )
                    
                    # Save to local approved location
                    local_approved = config.TRANSLATED_DONE_DIR / f"{stem}_APPROVED.json"
                    with open(local_approved, "w", encoding="utf-8") as f:
                        json.dump(reviewed_payload.get("segments", reviewed_payload), f, indent=2, ensure_ascii=False)

                    try:
                        apply_transition(
                            job_id=stem,
                            job_stem=stem,
                            from_stage="AWAITING_REVIEW",
                            to_stage="REVIEWED",
                            processing_step="human_review",
                            worker_id="omega_manager",
                            reason="Human review portal approval received",
                            skip_validation=_requires_validation_bypass("AWAITING_REVIEW", "REVIEWED"),
                            status="Human Review Complete",
                            progress=72.0,
                            meta={
                                "human_review_complete": True,
                                "human_review_completed_at": datetime.now().isoformat(),
                                "human_reviewer": reviewed_payload.get("approved_by", "Reviewer"),
                            },
                        )
                    except Exception as e:
                        logger.warning(f"Human review transition failed for {stem}: {e}")
                    logger.info("✅ Human review complete: %s (by %s)", stem, reviewed_payload.get("approved_by", "Reviewer"))
                    
                except Exception as e:
                    logger.error("❌ Failed to check human review for %s: %s", stem, e)

    # 2. TRANSLATED -> REVIEWING (Editor)
    for trans in config.EDITOR_DIR.glob("*.json"):
        if _is_hidden_artifact(trans):
            continue
        if trans.name.endswith("_SKELETON.json"): continue
        if trans.name.endswith("_APPROVED.json"): continue

        parts = trans.stem.split("_")
        if len(parts) < 2: continue
        stem = "_".join(parts[:-1])

        if _is_task_active(stem): continue
        if is_in_cooldown(stem): continue

        try:
            # Verify with DB
            job = jobs_by_stem.get(stem) or omega_db.get_job_via_track(stem)
            if not job:
                 if trans.name.endswith("_ICELANDIC.json"):
                     stem = trans.stem.replace("_ICELANDIC", "")
                 else:
                     continue

            meta = _job_meta(job)
            if meta.get("halted"):
                continue
            if _autocorrect_completed(stem, job):
                continue

            stage = (job.get("stage") or "").upper()
            if stage in {"TRANSCRIBED", "TRANSLATING"}:
                try:
                    apply_transition(
                        job_id=stem,
                        job_stem=stem,
                        from_stage=stage,
                        to_stage="TRANSLATED",
                        processing_step="translate_detect",
                        worker_id="omega_manager",
                        reason="Local translation file detected",
                        status="Ready for Review",
                        progress=55.0,
                        meta={"translation_path": str(trans)},
                    )
                except Exception as e:
                    logger.warning(f"Transition to TRANSLATED failed for {stem}: {e}")
                stage = "TRANSLATED"
            if stage not in {"TRANSLATED", "REVIEWING"}:
                continue

            _add_task(stem)
            executor.submit(task_wrapper, stem, "Review", _run_review, trans, stem)
        except Exception as loop_exc:
            logger.error(f"❌ Review dispatch error for {stem}: {loop_exc}")

    # 3. REVIEWED -> FINALIZING (Finalizer)
    review_storage_client = None
    for approved in config.TRANSLATED_DONE_DIR.glob("*_APPROVED.json"):
        if _is_hidden_artifact(approved):
            continue
        stem = approved.stem.replace("_APPROVED", "")
        if _is_task_active(stem): continue
        if is_in_cooldown(stem): continue
        
        try:
            # Race condition guard: if cloud_sync_service is actively downloading
            # this artifact, skip and let it finish (avoids partial file reads)
            try:
                sync_state_rec = omega_db.get_sync_state(stem, 'approved_json')
                if sync_state_rec and sync_state_rec.get('state') in ('DOWNLOADING', 'DOWNLOADED'):
                    logger.debug(f"Skipping {stem}: cloud_sync is handling approved.json (state={sync_state_rec.get('state')})")
                    continue
            except Exception as e:
                logger.debug(f"Sync state check failed for {stem}, proceeding: {e}")

            job = jobs_by_stem.get(stem) or omega_db.get_job_via_track(stem)
            if job:
                meta = _job_meta(job)
                if meta.get("halted"):
                    continue
                if _autocorrect_completed(stem, job):
                    continue
                stage = (job.get("stage") or "").upper()
                if stage in {"TRANSLATED", "REVIEWING"}:
                    # Use artifact lock to prevent concurrent reads with cloud_sync_service
                    with job_artifact_lock(stem, "approved_json"):
                        try:
                            apply_transition(
                                job_id=stem,
                                job_stem=stem,
                                from_stage=stage,
                                to_stage="REVIEWED",
                                processing_step="review_detect",
                                worker_id="omega_manager",
                                reason="Approved JSON file detected on disk",
                                status="Editor Approved",
                                progress=70.0,
                            )
                        except Exception as e:
                            logger.warning(f"Transition to REVIEWED failed for {stem}: {e}")
                    stage = "REVIEWED"
                    # Mark sync state as SYNCED since approved.json is present on disk
                    try:
                        sync_state_val = omega_db.get_sync_state(stem, 'approved_json')
                        if sync_state_val and sync_state_val.get('state') != 'SYNCED':
                            omega_db.update_sync_state(stem, 'approved_json', 'SYNCED')
                            logger.info(f"🔄 Marked sync state SYNCED for {stem} (approved.json on disk)")
                    except Exception as e:
                        logger.warning(f"Failed to update sync state to SYNCED for {stem}: {e}")
                if stage not in {"REVIEWED", "FINALIZING"}:
                    continue

                source_path = str(meta.get("source_path") or "")
                remote_review_required = bool(meta.get("remote_review_required")) or ("/03_remote_review/" in source_path.lower())
                if remote_review_required and not meta.get("remote_review_done"):
                    paths, bucket_name, prefix = _cloud_job_paths(meta)
                    if not paths or not bucket_name:
                        omega_db.update_job_via_track(
                            stem,
                            status="Blocked: Remote review missing cloud job",
                            meta={"remote_review_error": "missing_cloud_job"},
                        )
                        continue

                    if review_storage_client is None:
                        try:
                            ensure_google_application_credentials()
                            review_storage_client = storage.Client()
                        except Exception as e:
                            omega_db.update_job_via_track(
                                stem,
                                status=f"Blocked: Remote review auth failed ({e})",
                                meta={"remote_review_error": str(e)},
                            )
                            continue

                    portal_url = _review_portal_url()
                    recipients = _reviewer_emails(meta)
                    if not portal_url or not recipients:
                        omega_db.update_job_via_track(
                            stem,
                            status="Blocked: Remote review not configured",
                            meta={
                                "remote_review_error": "missing_portal_or_email",
                                "remote_review_portal": portal_url,
                            },
                        )
                        continue

                    requested = bool(meta.get("remote_review_requested"))
                    last_attempt = float(meta.get("remote_review_last_attempt") or 0.0)
                    email_attempts = int(meta.get("remote_review_email_attempts") or 0)
                    now = time.time()

                    # Max 3 email attempts, then give up on email but continue with review
                    MAX_EMAIL_ATTEMPTS = 3

                    if not requested and (now - last_attempt) >= 300:
                        review_payload = _build_review_payload(
                            stem=stem,
                            approved_path=approved,
                            target_language=job.get("target_language", "is"),
                            program_profile=job.get("program_profile", "standard"),
                        )
                        token = secrets.token_urlsafe(32)
                        expires_at = (datetime.utcnow() + timedelta(days=7)).isoformat() + "Z"
                        try:
                            upload_json(review_storage_client, bucket=bucket_name, blob_name=paths.review_json(), payload=review_payload)
                            upload_json(
                                review_storage_client,
                                bucket=bucket_name,
                                blob_name=paths.review_token_json(),
                                payload={"token": token, "expires_at": expires_at},
                            )
                            review_url = f"{portal_url.rstrip('/')}/review/{paths.job_id}?token={token}"

                            # Try to send email, but don't block if it fails
                            email_sent = False
                            if email_attempts < MAX_EMAIL_ATTEMPTS:
                                email_sent = _send_review_email(stem=stem, review_url=review_url, recipients=recipients)

                            # Mark as requested even if email failed after max attempts
                            # The review portal is still accessible via the URL
                            mark_requested = email_sent or (email_attempts >= MAX_EMAIL_ATTEMPTS - 1)

                            if not email_sent and email_attempts >= MAX_EMAIL_ATTEMPTS - 1:
                                logger.warning(f"[{stem}] Email failed after {MAX_EMAIL_ATTEMPTS} attempts. Review available at: {review_url}")

                            omega_db.update_job_via_track(
                                stem,
                                status="Waiting for Remote Review" + ("" if email_sent else " (email failed)"),
                                progress=70.0,
                                meta={
                                    "remote_review_requested": mark_requested,
                                    "remote_review_sent_at": datetime.utcnow().isoformat() + "Z" if email_sent else None,
                                    "remote_review_last_attempt": now,
                                    "remote_review_url": review_url,
                                    "remote_review_expires_at": expires_at,
                                    "remote_review_email_attempts": email_attempts + 1,
                                    "remote_review_email_failed": not email_sent,
                                },
                            )
                        except Exception as e:
                            omega_db.update_job_via_track(
                                stem,
                                status=f"Remote review send failed: {e}",
                                meta={
                                    "remote_review_last_attempt": now,
                                    "remote_review_error": str(e),
                                    "remote_review_email_attempts": email_attempts + 1,
                                },
                            )
                        continue

                    # Check for review completion - try multiple patterns
                    # Pattern 1: review_corrections.json (legacy)
                    # Pattern 2: {job_id}_REVIEWED.json (review portal)
                    # Pattern 3: review_status.json (approval status)
                    review_blob_name = None
                    if blob_exists(review_storage_client, bucket_name, paths.review_corrections_json()):
                        review_blob_name = paths.review_corrections_json()
                    elif blob_exists(review_storage_client, bucket_name, paths.reviewed_json()):
                        review_blob_name = paths.reviewed_json()
                    elif blob_exists(review_storage_client, bucket_name, paths.review_status_json()):
                        # If only status exists, check if approved
                        try:
                            status_data = download_json(review_storage_client, bucket=bucket_name, blob_name=paths.review_status_json())
                            if status_data.get("status") == "approved":
                                review_blob_name = paths.reviewed_json()  # Try to get segments from _REVIEWED.json
                        except Exception as e:
                            logger.warning(f"Failed to download review status JSON for {stem}: {e}")

                    if review_blob_name and blob_exists(review_storage_client, bucket_name, review_blob_name):
                        try:
                            corrections_payload = download_json(
                                review_storage_client,
                                bucket=bucket_name,
                                blob_name=review_blob_name,
                            )
                            # Handle both formats: {"corrections": [...]} or {"segments": [...]}
                            if "corrections" in corrections_payload:
                                corrections = corrections_payload.get("corrections", [])
                                applied, comment_count = _apply_remote_corrections(
                                    approved_path=approved,
                                    corrections=corrections or [],
                                )
                            elif "segments" in corrections_payload:
                                # _REVIEWED.json from portal has full segments
                                # Replace entire approved file with reviewed segments
                                with open(approved, 'w', encoding='utf-8') as f:
                                    json.dump(corrections_payload, f, indent=2, ensure_ascii=False)
                                applied = len(corrections_payload.get("segments", []))
                                comment_count = 0
                                logger.info(f"✅ Applied reviewed segments from portal for {stem}")
                            else:
                                corrections = []
                                applied, comment_count = 0, 0

                            omega_db.update_job_via_track(
                                stem,
                                status="Remote Review Applied",
                                progress=70.0,
                                meta={
                                    "remote_review_done": True,
                                    "remote_review_applied": applied,
                                    "remote_review_comment_count": comment_count,
                                    "remote_review_received_at": datetime.utcnow().isoformat() + "Z",
                                },
                            )
                        except Exception as e:
                            omega_db.update_job_via_track(
                                stem,
                                status=f"Remote review apply failed: {e}",
                                meta={"remote_review_error": str(e)},
                            )
                        continue

                    omega_db.update_job_via_track(stem, status="Waiting for Remote Review", progress=70.0)
                    continue

            if (config.SRT_DIR / f"{stem}.srt").exists(): continue
            if (config.VIDEO_DIR / f"{stem}_SUBBED.mp4").exists(): continue

            _add_task(stem)
            executor.submit(task_wrapper, stem, "Finalize", _run_finalize, approved, stem)
        except Exception as loop_exc:
            logger.error(f"❌ Finalize dispatch error for {stem}: {loop_exc}")

    # 4. FINALIZED -> BURNING (Publisher)
    # 4. FINALIZED -> BURNING (Publisher)
    # Calculate initial burning count for concurrency gate (M2 Max optimized)
    MAX_CONCURRENT_BURNS = int(os.environ.get("OMEGA_MAX_CONCURRENT_BURNS", "2"))
    currently_burning = sum(
        1 for _, j in jobs_by_stem.items()
        if (j.get("stage") or "").upper() == "BURNING"
    )

    for srt in config.SRT_DIR.glob("*.srt"):
        if _is_hidden_artifact(srt):
            continue
        if srt.name.startswith("DONE_"): continue
        stem = srt.stem
        if _is_task_active(stem):
            continue
        if is_in_cooldown(stem): continue

        try:
            job = jobs_by_stem.get(stem) or omega_db.get_job_via_track(stem)
            if job and _autocorrect_completed(stem, job):
                # Stop re-triggering from stale SRTs.
                done_srt = srt.parent / f"DONE_{srt.name}"
                try:
                    shutil.move(str(srt), str(done_srt))
                except Exception as e:
                    logger.debug(f"Failed to rename stale SRT {srt.name} to DONE_: {e}")
                continue

            legacy_output = config.VIDEO_DIR / f"{stem}_SUBBED.mp4"
            if legacy_output.exists():
                # Auto-Correction: If video exists but DB says otherwise, mark as DONE.
                if job and job.get("stage") != "COMPLETED":
                    logger.info(f"✅ Auto-Correcting Status for {stem} (Video Exists)")
                    current_stage = (job.get("stage") or "FINALIZED").upper()
                    try:
                        apply_transition(
                            job_id=stem,
                            job_stem=stem,
                            from_stage=current_stage,
                            to_stage="COMPLETED",
                            worker_id="omega_manager",
                            reason="Auto-correction: legacy output video exists",
                            skip_validation=_requires_validation_bypass(current_stage, "COMPLETED"),
                            status="Done",
                            progress=100.0,
                            meta={"final_output": str(legacy_output), "last_error": "", "failed_at": ""},
                        )
                    except Exception as e:
                        logger.warning(f"Auto-correction transition to COMPLETED failed for {stem}: {e}")
                # Stop re-triggering from stale SRTs.
                done_srt = srt.parent / f"DONE_{srt.name}"
                try:
                    shutil.move(str(srt), str(done_srt))
                except Exception as e:
                    logger.debug(f"Failed to rename stale SRT {srt.name} to DONE_: {e}")
                continue

            # Pre-Burn Gate
            if not job:
                continue

            meta = _job_meta(job)
            if meta.get("halted"):
                continue

            stage = (job.get("stage") or "").upper()
            if stage in {"REVIEWED", "FINALIZING"}:
                try:
                    apply_transition(
                        job_id=stem,
                        job_stem=stem,
                        from_stage=stage,
                        to_stage="FINALIZED",
                        processing_step="finalize",
                        worker_id="omega_manager",
                        reason="SRT exists, advancing to ready-to-burn state",
                        status="Ready to Burn",
                        progress=90.0,
                    )
                except Exception as e:
                    logger.warning(f"Transition to FINALIZED failed for {stem}: {e}")
                stage = "FINALIZED"
            if stage not in {"FINALIZED", "BURNING"}:
                continue

            status = job.get("status", "")
            source_path = str(meta.get("source_path") or "")
            review_required = bool(meta.get("review_required")) or ("/02_human_review/" in source_path.lower())
            burn_approved = bool(meta.get("burn_approved")) or (
                status in {"Approved for Burn", "Approved - queued for burn"}
            )

            if review_required and not burn_approved:
                if config.OMEGA_ALLOW_AUTO_BURN:
                    # Zero-Touch: Bypass manual approval
                    pass
                else:
                    if status != "Waiting for Burn Approval":
                        omega_db.update_job_via_track(
                            stem,
                            status="Waiting for Burn Approval",
                            progress=90.0,
                            meta={"review_required": review_required},
                        )
                        logger.info(f"🛑 Pre-Burn Gate: Stopping {stem} (Waiting for Approval)")
                    continue

            # Concurrency gate: max 2 burns at a time to prevent hardware contention (M2 Max)
            if currently_burning >= MAX_CONCURRENT_BURNS:
                 logger.debug(f"⏳ Waiting to burn {stem}: {currently_burning} jobs already burning (max {MAX_CONCURRENT_BURNS})")
                 continue

            logger.info(f"🔍 Found candidate for burning: {stem}")
            currently_burning += 1 # Local increment

            _add_task(stem)
            executor.submit(task_wrapper, stem, "Burn", _run_burn, srt, stem)
        except Exception as loop_exc:
            logger.error(f"❌ Burn dispatch error for {stem}: {loop_exc}")


def _run_translate_cloud(skel, stem, target_language):
    """
    Cloud-first path: Trigger the Cloud Run worker.
    The worker (omega_cloud_worker.py) now contains the Gemini 2-Step Pipeline (Translate + Review/Polish).
    """
    logger.info("☁️ Triggering Cloud Run for: %s (%s)", stem, str(target_language).upper())

    ensure_google_application_credentials()

    # Trigger Cloud Run
    # We use the configured Cloud Run job name (fixed: use correct env var names)
    job_name = os.environ.get("OMEGA_CLOUD_RUN_JOB", "omega-cloud-worker")
    region = os.environ.get("OMEGA_CLOUD_RUN_REGION", "us-central1")
    project = os.environ.get("OMEGA_CLOUD_PROJECT") or None
    bucket_name = config.OMEGA_JOBS_BUCKET
    prefix = config.OMEGA_JOBS_PREFIX

    # We pass overrides to the job to specify which job_id to process
    # Include bucket/prefix for explicit control (worker has defaults, but explicit is safer)
    args = ["--job-id", stem, "--bucket", bucket_name, "--prefix", prefix]

    logger.info(f"☁️ Cloud Run trigger: job={job_name}, region={region}, project={project or 'default'}")

    # Guardrail: do not trigger cloud execution when required payload artifacts
    # were never uploaded (prevents permanent TRANSLATING_CLOUD_SUBMITTED stalls).
    storage_client = storage.Client()
    paths = GcsJobPaths(bucket=bucket_name, prefix=prefix, job_id=stem)
    required_blobs = {
        "job.json": paths.job_blob,
        "skeleton.json": paths.skeleton_blob,
    }
    missing = [name for name, blob in required_blobs.items() if not blob_exists(storage_client, bucket_name, blob)]
    if missing:
        missing_list = ", ".join(missing)
        raise RuntimeError(
            f"Cloud payload missing for {stem}: {missing_list}. "
            "Retry ingest recovery to re-upload artifacts."
        )

    try:
        execution = run_cloud_run_job(job_name=job_name, args=args, region=region, project=project)
        # Fixed: run_cloud_run_job returns a dict, not an object - use dict access
        execution_name = execution.get("name", "unknown") if isinstance(execution, dict) else "unknown"
        logger.info(f"🚀 Triggered Cloud Run: {execution_name}")

        try:
            apply_transition(
                job_id=stem,
                job_stem=stem,
                from_stage="TRANSCRIBED",
                to_stage="TRANSLATING_CLOUD_SUBMITTED",
                processing_step="translate_cloud",
                worker_id="omega_manager",
                reason="Cloud Run job triggered for translation",
                status="Submitted to Cloud",
                progress=40.0,
                meta={
                    "cloud_run_execution": execution_name,
                    "cloud_triggered_at": datetime.now().isoformat(),
                },
            )
        except Exception as e:
            logger.warning(f"Transition TRANSCRIBED->TRANSLATING_CLOUD_SUBMITTED failed for {stem}: {e}")
    except Exception as e:
        logger.error(f"❌ Failed to trigger Cloud Run: {e}")
        omega_db.update_job_via_track(stem, status=f"Cloud Trigger Failed: {e}")
        raise e

def _run_review(trans, stem):
    logger.info(f"🕵️‍♂️ Reviewing: {stem}")
    job = omega_db.get_job_via_track(stem)
    review_from_stage = (job.get("stage") or "TRANSLATED").upper() if job else "TRANSLATED"
    try:
        apply_transition(
            job_id=stem,
            job_stem=stem,
            from_stage=review_from_stage,
            to_stage="REVIEWING",
            processing_step="review",
            worker_id="omega_manager",
            reason="Starting AI editor review",
            status="AI Reviewing",
            progress=60.0,
        )
    except Exception as e:
        logger.warning(f"Transition to REVIEWING failed for {stem}: {e}")

    editor.review(trans)
    track = omega_db.get_track_by_job(stem)
    master_script_id = track.get("master_script_id") if track else None
    if master_script_id:
        omega_db.update_master_script(master_script_id, state="approved")
    try:
        apply_transition(
            job_id=stem,
            job_stem=stem,
            from_stage="REVIEWING",
            to_stage="REVIEWED",
            processing_step="review",
            worker_id="omega_manager",
            reason="AI editor review completed",
            status="Editor Approved",
            progress=70.0,
        )
    except Exception as e:
        logger.warning(f"Transition REVIEWING->REVIEWED failed for {stem}: {e}")

def _run_finalize(approved, stem):
    logger.info(f"🎬 Finalizing: {stem}")
    job = omega_db.get_job_via_track(stem)
    current_stage = (job.get("stage") or "REVIEWED").upper() if job else "REVIEWED"
    try:
        apply_transition(
            job_id=stem,
            job_stem=stem,
            from_stage=current_stage,
            to_stage="FINALIZING",
            processing_step="finalize",
            worker_id="omega_manager",
            reason="Starting SRT generation",
            status="Finalizing",
            progress=80.0,
        )
    except Exception as e:
        logger.warning(f"Transition to FINALIZING failed for {stem}: {e}")

    with job_artifact_lock(stem, timeout_seconds=120.0):
        job = omega_db.get_job_via_track(stem)
        target_language = job.get("target_language", "is") if job else "is"
        approved_path = Path(approved)
        integrity_report = _ensure_text_integrity(
            stem=stem,
            approved_path=approved_path,
            target_language=target_language,
        )
        if integrity_report.get("enabled"):
            try:
                omega_db.update_job_via_track(stem, meta={"qa_text_integrity": integrity_report})
            except Exception as e:
                logger.warning(f"Text integrity QA meta update failed for {stem}: {e}")

        # Always run full finalization — even for pre-normalized segments.
        # The finalizer applies broadcast-quality passes that segments_to_srt() skips:
        # CPS optimization, orphan rescue, duration enforcement, gap enforcement,
        # scene-aware timing, frame quantization, and emergency merge.

        # Find video path for scene-aware timing (optional but recommended)
        video_path = None
        meta = job.get("meta", {}) if job else {}
        vault_path = meta.get("vault_path")
        if vault_path:
            candidate = Path(str(vault_path))
            if candidate.exists():
                video_path = candidate
        if video_path is None:
            original_filename = meta.get("original_filename")
            if original_filename:
                candidate = config.VAULT_VIDEOS / original_filename
                if candidate.exists():
                    video_path = candidate
        if video_path is None:
            original_stem = meta.get("original_stem")
            video_path = _find_vault_video(original_stem or stem)

        # Finalize with video-aware timing if video found
        finalizer.finalize(
            approved,
            target_language=target_language,
            video_path=video_path,
            apply_scene_snap=video_path is not None,
        )

    try:
        apply_transition(
            job_id=stem,
            job_stem=stem,
            from_stage="FINALIZING",
            to_stage="FINALIZED",
            processing_step="finalize",
            worker_id="omega_manager",
            reason="SRT written successfully",
            status="Ready to Burn",
            progress=90.0,
        )
    except Exception as e:
        logger.warning(f"Transition FINALIZING->FINALIZED failed for {stem}: {e}")

def _run_burn(srt, stem):
    logger.info(f"🔥 Burning: {stem}")
    with job_artifact_lock(stem, timeout_seconds=120.0):
        job = omega_db.get_job_via_track(stem)
        meta = job.get("meta", {}) if job else {}
        current_stage = (job.get("stage") or "FINALIZED").upper() if job else "FINALIZED"

        try:
            apply_transition(
                job_id=stem,
                job_stem=stem,
                from_stage=current_stage,
                to_stage="BURNING",
                processing_step="burn",
                worker_id="omega_manager",
                reason="Starting FFmpeg video burn",
                status="Burning",
                progress=95.0,
                meta={"burn_started_at": datetime.now().isoformat()},
            )
        except Exception as e:
            logger.warning(f"Transition to BURNING failed for {stem}: {e}")

        subtitle_style = job.get("subtitle_style", "RUV_BOX") if job else "RUV_BOX"
        delivery_profile = job.get("delivery_profile") if job else None  # Read from job settings
        video_path = None
        vault_path = meta.get("vault_path")
        if vault_path:
            candidate = Path(str(vault_path))
            if candidate.exists():
                video_path = candidate
        if video_path is None:
            original_filename = meta.get("original_filename")
            if original_filename:
                candidate = config.VAULT_VIDEOS / original_filename
                if candidate.exists():
                    video_path = candidate

        if video_path is None:
            original_stem = meta.get("original_stem")
            video_path = _find_vault_video(original_stem or stem)

        if video_path is None:
            raise FileNotFoundError(f"Video not found for {stem}")

        # P3: Disk Space Pre-Check (input size * 1.5 + 5GB buffer)
        video_size_gb = video_path.stat().st_size / (1024**3)
        required_gb = (video_size_gb * 1.5) + 5.0
        disk_ok, disk_free = config.disk_space_available(min_gb=required_gb)
        if not disk_ok:
            err_msg = f"Insufficient disk space for burn. Need {required_gb:.1f}GB, have {disk_free:.1f}GB"
            logger.error(f"❌ {err_msg}")
            raise RuntimeError(err_msg)

        approved_path = config.TRANSLATED_DONE_DIR / f"{stem}_APPROVED.json"
        integrity_report = _ensure_text_integrity(
            stem=stem,
            approved_path=approved_path,
            target_language=job.get("target_language", "is") if job else "is",
        )
        if integrity_report.get("enabled"):
            try:
                omega_db.update_job_via_track(stem, meta={"qa_text_integrity": integrity_report})
            except Exception as e:
                logger.warning(f"Text integrity QA meta update failed for {stem}: {e}")

        min_coverage = _safe_float_env("OMEGA_MIN_TEXT_COVERAGE", 0.995)
        if approved_path.exists():
            coverage = finalizer.compute_srt_text_coverage(
                approved_path=approved_path,
                srt_path=srt,
                min_ratio=min_coverage,
            )
            if not coverage.get("passed"):
                missing_preview = ", ".join(coverage.get("missing_samples", [])[:8]) or "unknown"
                raise RuntimeError(
                    "Pre-burn text coverage gate failed "
                    f"({coverage.get('coverage_ratio')} < {coverage.get('min_ratio')}); "
                    f"missing tokens: {missing_preview}"
                )
            try:
                omega_db.update_job_via_track(stem, meta={"qa_coverage": coverage})
            except Exception as e:
                logger.warning(f"Coverage QA meta update failed for {stem}: {e}")
        else:
            logger.warning(f"Coverage gate skipped (missing approved file): {approved_path.name}")

        output_video = publisher.publish(video_path, srt, subtitle_style=subtitle_style, delivery_profile=delivery_profile)
        try:
            apply_transition(
                job_id=stem,
                job_stem=stem,
                from_stage="BURNING",
                to_stage="COMPLETED",
                processing_step="burn",
                worker_id="omega_manager",
                reason="Video output generated successfully",
                status="Done",
                progress=100.0,
                meta={
                    "final_output": str(output_video),
                    "burn_end_time": datetime.now().isoformat(),
                    "last_error": "",
                    "failed_at": "",
                },
            )
        except Exception as e:
            logger.warning(f"Transition BURNING->COMPLETED failed for {stem}: {e}")
        logger.info(f"✅ Job Complete: {output_video.name}")
        
        done_srt = srt.parent / f"DONE_{srt.name}"
        shutil.move(str(srt), str(done_srt))

import signal

def cleanup(signum, frame):
    logger.info(f"🛑 Received signal {signum}. Initiating graceful shutdown...")
    _shutdown_event.set()

def main():
    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)
    
    logger.info("🚀 Omega Manager Started (Async Mode)")

    # Cleanup stale temp_render files from interrupted burns
    temp_render_dir = Path(__file__).parent / "temp_render"
    if temp_render_dir.exists():
        stale_files = [p for p in temp_render_dir.glob("*_SUBBED.mp4") if not _is_hidden_artifact(p)]
        if stale_files:
            logger.info(f"🧹 Cleaning {len(stale_files)} stale temp_render file(s) from interrupted burns")
            for f in stale_files:
                try:
                    f.unlink()
                    logger.info(f"   Deleted: {f.name}")
                except Exception as e:
                    logger.warning(f"   Failed to delete {f.name}: {e}")

    if _cloud_pipeline_enabled():
        logger.info(
            "☁️ Cloud pipeline enabled (bucket=%s, prefix=%s, job=%s, region=%s, project=%s)",
            config.OMEGA_JOBS_BUCKET,
            config.OMEGA_JOBS_PREFIX,
            config.OMEGA_CLOUD_RUN_JOB or "unset",
            config.OMEGA_CLOUD_RUN_REGION,
            config.OMEGA_CLOUD_PROJECT or "default",
        )
    else:
        logger.info("🧩 Cloud pipeline disabled (set OMEGA_CLOUD_PIPELINE=1 to enable).")
    
    # Initialize ThreadPool
    # 22 workers allows for full concurrency of 20 client jobs + 2 overhead
    # Most steps are I/O bound (Cloud API), so high thread count is safe.
    last_reconcile = 0
    db_failure_streak = 0
    circuit_open_until: Optional[float] = None
    with ThreadPoolExecutor(max_workers=22) as executor:
        while not _shutdown_event.is_set():
            try:
                system_health.update_heartbeat("omega_manager")

                # Circuit breaker: skip main loop work when DB is down
                now_cb = time.time()
                if circuit_open_until and now_cb < circuit_open_until:
                    remaining = max(0.0, circuit_open_until - now_cb)
                    logger.warning(
                        "Manager DB circuit breaker open (%.0fs remaining); skipping this cycle",
                        remaining,
                    )
                    time.sleep(min(10.0, remaining))
                    continue
                
                # Cleanup dead stations (every loop is fine, it's efficient)
                omega_db.cleanup_dead_stations(timeout_minutes=5)

                if RESTART_FLAG.exists():
                    if _shutdown_event.is_set():
                        break  # Don't restart during shutdown
                    force = RESTART_FORCE_FLAG.exists()
                    with _task_lock:
                        has_active = bool(active_tasks)
                        active_count = len(active_tasks)
                    if has_active and not force:
                        logger.warning(f"🔄 Restart requested; waiting for {active_count} active tasks to finish...")
                        time.sleep(2)
                        continue
                    try:
                        RESTART_FLAG.unlink()
                    except Exception as e:
                        logger.debug(f"Failed to remove restart flag: {e}")
                    try:
                        RESTART_FORCE_FLAG.unlink()
                    except Exception as e:
                        logger.debug(f"Failed to remove force restart flag: {e}")
                    logger.warning("🔄 Restarting Omega Manager now%s...", " (forced)" if force else "")
                    os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve())])

                if not config.critical_paths_ready(require_write=True):
                    logger.error("❌ Critical paths not writable/ready (external drive unmounted or permissions). Pausing.")
                    time.sleep(10)
                    continue
                
                # Check disk space before processing (warn if low)
                disk_ok, disk_gb = config.disk_space_available(min_gb=20.0)
                if not disk_ok:
                    logger.warning(f"⚠️ Low disk space: {disk_gb:.1f}GB available (need 20GB+). Pausing ingestion.")
                    # Still process existing jobs but don't ingest new ones
                    process_jobs(executor)
                    time.sleep(30)
                    continue
                
                ingest_new_files(executor)
                process_jobs(executor)
                
                # Run periodically (every 5 minutes for sync states, every hour for full reconciliation)
                now_ts = time.time()
                if now_ts - last_reconcile > 300:  # Every 5 minutes
                    try:
                        reconcile_orphaned_sync_states()
                    except Exception as e:
                        logger.debug(f"Sync state reconciliation failed: {e}")

                    if now_ts - last_reconcile > 3600:  # Every hour - full reconciliation
                        try:
                            reconcile_orphaned_deliveries()
                        except Exception as e:
                            logger.error(f"Reconciliation check failed: {e}")

                    # Purge stale failure_counts entries (>2h old, no active task)
                    with _task_lock:
                        stale_cutoff = time.time() - 7200
                        stale_stems = [
                            s for s, (c, t) in failure_counts.items()
                            if t < stale_cutoff and s not in active_tasks
                        ]
                        for s in stale_stems:
                            del failure_counts[s]
                    if stale_stems:
                        logger.debug(f"Cleaned {len(stale_stems)} stale failure_counts entries")

                    last_reconcile = now_ts

                # Reset circuit breaker on successful loop iteration
                if db_failure_streak > 0:
                    db_failure_streak = 0
                if circuit_open_until:
                    logger.info("Manager DB circuit breaker closed (successful loop)")
                    circuit_open_until = None

                time.sleep(2) # Faster polling since it's non-blocking

            except KeyboardInterrupt:
                logger.info("🛑 Manager Stopped by User")
                break
            except Exception as e:
                logger.error(f"🔥 Critical Manager Failure: {e}", exc_info=True)
                if _is_db_error(e):
                    db_failure_streak += 1
                    logger.error(
                        "Manager DB error (%d/%d): %s",
                        db_failure_streak,
                        _MANAGER_DB_FAILURE_THRESHOLD,
                        e,
                    )
                    if db_failure_streak >= _MANAGER_DB_FAILURE_THRESHOLD:
                        circuit_open_until = time.time() + _MANAGER_DB_COOLDOWN_SECONDS
                        db_failure_streak = 0
                        logger.error(
                            "Manager DB circuit breaker opened for %.0fs",
                            _MANAGER_DB_COOLDOWN_SECONDS,
                        )
                else:
                    db_failure_streak = 0
                time.sleep(10)

        # --- Graceful shutdown: executor drains here (context manager waits) ---
        with _task_lock:
            remaining = set(active_tasks)
        if remaining:
            logger.info(f"Waiting for {len(remaining)} active task(s) to finish: {remaining}")
        else:
            logger.info("No active tasks. Shutdown clean.")
        # The `with ThreadPoolExecutor` context manager calls executor.shutdown(wait=True)
        # which blocks until all submitted tasks are done.

    # Mark any tasks that were still active as interrupted
    with _task_lock:
        still_active = set(active_tasks)
    for stem in still_active:
        try:
            omega_db.update_job_via_track(
                stem,
                status="Interrupted: manager shutdown",
                meta={"interrupted_at": datetime.now().isoformat()},
            )
        except Exception:
            pass
    logger.info("Graceful shutdown complete.")

if __name__ == "__main__":
    try:
        with ProcessLock("omega_manager"):
            main()
    except KeyboardInterrupt:
        logger.info("🛑 Manager Stopped by User")
        raise SystemExit(0)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        if code not in (0, None):
            logger.error("🛑 Omega Manager exiting with SystemExit(%s)", exc.code)
        raise
    except BaseException:
        logger.exception("🔥 Unhandled Omega Manager crash")
        raise
