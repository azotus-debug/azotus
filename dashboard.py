
from flask import Flask, jsonify, request, send_file, Response, redirect
import os
import json
from workers.dubber import Dubber
import shutil
import time
import threading
from pathlib import Path
from datetime import datetime
from werkzeug.utils import secure_filename
import omega_db
import subprocess
import sys
import uuid
import config
import logging
import secrets
from functools import wraps
from typing import Optional
from job_logs import tail_job_log
from notification_manager import NotificationManager

# Configure Logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Dashboard")

app = Flask(__name__)

# Custom JSON encoder to handle datetime objects from PostgreSQL
class CustomJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)

app.json_encoder = CustomJSONEncoder

@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS, PUT, DELETE"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Omega-Admin-Token"
    return response

_force_burn_lock = threading.Lock()
_force_burn_inflight = set()

_ADMIN_TOKEN_ENV = "OMEGA_ADMIN_TOKEN"
_MANAGER_RESTART_FLAG = config.BASE_DIR / "heartbeats" / "omega_manager.restart"

@app.before_request
def _dashboard_heartbeat():
    try:
        beat_dir = config.BASE_DIR / "heartbeats"
        beat_dir.mkdir(exist_ok=True)
        (beat_dir / "dashboard.beat").touch()
    except Exception:
        pass

def _heartbeat_loop():
    while True:
        try:
            beat_dir = config.BASE_DIR / "heartbeats"
            beat_dir.mkdir(exist_ok=True)
            (beat_dir / "dashboard.beat").touch()
        except Exception:
            pass
        time.sleep(10)

def _start_heartbeat_thread():
    thread = threading.Thread(target=_heartbeat_loop, name="dashboard-heartbeat", daemon=True)
    thread.start()


def _is_loopback(addr: Optional[str]) -> bool:
    if not addr:
        return False
    if addr == "::1":
        return True
    if addr.startswith("127."):
        return True
    return addr == "localhost"


def _looks_like_email(value: Optional[str]) -> bool:
    if not value or "@" not in value:
        return False
    local, _, domain = value.partition("@")
    return bool(local and domain and "." in domain)

def _normalize_meta(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            return {}
    return value if isinstance(value, dict) else {}


def _station_scope_enabled() -> bool:
    return config.OMEGA_UI_SCOPE == 'station' and bool(config.OMEGA_STATION_ID)


def _station_match(meta: dict) -> bool:
    station_id = str(meta.get('station_id') or '').strip().lower()
    if not station_id:
        return bool(config.OMEGA_STATION_CLAIM_UNASSIGNED)
    return station_id == config.OMEGA_STATION_ID


def _track_allowed(track: dict) -> bool:
    if not _station_scope_enabled():
        return True
    meta = _normalize_meta(track.get('meta'))
    return _station_match(meta)


def _filter_tracks_by_station(tracks: list) -> list:
    if not _station_scope_enabled():
        return tracks
    filtered = []
    for track in tracks:
        meta = _normalize_meta(track.get('meta'))
        if _station_match(meta):
            track['meta'] = meta
            filtered.append(track)
    return filtered



def _get_request_admin_token() -> Optional[str]:
    token = request.headers.get("X-Omega-Admin-Token")
    if token:
        return token.strip()

    auth = request.headers.get("Authorization", "").strip()
    if auth.lower().startswith("bearer "):
        return auth.split(None, 1)[1].strip()

    token = request.args.get("admin_token")
    if token:
        return str(token).strip()

    if request.is_json:
        payload = request.get_json(silent=True) or {}
        token = payload.get("admin_token")
        if token:
            return str(token).strip()
    return None


def _is_admin_request() -> bool:
    """
    Admin policy:
    - Always allow loopback requests (127.0.0.1 / ::1).
    - For non-loopback requests, require OMEGA_ADMIN_TOKEN to be set and supplied.
    """
    remote = request.remote_addr
    if _is_loopback(remote):
        return True

    configured = (os.environ.get(_ADMIN_TOKEN_ENV) or "").strip()
    if not configured:
        return False

    provided = _get_request_admin_token()
    return bool(provided) and secrets.compare_digest(provided, configured)


def admin_required(fn):
    @wraps(fn)
    def _wrapped(*args, **kwargs):
        if not _is_admin_request():
            return jsonify({"error": "Admin access required"}), 403
        return fn(*args, **kwargs)

    return _wrapped


def _bump_output_version(version: str) -> str:
    try:
        if not version:
            return "1.1"
        parts = str(version).split(".")
        major = int(parts[0]) if parts[0].isdigit() else 1
        minor = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        return f"{major}.{minor + 1}"
    except Exception:
        return "1.1"

def _format_output_version(master_version: Optional[int]) -> str:
    try:
        return f"{int(master_version or 1)}.0"
    except Exception:
        return "1.0"

def _resolve_master_version(
    master_script_id: Optional[str],
    program_id: Optional[str] = None,
    language_code: Optional[str] = None,
) -> int:
    master = omega_db.get_master_script(master_script_id) if master_script_id else None
    if not master and program_id and language_code:
        master = omega_db.get_master_script_for_program(program_id, language_code)
    try:
        return int(master.get("version", 1)) if master else 1
    except Exception:
        return 1

def _hydrate_track_job_ids(tracks: list[dict]) -> None:
    job_map = {
        track.get("id"): track.get("job_id")
        for track in tracks
        if track.get("id") and track.get("job_id")
    }
    for track in tracks:
        if track.get("job_id"):
            continue
        depends_on = track.get("depends_on")
        if depends_on and depends_on in job_map:
            track["job_id"] = job_map[depends_on]


def _hydrate_track_file_paths(tracks: list[dict]) -> None:
    """Add srt_path, video_path, and files_ready to each track based on job_id."""
    for track in tracks:
        job_id = track.get('job_id')
        if job_id:
            # SRT may be DONE_{job_id}.srt or {job_id}.srt
            srt_path = config.SRT_DIR / f"DONE_{job_id}.srt"
            if not srt_path.exists():
                srt_path = config.SRT_DIR / f"{job_id}.srt"
            video_path = config.VIDEO_DIR / f"{job_id}_SUBBED.mp4"

            track['srt_path'] = str(srt_path) if srt_path.exists() else None
            track['video_path'] = str(video_path) if video_path.exists() else None
            track['files_ready'] = srt_path.exists() and video_path.exists()
        else:
            track['srt_path'] = None
            track['video_path'] = None
            track['files_ready'] = False


def _tail_lines(path: Path, line_count: int = 200, max_bytes: int = 200_000) -> list[str]:
    if line_count <= 0:
        return []
    try:
        if not path.exists():
            return []
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - max_bytes), os.SEEK_SET)
            data = f.read().decode("utf-8", errors="replace")
        lines = data.splitlines()
        return lines[-line_count:]
    except Exception:
        return []


def _heartbeat_age_seconds(process_name: str) -> Optional[float]:
    try:
        beat = config.BASE_DIR / "heartbeats" / f"{process_name}.beat"
        if not beat.exists():
            return None
        return max(0.0, time.time() - beat.stat().st_mtime)
    except Exception:
        return None


def _disk_free_gb(path: Path) -> Optional[float]:
    try:
        if not path.exists():
            return None
        total, used, free = shutil.disk_usage(str(path))
        return free / (2**30)
    except Exception:
        return None


def get_all_jobs():
    """Fetch all jobs using the shared database layer."""
    return omega_db.get_all_jobs_via_tracks()

_VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".mpg", ".mpeg", ".m4v", ".wmv", ".flv"}

def _resolve_vault_video(vault_dir: Path, stem: str) -> Path:
    candidates = []
    for path in vault_dir.glob(f"{stem}.*"):
        if path.name.startswith("._"):
            continue
        if path.suffix.lower() in _VIDEO_EXTS:
            candidates.append(path)
    if not candidates:
        raise FileNotFoundError(f"Video not found in vault for {stem}: {vault_dir}")
    # Prefer mp4 when multiple candidates exist.
    candidates.sort(key=lambda p: (p.suffix.lower() != ".mp4", p.name.lower()))
    return candidates[0]


def _derive_delivery_dir_from_vault(vault_dir: Path) -> Path:
    """
    Map `2_VAULT/<client>/<year>/<stem>` → `4_DELIVERY/<client>/<year>/<stem>`.
    Falls back to `4_DELIVERY/VIDEO` if the vault dir isn't under `config.VAULT_DIR`.
    """
    try:
        rel = vault_dir.resolve(strict=False).relative_to(config.VAULT_DIR.resolve(strict=False))
        return config.DELIVERY_DIR / rel
    except Exception:
        return config.DELIVERY_DIR / "VIDEO"


def _run_force_burn(file_stem: str):
    """
    Background burn task kicked off by the dashboard.
    Uses the job's `vault_path` (queue-system layout) when available.
    """
    try:
        logger.info(f"🔥 Force burn started: {file_stem}")
        job = omega_db.get_job_via_track(file_stem) or {}

        meta = job.get("meta", {}) or {}

        vault_path = job.get("vault_path") or meta.get("vault_path")
        vault_dir = Path(vault_path) if vault_path else None

        delivery_dir = _derive_delivery_dir_from_vault(vault_dir) if vault_dir else config.VIDEO_DIR
        delivery_dir.mkdir(parents=True, exist_ok=True)

        # Resolve SRT (prefer delivery; otherwise copy from vault; check DONE_ prefix; legacy fallback to SRT_DIR)
        srt_path = None
        delivery_srt = delivery_dir / f"{file_stem}.srt"
        done_delivery_srt = delivery_dir / f"DONE_{file_stem}.srt"  # Completed jobs have DONE_ prefix
        
        if delivery_srt.exists():
            srt_path = delivery_srt
        elif done_delivery_srt.exists():
            # Restore DONE_ prefixed SRT back to original name for re-burn
            shutil.copy2(done_delivery_srt, delivery_srt)
            srt_path = delivery_srt
            logger.info(f"   ♻️ Restored DONE_ SRT: {done_delivery_srt} -> {delivery_srt}")
        elif vault_dir is not None:
            vault_srt = vault_dir / f"{file_stem}.srt"
            if vault_srt.exists():
                shutil.copy2(vault_srt, delivery_srt)
                srt_path = delivery_srt
        
        if srt_path is None:
            legacy_srt = config.SRT_DIR / f"{file_stem}.srt"
            done_legacy_srt = config.SRT_DIR / f"DONE_{file_stem}.srt"
            if legacy_srt.exists():
                srt_path = legacy_srt
            elif done_legacy_srt.exists():
                # Restore DONE_ prefixed SRT
                shutil.copy2(done_legacy_srt, legacy_srt)
                srt_path = legacy_srt
                logger.info(f"   ♻️ Restored DONE_ SRT: {done_legacy_srt} -> {legacy_srt}")
        
        if srt_path is None:
            raise FileNotFoundError(f"SRT not found for {file_stem}")

        # Resolve video (prefer vault_path; legacy fallback to VAULT_VIDEOS)
        video_path = _resolve_vault_video(vault_dir, file_stem) if vault_dir else None
        if video_path is None:
            original_filename = meta.get("original_filename")
            if original_filename:
                candidate = config.VAULT_VIDEOS / original_filename
                if candidate.exists():
                    video_path = candidate
        if video_path is None:
            for candidate in config.VAULT_VIDEOS.glob(f"{file_stem}.*"):
                if candidate.name.startswith("._"):
                    continue
                if candidate.suffix.lower() in _VIDEO_EXTS:
                    video_path = candidate
                    break
        if video_path is None:
            source_path = meta.get("source_path")
            raise FileNotFoundError(f"Video not found for {file_stem} (source_path={source_path})")

        subtitle_style = job.get("subtitle_style") or meta.get("subtitle_style") or "Classic"
        logger.info(f"   Video: {video_path}")
        logger.info(f"   SRT: {srt_path}")
        logger.info(f"   Style: {subtitle_style}")
        logger.info(f"   Output dir: {delivery_dir}")

        omega_db.update_job_via_track(
            file_stem,
            stage="BURNING",
            status="Burning",
            progress=95.0,
            meta={"burn_started_at": datetime.now().isoformat(), "burn_requested_via": "dashboard"},
        )

        # Backup existing output so publisher will re-encode
        existing_output = delivery_dir / f"{file_stem}_SUBBED.mp4"
        if existing_output.exists():
            backup_name = f"{file_stem}_SUBBED.bak_{int(time.time())}.mp4"
            backup_path = delivery_dir / backup_name
            shutil.move(str(existing_output), str(backup_path))
            logger.info(f"   📦 Backed up existing output: {backup_name}")

        from workers import publisher
        output_path = publisher.publish(video_path, srt_path, subtitle_style=subtitle_style)
        logger.info(f"✅ Force burn complete: {output_path}")

        completed_at = datetime.now().isoformat()
        omega_db.update_job_via_track(
            file_stem,
            stage="COMPLETED",
            status="Done",
            progress=100.0,
            meta={
                "burn_completed_at": completed_at,
                "burn_end_time": completed_at,
                "final_output": str(output_path),
            },
        )
    except Exception as e:
        logger.error(f"Force burn failed for {file_stem}: {e}", exc_info=True)
        omega_db.update_job_via_track(
            file_stem,
            stage="FINALIZED",
            status=f"Burn Failed: {e}",
            progress=90.0,
            meta={"burn_failed_at": datetime.now().isoformat()},
        )
    finally:
        with _force_burn_lock:
            _force_burn_inflight.discard(file_stem)

@app.route("/health", methods=["GET"])
def health_check():
    """Liveness probe for Cloud Run (Lightweight)."""
    try:
        # Test DB connection
        conn = omega_db._connect()
        c = conn.cursor()
        c.execute("SELECT 1")
        conn.close()
        return jsonify({"status": "healthy"}), 200
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return jsonify({"status": "unhealthy", "error": str(e)}), 503


@app.route("/healthz", methods=["GET"])
def healthz():
    """Cloud Run health check endpoint (alias for /health)."""
    return health_check()

@app.route("/ready", methods=["GET"])
def readiness_check():
    """Startup probe for Cloud Run."""
    try:
        # Verify schema is initialized
        omega_db.ensure_schema()
        return jsonify({"status": "ready"}), 200
    except Exception as e:
        logger.error(f"Readiness check failed: {e}")
        return jsonify({"status": "not_ready", "error": str(e)}), 503

@app.route('/')
def index():
    frontend_url = os.environ.get("OMEGA_FRONTEND_URL")
    if frontend_url:
        return redirect(frontend_url)
    return jsonify(
        {
            "status": "ok",
            "message": "Frontend not configured. Set OMEGA_FRONTEND_URL to use the web UI.",
        }
    )

@app.route('/api/jobs')
def api_jobs():
    jobs = get_all_jobs()
    return jsonify(jobs)

@app.route('/api/jobs_grouped')
def api_jobs_grouped():
    """Get jobs grouped by client with urgent section."""
    from datetime import datetime, timedelta
    
    jobs = get_all_jobs()
    now = datetime.now()
    urgent_threshold = now + timedelta(days=2)
    
    urgent = []
    by_client = {}
    
    for job in jobs:
        # Check if urgent (due within 48 hours)
        due_date_str = job.get("due_date")
        is_urgent = False
        if due_date_str:
            try:
                due_date = datetime.fromisoformat(due_date_str)
                is_urgent = due_date <= urgent_threshold
            except:
                pass
        
        if is_urgent:
            urgent.append(job)
        
        # Group by client
        client = job.get("client", "unknown")
        if client not in by_client:
            by_client[client] = []
        by_client[client].append(job)
    
    return jsonify({
        "urgent": urgent,
        "by_client": by_client,
        "client_names": sorted(by_client.keys())
    })

@app.route('/api/mark_delivered', methods=['POST'])
def api_mark_delivered():
    """Mark a job as delivered using delivery templates."""
    try:
        from delivery_actions import mark_delivered
        
        data = request.json
        job_stem = data.get("job_stem") or data.get("file_stem")
        notes = data.get("notes", "")
        
        if not job_stem:
            return jsonify({"success": False, "error": "job_stem or file_stem required"}), 400
        
        result = mark_delivered(job_stem, notes)
        return jsonify(result)
    except Exception as e:
        logger.error(f"Mark delivered failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/delete_program', methods=['POST'])
@admin_required
def api_delete_program():
    """
    Delete a program and all its associated files.
    
    With project-based folder structure, this is clean:
    1. Delete the project folder (2_VAULT/{month}/{stem}/)
    2. Delete the database record
    3. Optionally delete from GCS
    """
    try:
        data = request.json
        stem = data.get("stem") or data.get("file_stem")
        delete_cloud = data.get("delete_cloud", False)
        
        if not stem:
            return jsonify({"success": False, "error": "stem required"}), 400
        
        deleted_paths = []
        errors = []
        
        # 1. Find and delete project folder (new structure)
        project_dir = config.find_project_folder(stem)
        if project_dir and project_dir.exists():
            try:
                shutil.rmtree(str(project_dir))
                deleted_paths.append(str(project_dir))
                logger.info(f"Deleted project folder: {project_dir}")
            except Exception as e:
                errors.append(f"Failed to delete project folder: {e}")
        
        # 2. Also check legacy flat files (for backwards compatibility)
        legacy_patterns = [
            config.VAULT_DATA / f"{stem}*",
            config.VAULT_VIDEOS / f"{stem}*",
        ]
        for pattern in legacy_patterns:
            for f in pattern.parent.glob(pattern.name):
                try:
                    if f.is_file():
                        f.unlink()
                        deleted_paths.append(str(f))
                    elif f.is_dir():
                        shutil.rmtree(str(f))
                        deleted_paths.append(str(f))
                except Exception as e:
                    errors.append(f"Failed to delete {f}: {e}")
        
        # 3. Delete database record
        try:
            conn = omega_db._connect()
            cur = conn.cursor()
            cur.execute("DELETE FROM jobs WHERE file_stem = ?", (stem,))
            db_deleted = cur.rowcount
            conn.commit()
            conn.close()
            if db_deleted:
                logger.info(f"Deleted DB record for: {stem}")
        except Exception as e:
            errors.append(f"Failed to delete DB record: {e}")
            db_deleted = 0
        
        # 4. Optionally delete from GCS
        cloud_deleted = False
        if delete_cloud:
            try:
                from google.cloud import storage
                client = storage.Client()
                bucket = client.bucket(config.OMEGA_JOBS_BUCKET)
                blobs = list(bucket.list_blobs(prefix=f"{config.OMEGA_JOBS_PREFIX}/{stem}"))
                for blob in blobs:
                    blob.delete()
                    deleted_paths.append(f"gs://{config.OMEGA_JOBS_BUCKET}/{blob.name}")
                cloud_deleted = len(blobs) > 0
            except Exception as e:
                errors.append(f"Failed to delete from GCS: {e}")
        
        return jsonify({
            "success": len(errors) == 0,
            "stem": stem,
            "deleted_paths": deleted_paths,
            "db_record_deleted": db_deleted > 0,
            "cloud_deleted": cloud_deleted,
            "errors": errors
        })
        
    except Exception as e:
        logger.error(f"Delete program failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/deliveries')
def api_get_deliveries():
    """Fetch delivery history for analytics."""
    try:
        limit = int(request.args.get("limit", 100))
        deliveries = omega_db.get_deliveries() # It defaults to 100 limit, checking args if I modify omega_db
        # Actually omega_db.get_deliveries() hardcodes LIMIT 100.
        return jsonify(deliveries)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/health")
def api_health():
    """
    Comprehensive health check endpoint.
    Returns detailed status of all system components.
    """
    # Collect job statistics
    jobs = get_all_jobs()
    stage_counts: dict[str, int] = {}
    halted_count = 0
    dead_count = 0
    stuck_count = 0

    # Stall thresholds (seconds)
    STALL_THRESHOLDS = {
        "TRANSLATING": 1800,  # 30 min
        "BURNING": 3600,      # 1 hour
        "INGESTING": 1800,    # 30 min
    }

    for job in jobs:
        stage = (job.get("stage") or "UNKNOWN").upper()
        stage_counts[stage] = stage_counts.get(stage, 0) + 1
        meta = job.get("meta") or {}
        if isinstance(meta, dict) and meta.get("halted"):
            halted_count += 1
        if stage == "DEAD":
            dead_count += 1

        # Check for stuck jobs
        if stage in STALL_THRESHOLDS:
            updated_at = job.get("updated_at")
            if updated_at:
                try:
                    if isinstance(updated_at, str):
                        updated_at = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                    age = (datetime.now() - updated_at.replace(tzinfo=None)).total_seconds()
                    if age > STALL_THRESHOLDS[stage]:
                        stuck_count += 1
                except Exception:
                    pass

    # Storage check
    storage_ready = False
    try:
        storage_ready = bool(config.critical_paths_ready(require_write=True))
    except Exception:
        storage_ready = False

    # Database check
    db_status = "unknown"
    db_type = os.getenv("DB_TYPE", "postgres")
    try:
        conn = omega_db._connect()
        c = conn.cursor()
        c.execute("SELECT 1")
        c.fetchone()
        conn.close()
        db_status = "ok"
    except Exception as e:
        db_status = f"error: {str(e)[:50]}"

    # GCS check (cached, only check every 60 seconds)
    gcs_status = "unknown"
    try:
        from google.cloud import storage
        client = storage.Client()
        bucket = client.bucket(config.OMEGA_JOBS_BUCKET)
        # Quick existence check
        bucket.reload()
        gcs_status = "ok"
    except ImportError:
        gcs_status = "sdk_not_installed"
    except Exception as e:
        gcs_status = f"error: {str(e)[:50]}"

    # ElevenLabs check
    elevenlabs_status = "unknown"
    api_key = os.getenv("ELEVENLABS_API_KEY", "")
    if api_key:
        elevenlabs_status = "configured"
    else:
        elevenlabs_status = "not_configured"

    # Heartbeats
    manager_age = _heartbeat_age_seconds("omega_manager")
    dashboard_age = _heartbeat_age_seconds("dashboard")

    # Get recent errors for telemetry
    recent_errors = []
    errors_24h_count = 0
    try:
        recent_errors_raw = omega_db.get_recent_errors(hours=24, limit=10)
        errors_24h_count = len(recent_errors_raw)
        # Format for response (exclude full traceback for brevity)
        recent_errors = [
            {
                "time": str(e.get("created_at", "")),
                "job_id": e.get("job_id"),
                "type": e.get("error_type"),
                "message": (e.get("message") or "")[:200],
                "worker": e.get("worker"),
            }
            for e in recent_errors_raw[:5]  # Only show last 5 in health
        ]
    except Exception:
        pass  # Error log table might not exist yet

    # Determine overall status
    overall_status = "healthy"
    if db_status != "ok":
        overall_status = "unhealthy"
    elif not storage_ready:
        overall_status = "degraded"
    elif gcs_status != "ok":
        overall_status = "degraded"
    elif stuck_count > 0 or dead_count > 0:
        overall_status = "degraded"
    elif manager_age is None or manager_age > 120:
        overall_status = "degraded"
    elif errors_24h_count > 5:
        overall_status = "degraded"

    return jsonify(
        {
            "status": overall_status,
            "time": datetime.now().isoformat(),
            "checks": {
                "database": {"status": db_status, "type": db_type},
                "storage": {"status": "ok" if storage_ready else "error"},
                "gcs": {"status": gcs_status, "bucket": config.OMEGA_JOBS_BUCKET},
                "elevenlabs": {"status": elevenlabs_status},
                "disk_space": {
                    "status": "ok" if (_disk_free_gb(config.DELIVERY_DIR) or 0) > 20 else "low",
                    "free_gb": round(_disk_free_gb(config.DELIVERY_DIR) or 0, 1)
                },
            },
            "heartbeats": {
                "omega_manager_age_seconds": manager_age,
                "dashboard_age_seconds": dashboard_age,
                "manager_alive": manager_age is not None and manager_age < 120,
            },
            "jobs": {
                "total": len(jobs),
                "active": len(jobs) - dead_count - stage_counts.get("COMPLETED", 0),
                "stages": stage_counts,
                "halted": halted_count,
                "dead": dead_count,
                "stuck": stuck_count,
            },
            "errors_24h": {
                "count": errors_24h_count,
                "recent": recent_errors,
            },
            "config": {
                "db_type": db_type,
                "cloud_pipeline": os.getenv("OMEGA_CLOUD_PIPELINE", "0"),
                "transcriber": os.getenv("OMEGA_TRANSCRIBER", "elevenlabs"),
                "delivery_profile": getattr(config, "DEFAULT_DELIVERY_PROFILE", "broadcast_hevc"),
            },
        }
    )


@app.route("/api/v2/health/diagnose", methods=["GET"])
def api_health_diagnose():
    """
    Comprehensive health check that identifies fixable issues.
    Returns a list of problems detected and whether they can be auto-fixed.
    """
    problems = []

    # Get all tracks/jobs
    tracks = omega_db.get_all_jobs_via_tracks()

    for job in tracks:
        stem = job.get("file_stem")
        if not stem:
            continue

        stage = (job.get("stage") or "").upper()
        meta = job.get("meta") or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except:
                meta = {}

        # Skip halted jobs (require manual intervention)
        if meta.get("halted"):
            continue

        # Check for stage/file misalignment
        srt_path = config.SRT_DIR / f"{stem}.srt"
        approved_path = config.TRANSLATED_DONE_DIR / f"{stem}_APPROVED.json"
        skeleton_path = config.find_skeleton(stem)

        # Check for burned video
        final_path = None
        language = job.get("language_code") or meta.get("target_language") or "is"
        delivery_profile = job.get("delivery_profile") or meta.get("delivery_profile") or "broadcast_hevc"
        profile_info = config.DELIVERY_PROFILES.get(delivery_profile, {})
        output_subdir = profile_info.get("output_subdir", "VIDEO")
        for ext in [".mp4", ".mov"]:
            candidate = config.DELIVERY_DIR / output_subdir / f"{stem}_SUBBED{ext}"
            if candidate.exists():
                final_path = candidate
                break

        # Problem: Video exists but stage not COMPLETED
        if final_path and stage not in {"COMPLETED", "DELIVERED"}:
            problems.append({
                "stem": stem,
                "issue": "video_exists_wrong_stage",
                "description": f"Burned video exists but stage is {stage}",
                "current_stage": stage,
                "suggested_stage": "COMPLETED",
                "auto_fixable": True,
            })

        # Problem: SRT exists but stage is before FINALIZED
        elif srt_path.exists() and stage not in {"FINALIZED", "BURNING", "COMPLETED", "DELIVERED"}:
            problems.append({
                "stem": stem,
                "issue": "srt_exists_wrong_stage",
                "description": f"SRT file exists but stage is {stage}",
                "current_stage": stage,
                "suggested_stage": "FINALIZED",
                "auto_fixable": True,
            })

        # Problem: Approved translation exists but stage is before REVIEWED
        elif approved_path.exists() and stage not in {"REVIEWED", "FINALIZING", "FINALIZED", "BURNING", "COMPLETED", "DELIVERED"}:
            problems.append({
                "stem": stem,
                "issue": "approved_exists_wrong_stage",
                "description": f"Approved translation exists but stage is {stage}",
                "current_stage": stage,
                "suggested_stage": "REVIEWED",
                "auto_fixable": True,
            })

        # Problem: Skeleton exists but stage stuck at QUEUED/INGEST
        elif skeleton_path and stage in {"QUEUED", "INGEST", ""}:
            problems.append({
                "stem": stem,
                "issue": "skeleton_exists_wrong_stage",
                "description": f"Skeleton exists but stage is {stage or 'empty'}",
                "current_stage": stage,
                "suggested_stage": "TRANSCRIBED",
                "auto_fixable": True,
            })

        # Problem: DEAD job that has recoverable files
        if stage == "DEAD":
            if final_path:
                problems.append({
                    "stem": stem,
                    "issue": "dead_but_video_exists",
                    "description": "Job is DEAD but burned video exists",
                    "current_stage": "DEAD",
                    "suggested_stage": "COMPLETED",
                    "auto_fixable": True,
                })
            elif srt_path.exists():
                problems.append({
                    "stem": stem,
                    "issue": "dead_but_srt_exists",
                    "description": "Job is DEAD but SRT file exists",
                    "current_stage": "DEAD",
                    "suggested_stage": "FINALIZED",
                    "auto_fixable": True,
                })
            elif approved_path.exists():
                problems.append({
                    "stem": stem,
                    "issue": "dead_but_approved_exists",
                    "description": "Job is DEAD but approved translation exists",
                    "current_stage": "DEAD",
                    "suggested_stage": "REVIEWED",
                    "auto_fixable": True,
                })
            elif skeleton_path:
                problems.append({
                    "stem": stem,
                    "issue": "dead_but_skeleton_exists",
                    "description": "Job is DEAD but skeleton exists",
                    "current_stage": "DEAD",
                    "suggested_stage": "TRANSCRIBED",
                    "auto_fixable": True,
                })

        # Problem: Stuck in cloud translation for too long
        if stage in {"TRANSLATING_CLOUD_SUBMITTED", "CLOUD_TRANSLATING"} and meta.get("cloud_trigger_last_attempt"):
            last_attempt = meta.get("cloud_trigger_last_attempt", 0)
            if isinstance(last_attempt, (int, float)) and time.time() - last_attempt > 1800:  # 30 min
                problems.append({
                    "stem": stem,
                    "issue": "stuck_in_cloud_translation",
                    "description": f"Job stuck in {stage} for over 30 minutes",
                    "current_stage": stage,
                    "suggested_action": "Re-trigger cloud job",
                    "auto_fixable": True,
                })

    return jsonify({
        "time": datetime.now().isoformat(),
        "total_problems": len(problems),
        "auto_fixable": sum(1 for p in problems if p.get("auto_fixable")),
        "problems": problems,
    })


@app.route("/api/v2/health/fix", methods=["POST"])
@admin_required
def api_health_fix():
    """
    Auto-fix detected problems.
    Pass specific stems to fix, or fix_all=true to fix everything auto-fixable.
    """
    data = request.get_json() or {}
    stems_to_fix = data.get("stems", [])
    fix_all = data.get("fix_all", False)

    # First, diagnose problems
    problems = []
    tracks = omega_db.get_all_jobs_via_tracks()

    for job in tracks:
        stem = job.get("file_stem")
        if not stem:
            continue
        if not fix_all and stem not in stems_to_fix:
            continue

        stage = (job.get("stage") or "").upper()
        meta = job.get("meta") or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except:
                meta = {}

        # Check for stage/file misalignment
        srt_path = config.SRT_DIR / f"{stem}.srt"
        approved_path = config.TRANSLATED_DONE_DIR / f"{stem}_APPROVED.json"
        skeleton_path = config.find_skeleton(stem)

        # Check for burned video
        final_path = None
        delivery_profile = job.get("delivery_profile") or meta.get("delivery_profile") or "broadcast_hevc"
        profile_info = config.DELIVERY_PROFILES.get(delivery_profile, {})
        output_subdir = profile_info.get("output_subdir", "VIDEO")
        for ext in [".mp4", ".mov"]:
            candidate = config.DELIVERY_DIR / output_subdir / f"{stem}_SUBBED{ext}"
            if candidate.exists():
                final_path = candidate
                break

        fixed = False

        # Fix: Video exists → COMPLETED
        if final_path and stage not in {"COMPLETED", "DELIVERED"}:
            omega_db.update_job_via_track(stem, stage="COMPLETED", status="Done", progress=100.0,
                meta={**meta, "halted": False, "auto_fixed": True, "fixed_at": datetime.now().isoformat()})
            problems.append({"stem": stem, "fixed": True, "action": f"Stage corrected to COMPLETED"})
            fixed = True

        # Fix: SRT exists → FINALIZED
        elif srt_path.exists() and stage not in {"FINALIZED", "BURNING", "COMPLETED", "DELIVERED"}:
            omega_db.update_job_via_track(stem, stage="FINALIZED", status="Ready to burn", progress=85.0,
                meta={**meta, "halted": False, "auto_fixed": True, "fixed_at": datetime.now().isoformat()})
            problems.append({"stem": stem, "fixed": True, "action": f"Stage corrected to FINALIZED"})
            fixed = True

        # Fix: Approved exists → REVIEWED
        elif approved_path.exists() and stage not in {"REVIEWED", "FINALIZING", "FINALIZED", "BURNING", "COMPLETED", "DELIVERED"}:
            omega_db.update_job_via_track(stem, stage="REVIEWED", status="Ready to finalize", progress=70.0,
                meta={**meta, "halted": False, "auto_fixed": True, "fixed_at": datetime.now().isoformat()})
            problems.append({"stem": stem, "fixed": True, "action": f"Stage corrected to REVIEWED"})
            fixed = True

        # Fix: Skeleton exists but QUEUED/INGEST → TRANSCRIBED
        elif skeleton_path and stage in {"QUEUED", "INGEST", ""}:
            omega_db.update_job_via_track(stem, stage="TRANSCRIBED", status="Ready for translation", progress=30.0,
                meta={**meta, "halted": False, "auto_fixed": True, "fixed_at": datetime.now().isoformat()})
            problems.append({"stem": stem, "fixed": True, "action": f"Stage corrected to TRANSCRIBED"})
            fixed = True

        # Fix: DEAD but recoverable
        if stage == "DEAD" and not fixed:
            if final_path:
                omega_db.update_job_via_track(stem, stage="COMPLETED", status="Recovered", progress=100.0,
                    meta={**meta, "halted": False, "recovered_from_dead": True, "fixed_at": datetime.now().isoformat()})
                problems.append({"stem": stem, "fixed": True, "action": "Recovered from DEAD to COMPLETED"})
            elif srt_path.exists():
                omega_db.update_job_via_track(stem, stage="FINALIZED", status="Recovered", progress=85.0,
                    meta={**meta, "halted": False, "recovered_from_dead": True, "fixed_at": datetime.now().isoformat()})
                problems.append({"stem": stem, "fixed": True, "action": "Recovered from DEAD to FINALIZED"})
            elif approved_path.exists():
                omega_db.update_job_via_track(stem, stage="REVIEWED", status="Recovered", progress=70.0,
                    meta={**meta, "halted": False, "recovered_from_dead": True, "fixed_at": datetime.now().isoformat()})
                problems.append({"stem": stem, "fixed": True, "action": "Recovered from DEAD to REVIEWED"})
            elif skeleton_path:
                omega_db.update_job_via_track(stem, stage="TRANSCRIBED", status="Recovered", progress=30.0,
                    meta={**meta, "halted": False, "recovered_from_dead": True, "fixed_at": datetime.now().isoformat()})
                problems.append({"stem": stem, "fixed": True, "action": "Recovered from DEAD to TRANSCRIBED"})

    return jsonify({
        "time": datetime.now().isoformat(),
        "fixed_count": len([p for p in problems if p.get("fixed")]),
        "fixes": problems,
    })


@app.route("/api/v2/health/stuck", methods=["GET"])
def api_health_stuck():
    """
    Detect jobs that are stuck in intermediate stages for too long.
    Returns list of stuck jobs with duration info.
    Optional: ?notify=1 to send email notification.
    """
    from notification_manager import NotificationManager

    # Stall thresholds (seconds) for each stage
    STALL_THRESHOLDS = {
        "INGEST": 600,  # 10 min
        "TRANSCRIBED": 300,  # 5 min (should move to translation quickly)
        "TRANSLATING": 1800,  # 30 min
        "TRANSLATING_CLOUD_SUBMITTED": 1800,  # 30 min
        "CLOUD_TRANSLATING": 2700,  # 45 min
        "CLOUD_REVIEWING": 3600,  # 1 hour
        "REVIEWED": 300,  # 5 min (should finalize quickly)
        "FINALIZING": 600,  # 10 min
        "FINALIZED": 300,  # 5 min (should start burning quickly)
        "BURNING": 7200,  # 2 hours (video encoding can take a while)
    }

    stuck_jobs = []
    now = datetime.now()
    tracks = omega_db.get_all_jobs_via_tracks()

    for job in tracks:
        stem = job.get("file_stem")
        if not stem:
            continue

        stage = (job.get("stage") or "").upper()
        threshold = STALL_THRESHOLDS.get(stage)
        if not threshold:
            continue

        meta = job.get("meta") or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except:
                meta = {}

        # Skip halted jobs
        if meta.get("halted"):
            continue

        # Get last update time
        updated_at_str = job.get("updated_at")
        if not updated_at_str:
            continue

        try:
            if isinstance(updated_at_str, str):
                # Handle ISO format
                updated_at = datetime.fromisoformat(updated_at_str.replace("Z", "+00:00").replace("+00:00", ""))
            else:
                updated_at = updated_at_str
        except:
            continue

        elapsed = (now - updated_at).total_seconds()
        if elapsed < threshold:
            continue

        # Format duration nicely
        if elapsed < 3600:
            duration_str = f"{int(elapsed / 60)} minutes"
        else:
            duration_str = f"{elapsed / 3600:.1f} hours"

        stuck_jobs.append({
            "stem": stem,
            "stage": stage,
            "stuck_duration": duration_str,
            "elapsed_seconds": int(elapsed),
            "threshold_seconds": threshold,
            "updated_at": updated_at_str,
            "status": job.get("status", ""),
        })

    # Sort by elapsed time (most stuck first)
    stuck_jobs.sort(key=lambda x: x["elapsed_seconds"], reverse=True)

    # Send notification if requested
    notification_sent = False
    if request.args.get("notify") == "1" and stuck_jobs:
        notification_sent = NotificationManager.notify_stuck_jobs(stuck_jobs)

    return jsonify({
        "time": datetime.now().isoformat(),
        "stuck_count": len(stuck_jobs),
        "notification_sent": notification_sent,
        "stuck_jobs": stuck_jobs,
    })


@app.route("/api/v2/health/notify-dead", methods=["POST"])
@admin_required
def api_health_notify_dead():
    """
    Send notification about DEAD jobs that need attention.
    """
    from notification_manager import NotificationManager

    dead_jobs = []
    tracks = omega_db.get_all_jobs_via_tracks()

    for job in tracks:
        stem = job.get("file_stem")
        stage = (job.get("stage") or "").upper()
        if stage != "DEAD":
            continue

        meta = job.get("meta") or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except:
                meta = {}

        dead_jobs.append({
            "stem": stem,
            "error": meta.get("halt_reason") or meta.get("last_error") or job.get("status") or "Unknown error",
            "failed_at": meta.get("failed_at") or job.get("updated_at"),
        })

    if not dead_jobs:
        return jsonify({"message": "No DEAD jobs found", "notification_sent": False})

    notification_sent = NotificationManager.notify_dead_jobs(dead_jobs)
    return jsonify({
        "time": datetime.now().isoformat(),
        "dead_count": len(dead_jobs),
        "notification_sent": notification_sent,
        "dead_jobs": dead_jobs,
    })


@app.route("/api/encoding_status")
def api_encoding_status():
    """
    Returns the status of any currently encoding (BURNING stage) jobs.
    Used by the dashboard to display the encoding progress banner.
    """
    jobs = get_all_jobs()
    encoding_jobs = []
    
    for job in jobs:
        stage = (job.get("stage") or "").upper()
        if stage != "BURNING":
            continue
            
        meta = job.get("meta") or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except:
                meta = {}
        
        # Get encoding info
        stem = job.get("file_stem", "Unknown")
        status = job.get("status", "Encoding...")
        delivery_profile = job.get("delivery_profile") or "broadcast_hevc"
        
        # Get profile display name
        profile_info = config.DELIVERY_PROFILES.get(delivery_profile, {})
        profile_name = profile_info.get("name", delivery_profile)
        
        # Calculate elapsed time if we have burn_started_at
        burn_started = meta.get("burn_started_at")
        elapsed_seconds = None
        if burn_started:
            try:
                start_time = datetime.fromisoformat(burn_started)
                elapsed_seconds = (datetime.now() - start_time).total_seconds()
            except:
                pass
        
        encoding_jobs.append({
            "stem": stem,
            "status": status,
            "profile": profile_name,
            "profile_key": delivery_profile,
            "started_at": burn_started,
            "elapsed_seconds": elapsed_seconds,
            "progress": job.get("progress", 95.0),
        })
    
    return jsonify({
        "encoding": len(encoding_jobs) > 0,
        "jobs": encoding_jobs,
        "count": len(encoding_jobs),
    })


@app.route("/api/logs")
@admin_required
def api_logs():
    name = (request.args.get("name") or "").strip().lower()
    try:
        lines = int(request.args.get("lines") or 200)
    except Exception:
        lines = 200
    lines = max(1, min(lines, 2000))

    log_map = {
        "manager": config.BASE_DIR / "logs" / "manager.log",
        "dashboard": config.BASE_DIR / "logs" / "dashboard.log",
    }
    path = log_map.get(name)
    if not path:
        return jsonify({"error": "Invalid log name"}), 400
    return jsonify({"name": name, "path": str(path), "lines": _tail_lines(path, line_count=lines)})


@app.route("/api/output/<stem>")
@admin_required
def api_output(stem: str):
    job = omega_db.get_job_via_track(stem) or {}
    meta = job.get("meta") or {}
    if not isinstance(meta, dict):
        meta = {}

    candidates: list[Path] = []
    final_output = meta.get("final_output")
    if final_output:
        candidates.append(Path(str(final_output)))

    vault_path = job.get("vault_path") or meta.get("vault_path")
    if vault_path:
        try:
            delivery_dir = _derive_delivery_dir_from_vault(Path(str(vault_path)))
            candidates.append(delivery_dir / f"{stem}_SUBBED.mp4")
        except Exception:
            pass

    candidates.append(config.VIDEO_DIR / f"{stem}_SUBBED.mp4")

    output_path = next((p for p in candidates if p.exists()), None)
    if not output_path:
        return jsonify({"error": "Output not found"}), 404

    try:
        resolved = output_path.resolve(strict=False)
        delivery_root = config.DELIVERY_DIR.resolve(strict=False)
        try:
            resolved.relative_to(delivery_root)
        except Exception:
            return jsonify({"error": "Refusing to serve path outside delivery"}), 403
    except Exception:
        return jsonify({"error": "Invalid output path"}), 400

    return send_file(str(output_path), mimetype="video/mp4", as_attachment=True, download_name=output_path.name)


@app.route("/metrics")
def metrics():
    jobs = get_all_jobs()
    stage_counts: dict[str, int] = {}
    for job in jobs:
        stage = (job.get("stage") or "UNKNOWN").upper()
        stage_counts[stage] = stage_counts.get(stage, 0) + 1

    manager_age = _heartbeat_age_seconds("omega_manager")
    storage_ready = 0
    try:
        storage_ready = 1 if config.critical_paths_ready(require_write=True) else 0
    except Exception:
        storage_ready = 0

    lines: list[str] = []
    lines.append("# HELP omega_storage_ready Storage paths ready/writable (1/0)")
    lines.append("# TYPE omega_storage_ready gauge")
    lines.append(f"omega_storage_ready {storage_ready}")
    lines.append("# HELP omega_jobs_total Total jobs in DB")
    lines.append("# TYPE omega_jobs_total gauge")
    lines.append(f"omega_jobs_total {len(jobs)}")
    lines.append("# HELP omega_jobs_stage_total Jobs by stage")
    lines.append("# TYPE omega_jobs_stage_total gauge")
    for stage, count in sorted(stage_counts.items()):
        lines.append(f'omega_jobs_stage_total{{stage="{stage}"}} {count}')
    if manager_age is not None:
        lines.append("# HELP omega_manager_heartbeat_age_seconds Seconds since manager heartbeat")
        lines.append("# TYPE omega_manager_heartbeat_age_seconds gauge")
        lines.append(f"omega_manager_heartbeat_age_seconds {manager_age:.3f}")

    return "\n".join(lines) + "\n", 200, {"Content-Type": "text/plain; version=0.0.4"}

@app.route('/api/action/fork', methods=['POST'])
@admin_required
def api_fork():
    """Fork a job into multiple target languages.

    Uses the bulletproof fork_language() function for each language.
    Accepts either job_id (file_stem) or program_id.
    """
    try:
        data = request.json
        job_id = data.get("jobId")
        program_id = data.get("program_id")
        languages = data.get("languages", [])

        if not languages:
            return jsonify({"error": "No languages provided"}), 400

        # Find program_id from job_id if needed
        if not program_id and job_id:
            track = omega_db.get_track_by_job(job_id)
            if track:
                program_id = track.get("program_id")

        if not program_id:
            return jsonify({"error": "No program_id or valid jobId provided"}), 400

        logger.info(f"Forking program {program_id} into {languages}")

        results = []
        errors = []
        for lang in languages:
            lang = lang.lower().strip()
            try:
                result = omega_db.fork_language(program_id, lang)
                results.append({
                    "language": lang,
                    "track_id": result["track_id"],
                    "job_id": result["job_id"],
                    "status": result["status"]
                })
            except ValueError as e:
                errors.append({"language": lang, "error": str(e)})
            except Exception as e:
                logger.error(f"Fork to {lang} failed: {e}")
                errors.append({"language": lang, "error": str(e)})

        return jsonify({
            "success": len(results) > 0,
            "message": f"Created {len(results)} language tracks",
            "tracks": results,
            "errors": errors if errors else None
        })
    except Exception as e:
        logger.error(f"Fork failed: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/action/dub', methods=['POST'])
def api_dub():
    """Trigger AI Dubbing for a job."""
    try:
        data = request.json
        job_id = data.get("jobId") # file_stem
        voice = data.get("voice", "alloy")
        
        if not job_id:
            return jsonify({"error": "No jobId provided"}), 400
            
        # Get Job from DB
        job = omega_db.get_job_via_track(job_id)
        if not job:
            return jsonify({"error": "Job not found"}), 404
            
        # We assume job structure: jobs/<file_stem>
        # Ensure we use absolute path
        job_dir = (Path("jobs") / job_id).resolve()
        
        def run_dubbing():
            try:
                logger.info(f"Starting dubbing for {job_id}")
                omega_db.update_job_via_track(job_id, status=f"Dubbing ({voice})")
                
                # Update Dubber to support voice selection if needed
                # For now, Dubber uses OpenAITTSProvider default
                dubber = Dubber(job_id, job_dir)
                dubber.run()
                
                omega_db.update_job_via_track(job_id, status="Dubbing Complete")
            except Exception as e:
                logger.error(f"Dubbing failed: {e}")
                omega_db.update_job_via_track(job_id, status="Dubbing Failed")

        thread = threading.Thread(target=run_dubbing)
        thread.start()

        return jsonify({"success": True, "message": "Dubbing started"})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/action', methods=['POST'])
@admin_required
def api_action():
    """Handle surgical actions."""
    data = request.get_json(silent=True) or {}
    action = data.get('action')
    file_stem = data.get('file_stem')
    logger.info(f"👉 API ACTION RECEIVED: {action} for {file_stem}")

    if action == "restart_manager":
        try:
            _MANAGER_RESTART_FLAG.parent.mkdir(exist_ok=True)
            _MANAGER_RESTART_FLAG.write_text(datetime.now().isoformat(), encoding="utf-8")
        except Exception as e:
            return jsonify({"error": f"Failed to write restart flag: {e}"}), 500

        # If the manager looks down, attempt to start it.
        started = False
        age = _heartbeat_age_seconds("omega_manager")
        if age is None or age > 30:
            try:
                mgr_path = config.BASE_DIR / "omega_manager.py"
                python_bin = os.environ.get("OMEGA_PYTHON") or sys.executable
                subprocess.Popen(
                    [python_bin, str(mgr_path)],
                    cwd=str(config.BASE_DIR),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                started = True
            except Exception as e:
                return jsonify({"error": f"Failed to start manager: {e}"}), 500

        msg = "Restart requested; manager will restart when idle."
        if started:
            msg = "Manager started and restart requested."
        return jsonify({"success": True, "message": msg})
    
    if not file_stem:
        return jsonify({"error": "Missing file_stem"}), 400

    if action == "reset_review":
        # Reset to REVIEWED stage (triggers Finalizer)
        omega_db.update_job_via_track(file_stem, stage="REVIEWED", status="Manual Reset", progress=70.0)
        return jsonify({"success": True, "message": f"Reset {file_stem} to Review"})

    elif action == "retry_translate":
        skel = config.VAULT_DATA / f"{file_stem}_SKELETON.json"
        skel_done = config.VAULT_DATA / f"{file_stem}_SKELETON_DONE.json"
        if not skel.exists():
            if skel_done.exists():
                shutil.copy2(skel_done, skel)
            else:
                return jsonify({"error": f"Skeleton not found for {file_stem}"}), 404

        omega_db.update_job_via_track(
            file_stem,
            stage="TRANSCRIBED",
            status="Manual Retry: Translation",
            progress=30.0,
            meta={"halted": False, "manual_retry_translate_at": datetime.now().isoformat()},
        )
        return jsonify({"success": True, "message": f"Retry translate queued for {file_stem}"})

    elif action == "retry_review":
        job = omega_db.get_job_via_track(file_stem) or {}
        lang = (job.get("target_language") or "is").lower()
        trans_path = config.EDITOR_DIR / f"{file_stem}_{lang.upper()}.json"

        if not trans_path.exists():
            # Reconstruct a review input file from the best available artifacts.
            src_path = config.VAULT_DATA / f"{file_stem}_SKELETON_DONE.json"
            if not src_path.exists():
                src_path = config.VAULT_DATA / f"{file_stem}_SKELETON.json"
            if not src_path.exists():
                return jsonify({"error": f"Source skeleton not found for {file_stem}"}), 404

            with open(src_path, "r", encoding="utf-8") as f:
                src_wrapper = json.load(f)
            source_data = src_wrapper.get("segments", src_wrapper) if isinstance(src_wrapper, dict) else src_wrapper
            if not isinstance(source_data, list):
                return jsonify({"error": f"Invalid skeleton format for {file_stem}"}), 400

            approved_path = config.TRANSLATED_DONE_DIR / f"{file_stem}_APPROVED.json"
            if not approved_path.exists():
                return jsonify({"error": f"Approved file not found for {file_stem}"}), 404

            with open(approved_path, "r", encoding="utf-8") as f:
                approved_wrapper = json.load(f)
            translated_data = (
                approved_wrapper.get("segments", approved_wrapper)
                if isinstance(approved_wrapper, dict)
                else approved_wrapper
            )
            if not isinstance(translated_data, list):
                return jsonify({"error": f"Invalid approved format for {file_stem}"}), 400

            payload = {"source_data": source_data, "translated_data": translated_data}
            trans_path.parent.mkdir(parents=True, exist_ok=True)
            with open(trans_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)

        omega_db.update_job_via_track(
            file_stem,
            stage="TRANSLATED",
            status="Manual Retry: Review",
            progress=55.0,
            meta={"halted": False, "translation_path": str(trans_path), "manual_retry_review_at": datetime.now().isoformat()},
        )
        return jsonify({"success": True, "message": f"Retry review queued for {file_stem}"})

    elif action == "unhalt_job":
        job = omega_db.get_job_via_track(file_stem)
        if not job:
            return jsonify({"error": f"Job not found: {file_stem}"}), 404

        meta = job.get("meta") or {}
        if not isinstance(meta, dict):
            meta = {}

        now = datetime.now().isoformat()

        # 1) Completed output exists?
        output_candidates: list[Path] = []
        final_output = meta.get("final_output")
        if final_output:
            output_candidates.append(Path(str(final_output)))

        vault_path = job.get("vault_path") or meta.get("vault_path")
        if vault_path:
            try:
                delivery_dir = _derive_delivery_dir_from_vault(Path(str(vault_path)))
                output_candidates.append(delivery_dir / f"{file_stem}_SUBBED.mp4")
            except Exception:
                pass
        output_candidates.append(config.VIDEO_DIR / f"{file_stem}_SUBBED.mp4")

        output_path = next((p for p in output_candidates if p.exists()), None)
        if output_path:
            omega_db.update_job_via_track(
                file_stem,
                stage="COMPLETED",
                status="Done",
                progress=100.0,
                meta={"halted": False, "unhalted_at": now, "final_output": str(output_path)},
            )
            return jsonify({"success": True, "message": f"Unhalted {file_stem} (already completed)"})

        # 2) SRT exists -> ready to burn
        srt_path = config.SRT_DIR / f"{file_stem}.srt"
        if srt_path.exists():
            omega_db.update_job_via_track(
                file_stem,
                stage="FINALIZED",
                status="Ready to Burn",
                progress=90.0,
                meta={"halted": False, "unhalted_at": now},
            )
            return jsonify({"success": True, "message": f"Unhalted {file_stem} (resume at FINALIZED)"})

        # 3) Approved exists -> ready to finalize
        approved = config.TRANSLATED_DONE_DIR / f"{file_stem}_APPROVED.json"
        if approved.exists():
            omega_db.update_job_via_track(
                file_stem,
                stage="REVIEWED",
                status="Editor Approved",
                progress=70.0,
                meta={"halted": False, "unhalted_at": now},
            )
            return jsonify({"success": True, "message": f"Unhalted {file_stem} (resume at REVIEWED)"})

        # 4) Skeleton exists -> ready to translate
        skel = config.VAULT_DATA / f"{file_stem}_SKELETON.json"
        skel_done = config.VAULT_DATA / f"{file_stem}_SKELETON_DONE.json"
        if not skel.exists() and skel_done.exists():
            try:
                shutil.copy2(skel_done, skel)
            except Exception:
                pass

        omega_db.update_job_via_track(
            file_stem,
            stage="TRANSCRIBED",
            status="Ready for Translation",
            progress=30.0,
            meta={"halted": False, "unhalted_at": now},
        )
        return jsonify({"success": True, "message": f"Unhalted {file_stem} (resume at TRANSCRIBED)"})
        
    elif action == "force_burn":
        with _force_burn_lock:
            if file_stem in _force_burn_inflight:
                return jsonify({"success": True, "message": f"Burn already running for {file_stem}"}), 200
            _force_burn_inflight.add(file_stem)

        omega_db.update_job_via_track(
            file_stem,
            stage="FINALIZED",
            status="Manual Burn (Queued)",
            progress=90.0,
            meta={"burn_requested_at": datetime.now().isoformat()},
        )

        t = threading.Thread(target=_run_force_burn, args=(file_stem,), name=f"force_burn_{file_stem}", daemon=True)
        t.start()
        return jsonify({"success": True, "message": f"Queued burn for {file_stem}"})
        
    elif action == "remove_lyrics":
        return jsonify({"success": False, "message": "Not implemented yet"}), 501

    elif action == "set_language":
        target_language = data.get('target_language')
        if not target_language:
            return jsonify({"error": "Missing target_language"}), 400
        omega_db.update_job_via_track(file_stem, target_language=target_language)
        return jsonify({"success": True, "message": f"Language set to {target_language}"})

    elif action == "set_profile":
        program_profile = data.get('program_profile')
        if not program_profile:
            return jsonify({"error": "Missing program_profile"}), 400
        omega_db.update_job_via_track(file_stem, program_profile=program_profile)
        return jsonify({"success": True, "message": f"Profile set to {program_profile}"})

    elif action == "set_style":
        subtitle_style = data.get('subtitle_style')
        if not subtitle_style:
            return jsonify({"error": "Missing subtitle_style"}), 400
        omega_db.update_job_via_track(file_stem, subtitle_style=subtitle_style)
        return jsonify({"success": True, "message": f"Style set to {subtitle_style}"})

    elif action == "approve_burn":
        omega_db.update_job_via_track(
            file_stem,
            status="Approved for Burn",
            meta={
                "burn_approved": True,
                "burn_approved_at": datetime.now().isoformat(),
            },
        )
        return jsonify({"success": True, "message": f"Approved Burn for {file_stem}"})

    elif action == "set_mode":
        mode = data.get('mode')
        if mode not in ["AUTO", "REVIEW"]:
             return jsonify({"error": "Invalid mode"}), 400
        omega_db.update_job_via_track(
            file_stem,
            meta={
                "mode": mode,
                "review_required": mode == "REVIEW",
                "mode_set_at": datetime.now().isoformat(),
            },
        )
        return jsonify({"success": True, "message": f"Mode set to {mode}"})

    elif action == "delete_job":
        logger.info(f"🗑️ DELETE JOB REQUEST: file_stem='{file_stem}'")
        
        deleted_paths = []
        
        # 1. Delete project folder (new structure)
        project_dir = config.find_project_folder(file_stem)
        if project_dir and project_dir.exists():
            try:
                shutil.rmtree(str(project_dir))
                deleted_paths.append(str(project_dir))
                logger.info(f"   📁 Deleted project folder: {project_dir}")
            except Exception as e:
                logger.warning(f"   ⚠️ Failed to delete project folder: {e}")
        
        # 2. Delete legacy flat files
        for pattern_dir in [config.VAULT_DATA, config.VAULT_VIDEOS]:
            for f in pattern_dir.glob(f"{file_stem}*"):
                try:
                    if f.is_file():
                        f.unlink()
                        deleted_paths.append(str(f))
                    elif f.is_dir():
                        shutil.rmtree(str(f))
                        deleted_paths.append(str(f))
                except Exception as e:
                    logger.warning(f"   ⚠️ Failed to delete {f}: {e}")
        
        # 3. Delete database record
        omega_db.delete_job(file_stem)
        
        logger.info(f"✅ DELETE JOB COMPLETED: file_stem='{file_stem}', {len(deleted_paths)} files removed")
        return jsonify({"success": True, "message": f"Deleted {file_stem}", "deleted_paths": deleted_paths})

    elif action == "re_burn":
        logger.info(f"🔄 Re-Burn Triggered for {file_stem}")
        
        # Extract delivery profile from request (optional)
        delivery_profile = data.get('delivery_profile')
        if delivery_profile:
            logger.info(f"   📦 Delivery Profile: {delivery_profile}")
        
        # 1. Backup existing output (so manager doesn't auto-complete it)
        try:
            output_path = config.VIDEO_DIR / f"{file_stem}_SUBBED.mp4"
            logger.info(f"   Checking Video: {output_path} (Exists: {output_path.exists()})")
            if output_path.exists():
                backup_name = f"{file_stem}_SUBBED.bak_{int(time.time())}.mp4"
                backup_path = config.VIDEO_DIR / backup_name
                shutil.move(str(output_path), str(backup_path))
                logger.info(f"   📦 Backed up old video to {backup_name}")
        except Exception as e:
            logger.error(f"   ❌ Backup failed: {e}")
            return jsonify({"error": f"Failed to backup output: {e}"}), 500

        # 2. Restore SRT (if it was moved to DONE_)
        srt_path = config.SRT_DIR / f"{file_stem}.srt"
        done_srt_path = config.SRT_DIR / f"DONE_{file_stem}.srt"
        logger.info(f"   Checking SRT: {srt_path} (Exists: {srt_path.exists()})")
        logger.info(f"   Checking DONE_SRT: {done_srt_path} (Exists: {done_srt_path.exists()})")
        
        if not srt_path.exists() and done_srt_path.exists():
            try:
                shutil.move(str(done_srt_path), str(srt_path))
                logger.info(f"   ♻️ Restored SRT for {file_stem}")
            except Exception as e:
                logger.error(f"   ❌ Restore failed: {e}")
                return jsonify({"error": f"Failed to restore SRT: {e}"}), 500
        
        # 3. Reset DB Status to FINALIZED (Ready to Burn)
        # Also save delivery_profile if provided
        logger.info(f"   📝 Updating DB for {file_stem}...")
        try:
            update_kwargs = {
                "stage": "FINALIZED",
                "status": "Queued for Re-Burn",
                "progress": 90.0,
                "meta": {
                    "burn_completed_at": None,
                    "final_output": None,
                    "burn_approved": True,
                    "reburn_requested_at": datetime.now().isoformat(),
                    "halted": False 
                }
            }
            # Save delivery_profile to job record
            if delivery_profile:
                update_kwargs["delivery_profile"] = delivery_profile
            
            omega_db.update_job_via_track(file_stem, **update_kwargs)
            logger.info("   ✅ DB Updated successfully")
        except Exception as e:
            logger.error(f"   ❌ DB Update failed: {e}")
            return jsonify({"error": f"DB Update failed: {e}"}), 500

        return jsonify({"success": True, "message": f"Queued {file_stem} for Re-Burn"})

    return jsonify({"error": "Invalid action"}), 400

@app.route('/api/smart_upload', methods=['POST'])
@admin_required
def smart_upload():
    """
    Smart file upload that handles multiple file combinations:
    - full_pipeline: Video only → Transcribe → Translate → Burn
    - quick_burn: Video + SRT → Skip to Finalize → Burn
    - skip_transcription: Video + Transcript → Translate → Burn
    - srt_update: SRT only → Update existing job → Re-Burn
    """
    mode = request.form.get('mode', 'full_pipeline')
    logger.info(f"📥 Smart Upload: mode={mode}")
    
    # Collect all uploaded files
    files = []
    for key in request.files:
        if key.startswith('file_'):
            files.append(request.files[key])
    
    if not files:
        return jsonify({"error": "No files uploaded"}), 400
    
    VIDEO_EXTS = {'.mp4', '.mov', '.mkv', '.avi', '.webm', '.m4v', '.wmv'}
    SRT_EXTS = {'.srt'}
    TRANSCRIPT_EXTS = {'.txt', '.json'}
    
    video_files = [f for f in files if any(f.filename.lower().endswith(ext) for ext in VIDEO_EXTS)]
    srt_files = [f for f in files if any(f.filename.lower().endswith(ext) for ext in SRT_EXTS)]
    transcript_files = [f for f in files if any(f.filename.lower().endswith(ext) for ext in TRANSCRIPT_EXTS)]
    
    try:
        if mode == 'quick_burn' and video_files and srt_files:
            # Video + SRT → Skip to burn
            video_file = video_files[0]
            srt_file = srt_files[0]
            
            video_filename = secure_filename(video_file.filename)
            srt_filename = secure_filename(srt_file.filename)
            stem = Path(video_filename).stem
            
            # Save video to vault
            video_path = config.VAULT_VIDEOS / video_filename
            config.VAULT_VIDEOS.mkdir(parents=True, exist_ok=True)
            video_file.save(video_path)
            logger.info(f"   Saved video: {video_path}")
            
            # Save SRT to delivery folder (ready for burn)
            srt_dest = config.SRT_DIR / f"{stem}.srt"
            config.SRT_DIR.mkdir(parents=True, exist_ok=True)
            srt_file.save(srt_dest)
            logger.info(f"   Saved SRT: {srt_dest}")
            
            # Create Program/Track (Replaces legacy upsert)
            program_id = omega_db.create_program(
                title=stem,
                original_filename=video_filename,
                video_path=str(video_path),
                default_style="Classic",
                meta={
                    "original_filename": video_filename,
                    "quick_burn": True,
                    "external_srt": True,
                    "uploaded_at": datetime.now().isoformat()
                }
            )
            
            master_script_id = omega_db.ensure_master_script(
                program_id=program_id,
                language_code="is",
                language_name="Icelandic"
            )

            omega_db.create_track(
                program_id=program_id,
                type='subtitle',
                language_code="is",
                language_name="Icelandic",
                stage="FINALIZED", 
                status="Quick Burn (Video+SRT)",
                progress=90.0,
                job_id=stem,
                master_script_id=master_script_id,
                meta={
                    "original_filename": video_filename,
                    "quick_burn": True,
                    "external_srt": True,
                    "uploaded_at": datetime.now().isoformat()
                }
            )
            
            return jsonify({
                "success": True, 
                "message": f"Quick Burn queued: {stem}",
                "stem": stem,
                "mode": "quick_burn"
            })
            
        elif mode == 'srt_update' and srt_files:
            # SRT only → Update existing job
            srt_file = srt_files[0]
            srt_filename = secure_filename(srt_file.filename)
            stem = Path(srt_filename).stem
            
            # Check if job exists
            existing = omega_db.get_job_via_track(stem)
            if not existing:
                return jsonify({"error": f"No existing job found for {stem}"}), 404
            
            # Backup old SRT
            old_srt = config.SRT_DIR / f"{stem}.srt"
            if old_srt.exists():
                backup = config.SRT_DIR / f"{stem}.srt.bak_{int(time.time())}"
                shutil.copy2(old_srt, backup)
            
            # Save new SRT
            srt_file.save(old_srt)
            logger.info(f"   Updated SRT: {old_srt}")
            
            # Update job to re-burn
            omega_db.update_job_via_track(
                stem,
                stage="FINALIZED",
                status="SRT Updated - Re-Burn",
                progress=90.0,
                meta={
                    "srt_updated_at": datetime.now().isoformat(),
                    "final_output": None  # Clear to trigger re-burn
                }
            )
            
            return jsonify({
                "success": True,
                "message": f"SRT updated for {stem}, queued for re-burn",
                "stem": stem,
                "mode": "srt_update"
            })
            
        elif mode == 'skip_transcription' and video_files and transcript_files:
            # Video + Transcript → Skip transcription
            video_file = video_files[0]
            transcript_file = transcript_files[0]
            
            video_filename = secure_filename(video_file.filename)
            transcript_filename = secure_filename(transcript_file.filename)
            stem = Path(video_filename).stem
            
            # Save video
            video_path = config.VAULT_VIDEOS / video_filename
            config.VAULT_VIDEOS.mkdir(parents=True, exist_ok=True)
            video_file.save(video_path)
            
            # Save transcript as skeleton
            skeleton_path = config.VAULT_DATA / f"{stem}_SKELETON.json"
            config.VAULT_DATA.mkdir(parents=True, exist_ok=True)
            
            # Read and convert transcript
            transcript_content = transcript_file.read().decode('utf-8')
            if transcript_filename.endswith('.json'):
                # Assume already in skeleton format
                with open(skeleton_path, 'w', encoding='utf-8') as f:
                    f.write(transcript_content)
            else:
                # Plain text - wrap in skeleton format
                skeleton = {
                    "meta": {"stem": stem, "source": "external_transcript"},
                    "segments": [{"id": 1, "start": 0, "end": 0, "text": transcript_content}]
                }
                with open(skeleton_path, 'w', encoding='utf-8') as f:
                    json.dump(skeleton, f, ensure_ascii=False, indent=2)
            
            # Create Program/Track (Replaces legacy upsert)
            program_id = omega_db.create_program(
                title=stem,
                original_filename=video_filename,
                video_path=str(video_path),
                default_style="Classic",
                meta={
                    "original_filename": video_filename,
                    "external_transcript": True,
                    "uploaded_at": datetime.now().isoformat()
                }
            )

            master_script_id = omega_db.ensure_master_script(
                program_id=program_id,
                language_code="is",
                language_name="Icelandic"
            )

            omega_db.create_track(
                program_id=program_id,
                type='subtitle',
                language_code="is",
                language_name="Icelandic",
                stage="TRANSCRIBED",
                status="External Transcript - Ready to Translate",
                progress=30.0,
                job_id=stem,
                master_script_id=master_script_id,
                meta={
                    "original_filename": video_filename,
                    "external_transcript": True,
                    "uploaded_at": datetime.now().isoformat()
                }
            )
            
            return jsonify({
                "success": True,
                "message": f"Transcript imported for {stem}, ready for translation",
                "stem": stem,
                "mode": "skip_transcription"
            })
            
        else:
            # Full pipeline - save video to INBOX
            video_file = video_files[0] if video_files else files[0]
            video_filename = secure_filename(video_file.filename)

            # Get languages and review mode from the request
            languages_json = request.form.get('languages', '["is"]')
            review_mode = request.form.get('review_mode', 'automatic')
            try:
                languages = json.loads(languages_json)
            except:
                languages = ["is"]

            logger.info(f"   Languages: {languages}, Review mode: {review_mode}")

            # Determine the inbox subfolder based on review mode
            if review_mode == 'human':
                inbox_subfolder = "03_REMOTE_REVIEW"
            else:
                inbox_subfolder = "01_AUTO_PILOT"

            save_path = config.INBOX_DIR / inbox_subfolder / "Classic" / video_filename
            save_path.parent.mkdir(parents=True, exist_ok=True)
            video_file.save(save_path)

            # Store language preferences in a sidecar file so omega_manager can use them
            # The sidecar file should have the same name as the video but with .json extension
            stem = Path(video_filename).stem
            sidecar_path = config.INBOX_DIR / inbox_subfolder / "Classic" / f"{stem}.json"
            with open(sidecar_path, 'w', encoding='utf-8') as f:
                json.dump({
                    "languages": languages,
                    "review_mode": review_mode,
                    "uploaded_at": datetime.now().isoformat()
                }, f, indent=2)

            logger.info(f"   Full pipeline: {save_path} (languages: {languages})")

            return jsonify({
                "success": True,
                "message": f"Full pipeline started: {video_filename}",
                "languages": languages,
                "review_mode": review_mode,
                "mode": "full_pipeline"
            })
            
    except Exception as e:
        logger.error(f"Smart upload error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/surgical/segments', methods=['GET'])
@admin_required
def get_segments():
    stem = request.args.get('stem')
    if not stem: return jsonify({"error": "Missing stem"}), 400
    
    # Try APPROVED first, then TRANSLATED
    paths = [
        config.SRT_DIR / f"{stem}_normalized.json", # Finalized output (Best)
        config.SRT_DIR / f"DONE_{stem}_normalized.json", # Completed output
        config.TRANSLATED_DONE_DIR / f"{stem}_APPROVED.json",
        config.TRANSLATED_DONE_DIR / f"{stem}_ICELANDIC.json", # Legacy
        config.TRANSLATED_DONE_DIR / f"{stem}_is.json" # New standard
    ]
    
    for p in paths:
        if p.exists():
            try:
                with open(p, 'r') as f:
                    data = json.load(f)
                    # Normalize if it's the old format with "translated_data"
                    if isinstance(data, dict):
                        if "translated_data" in data:
                            data = data["translated_data"]
                        elif "segments" in data:
                            data = data["segments"]
                        elif "events" in data:
                             # Transform 'events' (start, end, lines) to 'segments' (id, start, end, text)
                             raw_events = data["events"]
                             data = []
                             for idx, ev in enumerate(raw_events):
                                 data.append({
                                     "id": idx + 1,
                                     "start": ev["start"],
                                     "end": ev["end"],
                                     "text": "\n".join(ev.get("lines", []))
                                 })
                    
                    # FETCH SOURCE TEXT
                    try:
                        source_path = config.VAULT_DATA / f"{stem}_SKELETON_DONE.json"
                        if not source_path.exists():
                            source_path = config.VAULT_DATA / f"{stem}_SKELETON.json"
                        
                        if source_path.exists():
                            with open(source_path, 'r') as f:
                                source_data = json.load(f)
                                # Handle wrapper
                                if isinstance(source_data, dict) and "segments" in source_data:
                                    source_data = source_data["segments"]
                                
                                source_map = {s['id']: s['text'] for s in source_data if 'id' in s}
                                
                                # Merge
                                for seg in data:
                                    if 'id' in seg and seg['id'] in source_map:
                                        seg['source_text'] = source_map[seg['id']]
                    except Exception as e:
                        logger.warning(f"Failed to load source text for {stem}: {e}")

                    return jsonify({"segments": data, "source": p.name})
            except Exception as e:
                return jsonify({"error": str(e)}), 500
                
    return jsonify({"error": "No editable file found"}), 404



@app.route('/api/surgical/save', methods=['POST'])
@admin_required
def save_segments():
    data = request.json
    stem = data.get('stem')
    segments = data.get('segments')
    
    if not stem or not segments:
        return jsonify({"error": "Missing data"}), 400
        
    try:
        # 1. Save back to the canonical approved file
        output_path = config.TRANSLATED_DONE_DIR / f"{stem}_APPROVED.json"
        
        # 2. Backup if exists
        if output_path.exists():
            backup_path = output_path.with_suffix(f".json.bak_{int(time.time())}")
            shutil.copy(output_path, backup_path)
            
        # 3. Save New Content
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump({"segments": segments, "meta": {"edited_via": "dashboard", "edited_at": datetime.now().isoformat()}}, f, indent=2, ensure_ascii=False)
            
        # 4. Auto-Finalize
        from workers import finalizer
        
        # Get language from DB
        job = omega_db.get_job_via_track(stem)
        lang = job.get('target_language', 'is') if job else 'is'
        
        srt_path, normalized_path = finalizer.finalize(output_path, target_language=lang)
        omega_db.update_job_via_track(
            stem,
            stage="FINALIZED",
            status="Ready to Burn",
            progress=90.0,
            meta={
                "surgical_edit_at": datetime.now().isoformat(),
                "srt_path": str(srt_path),
                "normalized_path": str(normalized_path),
            },
        )
        
        return jsonify({"success": True})
        
    except Exception as e:
        logger.error(f"Surgical Save Failed: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/assistant/chat', methods=['POST'])
@admin_required
def api_assistant_chat():
    """
    Interact with the Omega Assistant (Gemini).
    """
    try:
        data = request.json
        job_id = data.get('job_id')
        message = data.get('message')
        history = data.get('history', [])
        
        if not job_id or not message:
            return jsonify({"error": "Missing job_id or message"}), 400
            
        from workers import assistant
        
        # Run Assistant
        # TODO: Move to thread if slow, but text-only is usually fast enough (2-5s)
        # for Flash model.
        result = assistant.chat_with_job(job_id, message, history)
        
        if result.get("edits_performed"):
            # If AI modified the file, we must re-finalize to update SRT/Preview
            from workers import finalizer
            stem = job_id
            
            # Find the file that was edited (Assistant edits APPROVED or SKELETON)
            # We assume APPROVED for finalized jobs
            approved_path = config.VAULT_DATA / f"{stem}_APPROVED.json"
            
            if approved_path.exists():
                 # Get language from DB
                job = omega_db.get_job_via_track(stem)
                lang = job.get('target_language', 'is') if job else 'is'
                
                # Re-finalize
                srt_path, normalized_path = finalizer.finalize(approved_path, target_language=lang)
                
                omega_db.update_job_via_track(
                    stem,
                    stage="FINALIZED",
                    status="AI Edited",
                    progress=90.0,
                    meta={
                        "ai_edit_at": datetime.now().isoformat(),
                        "srt_path": str(srt_path),
                        "normalized_path": str(normalized_path),
                    },
                )
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Assistant Endpoint Failed: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/editor/<job_id>', methods=['GET', 'POST'])
@admin_required
def api_editor(job_id):
    """
    GET: Retrieve full segments for the editor.
    POST: Save updated segments and re-finalize.
    """
    try:
        from workers import assistant
        # Reuse the file loading logic from assistant (it knows the priority APPROVED > SKELETON)
        file_path, data = assistant._load_job_file(job_id)
        
        if request.method == 'GET':
            if not file_path or not data:
                return jsonify({"error": "Job file not found"}), 404
                
            segments = []
            if isinstance(data, dict):
                # Handle APPROVED format vs SKELETON format vs NORMALIZED
                if "segments" in data:
                    segments = data["segments"]
                elif "events" in data:
                    segments = data["events"]
                elif "translated_data" in data:
                    segments = data.get("translated_data", [])
            elif isinstance(data, list):
                segments = data
            
            # Normalize: Convert 'lines' array to 'text' string
            for seg in segments:
                if "lines" in seg and not "text" in seg:
                    seg["text"] = "\n".join(seg["lines"])
            
            # Populate "source_text" from Skeleton if missing
            # This ensures "Original Text" column is always filled
            try:
                skeleton_path = config.VAULT_DATA / f"{job_id}_SKELETON_DONE.json"
                if not skeleton_path.exists():
                    skeleton_path = config.VAULT_DATA / f"{job_id}_SKELETON.json"
                
                if skeleton_path.exists():
                    with open(skeleton_path, "r", encoding="utf-8") as f:
                        skel_data = json.load(f)
                        skel_segs = skel_data.get("segments", [])
                        
                    # Create timing map (start_time -> text)
                    skel_map = {}
                    for s in skel_segs:
                        # Use loose timing match (1 decimal place)
                        key = round(float(s.get("start", 0)), 1)
                        skel_map[key] = s.get("text", "")
                        
                    for seg in segments:
                        if not seg.get("source_text"):
                            start_key = round(float(seg.get("start", 0)), 1)
                            if start_key in skel_map:
                                seg["source_text"] = skel_map[start_key]
            except Exception as e:
                logger.warning(f"Failed to populate source_text from skeleton: {e}")

            track = omega_db.get_track_by_job(job_id)
            if track and (_station_scope_enabled() and not _track_allowed(track)):
                return jsonify({"error": "Job not found"}), 404

            return jsonify({
                "job_id": job_id,
                "file_path": str(file_path),
                "segments": segments,
                "graphic_zones": data.get("graphic_zones", []) if isinstance(data, dict) else [],
                "history": data.get("history", []) if isinstance(data, dict) else [],
                "track": track
            })

        elif request.method == 'POST':
            if not file_path:
                return jsonify({"error": "Original file not found, cannot save"}), 404
            
            payload = request.json
            new_segments = payload.get("segments")
            if not isinstance(new_segments, list):
                return jsonify({"error": "Invalid segments format"}), 400
            
            # 1. Backup
            assistant._backup_file(file_path)
            
            # 2. Save
            with open(file_path, "r", encoding="utf-8") as f:
                current_full_data = json.load(f)

            original_segments = []
            if isinstance(current_full_data, dict):
                original_segments = current_full_data.get("segments") or []
            elif isinstance(current_full_data, list):
                original_segments = current_full_data

            def _timing_match(left, right, tol=0.001):
                try:
                    return (
                        abs(float(left.get("start", 0.0)) - float(right.get("start", 0.0))) <= tol
                        and abs(float(left.get("end", 0.0)) - float(right.get("end", 0.0))) <= tol
                    )
                except Exception:
                    return False

            def _maybe_copy_fields(target, source):
                if not isinstance(source, dict) or not isinstance(target, dict):
                    return
                if (not isinstance(target.get("words"), list) or not target.get("words")) and isinstance(source.get("words"), list):
                    if _timing_match(target, source):
                        target["words"] = source.get("words")
                if not target.get("source_text") and source.get("source_text"):
                    if _timing_match(target, source):
                        target["source_text"] = source.get("source_text")

            def _timing_key(segment, precision=3):
                try:
                    return (
                        round(float(segment.get("start", 0.0)), precision),
                        round(float(segment.get("end", 0.0)), precision),
                    )
                except Exception:
                    return None

            timing_map = {}
            for seg in original_segments:
                if not isinstance(seg, dict):
                    continue
                key = _timing_key(seg)
                if key is not None:
                    timing_map[key] = seg

            for seg in new_segments:
                if not isinstance(seg, dict):
                    continue
                key = _timing_key(seg)
                source = timing_map.get(key) if key is not None else None
                if source:
                    _maybe_copy_fields(seg, source)
                else:
                    # If timing changed, drop word timing to avoid stale alignment.
                    seg.pop("words", None)
                    seg.pop("source_text", None)
            
            if isinstance(current_full_data, dict):
                current_full_data["segments"] = new_segments
                current_full_data["graphic_zones"] = payload.get("graphic_zones", [])
                current_full_data["history"] = payload.get("history", [])
                # Mark as manually edited (reviewer-approved content)
                current_full_data["normalized_for_review"] = True
                current_full_data["normalized_at"] = datetime.now().isoformat()
                # Update meta
                if "meta" not in current_full_data: current_full_data["meta"] = {}
                current_full_data["meta"]["last_manual_edit"] = datetime.now().isoformat()
            else:
                # Upgrade list format to dict format to support metadata
                current_full_data = {
                    "segments": new_segments,
                    "graphic_zones": payload.get("graphic_zones", []),
                    "history": payload.get("history", []),
                    "normalized_for_review": True,
                    "normalized_at": datetime.now().isoformat(),
                    "meta": {"last_manual_edit": datetime.now().isoformat()}
                }

            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(current_full_data, f, ensure_ascii=False, indent=2)

            # 3. Generate SRT directly (segments are already normalized by editor review)
            from workers import finalizer
            job = omega_db.get_job_via_track(job_id)
            lang = job.get('target_language', 'is') if job else 'is'

            # Use SRT conversion with line-breaking enforcement
            srt_path = config.SRT_DIR / f"{job_id}.srt"
            finalizer.segments_to_srt(new_segments, srt_path, target_language=lang)

            # Create normalized JSON for compatibility
            normalized_path = config.SRT_DIR / f"{job_id}_normalized.json"
            normalized_payload = {
                "events": [
                    {
                        "start": seg.get("start"),
                        "end": seg.get("end"),
                        "lines": seg.get("text", "").split("\n") if isinstance(seg.get("text"), str) else seg.get("lines", []),
                    }
                    for seg in new_segments
                ],
                "language": lang,
            }
            with open(normalized_path, "w", encoding="utf-8") as f:
                json.dump(normalized_payload, f, ensure_ascii=False, indent=2)
            
            # 4. Update DB
            omega_db.update_job_via_track(
                job_id,
                stage="FINALIZED",
                status="Manual Edit Saved",
                progress=90.0, 
                meta={
                    "manual_edit_at": datetime.now().isoformat(),
                    "srt_path": str(srt_path),
                    "normalized_path": str(normalized_path)
                }
            )

            change_type = payload.get("change_type")
            if change_type:
                change_type = str(change_type).strip()
                change_summary = payload.get("change_summary") or payload.get("summary")
                change_author = payload.get("change_author") or payload.get("author")
                track = omega_db.get_track_by_job(job_id)
                master_script_id = track.get("master_script_id") if track else None
                new_version = None

                if master_script_id:
                    omega_db.log_script_edit(
                        master_script_id=master_script_id,
                        track_id=track.get("id") if track else None,
                        change_type=str(change_type),
                        summary=change_summary,
                        author=change_author,
                    )
                    if change_type in {"text_change_minor", "text_change_material"}:
                        master = omega_db.get_master_script(master_script_id)
                        new_version = (master.get("version", 1) if master else 1) + 1
                        omega_db.update_master_script(master_script_id, version=new_version)

                        if change_type == "text_change_minor" and track:
                            program_id = track.get("program_id")
                            language_code = track.get("language_code")
                            if program_id and language_code:
                                for output in omega_db.get_tracks_for_program(program_id):
                                    if output.get("language_code") != language_code:
                                        continue
                                    if output.get("type") == "dub":
                                        omega_db.update_track(output.get("id"), pending_resync=True)

                if track:
                    track_updates = {
                        "output_override": False,
                        "override_reason": None,
                        "override_author": None,
                        "override_timestamp": None,
                        "pending_resync": False,
                    }
                    if change_type == "formatting_only":
                        track_updates["output_version"] = _bump_output_version(
                            track.get("output_version") or "1.0"
                        )
                    elif new_version is not None:
                        track_updates["output_version"] = _format_output_version(new_version)

                    omega_db.update_track(track.get("id"), **track_updates)
            
            return jsonify({"success": True})

    except Exception as e:
        logger.error(f"Editor API Failed: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@app.route('/api/stream/<job_id>')
def api_stream_video(job_id):
    """
    Stream the video file for a job.
    Supports Range requests via Flask's send_file.
    
    Priority order for finding video:
    1. meta.vault_path (after ingest)
    2. meta.source_path (original location)
    3. VAULT_VIDEOS / original_filename (fallback lookup)
    """
    try:
        job = omega_db.get_job_via_track(job_id)
        # if not job:
        #    return jsonify({"error": "Job not found"}), 404
            
        meta = job.get("meta", {}) if job else {}
        video_path = None
        
        # Priority 0: PROXY File (Always prefer web-ready proxy if available)
        candidates_proxies = []
        candidates_proxies.append(config.PROXIES_DIR / f"{job_id}_PROXY.mp4")
        
        # Also check original_stem (e.g. CBNJD..._PROXY.mp4)
        if meta and meta.get("original_stem"):
             candidates_proxies.append(config.PROXIES_DIR / f"{meta.get('original_stem')}_PROXY.mp4")
        
        # Fallback: Try to derive original stem from job_id (remove timestamp suffix)
        try:
            # Assumes format: STEM-TIMESTAMP
            derived_stem = job_id.rsplit('-', 1)[0].upper()
            candidates_proxies.append(config.PROXIES_DIR / f"{derived_stem}_PROXY.mp4")
        except Exception:
            pass
             
        # logger.info(f"Stream Candidates for {job_id}: {[str(p) for p in candidates_proxies]}")
        
        # DEBUG: Log all candidates
        logger.info(f"DEBUG STREAM {job_id}: Checking candidates: {[str(p) for p in candidates_proxies]}")
        
        proxy_path = next((p for p in candidates_proxies if p.exists()), None)
        if proxy_path:
            # logger.info(f"Found proxy: {proxy_path}")
            video_path = proxy_path
            
        # Priority 1: Check vault_path (set after ingest moves file) (Fallback)
        if not video_path:
            vault_path = meta.get("vault_path")
            if vault_path:
                candidate = Path(vault_path)
                if candidate.exists():
                    video_path = candidate
                
        # Priority 2: Check source_path
        if not video_path:
            source_path = meta.get("source_path")
            if source_path:
                candidate = Path(source_path)
                if candidate.exists():
                    video_path = candidate
        
        # Priority 3: Fallback - search VAULT_VIDEOS by original_filename
        if meta.get("original_stem"):
             candidates_proxies.append(config.PROXIES_DIR / f"{meta.get('original_stem')}_PROXY.mp4")

        # 3. Add case-insensitive candidates (explicit upper/lower) to handle Linux case sensitivity
        # This handles when frontend requests lowercase ID but file is UPPERCASE
        candidates_proxies.append(config.PROXIES_DIR / f"{job_id.upper()}_PROXY.mp4")
        if meta.get("original_stem"):
             candidates_proxies.append(config.PROXIES_DIR / f"{meta.get('original_stem').upper()}_PROXY.mp4")

        logger.info(f"Stream Candidates for {job_id}: {[str(p) for p in candidates_proxies]}")
        
        proxy_path = next((p for p in candidates_proxies if p.exists()), None)
        if proxy_path:
            logger.info(f"Found proxy: {proxy_path}")
            video_path = proxy_path
                    
        # Priority 4: Fallback - search VAULT_VIDEOS by job_id pattern
        if not video_path:
            for ext in [".mp4", ".mov", ".mkv", ".avi", ".m4v"]:
                candidate = config.VAULT_VIDEOS / f"{job_id}{ext}"
                if candidate.exists():
                    video_path = candidate
                    break
                    
        if not video_path:
            return jsonify({"error": "Video file not found", "checked": [
                str(vault_path) if vault_path else None,
                str(meta.get("source_path")) if meta.get("source_path") else None,
                str(config.VAULT_VIDEOS)
            ]}), 404
            
        return send_file(video_path, as_attachment=False, conditional=True)

    except Exception as e:
        logger.error(f"Stream Failed: {e}")
        return jsonify({"error": str(e)}), 500

# =============================================================================
# API V2: Programs, Tracks, Deliveries (Localization Platform)
# =============================================================================

@app.route('/api/v2/programs', methods=['GET'])
def api_v2_get_programs():
    """Get all programs with their tracks."""
    client = request.args.get('client')
    limit = int(request.args.get('limit', 100))
    
    programs = omega_db.get_all_programs(client=client, limit=limit)
    filtered_programs = []
    
    # Enrich with tracks
    for program in programs:
        program['tracks'] = omega_db.get_tracks_for_program(program['id'])
        program['tracks'] = _filter_tracks_by_station(program['tracks'])
        if _station_scope_enabled() and not program['tracks']:
            continue
        _hydrate_track_job_ids(program['tracks'])
        _hydrate_track_file_paths(program['tracks'])

        # Calculate completion stats
        total_tracks = len(program['tracks'])
        complete_tracks = sum(
            1 for t in program['tracks'] if t['stage'] in ('COMPLETE', 'COMPLETED', 'DELIVERED')
        )
        program['track_completion'] = f"{complete_tracks}/{total_tracks}" if total_tracks > 0 else "0/0"
        program['needs_attention'] = any(
            t['stage'] in ('AWAITING_REVIEW', 'AWAITING_APPROVAL', 'FAILED', 'DEAD')
            for t in program['tracks']
        )
        filtered_programs.append(program)
    
    return jsonify(filtered_programs)


@app.route('/api/v2/programs/<program_id>', methods=['GET'])
def api_v2_get_program(program_id):
    """Get a single program with all details."""
    program = omega_db.get_program(program_id)
    if not program:
        return jsonify({"error": "Program not found"}), 404
    
    program['tracks'] = omega_db.get_tracks_for_program(program_id)
    program['tracks'] = _filter_tracks_by_station(program['tracks'])
    if _station_scope_enabled() and not program['tracks']:
        return jsonify({"error": "Program not found"}), 404
    _hydrate_track_job_ids(program['tracks'])
    _hydrate_track_file_paths(program['tracks'])
    program['deliveries'] = []
    
    # Get deliveries for each track
    for track in program['tracks']:
        track_deliveries = omega_db.get_deliveries_for_track(track['id'])
        program['deliveries'].extend(track_deliveries)
    
    return jsonify(program)


@app.route('/api/v2/programs/<program_id>', methods=['DELETE'])
@admin_required
def api_v2_delete_program(program_id):
    """
    Delete a program and all associated data.
    
    Deletes:
    1. All tracks, jobs, deliveries, and scripts associated with the program
    2. Project folder and all files
    3. The program record is soft-deleted (tombstoned)
    """
    try:
        program = omega_db.get_program(program_id, include_deleted=True)
        if not program:
            return jsonify({"success": False, "error": "Program not found"}), 404

        if program.get("status") == "DELETED":
            return jsonify({
                "success": True,
                "message": f"Program '{program.get('title') or program_id}' already deleted",
                "program_id": program_id
            })

        original_filename = program.get("original_filename") or ""
        video_path = program.get("video_path") or ""
        title = program.get("title") or "Unknown"

        if not original_filename and video_path:
            try:
                original_filename = Path(video_path).name
            except Exception:
                original_filename = ""

        deleted_paths = []

        # 1. Delete project folder (new structure)
        if original_filename:
            stem = Path(original_filename).stem

            project_dir = config.find_project_folder(original_filename)
            if project_dir and project_dir.exists():
                try:
                    shutil.rmtree(str(project_dir))
                    deleted_paths.append(str(project_dir))
                    logger.info(f"   📁 Deleted project folder: {project_dir}")
                except Exception as e:
                    logger.warning(f"   ⚠️ Failed to delete project folder: {e}")

            # 2. Delete legacy flat files using stem to catch everything (video.json, video.srt, etc)
            directories_to_clean = [
                config.VAULT_DATA,
                config.VAULT_VIDEOS,
                config.VAULT_DIR / "Audio",
                config.VAULT_DIR / "Proxies",
                config.VAULT_DIR / "Thumbnails",
            ]

            for directory in directories_to_clean:
                if not directory.exists():
                    continue

                for f in directory.glob(f"{stem}*"):
                    try:
                        if f.name == original_filename or f.stem == stem or f.name.startswith(f"{stem}."):
                            if f.is_file():
                                f.unlink()
                                deleted_paths.append(str(f))
                            elif f.is_dir():
                                shutil.rmtree(str(f))
                                deleted_paths.append(str(f))
                    except Exception as e:
                        logger.warning(f"   ⚠️ Failed to delete {f}: {e}")

        conn = omega_db._connect()
        c = conn.cursor()

        try:
            c.execute(
                "SELECT id, job_id, master_script_id FROM tracks WHERE program_id = ?",
                (program_id,),
            )
            track_rows = c.fetchall()
            track_ids = [row["id"] for row in track_rows]
            job_ids = [row["job_id"] for row in track_rows if row["job_id"]]
            master_script_ids = {row["master_script_id"] for row in track_rows if row["master_script_id"]}

            c.execute("SELECT id FROM master_scripts WHERE program_id = ?", (program_id,))
            master_rows = c.fetchall()
            master_script_ids.update({row["id"] for row in master_rows})
            master_script_ids = list(master_script_ids)

            def _delete_in(table: str, column: str, values: list) -> int:
                if not values:
                    return 0
                placeholders = ", ".join(["?"] * len(values))
                c.execute(f"DELETE FROM {table} WHERE {column} IN ({placeholders})", values)
                return c.rowcount

            track_deliveries_deleted = _delete_in("track_deliveries", "track_id", track_ids)
            script_edits_deleted = _delete_in("script_edits", "track_id", track_ids)
            script_edits_deleted += _delete_in("script_edits", "master_script_id", master_script_ids)
            deliveries_deleted = _delete_in("deliveries", "job_stem", job_ids)
            jobs_deleted = _delete_in("jobs", "file_stem", job_ids)

            c.execute("DELETE FROM tracks WHERE program_id = ?", (program_id,))
            tracks_deleted = c.rowcount

            c.execute("DELETE FROM master_scripts WHERE program_id = ?", (program_id,))
            master_scripts_deleted = c.rowcount

            now_iso = datetime.now().isoformat()
            c.execute(
                """
                UPDATE programs
                SET status = 'DELETED',
                    deleted_at = ?,
                    deleted_original_filename = ?,
                    deleted_video_path = ?,
                    original_filename = NULL,
                    video_path = NULL,
                    thumbnail_path = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (now_iso, original_filename or None, video_path or None, now_iso, program_id),
            )

            conn.commit()
        finally:
            conn.close()

        omega_db._sync_row("programs", "id", program_id)

        logger.info(f"✅ SOFT DELETED PROGRAM: {title} (id={program_id})")

        return jsonify({
            "success": True,
            "message": f"Deleted program '{title}'",
            "tracks_deleted": tracks_deleted,
            "track_deliveries_deleted": track_deliveries_deleted,
            "jobs_deleted": jobs_deleted,
            "deliveries_deleted": deliveries_deleted,
            "master_scripts_deleted": master_scripts_deleted,
            "script_edits_deleted": script_edits_deleted,
            "files_deleted": len(deleted_paths),
            "deleted_paths": deleted_paths
        })
        
    except Exception as e:
        logger.error(f"Delete program failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/v2/programs', methods=['POST'])
@admin_required
def api_v2_create_program():
    """Create a new program manually."""
    data = request.json
    
    meta = data.get('meta', {}) or {}
    if 'station_id' not in meta:
        meta['station_id'] = config.OMEGA_STATION_ID

    program_id = omega_db.create_program(
        title=data.get('title', 'Untitled'),
        original_filename=data.get('original_filename'),
        video_path=data.get('video_path'),
        client=data.get('client'),
        due_date=data.get('due_date'),
        default_style=data.get('default_style', 'Classic'),
        meta=meta
    )
    
    return jsonify({"success": True, "program_id": program_id})


# =============================================================================
# API V2: Staged Programs (Workflow Architecture)
# =============================================================================

@app.route('/api/v2/programs/staged', methods=['GET'])
def api_v2_get_staged_programs():
    """Get programs waiting for configuration (ingest_mode='staged')."""
    try:
        conn = omega_db._connect()
        c = conn.cursor()
        c.execute("""
            SELECT * FROM programs 
            WHERE ingest_mode = 'staged'
              AND (status != 'DELETED' OR status IS NULL)
            ORDER BY created_at DESC
        """)
        programs = [dict(row) for row in c.fetchall()]
        conn.close()
        
        # Parse meta JSON for each program
        for program in programs:
            if program.get('meta'):
                try:
                    program['meta'] = json.loads(program['meta'])
                except:
                    program['meta'] = {}

        if _station_scope_enabled():
            programs = [p for p in programs if _station_match(_normalize_meta(p.get('meta')))]

        return jsonify(programs)
    except Exception as e:
        logger.error(f"Failed to get staged programs: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/v2/programs/<program_id>/configure', methods=['POST'])
@admin_required
def api_v2_configure_staged_program(program_id):
    """
    Configure a staged program before starting translation.
    
    Expected payload:
    {
        "ministry_id": "uuid",       # Optional: ministry profile ID
        "delivery_id": "uuid",       # Optional: delivery profile ID
        "languages": ["nl", "is"],   # Required: list of target languages
        "style": "Classic"           # Optional: subtitle style
    }
    """
    try:
        program = omega_db.get_program(program_id)
        if not program:
            return jsonify({"error": "Program not found"}), 404
        
        if program.get('ingest_mode') != 'staged':
            return jsonify({"error": "Program is not in staged mode"}), 400
        
        data = request.json or {}
        languages = data.get('languages', [])
        
        if not languages:
            return jsonify({"error": "At least one target language is required"}), 400
        
        # Update program with configuration
        updates = {}
        if data.get('ministry_id'):
            updates['ministry_id'] = data['ministry_id']
        if data.get('delivery_id'):
            updates['delivery_id'] = data['delivery_id']
        if data.get('style'):
            updates['default_style'] = data['style']
        
        # Store languages in meta
        current_meta = program.get('meta', {})
        if isinstance(current_meta, str):
            try:
                current_meta = json.loads(current_meta)
            except:
                current_meta = {}
        
        current_meta['configured_languages'] = languages
        current_meta['configured_at'] = datetime.now().isoformat()
        updates['meta'] = json.dumps(current_meta)
        
        if updates:
            omega_db.update_program(program_id, **updates)
        
        return jsonify({
            "success": True,
            "program_id": program_id,
            "configured_languages": languages,
            "message": "Program configured. Call /start to begin translation."
        })
        
    except Exception as e:
        logger.error(f"Failed to configure staged program: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/v2/programs/<program_id>/start', methods=['POST'])
@admin_required
def api_v2_start_staged_program(program_id):
    """
    Start translation for a staged program.
    Creates tracks for each configured language and triggers cloud translation.
    """
    try:
        program = omega_db.get_program(program_id)
        if not program:
            return jsonify({"error": "Program not found"}), 404
        
        if program.get('ingest_mode') != 'staged':
            return jsonify({"error": "Program is not in staged mode"}), 400
        
        # Get configured languages from meta
        meta = program.get('meta', {})
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except:
                meta = {}
        
        languages = meta.get('configured_languages', [])
        if not languages:
            return jsonify({"error": "No languages configured. Call /configure first."}), 400
        
        # Get skeleton path from meta
        skeleton_path = meta.get('skeleton_path')
        if not skeleton_path or not Path(skeleton_path).exists():
            return jsonify({"error": "Skeleton file not found. Re-run transcription."}), 400
        
        created_tracks = []
        from gcs_jobs import GcsJobPaths, new_job_id, upload_json
        from workers import publisher
        
        for lang_code in languages:
            # Generate job ID for this language track
            original_stem = meta.get('original_stem', program.get('title', 'unknown'))
            job_id = new_job_id(f"{original_stem}_{lang_code}")
            
            # Get language name
            language_name = None
            try:
                from profiles import LANGUAGES
                language_name = (LANGUAGES.get(lang_code) or {}).get("name")
            except:
                pass
            
            # Create master script
            master_script_id = omega_db.ensure_master_script(
                program_id=program_id,
                language_code=lang_code,
                language_name=language_name,
            )
            
            # Create track
            track_meta = {
                **meta,
                "target_language": lang_code,
                "cloud_job_id": job_id,
            }
            
            track_id = omega_db.create_track(
                program_id=program_id,
                type='subtitle',
                language_code=lang_code,
                language_name=language_name,
                stage='TRANSCRIBED',
                status='Ready for Translation',
                job_id=job_id,
                master_script_id=master_script_id,
                meta=track_meta
            )
            
            # Upload to cloud for translation
            bucket_name = meta.get('cloud_bucket') or config.OMEGA_JOBS_BUCKET
            prefix = meta.get('cloud_prefix') or config.OMEGA_JOBS_PREFIX
            paths = GcsJobPaths(bucket=bucket_name, prefix=prefix, job_id=job_id)
            
            # Upload skeleton
            upload_json(Path(skeleton_path), paths.skeleton_blob)
            
            # Upload job.json
            job_payload = {
                "id": job_id,
                "file_stem": original_stem,
                "target_language": lang_code,
                "program_profile": meta.get("program_profile") or "standard",
                "glossary_terms": meta.get("glossary_terms") or [],
                "meta": track_meta,
                "created_at": publisher.iso_now()
            }
            temp_job_json = config.VAULT_DATA / f"{job_id}_cloud_job.json"
            with open(temp_job_json, "w") as f:
                json.dump(job_payload, f, indent=2)
            upload_json(temp_job_json, paths.job_blob)
            temp_job_json.unlink(missing_ok=True)
            
            created_tracks.append({
                "track_id": track_id,
                "language_code": lang_code,
                "job_id": job_id
            })
            
            logger.info(f"🚀 Started translation for {program_id} -> {lang_code} (job {job_id})")
        
        # Update program to no longer be staged
        omega_db.update_program(program_id, ingest_mode='auto')
        
        return jsonify({
            "success": True,
            "program_id": program_id,
            "tracks_created": len(created_tracks),
            "tracks": created_tracks,
            "message": f"Started translation for {len(created_tracks)} language(s)"
        })
        
    except Exception as e:
        logger.error(f"Failed to start staged program: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/api/v2/ministries', methods=['GET'])
def api_v2_list_ministries():
    """List all ministry profiles."""
    try:
        ministries = omega_db.list_ministry_profiles()
        return jsonify(ministries)
    except Exception as e:
        logger.error(f"Failed to list ministries: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/v2/ministries', methods=['POST'])
@admin_required
def api_v2_create_ministry():
    """Create a new ministry profile."""
    try:
        data = request.get_json()
        if not data.get('name') or not data.get('slug'):
            return jsonify({"error": "name and slug are required"}), 400

        profile_id = omega_db.create_ministry_profile(
            name=data['name'],
            slug=data['slug'],
            languages=data.get('languages', []),
            default_delivery_id=data.get('default_delivery_id'),
            terminology=data.get('terminology'),
            style=data.get('style'),
            watch_folder=data.get('watch_folder'),
            workflow=data.get('workflow', 'standard')
        )
        return jsonify({"id": profile_id, "ok": True})
    except Exception as e:
        logger.error(f"Failed to create ministry: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/v2/ministries/<profile_id>', methods=['PUT'])
@admin_required
def api_v2_update_ministry(profile_id):
    """Update a ministry profile."""
    try:
        data = request.get_json()
        omega_db.update_ministry_profile(profile_id, **data)
        return jsonify({"ok": True})
    except Exception as e:
        logger.error(f"Failed to update ministry: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/v2/ministries/<profile_id>', methods=['DELETE'])
@admin_required
def api_v2_delete_ministry(profile_id):
    """Delete a ministry profile."""
    try:
        omega_db.delete_ministry_profile(profile_id)
        return jsonify({"ok": True})
    except Exception as e:
        logger.error(f"Failed to delete ministry: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/v2/deliveries/profiles', methods=['GET'])
def api_v2_list_delivery_profiles():
    """List all delivery profiles."""
    try:
        profiles = omega_db.list_delivery_profiles()
        return jsonify(profiles)
    except Exception as e:
        logger.error(f"Failed to list delivery profiles: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/v2/deliveries/profiles', methods=['POST'])
@admin_required
def api_v2_create_delivery_profile():
    """Create a new delivery profile."""
    try:
        data = request.get_json()
        if not data.get('name') or not data.get('slug'):
            return jsonify({"error": "name and slug are required"}), 400

        profile_id = omega_db.create_delivery_profile(
            name=data['name'],
            slug=data['slug'],
            outputs=data.get('outputs', [])
        )
        return jsonify({"id": profile_id, "ok": True})
    except Exception as e:
        logger.error(f"Failed to create delivery profile: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/v2/deliveries/profiles/<profile_id>', methods=['PUT'])
@admin_required
def api_v2_update_delivery_profile(profile_id):
    """Update a delivery profile."""
    try:
        data = request.get_json()
        omega_db.update_delivery_profile(profile_id, **data)
        return jsonify({"ok": True})
    except Exception as e:
        logger.error(f"Failed to update delivery profile: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/v2/deliveries/profiles/<profile_id>', methods=['DELETE'])
@admin_required
def api_v2_delete_delivery_profile(profile_id):
    """Delete a delivery profile."""
    try:
        omega_db.delete_delivery_profile(profile_id)
        return jsonify({"ok": True})
    except Exception as e:
        logger.error(f"Failed to delete delivery profile: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/v2/dropzones', methods=['GET'])
def api_v2_list_dropzones():
    """List all drop zone recipes."""
    try:
        recipes = omega_db.list_dropzone_recipes()
        return jsonify(recipes)
    except Exception as e:
        logger.error(f"Failed to list drop zone recipes: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/v2/dropzones', methods=['POST'])
@admin_required
def api_v2_create_dropzone():
    """Create a new drop zone recipe."""
    try:
        data = request.get_json()
        if not data.get('name') or not data.get('folder_name') or not data.get('ministry_id'):
            return jsonify({"error": "name, folder_name, and ministry_id are required"}), 400

        recipe_id = omega_db.create_dropzone_recipe(
            name=data['name'],
            folder_name=data['folder_name'],
            ministry_id=data['ministry_id'],
            languages=data.get('languages', []),
            delivery_id=data.get('delivery_id')
        )
        return jsonify({"id": recipe_id, "ok": True})
    except Exception as e:
        logger.error(f"Failed to create drop zone recipe: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/v2/dropzones/<recipe_id>', methods=['PUT'])
@admin_required
def api_v2_update_dropzone(recipe_id):
    """Update a drop zone recipe."""
    try:
        data = request.get_json()
        omega_db.update_dropzone_recipe(recipe_id, **data)
        return jsonify({"ok": True})
    except Exception as e:
        logger.error(f"Failed to update drop zone recipe: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/v2/dropzones/<recipe_id>', methods=['DELETE'])
@admin_required
def api_v2_delete_dropzone(recipe_id):
    """Delete a drop zone recipe."""
    try:
        omega_db.delete_dropzone_recipe(recipe_id)
        return jsonify({"ok": True})
    except Exception as e:
        logger.error(f"Failed to delete drop zone recipe: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/v2/programs/<program_id>/tracks', methods=['GET'])
def api_v2_get_program_tracks(program_id):
    """Get all tracks for a program."""
    tracks = omega_db.get_tracks_for_program(program_id)
    tracks = _filter_tracks_by_station(tracks)
    _hydrate_track_job_ids(tracks)
    _hydrate_track_file_paths(tracks)
    return jsonify(tracks)


@app.route('/api/v2/programs/<program_id>/tracks', methods=['POST'])
@admin_required
def api_v2_add_track(program_id):
    """Add a new track (subtitle or dub) to a program.

    Uses the bulletproof fork_language() function for subtitle tracks.
    For dub tracks, requires a completed subtitle track to base on.
    """
    if _station_scope_enabled():
        program = omega_db.get_program(program_id)
        if not program:
            return jsonify({"error": "Program not found"}), 404
        meta = _normalize_meta(program.get("meta"))
        if not _station_match(meta):
            return jsonify({"error": "Program not found"}), 404

    data = request.json

    track_type = str(data.get('type', 'subtitle') or 'subtitle').strip().lower() or 'subtitle'
    language_code = str(data.get('language_code', 'is') or 'is').strip().lower() or 'is'
    voice_id = data.get('voice_id')  # For dub tracks
    depends_on = data.get('depends_on')  # For dub tracks, the subtitle track to use

    # For subtitle tracks, use the bulletproof fork_language() function
    if track_type == 'subtitle':
        try:
            result = omega_db.fork_language(program_id, language_code, track_type='subtitle')
            return jsonify({
                "success": True,
                "track_id": result["track_id"],
                "job_id": result["job_id"],
                "message": f"Created {language_code} subtitle track, ready for translation"
            })
        except ValueError as e:
            error_msg = str(e)
            if "not found" in error_msg.lower():
                return jsonify({"error": error_msg}), 404
            elif "already exists" in error_msg.lower():
                return jsonify({"error": error_msg}), 409
            elif "skeleton" in error_msg.lower():
                return jsonify({"error": error_msg}), 400
            else:
                return jsonify({"error": error_msg}), 400
        except Exception as e:
            logger.error(f"Failed to fork language: {e}")
            return jsonify({"error": f"Failed to create track: {e}"}), 500

    # For dub tracks, handle separately (requires completed subtitle)
    program = omega_db.get_program(program_id)
    if not program:
        return jsonify({"error": "Program not found"}), 404

    existing_tracks = omega_db.get_tracks_for_program(program_id)

    # Check for duplicate dub track
    duplicate = next(
        (t for t in existing_tracks
         if str(t.get('type') or '').lower() == 'dub'
         and str(t.get('language_code') or '').lower() == language_code),
        None,
    )
    if duplicate:
        return jsonify({
            "error": f"{language_code} dub track already exists",
            "track_id": duplicate.get("id"),
        }), 409

    # Find a completed subtitle track to base dub on
    if not depends_on:
        subtitle_track = next(
            (t for t in existing_tracks
             if str(t.get('type') or '').lower() == 'subtitle'
             and str(t.get('language_code') or '').lower() == language_code
             and t.get('stage') in ('COMPLETE', 'COMPLETED', 'DELIVERED')),
            None
        )
        if subtitle_track:
            depends_on = subtitle_track['id']
        else:
            return jsonify({
                "error": f"No completed subtitle track in {language_code} to base dubbing on"
            }), 400

    # Create dub track (simpler - no GCS upload needed for now)
    from gcs_jobs import new_job_id
    original_stem = Path(program.get('original_filename', '')).stem or program_id[:8]
    new_track_job_id = new_job_id(f"{original_stem}_{language_code}_dub")

    track_id = omega_db.create_track(
        program_id=program_id,
        type='dub',
        language_code=language_code,
        stage='PENDING_DUB',
        status='Waiting for dubbing',
        job_id=new_track_job_id,
        voice_id=voice_id,
        depends_on=depends_on,
        meta={
            "original_filename": program.get("original_filename"),
            "depends_on": depends_on,
            "voice_id": voice_id,
            "created_at": datetime.now().isoformat(),
        }
    )

    logger.info(f"Created dub track {track_id} with job_id={new_track_job_id}")

    return jsonify({
        "success": True,
        "track_id": track_id,
        "job_id": new_track_job_id,
        "message": f"Created {language_code} dub track, pending dubbing"
    })


@app.route('/api/v2/fork-language', methods=['POST'])
@admin_required
def api_v2_fork_language():
    """Add a new language track to an existing program.

    This is the RECOMMENDED endpoint for adding languages.
    Single, simple, bulletproof.

    Request body:
        {
            "program_id": "uuid-here",
            "language_code": "nl"
        }

    Response:
        {
            "success": true,
            "track_id": "uuid",
            "job_id": "stem_nl-timestamp",
            "status": "translating"
        }
    """
    data = request.json or {}

    program_id = data.get('program_id')
    language_code = data.get('language_code')

    if not program_id:
        return jsonify({"error": "program_id is required"}), 400
    if not language_code:
        return jsonify({"error": "language_code is required"}), 400

    try:
        result = omega_db.fork_language(program_id, language_code)
        return jsonify({
            "success": True,
            **result
        })
    except ValueError as e:
        error_msg = str(e)
        if "not found" in error_msg.lower():
            return jsonify({"error": error_msg}), 404
        elif "already exists" in error_msg.lower():
            return jsonify({"error": error_msg}), 409
        elif "skeleton" in error_msg.lower():
            return jsonify({"error": error_msg}), 400
        else:
            return jsonify({"error": error_msg}), 400
    except Exception as e:
        logger.error(f"fork_language failed: {e}")
        return jsonify({"error": f"Internal error: {e}"}), 500


@app.route('/api/v2/tracks/<track_id>', methods=['GET'])
def api_v2_get_track(track_id):
    """Get a single track with details."""
    track = omega_db.get_track(track_id)
    if not track or (_station_scope_enabled() and not _track_allowed(track)):
        return jsonify({"error": "Track not found"}), 404

    if not track.get("job_id") and track.get("depends_on"):
        source_track = omega_db.get_track(track["depends_on"])
        if source_track and source_track.get("job_id"):
            track["job_id"] = source_track["job_id"]
    
    # Include program info
    program = omega_db.get_program(track['program_id'])
    track['program'] = program

    # Include deliveries
    track['deliveries'] = omega_db.get_deliveries_for_track(track_id)

    # Compute file paths based on job_id
    _hydrate_track_file_paths([track])

    # Auto-correct stuck BURNING status
    job_id = track.get('job_id')
    if job_id and track.get('stage') == 'BURNING':
        video_path = config.VIDEO_DIR / f"{job_id}_SUBBED.mp4"
        if video_path.exists():
            logger.info(f"Auto-correcting stuck BURNING status for {job_id}")
            omega_db.update_track(track_id, stage="COMPLETED", status="Done", progress=100.0)
            track['stage'] = "COMPLETED"
            track['status'] = "Done"
            track['progress'] = 100.0

    return jsonify(track)


@app.route('/api/v2/tracks/<track_id>', methods=['PUT'])
@admin_required
def api_v2_update_track(track_id):
    """Update track fields."""
    data = request.json
    
    # Filter allowed fields
    allowed = {'stage', 'status', 'progress', 'rating', 'voice_id', 'output_path', 'meta'}
    updates = {k: v for k, v in data.items() if k in allowed}
    
    if omega_db.update_track(track_id, **updates):
        return jsonify({"success": True})
    return jsonify({"error": "Track not found"}), 404


@app.route('/api/v2/tracks/active', methods=['GET'])
def api_v2_get_active_tracks():
    """Get all tracks that are currently in progress."""
    limit = int(request.args.get('limit', 50))
    tracks = omega_db.get_active_tracks(limit=limit)
    tracks = _filter_tracks_by_station(tracks)
    _hydrate_track_job_ids(tracks)
    _hydrate_track_file_paths(tracks)
    return jsonify(tracks)


@app.route('/api/v2/jobs/<job_id>/logs', methods=['GET'])
@admin_required
def api_v2_get_job_logs(job_id):
    """Tail the latest log lines for a job."""
    raw_lines = request.args.get("lines", 100)
    try:
        line_count = int(raw_lines)
    except Exception:
        line_count = 100

    log_lines = tail_job_log(job_id, lines=line_count)
    return jsonify({"job_id": job_id, "lines": log_lines})


@app.route('/api/v2/tracks/<track_id>/reveal', methods=['POST'])
@admin_required
def api_v2_reveal_track_file(track_id):
    """Open the track's output file in Finder (macOS)."""
    data = request.json or {}
    file_type = data.get('type', 'video')  # 'video' or 'srt'
    
    track = omega_db.get_track(track_id)
    if not track or (_station_scope_enabled() and not _track_allowed(track)):
        return jsonify({"error": "Track not found"}), 404
    
    job_id = track.get('job_id')
    if not job_id:
        return jsonify({"error": "No job_id for track"}), 400
    
    if file_type == 'srt':
        # SRT may be DONE_{job_id}.srt or {job_id}.srt
        file_path = config.SRT_DIR / f"DONE_{job_id}.srt"
        if not file_path.exists():
            file_path = config.SRT_DIR / f"{job_id}.srt"
    else:
        file_path = config.VIDEO_DIR / f"{job_id}_SUBBED.mp4"

    if not file_path.exists():
        return jsonify({"error": f"File not found: {file_path.name}"}), 404

    # macOS: reveal in Finder
    import subprocess
    subprocess.run(['open', '-R', str(file_path)], check=False)
    
    return jsonify({"success": True, "path": str(file_path)})


@app.route('/api/v2/tracks/<track_id>/deliver', methods=['POST'])
@admin_required
def api_v2_deliver_track(track_id):
    """Record that a track was delivered."""
    data = request.json
    
    track = omega_db.get_track(track_id)
    if not track or (_station_scope_enabled() and not _track_allowed(track)):
        return jsonify({"error": "Track not found"}), 404
    
    delivery_id = omega_db.record_track_delivery(
        track_id=track_id,
        destination=data.get('destination', 'Unknown'),
        recipient=data.get('recipient'),
        notes=data.get('notes')
    )
    
    return jsonify({"success": True, "delivery_id": delivery_id})


@app.route('/api/v2/deliveries', methods=['GET'])
def api_v2_get_deliveries():
    """Get recent deliveries."""
    days = int(request.args.get('days', 7))
    limit = int(request.args.get('limit', 100))
    
    deliveries = omega_db.get_recent_deliveries(days=days, limit=limit)
    if _station_scope_enabled():
        deliveries = [d for d in deliveries if _track_allowed(omega_db.get_track(d.get('track_id')) or {})]
    return jsonify(deliveries)


@app.route('/api/v2/thumbnails/<program_id>')
def api_v2_thumbnail(program_id):
    """Serve program thumbnail."""
    program = omega_db.get_program(program_id)
    if not program:
        return jsonify({"error": "Program not found"}), 404
    
    thumbnail_path = program.get('thumbnail_path')
    if thumbnail_path and Path(thumbnail_path).exists():
        return send_file(thumbnail_path, mimetype='image/jpeg')
    
    # Return placeholder
    return jsonify({"error": "No thumbnail"}), 404


# =============================================================================
# API V2: Track Actions (for Program Detail redesign)
# =============================================================================

@app.route('/api/v2/tracks/<track_id>/send-to-review', methods=['POST'])
@admin_required
def api_v2_send_to_review(track_id):
    """Send a track to review stage."""
    track = omega_db.get_track(track_id)
    if not track or (_station_scope_enabled() and not _track_allowed(track)):
        return jsonify({"error": "Track not found"}), 404
    
    # Update track stage
    omega_db.update_track(track_id, stage='AWAITING_REVIEW', status='Sent for review')
    
    # Also update the linked job if exists
    if track.get('job_id'):
        omega_db.update_job_via_track(track['job_id'], stage='AWAITING_REVIEW', status='Sent for review')
    
    return jsonify({"success": True, "stage": "AWAITING_REVIEW"})


@app.route('/api/v2/tracks/<track_id>/approve', methods=['POST'])
@admin_required
def api_v2_approve_track(track_id):
    """Approve a track and move to next stage (BURNING for subtitles, DUBBING for dubs)."""
    track = omega_db.get_track(track_id)
    if not track or (_station_scope_enabled() and not _track_allowed(track)):
        return jsonify({"error": "Track not found"}), 404
    
    # Determine next stage based on track type
    if track['type'] == 'dub':
        next_stage = 'DUBBING'
        next_status = 'Generating audio'
    else:
        next_stage = 'BURNING'
        next_status = 'Approved - queued for burn'
    
    # Update track
    omega_db.update_track(track_id, stage=next_stage, status=next_status)
    
    # Also update the linked job if exists
    if track.get('job_id'):
        job_id = track['job_id']
        # Clear halted and set burn_approved to allow re-burn
        omega_db.update_job_via_track(
            job_id, 
            stage=next_stage, 
            status=next_status, 
            progress=90.0,
            meta={
                "halted": False,
                "burn_approved": True,
                "last_error": "",
                "failed_at": "",
            }
        )
        
        # RESET FILES FOR RE-BURN
        if next_stage == 'BURNING':
            try:
                # 1. Reset SRT (DONE_stem.srt -> stem.srt)
                stem = job_id
                srt_path = config.SRT_DIR / f"{stem}.srt"
                done_srt_path = config.SRT_DIR / f"DONE_{stem}.srt"
                if not srt_path.exists() and done_srt_path.exists():
                    shutil.move(str(done_srt_path), str(srt_path))
                    
                # 2. Backup existing video to force re-burn
                 # If video exists, manager thinks it's done.
                video_path = config.VIDEO_DIR / f"{stem}_SUBBED.mp4"
                if video_path.exists():
                     backup_path = config.VIDEO_DIR / f"{stem}_SUBBED_BACKUP_{int(time.time())}.mp4"
                     shutil.move(str(video_path), str(backup_path))
            except Exception as e:
                logger.error(f"Failed to reset files for re-burn {job_id}: {e}")
    
    return jsonify({"success": True, "stage": next_stage})


@app.route('/api/v2/tracks/<track_id>/override', methods=['POST'])
@admin_required
def api_v2_override_track(track_id):
    """Apply an output-only override to a track (does not modify master script)."""
    data = request.json or {}
    reason = data.get("reason") or data.get("override_reason") or "Output-only fix"
    author = data.get("author") or data.get("override_author") or "operator"

    track = omega_db.get_track(track_id)
    if not track or (_station_scope_enabled() and not _track_allowed(track)):
        return jsonify({"error": "Track not found"}), 404

    new_version = _bump_output_version(track.get("output_version") or "1.0")
    omega_db.update_track(
        track_id,
        output_version=new_version,
        output_override=True,
        override_reason=reason,
        override_author=author,
        override_timestamp=datetime.now().isoformat(),
    )

    return jsonify({
        "success": True,
        "output_version": new_version,
        "override_reason": reason,
        "override_author": author,
        "message": "Override applied",
    })


@app.route('/api/v2/tracks/<track_id>/lock', methods=['POST'])
@admin_required
def api_v2_lock_track(track_id):
    """Lock an output track (and optionally its master script)."""
    data = request.json or {}
    lock_master = bool(data.get("lock_master", False))
    locked_by = data.get("locked_by") or "operator"
    now = datetime.now().isoformat()

    track = omega_db.get_track(track_id)
    if not track or (_station_scope_enabled() and not _track_allowed(track)):
        return jsonify({"error": "Track not found"}), 404

    omega_db.update_track(track_id, locked_at=now, locked_by=locked_by)

    if lock_master:
        master_script_id = track.get("master_script_id")
        if master_script_id:
            master = omega_db.get_master_script(master_script_id)
            if master and not master.get("locked_at"):
                omega_db.update_master_script(
                    master_script_id,
                    state="locked",
                    locked_at=now,
                    locked_by=locked_by,
                )

    return jsonify({"success": True, "locked_at": now, "locked_by": locked_by})


@app.route('/api/v2/tracks/<track_id>/unlock', methods=['POST'])
@admin_required
def api_v2_unlock_track(track_id):
    """Unlock an output track."""
    data = request.json or {}
    unlocked_by = data.get("unlocked_by") or "operator"

    track = omega_db.get_track(track_id)
    if not track or (_station_scope_enabled() and not _track_allowed(track)):
        return jsonify({"error": "Track not found"}), 404

    omega_db.update_track(track_id, locked_at=None, locked_by=None)
    return jsonify({"success": True, "unlocked_by": unlocked_by})


@app.route('/api/v2/master-scripts/<master_script_id>', methods=['GET'])
def api_v2_get_master_script(master_script_id):
    """Get a master script."""
    master = omega_db.get_master_script(master_script_id)
    if not master:
        return jsonify({"error": "Master script not found"}), 404

    payload = {
        "id": master.get("id"),
        "program_id": master.get("program_id"),
        "language_code": master.get("language_code"),
        "language_name": master.get("language_name"),
        "state": master.get("state"),
        "version": master.get("version"),
        "locked_at": master.get("locked_at"),
        "locked_by": master.get("locked_by"),
        "created_at": master.get("created_at"),
        "updated_at": master.get("updated_at"),
    }
    return jsonify(payload)


@app.route('/api/v2/master-scripts/<master_script_id>/lock', methods=['POST'])
@admin_required
def api_v2_lock_master_script(master_script_id):
    """Lock a master script."""
    data = request.json or {}
    locked_by = data.get("locked_by") or "operator"
    now = datetime.now().isoformat()

    master = omega_db.get_master_script(master_script_id)
    if not master:
        return jsonify({"error": "Master script not found"}), 404

    if not master.get("locked_at"):
        omega_db.update_master_script(
            master_script_id,
            state="locked",
            locked_at=now,
            locked_by=locked_by,
        )

    return jsonify({"success": True, "locked_at": now, "locked_by": locked_by})


@app.route('/api/v2/master-scripts/<master_script_id>/unlock', methods=['POST'])
@admin_required
def api_v2_unlock_master_script(master_script_id):
    """Unlock a master script and optionally bump version."""
    data = request.json or {}
    unlocked_by = data.get("unlocked_by") or "operator"
    bump_version = bool(data.get("bump_version", True))

    master = omega_db.get_master_script(master_script_id)
    if not master:
        return jsonify({"error": "Master script not found"}), 404

    new_version = master.get("version", 1)
    if bump_version:
        new_version = int(new_version) + 1

    omega_db.update_master_script(
        master_script_id,
        state="draft",
        version=new_version,
        locked_at=None,
        locked_by=None,
    )

    return jsonify({"success": True, "version": new_version, "unlocked_by": unlocked_by})


@app.route('/api/v2/tracks/<track_id>/send-review', methods=['POST'])
@admin_required
def api_v2_send_for_review(track_id):
    """
    Send a track for remote review.
    Generates proxy, uploads to Bunny Stream, sends email.
    """
    from workers import remote_review
    
    data = request.json or {}
    reviewer_email = data.get("email")
    
    if not reviewer_email:
        return jsonify({"error": "Email address required"}), 400
    
    # Get track and job info
    track = omega_db.get_track(track_id)
    if not track or (_station_scope_enabled() and not _track_allowed(track)):
        return jsonify({"error": "Track not found"}), 404
    
    job_id = track.get("job_id")
    if not job_id:
        return jsonify({"error": "No linked job for this track"}), 400
    
    # Check if already sent
    job = omega_db.get_job_via_track(job_id)
    meta = job.get("meta", {}) if job else {}
    if meta.get("bunny_video_id"):
        # Already uploaded, just resend email
        review_url = review_notifier.build_review_url(job_id)
        review_notifier.send_review_notification(
            job_id=job_id,
            program_name=meta.get("original_filename", job_id),
            target_language=job.get("target_language", "Icelandic"),
            reviewer_email=reviewer_email
        )
        return jsonify({
            "success": True,
            "message": "Review link resent",
            "review_url": review_url,
            "bunny_embed_url": meta.get("bunny_embed_url")
        })
    
    # Queue the remote review job (runs in background)
    try:
        executor.submit(remote_review.send_for_remote_review, job_id, reviewer_email)
        logger.info(f"Queued remote review for {job_id} to {reviewer_email}")
        return jsonify({
            "success": True,
            "message": "Review being prepared. Email will be sent when ready."
        })
    except Exception as e:
        logger.error(f"Failed to queue remote review: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/v2/tracks/<track_id>/review-status', methods=['GET'])
@admin_required
def api_v2_get_review_status(track_id):
    """Get the remote review status for a track."""
    from workers import remote_review
    
    track = omega_db.get_track(track_id)
    if not track or (_station_scope_enabled() and not _track_allowed(track)):
        return jsonify({"error": "Track not found"}), 404
    
    job_id = track.get("job_id")
    if not job_id:
        return jsonify({"error": "No linked job"}), 400
    
    status = remote_review.get_review_status(job_id)
    return jsonify(status)

@app.route('/api/v2/tracks/<track_id>/open-editor', methods=['GET'])
def api_v2_open_editor(track_id):
    """Get the editor URL for a track."""
    track = omega_db.get_track(track_id)
    if not track or (_station_scope_enabled() and not _track_allowed(track)):
        return jsonify({"error": "Track not found"}), 404
    
    # The editor uses job_id
    job_id = track.get('job_id')
    if not job_id:
        return jsonify({"error": "No linked job for this track"}), 400
    
    editor_url = f"/editor/{job_id}"
    return jsonify({"editor_url": editor_url, "job_id": job_id})


@app.route('/api/v2/tracks/<track_id>/start-dub', methods=['POST'])
@admin_required
def api_v2_start_dub(track_id):
    """Start dubbing for a dub track."""
    from workers.dubber import Dubber
    
    track = omega_db.get_track(track_id)
    if not track or (_station_scope_enabled() and not _track_allowed(track)):
        return jsonify({"error": "Track not found"}), 404
    
    if track['type'] != 'dub':
        return jsonify({"error": "Track is not a dub type"}), 400
    
    voice_id = track.get('voice_id', 'alloy')
    
    # Get the source subtitle track (depends_on)
    source_track_id = track.get('depends_on')
    if not source_track_id:
        # Try to find a completed subtitle track in same language
        program_id = track['program_id']
        all_tracks = omega_db.get_tracks_for_program(program_id)
        subtitle_tracks = [
            t for t in all_tracks
            if t['type'] == 'subtitle' and t['stage'] in ('COMPLETE', 'COMPLETED', 'DELIVERED')
        ]
        if subtitle_tracks:
            source_track_id = subtitle_tracks[0]['id']
    
    if not source_track_id:
        return jsonify({"error": "No source subtitle track available"}), 400
    
    source_track = omega_db.get_track(source_track_id)
    if not source_track or not source_track.get('job_id'):
        return jsonify({"error": "Source track missing job data"}), 400
    
    job_id = source_track['job_id']
    job_dir = (Path("jobs") / job_id).resolve()

    if not track.get("job_id"):
        omega_db.update_track(track_id, job_id=job_id)
        track["job_id"] = job_id
    
    def run_dubbing():
        try:
            logger.info(f"Starting dubbing for track {track_id} with voice {voice_id}")
            omega_db.update_track(track_id, stage='DUBBING', status=f'Generating audio ({voice_id})', progress=10)
            
            dubber = Dubber(job_id, job_dir)
            # TODO: Pass voice_id to dubber when we enhance it
            dubber.run()

            master_version = _resolve_master_version(
                track.get("master_script_id"),
                program_id=track.get("program_id"),
                language_code=track.get("language_code"),
            )
            omega_db.update_track(
                track_id,
                stage='COMPLETE',
                status='Dubbing complete',
                progress=100,
                output_version=_format_output_version(master_version),
                output_override=False,
                override_reason=None,
                override_author=None,
                override_timestamp=None,
                pending_resync=False,
            )
            logger.info(f"Dubbing complete for track {track_id}")
        except Exception as e:
            logger.error(f"Dubbing failed for track {track_id}: {e}")
            omega_db.update_track(track_id, stage='FAILED', status=f'Dubbing failed: {str(e)[:50]}')
    
    thread = threading.Thread(target=run_dubbing)
    thread.start()
    
    return jsonify({"success": True, "message": "Dubbing started", "track_id": track_id})




@app.route('/api/v2/tracks/<track_id>/reject', methods=['POST'])
@admin_required
def api_v2_reject_track(track_id):
    """Reject a track and send back for rework."""
    data = request.json or {}
    reason = data.get('reason', 'Needs rework')
    
    track = omega_db.get_track(track_id)
    if not track or (_station_scope_enabled() and not _track_allowed(track)):
        return jsonify({"error": "Track not found"}), 404
    
    # Send back to translation stage
    omega_db.update_track(track_id, stage='TRANSLATING', status=f'Rejected: {reason}')
    
    if track.get('job_id'):
        omega_db.update_job_via_track(track['job_id'], stage='TRANSLATING', status=f'Rejected: {reason}')
    
    return jsonify({"success": True, "stage": "TRANSLATING"})


@app.route('/api/v2/tracks/<track_id>/retry', methods=['POST'])
@admin_required
def api_v2_retry_track(track_id):
    """Retry a failed track by resetting to the best available checkpoint.

    Automatically determines the furthest valid stage based on existing files:
    - Has SRT? → Reset to FINALIZING (re-burn)
    - Has approved.json? → Reset to REVIEWED (re-finalize)
    - Has skeleton? → Reset to TRANSCRIBED (re-translate)
    - Nothing? → Reset to QUEUED (start over)
    """
    track = omega_db.get_track(track_id)
    if not track or (_station_scope_enabled() and not _track_allowed(track)):
        return jsonify({"error": "Track not found"}), 404

    job_id = track.get("job_id")
    if not job_id:
        return jsonify({"error": "Track has no job_id"}), 400

    now = datetime.now().isoformat()
    track_meta = track.get("meta") or {}
    retry_count = int(track_meta.get("retry_count") or 0)

    # Allow force retry via query param
    force = request.args.get("force", "").lower() in ("1", "true", "yes")
    if retry_count >= 3 and not force:
        return jsonify({
            "error": "Retry limit reached (3). Use ?force=1 to override.",
            "retry_count": retry_count
        }), 409

    # Determine best checkpoint based on existing files
    srt_path = config.SRT_DIR / f"{job_id}.srt"
    approved_path = config.TRANSLATED_DONE_DIR / f"{job_id}_APPROVED.json"
    skeleton_path = config.find_skeleton(job_id)

    if srt_path.exists():
        reset_stage = "FINALIZING"
        reset_status = "Re-burning video"
        progress = 80
    elif approved_path.exists():
        reset_stage = "REVIEWED"
        reset_status = "Re-finalizing subtitles"
        progress = 70
    elif skeleton_path:
        reset_stage = "TRANSCRIBED"
        reset_status = "Re-translating"
        progress = 30
    else:
        reset_stage = "QUEUED"
        reset_status = "Starting over"
        progress = 0

    # Update track
    omega_db.update_track(
        track_id,
        stage=reset_stage,
        status=reset_status,
        progress=progress,
        meta={
            **track_meta,
            "last_error": "",
            "failed_at": "",
            "retry_requested_at": now,
            "retry_count": retry_count + 1,
        },
    )

    logger.info(f"Retry track {track_id}: reset to {reset_stage}")

    return jsonify({
        "success": True,
        "track_id": track_id,
        "job_id": job_id,
        "reset_stage": reset_stage,
        "status": reset_status,
        "retry_count": retry_count + 1
    })


@app.route('/api/v2/tracks/<track_id>/finalize', methods=['POST'])
@admin_required
def api_v2_finalize_track(track_id):
    """Trigger finalization (SRT/ASS generation) for a track.

    Requires approved.json to exist. Generates subtitle files from translation.
    """
    from workers import finalizer

    track = omega_db.get_track(track_id)
    if not track or (_station_scope_enabled() and not _track_allowed(track)):
        return jsonify({"error": "Track not found"}), 404

    job_id = track.get("job_id")
    if not job_id:
        return jsonify({"error": "Track has no job_id"}), 400

    # Check for approved translation
    approved_path = config.TRANSLATED_DONE_DIR / f"{job_id}_APPROVED.json"
    if not approved_path.exists():
        # Try GCS download
        approved_path = config.VAULT_DATA / f"{job_id}_APPROVED.json"
    if not approved_path.exists():
        return jsonify({"error": "No approved translation found. Complete translation first."}), 400

    # Get language from track
    language_code = track.get("language_code", "is")

    omega_db.update_track(
        track_id,
        stage="FINALIZING",
        status="Generating subtitles",
        progress=75,
    )

    try:
        # Run finalizer
        srt_path = finalizer.finalize(str(approved_path), language_code)

        omega_db.update_track(
            track_id,
            stage="FINALIZED",
            status="Ready to burn",
            progress=85,
            meta={"srt_path": str(srt_path), "finalized_at": datetime.now().isoformat()},
        )

        return jsonify({
            "success": True,
            "track_id": track_id,
            "srt_path": str(srt_path),
            "status": "Ready to burn"
        })

    except Exception as e:
        logger.error(f"Finalize failed for {track_id}: {e}")
        omega_db.update_track(
            track_id,
            stage="REVIEWED",
            status=f"Finalize failed: {e}",
            meta={"finalize_error": str(e), "failed_at": datetime.now().isoformat()},
        )
        return jsonify({"error": f"Finalization failed: {e}"}), 500


@app.route('/api/v2/tracks/<track_id>/burn', methods=['POST'])
@admin_required
def api_v2_burn_track(track_id):
    """Trigger video burning for a track.

    Requires SRT to exist. Burns subtitles into video file.
    """
    track = omega_db.get_track(track_id)
    if not track or (_station_scope_enabled() and not _track_allowed(track)):
        return jsonify({"error": "Track not found"}), 404

    job_id = track.get("job_id")
    if not job_id:
        return jsonify({"error": "Track has no job_id"}), 400

    # Check for SRT
    srt_path = config.SRT_DIR / f"{job_id}.srt"
    if not srt_path.exists():
        return jsonify({"error": "No SRT found. Run finalize first."}), 400

    # Check if already burning
    with _force_burn_lock:
        if job_id in _force_burn_inflight:
            return jsonify({"error": "Burn already in progress"}), 409
        _force_burn_inflight.add(job_id)

    omega_db.update_track(
        track_id,
        stage="BURNING",
        status="Burning subtitles into video",
        progress=90,
        meta={"burn_started_at": datetime.now().isoformat()},
    )

    # Start burn in background thread
    t = threading.Thread(
        target=_run_force_burn,
        args=(job_id,),
        name=f"burn_{job_id}",
        daemon=True
    )
    t.start()

    return jsonify({
        "success": True,
        "track_id": track_id,
        "job_id": job_id,
        "status": "Burning started"
    })


@app.route('/api/v2/pipeline/stats', methods=['GET'])
def api_v2_pipeline_stats():
    """Get pipeline statistics for dashboard headers."""
    # Get all tracks to compute stats
    all_tracks = omega_db.get_active_tracks(limit=1000)
    all_tracks = _filter_tracks_by_station(all_tracks)
    
    # Count by stage
    stage_counts = {}
    blocked_count = 0
    active_count = 0
    complete_stages = {'COMPLETE', 'COMPLETED', 'DELIVERED'}
    blocked_stages = {'AWAITING_REVIEW', 'AWAITING_APPROVAL', 'FAILED', 'DEAD'}
    
    for track in all_tracks:
        stage = track.get('stage', 'UNKNOWN')
        stage_counts[stage] = stage_counts.get(stage, 0) + 1
        
        # Blocked = stuck in review or failed
        if stage in blocked_stages:
            blocked_count += 1
        elif stage not in complete_stages:
            active_count += 1
    
    # Get recent completions for throughput
    completed_today = sum(1 for t in all_tracks if t.get('stage') in complete_stages)
    
    # Needs attention = awaiting review + failed
    needs_attention = (
        stage_counts.get('AWAITING_REVIEW', 0)
        + stage_counts.get('AWAITING_APPROVAL', 0)
        + stage_counts.get('FAILED', 0)
        + stage_counts.get('DEAD', 0)
    )
    
    return jsonify({
        "total_active": len(all_tracks),
        "blocked": blocked_count,
        "active": active_count,
        "needs_attention": needs_attention,
        "stage_counts": stage_counts,
        "stages": [
            {"stage": stage, "count": count} for stage, count in stage_counts.items()
        ]
    })


@app.route('/api/v2/languages', methods=['GET'])
def api_v2_languages():
    """Get available languages for track creation."""
    from profiles import LANGUAGES, LANGUAGE_POLICIES
    
    languages = []
    for code, lang in LANGUAGES.items():
        policy = LANGUAGE_POLICIES.get(code, {"mode": "sub", "voice": "alloy"})
        languages.append({
            "code": code,
            "name": lang["name"],
            "default_mode": policy["mode"],  # 'sub' or 'dub'
            "default_voice": policy["voice"],
        })
    
    # Sort by name
    languages.sort(key=lambda x: x["name"])
    
    return jsonify({"languages": languages})


@app.route('/api/v2/voices', methods=['GET'])
def api_v2_voices():
    """Get available voices for dubbing."""
    # OpenAI TTS voices
    voices = [
        {"id": "alloy", "name": "Alloy", "description": "Neutral, balanced voice"},
        {"id": "echo", "name": "Echo", "description": "Male, clear and articulate"},
        {"id": "fable", "name": "Fable", "description": "Warm, expressive British accent"},
        {"id": "onyx", "name": "Onyx", "description": "Deep, authoritative male voice"},
        {"id": "nova", "name": "Nova", "description": "Friendly, conversational female"},
        {"id": "shimmer", "name": "Shimmer", "description": "Soft, gentle female voice"},
    ]
    
    return jsonify({"voices": voices})


# =============================================================================
# App Settings API
# =============================================================================

@app.route('/api/v2/settings', methods=['GET'])
def api_v2_get_settings():
    """Return the full app settings object."""
    try:
        settings = omega_db.get_app_settings()
        return jsonify(settings)
    except Exception as e:
        logger.error(f"Failed to get settings: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/v2/settings', methods=['PATCH'])
def api_v2_update_settings():
    """Update one or more app settings. Accepts a partial JSON object."""
    try:
        data = request.get_json()
        if not data or not isinstance(data, dict):
            return jsonify({"error": "Request body must be a JSON object"}), 400

        updated = omega_db.update_app_settings(data)
        return jsonify(updated)
    except Exception as e:
        logger.error(f"Failed to update settings: {e}")
        return jsonify({"error": str(e)}), 500


# =============================================================================
# Server-Sent Events (SSE) for Real-Time Updates
# =============================================================================

import queue
from typing import Generator

# Global event queue for SSE connections
_sse_connections: list[queue.Queue] = []
_sse_lock = threading.Lock()
_last_known_jobs: dict = {}  # Cache to detect changes


def _broadcast_event(event_type: str, data: dict):
    """Broadcast an event to all connected SSE clients."""
    message = json.dumps({"type": event_type, "data": data, "timestamp": datetime.now().isoformat()})
    with _sse_lock:
        dead_queues = []
        for q in _sse_connections:
            try:
                q.put_nowait(message)
            except queue.Full:
                dead_queues.append(q)
        for q in dead_queues:
            _sse_connections.remove(q)


def _check_for_changes():
    """Check for job changes and broadcast events."""
    global _last_known_jobs
    
    try:
        current_jobs = {job["file_stem"]: job for job in get_all_jobs()}
        
        # Detect new jobs
        for stem, job in current_jobs.items():
            if stem not in _last_known_jobs:
                _broadcast_event("job.created", job)
            else:
                # Check if job changed (compare updated_at and key fields)
                old_job = _last_known_jobs[stem]
                if (job.get("updated_at") != old_job.get("updated_at") or
                    job.get("stage") != old_job.get("stage") or
                    job.get("status") != old_job.get("status") or
                    job.get("progress") != old_job.get("progress")):
                    _broadcast_event("job.updated", job)
        
        # Detect deleted jobs
        for stem in _last_known_jobs:
            if stem not in current_jobs:
                _broadcast_event("job.deleted", {"file_stem": stem})
        
        _last_known_jobs = current_jobs
        
    except Exception as e:
        logger.error(f"SSE change detection error: {e}")


def _sse_monitor_loop():
    """Background thread to monitor for changes and broadcast events."""
    global _last_known_jobs
    
    # Initial load
    try:
        _last_known_jobs = {job["file_stem"]: job for job in get_all_jobs()}
    except Exception:
        pass
    
    while True:
        try:
            if _sse_connections:  # Only check if clients connected
                _check_for_changes()
                
                # Also broadcast health updates periodically
                health_data = {
                    "storage_ready": config.critical_paths_ready(),
                    "disk_free_gb": _disk_free_gb(config.DELIVERY_DIR),
                    "heartbeats": {
                        "omega_manager_age_seconds": _heartbeat_age_seconds("omega_manager"),
                        "dashboard_age_seconds": _heartbeat_age_seconds("dashboard"),
                    },
                }
                _broadcast_event("health.updated", health_data)
                
        except Exception as e:
            logger.error(f"SSE monitor error: {e}")
        
        time.sleep(2)  # Check every 2 seconds for changes


def _start_sse_monitor_thread():
    """Start the SSE monitor thread."""
    thread = threading.Thread(target=_sse_monitor_loop, name="sse-monitor", daemon=True)
    thread.start()


@app.route("/api/events")
def api_events():
    """
    Server-Sent Events endpoint for real-time updates.
    
    Events:
    - job.created: New job added
    - job.updated: Job changed (stage, status, progress)
    - job.deleted: Job removed
    - health.updated: System health changed
    """
    def event_stream() -> Generator[str, None, None]:
        client_queue: queue.Queue = queue.Queue(maxsize=100)
        
        with _sse_lock:
            _sse_connections.append(client_queue)
        
        try:
            # Send initial jobs snapshot
            try:
                jobs = get_all_jobs()
                health_data = {
                    "storage_ready": config.critical_paths_ready(),
                    "disk_free_gb": _disk_free_gb(config.DELIVERY_DIR),
                    "heartbeats": {
                        "omega_manager_age_seconds": _heartbeat_age_seconds("omega_manager"),
                        "dashboard_age_seconds": _heartbeat_age_seconds("dashboard"),
                    },
                }
                init_message = json.dumps({
                    "type": "init",
                    "data": {"jobs": jobs, "health": health_data},
                    "timestamp": datetime.now().isoformat()
                })
                yield f"data: {init_message}\n\n"
                logger.info("SSE Init sent")
            except Exception as e:
                logger.error(f"SSE init error: {e}")
            
            # Stream events
            logger.info("SSE entering loop")
            while True:
                try:
                    message = client_queue.get(timeout=5)
                    yield f"data: {message}\n\n"
                except queue.Empty:
                    # Send keepalive comment
                    yield ": keepalive\n\n"
                    
        except GeneratorExit:
            pass
        finally:
            with _sse_lock:
                if client_queue in _sse_connections:
                    _sse_connections.remove(client_queue)
    
    response = Response(event_stream(), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["Connection"] = "keep-alive"
    response.headers["X-Accel-Buffering"] = "no"
    return response



@app.route('/api/v2/deliveries', methods=['POST'])
@admin_required
def api_v2_create_delivery():
    """Create a delivery for a track."""
    data = request.json or {}
    track_id = data.get('track_id')
    method = data.get('method', 'folder')
    recipient = data.get('recipient', '')
    notes = data.get('notes', '')
    provisional = bool(data.get('provisional', False))
    lock_master = bool(data.get('lock_master', True))
    clear_pending_resync = bool(data.get('clear_pending_resync', True))
    locked_by = data.get('locked_by') or "system"
    
    if not track_id:
        return jsonify({"error": "Missing track_id"}), 400
        
    track = omega_db.get_track(track_id)
    if not track or (_station_scope_enabled() and not _track_allowed(track)):
        return jsonify({"error": "Track not found"}), 404

    if provisional:
        lock_master = False
        clear_pending_resync = False
        if not notes:
            notes = "Provisional delivery"

    delivery_id = omega_db.record_track_delivery(
        track_id=track_id,
        destination=method,
        recipient=recipient,
        notes=notes,
        lock_master=lock_master,
        clear_pending_resync=clear_pending_resync,
        locked_by=locked_by,
    )

    if provisional:
        omega_db.update_track(track_id, status=f"Provisional delivery via {method}")
    else:
        omega_db.update_track(track_id, status=f"Delivered via {method}")

    if track.get('job_id'):
        omega_db.log_delivery(track['job_id'], "Manual", datetime.now().isoformat(), method, notes)

    client_email = data.get("client_email") or data.get("notify_email")
    if not client_email and _looks_like_email(recipient):
        client_email = recipient
    delivery_link = data.get("download_link") or data.get("delivery_link")
    if client_email and delivery_link:
        program = omega_db.get_program(track.get("program_id")) if track else None
        program_title = data.get("program_title") or (program.get("title") if program else None) or track_id
        version = track.get("output_version") or "1.0"
        try:
            if provisional:
                NotificationManager.notify_provisional_delivery(
                    client_email=client_email,
                    program_title=program_title,
                    download_link=delivery_link,
                    version=version,
                )
            else:
                NotificationManager.notify_final_delivery(
                    client_email=client_email,
                    program_title=program_title,
                    download_link=delivery_link,
                    version=version,
                )
        except Exception as exc:
            logger.warning(f"Delivery notification failed: {exc}")

    return jsonify({
        "success": True, 
        "delivery_id": delivery_id,
        "provisional": provisional,
        "message": f"Track {track_id} delivered"
    })
@app.route('/api/upload', methods=['POST', 'OPTIONS'])
@admin_required
def api_upload_file():
    """
    Handle file uploads to 1_INBOX via the dashboard.
    Supports multipart/form-data.
    """
    if request.method == 'OPTIONS':
        return jsonify({'status': 'ok'})
    try:
        if 'file' not in request.files:
            return jsonify({"error": "No file part"}), 400
            
        file = request.files['file']
        if file.filename == '':
            return jsonify({"error": "No selected file"}), 400
            
        if file:
            filename = secure_filename(file.filename)

            # Parse Sidecar Metadata if provided
            sidecar_json = request.form.get('sidecar_json')

            # Save to temporary location first for validation
            temp_path = config.INBOX_DIR / "temp" / filename
            temp_path.parent.mkdir(parents=True, exist_ok=True)
            file.save(str(temp_path))

            # Validate video file
            from utils.video_validator import validate_video
            is_valid, error_message = validate_video(temp_path)

            if not is_valid:
                try:
                    temp_path.unlink()
                except Exception:
                    pass
                logger.warning(f"❌ Invalid video rejected: {filename} - {error_message}")
                return jsonify({
                    "success": False,
                    "error": f"Invalid video: {error_message}"
                }), 400
            
            # Determine target folder
            # Default to Auto Pilot / Classic
            target_folder = config.INBOX_DIR / "01_AUTO_PILOT" / "Classic"
            
            target_folder.mkdir(parents=True, exist_ok=True)
            save_path = target_folder / filename

            temp_path.rename(save_path)
            logger.info(f"📥 Uploaded file via API: {save_path}")
            
            # If sidecar metadata provided, write the json file
            if sidecar_json:
                try:
                    sidecar_path = save_path.with_suffix('.json')
                    # Validate JSON
                    meta = json.loads(sidecar_json)
                    with open(sidecar_path, 'w', encoding='utf-8') as f:
                        json.dump(meta, f, indent=2)
                    logger.info(f"   📋 Wrote sidecar metadata: {sidecar_path}")
                except Exception as e:
                    logger.error(f"   ❌ Failed to write sidecar JSON: {e}")
                    # Don't fail the upload, just log error
            
            return jsonify({"success": True, "filename": filename, "path": str(save_path)})

    except Exception as e:
        logger.error(f"Upload failed: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/v2/books/upload', methods=['POST'])
@admin_required
def api_upload_book():
    """
    Upload a book (PDF/DOCX), extract text, and initialize project.
    
    1. Saves file to disk
    2. Extracts text and metadata
    3. Detects chapters
    4. Auto-generates glossary
    5. Creates book_project and book_chapters in DB
    
    Returns:
        JSON with book_id, chapter_count, and glossary preview
    """
    try:
        from utils import book_extractor
        
        if 'file' not in request.files:
            return jsonify({"error": "No file part"}), 400
            
        file = request.files['file']
        if file.filename == '':
            return jsonify({"error": "No selected file"}), 400
            
        if file:
            filename = secure_filename(file.filename)
            upload_dir = config.VAULT_DATA / "books_source"
            upload_dir.mkdir(parents=True, exist_ok=True)
            
            save_path = upload_dir / filename
            file.save(str(save_path))
            logger.info(f"Uploaded book source: {save_path}")
            
            # Extract metadata
            metadata = book_extractor.extract_book_metadata(save_path)
            
            # Extract text and chapters
            chapters = book_extractor.extract_book_text(save_path, detect_chapters_flag=True)
            full_text = "\n".join([c['text'] for c in chapters])
            
            # Create Book Project
            # client/genre defaults can be updated later via PATCH
            book_project = omega_db.create_book_project(
                title=metadata.get('title', filename),
                author=metadata.get('author', 'Unknown'),
                source_language="en",
                target_language="is",
                client="unknown",
                glossary={},  # Will fill after extraction
                style_guide="Literary quality, warm tone. Preserve author voice.",
                meta={
                    "original_filename": filename,
                    "page_count": len(full_text) // 2000,  # Rough estimate
                    "subject": metadata.get('subject')
                }
            )
            book_id = book_project['id']
            
            # Create Chapters
            for ch in chapters:
                omega_db.add_book_chapter(
                    book_id=book_id,
                    chapter_number=ch['chapter_number'],
                    chapter_title=ch['title'],
                    source_text=ch['text'],
                    word_count=ch['word_count']
                )
                
            # Extract Glossary (now that we have book_id for metadata)
            glossary = book_extractor.extract_glossary(full_text, book_id)
            
            # Update book with extracted glossary
            omega_db.update_book_project(book_id, glossary=glossary)
            
            return jsonify({
                "success": True,
                "book_id": book_id,
                "title": metadata.get('title', filename),
                "author": metadata.get('author'),
                "chapter_count": len(chapters),
                "glossary_terms": len(glossary.get('characters', [])) + len(glossary.get('theological_terms', []))
            })

    except Exception as e:
        logger.error(f"Book upload failed: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/v2/books', methods=['GET'])
@admin_required
def api_get_books():
    """Get all book projects, optionally filtered by client or stage."""
    client = request.args.get('client')
    stage = request.args.get('stage')

    try:
        books = omega_db.get_all_book_projects(client=client, stage=stage)
        return jsonify({"success": True, "books": books})
    except Exception as e:
        logger.error(f"Failed to get books: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/v2/books/<book_id>', methods=['GET'])
@admin_required
def api_get_book(book_id):
    """Get a single book with all its chapters."""
    try:
        book = omega_db.get_book_with_chapters(book_id)
        if not book:
            return jsonify({"error": "Book not found"}), 404
        return jsonify({"success": True, "book": book})
    except Exception as e:
        logger.error(f"Failed to get book {book_id}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/v2/books', methods=['POST'])
@admin_required
def api_create_book():
    """
    Create a new book project.

    Request body:
    {
        "title": "Book Title",
        "author": "Author Name",
        "source_language": "en",
        "target_language": "is",
        "client": "studio_iceland",
        "glossary": {"term": "translation", ...},
        "style_guide": "Warm, pastoral tone...",
        "meta": {"genre": "testimony", ...}
    }
    """
    try:
        data = request.json

        book_id = omega_db.create_book_project(
            title=data.get('title', 'Untitled'),
            author=data.get('author'),
            source_language=data.get('source_language', 'en'),
            target_language=data.get('target_language', 'is'),
            client=data.get('client'),
            glossary=data.get('glossary', {}),
            style_guide=data.get('style_guide'),
            character_bible=data.get('character_bible', {}),
            meta=data.get('meta', {})
        )

        return jsonify({"success": True, "book_id": book_id}), 201

    except Exception as e:
        logger.error(f"Failed to create book: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/v2/books/<book_id>', methods=['PATCH'])
@admin_required
def api_update_book(book_id):
    """
    Update book metadata.

    Request body: Any fields from book_projects table
    """
    try:
        data = request.json
        success = omega_db.update_book_project(book_id, **data)

        if not success:
            return jsonify({"error": "Book not found"}), 404

        return jsonify({"success": True})

    except Exception as e:
        logger.error(f"Failed to update book {book_id}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/v2/books/<book_id>/chapters', methods=['POST'])
@admin_required
def api_add_chapter(book_id):
    """
    Add a chapter to a book.

    Request body:
    {
        "chapter_number": 1,
        "chapter_title": "Introduction",
        "source_text": "English text...",
        "word_count": 2500
    }
    """
    try:
        data = request.json

        chapter_id = omega_db.add_book_chapter(
            book_id=book_id,
            chapter_number=data.get('chapter_number'),
            chapter_title=data.get('chapter_title'),
            source_text=data.get('source_text'),
            word_count=data.get('word_count', 0),
            meta=data.get('meta', {})
        )

        return jsonify({"success": True, "chapter_id": chapter_id}), 201

    except Exception as e:
        logger.error(f"Failed to add chapter to book {book_id}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/v2/chapters/<chapter_id>', methods=['GET'])
@admin_required
def api_get_chapter(chapter_id):
    """Get a single chapter with all translation steps."""
    try:
        chapter = omega_db.get_book_chapter(chapter_id)
        if not chapter:
            return jsonify({"error": "Chapter not found"}), 404
        return jsonify({"success": True, "chapter": chapter})
    except Exception as e:
        logger.error(f"Failed to get chapter {chapter_id}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/v2/chapters/<chapter_id>', methods=['PATCH'])
@admin_required
def api_update_chapter(chapter_id):
    """
    Update chapter content or metadata.

    Request body: Any fields from book_chapters table
    Special fields:
    - step, translation_text, status: For updating translation progress
    - stage, final_text: For marking complete
    """
    try:
        data = request.json

        # If updating translation step, use special method
        if 'step' in data and 'translation_text' in data:
            success = omega_db.update_chapter_translation(
                chapter_id=chapter_id,
                step=data['step'],
                translation_text=data['translation_text'],
                status=data.get('status'),
                stage=data.get('stage'),
                final_text=data.get('final_text')
            )
        else:
            # General update
            success = omega_db.update_chapter_translation(
                chapter_id=chapter_id,
                **data
            )

        if not success:
            return jsonify({"error": "Chapter not found"}), 404

        return jsonify({"success": True})

    except Exception as e:
        logger.error(f"Failed to update chapter {chapter_id}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/v2/chapters/<chapter_id>/lock', methods=['POST'])
@admin_required
def api_lock_chapter(chapter_id):
    """
    Lock a chapter to prevent re-translation.

    Request body:
    {
        "locked_by": "user@example.com"
    }
    """
    try:
        data = request.json
        locked_by = data.get('locked_by', 'admin')

        success = omega_db.lock_chapter(chapter_id, locked_by)
        if not success:
            return jsonify({"error": "Chapter not found or already locked"}), 400

        return jsonify({"success": True, "message": f"Chapter locked by {locked_by}"})

    except Exception as e:
        logger.error(f"Failed to lock chapter {chapter_id}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/v2/chapters/<chapter_id>/unlock', methods=['POST'])
@admin_required
def api_unlock_chapter(chapter_id):
    """Unlock a chapter to allow re-translation."""
    try:
        success = omega_db.unlock_chapter(chapter_id)
        if not success:
            return jsonify({"error": "Chapter not found"}), 404

        return jsonify({"success": True, "message": "Chapter unlocked"})

    except Exception as e:
        logger.error(f"Failed to unlock chapter {chapter_id}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/v2/books/<book_id>/translate', methods=['POST'])
@admin_required
def api_translate_book(book_id):
    """
    Start translation for a book (background job).

    Request body:
    {
        "start_chapter": 1,  // optional, default 1
        "end_chapter": 10,   // optional, default all
        "force_restart": false  // optional, re-translate completed chapters
    }

    Returns immediately with job status.
    Translation runs in background thread.
    """
    return jsonify({"error": "Book translation is not yet implemented"}), 501


@app.route('/api/v2/chapters/<chapter_id>/translate', methods=['POST'])
@admin_required
def api_translate_chapter(chapter_id):
    """
    Translate a single chapter (background job).

    Request body:
    {
        "force_restart": false  // optional, re-translate if already done
    }
    """
    return jsonify({"error": "Chapter translation is not yet implemented"}), 501


@app.route('/api/v2/books/<book_id>', methods=['DELETE'])
@admin_required
def api_delete_book(book_id):
    """Delete a book and all its chapters."""
    try:
        success = omega_db.delete_book_project(book_id)
        if not success:
            return jsonify({"error": "Book not found"}), 404

        return jsonify({"success": True, "message": f"Book {book_id} deleted"})

    except Exception as e:
        logger.error(f"Failed to delete book {book_id}: {e}")
        return jsonify({"error": str(e)}), 500



# =========================================================================
# V2 API - WEEKLY GRID & PROGRAM-CENTRIC WORKFLOW
# =========================================================================

@app.route("/api/v2/weekly_grid", methods=["GET"])
def api_weekly_grid():
    """
    Returns the Weekly Grid data structure populated from the DB.
    Replaces the static weekly.json fixture.
    """
    try:
        conn = omega_db._connect()
        c = conn.cursor()

        # Fetch Programs
        c.execute("""
            SELECT * FROM programs
            WHERE (status != 'DELETED' OR status IS NULL)
            ORDER BY due_date DESC, created_at DESC
            LIMIT 50
        """)
        programs_rows = c.fetchall()
        
        # Fetch Tracks (all for now)
        c.execute("SELECT * FROM tracks")
        tracks_rows = c.fetchall()
        
        # Group tracks by program
        tracks_by_prog = {}
        for t in tracks_rows:
            t_dict = dict(t)
            pid = t_dict["program_id"]
            if pid not in tracks_by_prog:
                tracks_by_prog[pid] = {}
            
            lang = t_dict["language_code"]
            stage = (t_dict.get("stage") or "QUEUED").upper()
            status_label = t_dict.get("status") or stage.title()
            
            # Mock outputs 
            outputs = []
            if stage in ["COMPLETED", "APPROVED"] and t_dict.get("output_path"):
                 outputs.append({
                     "type": "Main Output",
                     "filename": Path(t_dict["output_path"]).name,
                     "status": "READY",
                     "size": "--",
                     "url": "#"
                 })

            tracks_by_prog[pid][lang] = {
                "track_id": t_dict["id"],
                "status": stage,
                "status_label": status_label,
                "outputs_total": len(outputs),
                "outputs_ready": len(outputs),
                "has_error": stage == "ERROR",
                "outputs": outputs
            }

        programs_out = []
        for p in programs_rows:
            p_dict = dict(p)
            pid = p_dict["id"]
            
            # Robust air_date logic
            air_date = p_dict.get("due_date")
            if not air_date:
                created = p_dict.get("created_at")
                if isinstance(created, datetime):
                    air_date = created.isoformat()[:10]
                elif created:
                     air_date = str(created)[:10]
                else:
                    air_date = ""
            
            title = p_dict.get("title") or "Untitled"
            # Cleanup backfilled titles that look like filenames
            if title == p_dict.get("original_filename"):
               title = title.replace("_", " ").replace("-", " ").title()

            programs_out.append({
                "program_id": pid,
                "title": title,
                "client_id": p_dict.get("client") or "unknown",
                "air_date": air_date,
                "thumbnail": "",
                "tracks": tracks_by_prog.get(pid, {})
            })

        conn.close()
        return jsonify({
            "week_label": "All Programs",
            "programs": programs_out
        })
    except Exception as e:
        logger.error(f"Weekly Grid API Error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/v2/tracks", methods=["POST"])
def api_create_track():
    """
    Create a new Track (Fork Language).
    Payload: { "program_id": "...", "language_code": "..." }
    """
    data = request.get_json() or {}
    program_id = data.get("program_id")
    language_code = data.get("language_code")
    
    if not program_id or not language_code:
        return jsonify({"error": "Missing program_id or language_code"}), 400
        
    try:
        conn = omega_db._connect()
        c = conn.cursor()
        
        c.execute(
            "SELECT id FROM programs WHERE id=? AND (status != 'DELETED' OR status IS NULL)",
            (program_id,),
        )
        if not c.fetchone():
            conn.close()
            return jsonify({"error": "Program not found"}), 404
            
        track_key = f"{program_id}|{language_code}|subtitle"
        track_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, track_key))
        
        c.execute("SELECT id FROM tracks WHERE id=?", (track_id,))
        if c.fetchone():
            conn.close()
            return jsonify({"error": "Track already exists"}), 409
            
        now = datetime.now()
        c.execute("""
            INSERT INTO tracks (id, program_id, language_code, type, stage, status, created_at, updated_at)
            VALUES (?, ?, ?, 'subtitle', 'QUEUED', 'Created', ?, ?)
        """, (track_id, program_id, language_code, now, now))
        
        conn.commit()
        conn.close()
        
        return jsonify({
            "track_id": track_id,
            "status": "QUEUED",
            "message": f"Created {language_code} track"
        }), 201

    except Exception as e:
        logger.error(f"Create Track API Error: {e}")
        return jsonify({"error": str(e)}), 500


# =============================================================================
# Operations Command Center APIs
# =============================================================================

@app.route("/api/v2/ops/summary", methods=["GET"])
def api_ops_summary():
    """
    Get operations summary: counts per stage, bottlenecks, urgent items.
    Returns data optimized for the Operations Command Center.
    """
    try:
        conn = omega_db._connect()
        c = conn.cursor()

        # Get all active tracks with program info (including due_date)
        c.execute('''
            SELECT t.*, p.title as program_title, p.due_date, p.client
            FROM tracks t
            JOIN programs p ON t.program_id = p.id
            WHERE t.stage NOT IN ('COMPLETE', 'COMPLETED', 'DELIVERED')
            ORDER BY t.updated_at DESC
        ''')
        tracks = [dict(row) for row in c.fetchall()]
        tracks = _filter_tracks_by_station(tracks)

        # Stage counts
        stage_counts = {}
        for t in tracks:
            stage = t.get('stage', 'UNKNOWN')
            stage_counts[stage] = stage_counts.get(stage, 0) + 1

        # Bottleneck detection (stages with most items)
        bottlenecks = []
        if stage_counts:
            max_count = max(stage_counts.values())
            threshold = max(3, max_count * 0.5)  # At least 3 or 50% of max
            for stage, count in stage_counts.items():
                if count >= threshold and count > 2:
                    bottlenecks.append({"stage": stage, "count": count})

        # Urgent items (due within 24 hours or overdue)
        now = datetime.now()
        urgent_count = 0
        overdue_count = 0
        for t in tracks:
            due_date_str = t.get('due_date')
            if due_date_str:
                try:
                    due_date = datetime.fromisoformat(due_date_str.replace('Z', '+00:00').split('+')[0])
                    hours_until_due = (due_date - now).total_seconds() / 3600
                    if hours_until_due < 0:
                        overdue_count += 1
                    elif hours_until_due <= 24:
                        urgent_count += 1
                except:
                    pass        # Ready for delivery (COMPLETED but not delivered)
        ready_for_delivery = sum(
            1 for t in tracks
            if t.get('stage') in ('COMPLETE', 'COMPLETED')
            and (t.get('delivery_status') or '') != 'DELIVERED'
        )

        # Ready for burn (FINALIZING stage or has srt but no video)
        ready_for_burn = stage_counts.get('FINALIZING', 0) + stage_counts.get('REVIEWED', 0)

        # Awaiting review
        awaiting_review = stage_counts.get('AWAITING_REVIEW', 0) + stage_counts.get('CLOUD_REVIEWING', 0)

        # Failed
        failed_count = stage_counts.get('FAILED', 0) + stage_counts.get('DEAD', 0)

        conn.close()

        return jsonify({
            "total_active": len(tracks),
            "stage_counts": stage_counts,
            "bottlenecks": bottlenecks,
            "urgent": urgent_count,
            "overdue": overdue_count,
            "ready_for_delivery": ready_for_delivery,
            "ready_for_burn": ready_for_burn,
            "awaiting_review": awaiting_review,
            "failed": failed_count,
            "timestamp": now.isoformat()
        })

    except Exception as e:
        logger.error(f"Ops Summary API Error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/v2/ops/queue", methods=["GET"])
def api_ops_queue():
    """
    Get priority queue of tracks sorted by due date.
    Query params:
      - stage: filter by stage (comma-separated)
      - due_before: ISO date string
      - limit: max items (default 100)
      - include_completed: include completed tracks (default false)
    """
    try:
        stage_filter = request.args.get('stage', '').strip()
        due_before = request.args.get('due_before', '').strip()
        limit = int(request.args.get('limit', 100))
        include_completed = request.args.get('include_completed', '').lower() in ('true', '1', 'yes')

        conn = omega_db._connect()
        c = conn.cursor()

        # Build query
        query = '''
            SELECT t.*, p.title as program_title, p.due_date, p.client, p.video_path
            FROM tracks t
            JOIN programs p ON t.program_id = p.id
            WHERE 1=1
        '''
        params = []

        if not include_completed:
            query += " AND t.stage NOT IN ('COMPLETE', 'COMPLETED', 'DELIVERED')"

        if stage_filter:
            stages = [s.strip() for s in stage_filter.split(',')]
            placeholders = ','.join(['?'] * len(stages))
            query += f" AND t.stage IN ({placeholders})"
            params.extend(stages)

        if due_before:
            query += " AND p.due_date <= ?"
            params.append(due_before)

        # Sort by: overdue first (NULL due_date last), then by due_date ascending
        query += '''
            ORDER BY
                CASE WHEN p.due_date IS NULL THEN 1 ELSE 0 END,
                p.due_date ASC,
                t.updated_at DESC
            LIMIT ?
        '''
        params.append(limit)

        c.execute(query, params)
        rows = c.fetchall()
        conn.close()

        # Process results with urgency classification
        now = datetime.now()
        results = []
        for row in rows:
            track = dict(row)

            # Parse due_date and classify urgency
            urgency = 'normal'
            hours_until_due = None
            due_date_str = track.get('due_date')
            if due_date_str:
                try:
                    due_date = datetime.fromisoformat(due_date_str.replace('Z', '+00:00').split('+')[0])
                    hours_until_due = (due_date - now).total_seconds() / 3600
                    if hours_until_due < 0:
                        urgency = 'overdue'
                    elif hours_until_due <= 24:
                        urgency = 'urgent'
                    elif hours_until_due <= 72:
                        urgency = 'soon'
                except:
                    pass

            track['urgency'] = urgency
            track['hours_until_due'] = hours_until_due

            # Parse meta if present
            if track.get('meta'):
                try:
                    track['meta'] = json.loads(track['meta'])
                except:
                    track['meta'] = {}

            results.append(track)

        results = _filter_tracks_by_station(results)

        return jsonify({
            "tracks": results,
            "count": len(results),
            "timestamp": now.isoformat()
        })

    except Exception as e:
        logger.error(f"Ops Queue API Error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/v2/ops/actions", methods=["POST"])
@admin_required
def api_ops_batch_actions():
    """
    Execute batch actions on multiple tracks.
    Payload: {
        "action": "deliver" | "burn" | "approve" | "retry",
        "track_ids": ["id1", "id2", ...],
        "idempotency_key": "optional-unique-key"
    }

    Server validates eligibility for each track before acting.
    Returns results per track.
    """
    try:
        data = request.get_json() or {}
        action = data.get('action', '').strip()
        track_ids = data.get('track_ids', [])
        idempotency_key = data.get('idempotency_key')

        if not action:
            return jsonify({"error": "Missing action"}), 400
        if not track_ids or not isinstance(track_ids, list):
            return jsonify({"error": "Missing or invalid track_ids"}), 400

        valid_actions = {'deliver', 'burn', 'approve', 'retry', 'finalize'}
        if action not in valid_actions:
            return jsonify({"error": f"Invalid action. Must be one of: {valid_actions}"}), 400

        # Define eligible stages for each action
        eligibility = {
            'deliver': {'COMPLETE', 'COMPLETED', 'BURNING'},  # BURNING included if video exists
            'burn': {'REVIEWED', 'FINALIZING', 'FINALIZED'},
            'finalize': {'REVIEWED', 'AWAITING_REVIEW'},
            'approve': {'AWAITING_REVIEW', 'CLOUD_REVIEWING', 'AWAITING_APPROVAL'},
            'retry': {'FAILED', 'DEAD', 'ERROR'}
        }

        eligible_stages = eligibility.get(action, set())

        results = []
        success_count = 0
        skip_count = 0
        error_count = 0

        conn = omega_db._connect()
        c = conn.cursor()

        for track_id in track_ids:
            # Get track info
            c.execute('''
                SELECT t.*, p.title as program_title, p.client
                FROM tracks t
                JOIN programs p ON t.program_id = p.id
                WHERE t.id = ?
            ''', (track_id,))
            row = c.fetchone()

            if not row:
                results.append({
                    "track_id": track_id,
                    "status": "error",
                    "message": "Track not found"
                })
                error_count += 1
                continue

            track = dict(row)
            track['meta'] = _normalize_meta(track.get('meta'))
            if _station_scope_enabled() and not _station_match(track.get('meta')):
                results.append({
                    "track_id": track_id,
                    "status": "skipped",
                    "message": "Track belongs to another station"
                })
                skip_count += 1
                continue
            stage = track.get('stage', '')

            # Check eligibility
            if stage not in eligible_stages:
                # Special case: deliver can work if track has output files
                if action == 'deliver' and track.get('output_path'):
                    pass  # Allow
                else:
                    results.append({
                        "track_id": track_id,
                        "status": "skipped",
                        "message": f"Not eligible: stage is {stage}, need one of {eligible_stages}"
                    })
                    skip_count += 1
                    continue

            # Execute action
            try:
                if action == 'deliver':
                    # Mark as delivered
                    c.execute('''
                        UPDATE tracks SET delivery_status = 'DELIVERED', updated_at = ?
                        WHERE id = ?
                    ''', (datetime.now(), track_id))

                    # Record delivery
                    delivery_id = str(uuid.uuid4())
                    c.execute('''
                        INSERT INTO track_deliveries (id, track_id, destination, delivered_at)
                        VALUES (?, ?, ?, ?)
                    ''', (delivery_id, track_id, 'batch_delivery', datetime.now()))

                    results.append({
                        "track_id": track_id,
                        "status": "success",
                        "message": "Marked as delivered"
                    })
                    success_count += 1

                elif action == 'burn':
                    # Queue for burning by updating stage
                    c.execute('''
                        UPDATE tracks SET stage = 'BURNING', updated_at = ?
                        WHERE id = ?
                    ''', (datetime.now(), track_id))

                    results.append({
                        "track_id": track_id,
                        "status": "success",
                        "message": "Queued for burning"
                    })
                    success_count += 1

                elif action == 'finalize':
                    # Queue for finalization
                    c.execute('''
                        UPDATE tracks SET stage = 'FINALIZING', updated_at = ?
                        WHERE id = ?
                    ''', (datetime.now(), track_id))

                    results.append({
                        "track_id": track_id,
                        "status": "success",
                        "message": "Queued for finalization"
                    })
                    success_count += 1

                elif action == 'approve':
                    # Mark as approved, move to REVIEWED
                    c.execute('''
                        UPDATE tracks SET stage = 'REVIEWED', updated_at = ?
                        WHERE id = ?
                    ''', (datetime.now(), track_id))

                    results.append({
                        "track_id": track_id,
                        "status": "success",
                        "message": "Approved for finalization"
                    })
                    success_count += 1

                elif action == 'retry':
                    # Reset to TRANSCRIBED for retry
                    c.execute('''
                        UPDATE tracks SET stage = 'TRANSCRIBED', status = 'Retrying', updated_at = ?
                        WHERE id = ?
                    ''', (datetime.now(), track_id))

                    results.append({
                        "track_id": track_id,
                        "status": "success",
                        "message": "Reset for retry"
                    })
                    success_count += 1

            except Exception as action_err:
                results.append({
                    "track_id": track_id,
                    "status": "error",
                    "message": str(action_err)
                })
                error_count += 1

        conn.commit()
        conn.close()

        return jsonify({
            "action": action,
            "total": len(track_ids),
            "success": success_count,
            "skipped": skip_count,
            "errors": error_count,
            "results": results,
            "idempotency_key": idempotency_key
        })

    except Exception as e:
        logger.error(f"Ops Batch Actions API Error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/v2/ops/deliveries", methods=["GET"])
def api_ops_deliveries():
    """
    Get delivery status and history for operations view.
    Query params:
      - status: filter by status ('pending', 'delivered', 'failed')
      - days: how many days of history (default 7)
      - limit: max items (default 100)
    """
    try:
        status_filter = request.args.get('status', '').strip().lower()
        days = int(request.args.get('days', 7))
        limit = int(request.args.get('limit', 100))

        conn = omega_db._connect()
        c = conn.cursor()

        # Get pending deliveries (completed but not delivered)
        pending = []
        if not status_filter or status_filter == 'pending':
            c.execute('''
                SELECT t.*, p.title as program_title, p.client, p.due_date
                FROM tracks t
                JOIN programs p ON t.program_id = p.id
                WHERE t.stage IN ('COMPLETE', 'COMPLETED')
                AND (t.delivery_status IS NULL OR t.delivery_status != 'DELIVERED')
                ORDER BY p.due_date ASC, t.updated_at DESC
                LIMIT ?
            ''', (limit,))
            pending = [dict(row) for row in c.fetchall()]
            pending = _filter_tracks_by_station(pending)

        # Get recent deliveries
        delivered = []
        if not status_filter or status_filter == 'delivered':
            c.execute('''
                SELECT td.*, t.language_code, t.type as track_type,
                       p.title as program_title, p.client
                FROM track_deliveries td
                JOIN tracks t ON td.track_id = t.id
                JOIN programs p ON t.program_id = p.id
                WHERE td.delivered_at >= datetime('now', ?)
                ORDER BY td.delivered_at DESC
                LIMIT ?
            ''', (f'-{days} days', limit))
            delivered = [dict(row) for row in c.fetchall()]
            if _station_scope_enabled():
                delivered = [d for d in delivered if _track_allowed(omega_db.get_track(d.get('track_id')) or {})]

        conn.close()

        # Summary stats
        return jsonify({
            "pending": pending,
            "pending_count": len(pending),
            "delivered": delivered,
            "delivered_count": len(delivered),
            "timestamp": datetime.now().isoformat()
        })

    except Exception as e:
        logger.error(f"Ops Deliveries API Error: {e}")
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    # Ensure DB exists
    # omega_db.init_db() # Skipped to prevent startup lock contention
    _start_heartbeat_thread()
    if os.environ.get('OMEGA_SSE_ENABLED', '0').strip().lower() in {'1','true','yes','on'}:
        _start_sse_monitor_thread()  # Optional SSE real-time updates
    # Run server (Disable reloader to prevent zombie processes)
    host = os.environ.get("OMEGA_DASH_HOST", "0.0.0.0")
    port = int(os.environ.get("OMEGA_DASH_PORT", "8080"))
    debug = (os.environ.get("OMEGA_DASH_DEBUG") or "").strip().lower() in {"1", "true", "yes"}
    app.run(host=host, port=port, debug=debug, use_reloader=False, threaded=True)
