import json
import logging
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional, Tuple

from google.cloud import storage

import config
import omega_db
import system_health
from gcp_auth import ensure_google_application_credentials
from gcs_jobs import GcsJobPaths, blob_exists, download_json, try_download_json
from lock_manager import ProcessLock
from profiles import LANGUAGES

LOG = logging.getLogger("OmegaCloudSync")

JOB_ID_TS_RE = re.compile(r"^(?P<stem>.+)-(?P<ts>\d{8}T\d{12}Z)$")

STAGE_ORDER = {
    "QUEUED": 10,
    "INGEST": 20,
    "TRANSCRIBED": 30,
    "TRANSLATING": 40,
    "TRANSLATING_CLOUD_SUBMITTED": 45,
    "CLOUD_TRANSLATING": 46,
    "CLOUD_REVIEWING": 47,
    "TRANSLATED": 50,
    "REVIEWING": 55,
    "REVIEWED": 60,
    "FINALIZING": 70,
    "FINALIZED": 80,
    "BURNING": 90,
    "COMPLETED": 100,
    "DELIVERED": 110,
}

DEADMAN_STAGES = {
    "TRANSLATING_CLOUD_SUBMITTED",
    "CLOUD_TRANSLATING",
    "CLOUD_REVIEWING",
}

LANG_NAME_MAP = {
    str(info.get("name", "")).strip().lower(): code
    for code, info in LANGUAGES.items()
    if info.get("name")
}


def _stage_rank(stage: str) -> int:
    return STAGE_ORDER.get(str(stage or "").upper(), 0)


def _normalize_language(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    code = str(value).strip().lower()
    if code in LANGUAGES:
        return code
    mapped = LANG_NAME_MAP.get(code)
    if mapped:
        return mapped
    return None


def _parse_iso_timestamp(value: object) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    raw = str(value).strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        try:
            return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
        except Exception:
            return None


def _progress_recent(storage_client: storage.Client, job_id: Optional[str], cutoff: datetime) -> bool:
    if not job_id:
        return False
    try:
        paths = GcsJobPaths(bucket=config.OMEGA_JOBS_BUCKET, prefix=config.OMEGA_JOBS_PREFIX, job_id=job_id)
        blob = storage_client.bucket(paths.bucket).get_blob(paths.progress_json())
        if not blob or not blob.updated:
            return False
        updated = blob.updated
        if isinstance(updated, datetime) and updated.tzinfo:
            updated = updated.replace(tzinfo=None)
        return updated >= cutoff
    except Exception:
        return False


def _apply_deadman(storage_client: storage.Client) -> int:
    minutes = int(getattr(config, "OMEGA_CLOUD_DEADMAN_MINUTES", 0) or 0)
    if minutes <= 0:
        return 0

    cutoff = datetime.now() - timedelta(minutes=minutes)
    stages = sorted(DEADMAN_STAGES)
    if not stages:
        return 0

    placeholders = ", ".join(["?"] * len(stages))
    conn = omega_db._connect()
    c = conn.cursor()
    try:
        c.execute(f"SELECT id, job_id, stage, updated_at FROM tracks WHERE stage IN ({placeholders})", stages)
        rows = c.fetchall()
    finally:
        conn.close()

    dead_count = 0
    for row in rows:
        record = dict(row) if hasattr(row, "keys") else row
        updated_at = _parse_iso_timestamp(record.get("updated_at"))
        if not updated_at:
            continue
        if updated_at.tzinfo:
            updated_at = updated_at.replace(tzinfo=None)
        if updated_at >= cutoff:
            continue
        if _progress_recent(storage_client, record.get("job_id"), cutoff):
            continue

        omega_db.update_track(
            record.get("id"),
            stage="DEAD",
            status=f"Dead-man timeout after {minutes}m",
            progress=0.0,
            meta={
                "deadman_timeout": True,
                "deadman_minutes": minutes,
                "deadman_at": datetime.now().isoformat(),
            },
        )
        dead_count += 1

    if dead_count:
        LOG.warning("Dead-man switch marked %d track(s) DEAD", dead_count)
    return dead_count


def _split_stem_language(stem: str) -> Tuple[str, Optional[str]]:
    if not stem:
        return stem, None
    base, sep, suffix = stem.rpartition("_")
    if not sep:
        return stem, None
    lang = suffix.lower()
    if lang in LANGUAGES:
        return base, lang
    return stem, None


def _parse_job_id(job_id: str) -> Tuple[str, Optional[str], Optional[str]]:
    base = job_id
    timestamp = None
    match = JOB_ID_TS_RE.match(job_id or "")
    if match:
        base = match.group("stem")
        timestamp = match.group("ts")
    program_stem, lang = _split_stem_language(base)
    return program_stem, lang, timestamp


def _strip_prefix(blob_name: str, prefix: str) -> str:
    prefix = (prefix or "").strip("/ ")
    if not prefix:
        return blob_name.lstrip("/")
    if blob_name.startswith(prefix + "/"):
        return blob_name[len(prefix) + 1 :]
    return blob_name


def _iter_completed_blobs(bucket: storage.Bucket, prefix: str):
    prefix = (prefix or "").strip("/ ")
    gcs_prefix = f"{prefix}/" if prefix else ""
    try:
        for blob in bucket.list_blobs(prefix=gcs_prefix, match_glob="**/approved.json"):
            yield blob
        for blob in bucket.list_blobs(prefix=gcs_prefix, match_glob="**/*_REVIEWED.json"):
            yield blob
        for blob in bucket.list_blobs(prefix=gcs_prefix, match_glob="**/progress.json"):
            yield blob
    except TypeError:
        for blob in bucket.list_blobs(prefix=gcs_prefix):
            if blob.name.endswith("/approved.json") or blob.name.endswith("_REVIEWED.json") or blob.name.endswith("/progress.json"):
                yield blob


def _list_completed_jobs(storage_client: storage.Client, bucket_name: str, prefix: str) -> Dict[str, dict]:
    bucket = storage_client.bucket(bucket_name)
    results: Dict[str, dict] = {}
    for blob in _iter_completed_blobs(bucket, prefix):
        rel = _strip_prefix(blob.name, prefix)
        parts = rel.split("/", 1)
        if len(parts) < 2:
            continue
        job_id = parts[0]
        if not job_id:
            continue
        
        kind = "approved"
        if blob.name.endswith("_REVIEWED.json"):
            kind = "reviewed"
        elif blob.name.endswith("progress.json"):
            kind = "progress"

        record = {
            "blob": blob.name,
            "kind": kind,
            "updated": blob.updated,
        }
        existing = results.get(job_id)
        if not existing:
            results[job_id] = record
            continue
        
        # Priority: reviewed > approved > progress
        # If we already have a 'finished' state, ignore 'progress'
        if existing["kind"] in ("reviewed", "approved") and kind == "progress":
            continue
        if existing["kind"] == "reviewed":
            continue
        
        # If new one is reviewed, take it
        if kind == "reviewed":
            results[job_id] = record
            continue
            
        # If new one is approved and existing is progress, take it
        if kind == "approved" and existing["kind"] == "progress":
            results[job_id] = record
            continue

        if blob.updated and existing.get("updated") and blob.updated <= existing.get("updated"):
            continue
        results[job_id] = record
    return results


def _find_vault_video(stem: str) -> Optional[Path]:
    if not stem:
        return None
    try:
        candidates = sorted(config.VAULT_VIDEOS.glob(f"{stem}.*"))
    except Exception:
        candidates = []
    if not candidates and stem.upper() != stem:
        try:
            candidates = sorted(config.VAULT_VIDEOS.glob(f"{stem.upper()}.*"))
        except Exception:
            candidates = []
    for candidate in candidates:
        if candidate.is_file() and not candidate.name.startswith("._"):
            return candidate
    return None


def _load_job_payload(storage_client: storage.Client, bucket: str, prefix: str, job_id: str) -> Optional[dict]:
    paths = GcsJobPaths(bucket=bucket, prefix=prefix, job_id=job_id)
    if not blob_exists(storage_client, bucket, paths.job_json()):
        return None
    payload = try_download_json(storage_client, bucket=bucket, blob_name=paths.job_json())
    if not isinstance(payload, dict):
        return None
    return payload


def _extract_job_context(job_id: str, payload: Optional[dict]) -> dict:
    meta = {}
    if isinstance(payload, dict):
        raw_meta = payload.get("meta")
        if isinstance(raw_meta, dict):
            meta = raw_meta

    program_stem, lang_from_job_id, _ = _parse_job_id(job_id)
    payload_stem = None
    if isinstance(payload, dict):
        payload_stem = payload.get("file_stem") or payload.get("stem")

    if payload_stem:
        payload_stem, lang_from_stem = _split_stem_language(str(payload_stem))
        program_stem = payload_stem or program_stem
    else:
        lang_from_stem = None

    target_language = None
    if isinstance(payload, dict):
        target_language = _normalize_language(payload.get("target_language_code") or payload.get("target_language"))

    if not target_language:
        target_language = _normalize_language(meta.get("target_language"))

    if not target_language:
        target_language = lang_from_stem or lang_from_job_id or "is"

    program_profile = None
    if isinstance(payload, dict):
        program_profile = payload.get("program_profile")
    if not program_profile:
        program_profile = meta.get("program_profile")
    program_profile_explicit = bool(program_profile)
    program_profile = (program_profile or "standard").strip() or "standard"

    subtitle_style = meta.get("subtitle_style") or meta.get("style")
    if not subtitle_style and isinstance(payload, dict):
        subtitle_style = payload.get("subtitle_style")
    subtitle_style_explicit = bool(subtitle_style)
    subtitle_style = (subtitle_style or "Classic").strip() or "Classic"

    context = {
        "meta": meta,
        "program_stem": program_stem,
        "target_language": target_language,
        "program_profile": program_profile,
        "program_profile_explicit": program_profile_explicit,
        "subtitle_style": subtitle_style,
        "subtitle_style_explicit": subtitle_style_explicit,
    }
    return context


def _clean_meta(value: dict) -> dict:
    return {k: v for k, v in (value or {}).items() if v not in (None, "")}


def _ensure_program(
    *,
    program_stem: str,
    original_filename: Optional[str],
    vault_path: Optional[str],
    video_path: Optional[Path],
    client: Optional[str],
    due_date: Optional[str],
    subtitle_style: str,
    subtitle_style_explicit: bool,
    meta: dict,
) -> Optional[str]:
    program = None
    if vault_path:
        program = omega_db.get_program_by_video(vault_path)
    if not program and video_path:
        program = omega_db.get_program_by_video(str(video_path))
    if not program and original_filename:
        program = omega_db.get_program_by_original_filename(original_filename)
    if not program and program_stem:
        program = omega_db.get_program_by_title(program_stem)

    if program and program.get("status") == "DELETED":
        LOG.info("Skipping sync for deleted program %s", program.get("id"))
        return None

    if not program:
        deleted_program = None
        if vault_path:
            deleted_program = omega_db.get_deleted_program_by_video(vault_path)
        if not deleted_program and video_path:
            deleted_program = omega_db.get_deleted_program_by_video(str(video_path))
        if not deleted_program and original_filename:
            deleted_program = omega_db.get_deleted_program_by_original_filename(original_filename)
        if not deleted_program and program_stem:
            deleted_program = omega_db.get_deleted_program_by_title(program_stem)
        if deleted_program:
            LOG.info("Skipping sync for tombstoned program %s", deleted_program.get("id"))
            return None

    program_meta = _clean_meta({
        "original_stem": program_stem,
        "original_filename": original_filename,
        "vault_path": vault_path,
        "source_path": meta.get("source_path"),
        "source": "cloud_sync",
    })

    if program:
        updates = {}
        if not program.get("video_path") and (vault_path or video_path):
            updates["video_path"] = vault_path or str(video_path)
        if not program.get("original_filename") and original_filename:
            updates["original_filename"] = original_filename
        if not program.get("client") and client:
            updates["client"] = client
        if not program.get("due_date") and due_date:
            updates["due_date"] = due_date
        if subtitle_style_explicit and subtitle_style:
            updates["default_style"] = subtitle_style
        if updates or program_meta:
            omega_db.update_program(program["id"], meta=program_meta, **updates)
        return program["id"]

    default_style = subtitle_style if subtitle_style_explicit else "Classic"
    title = program_stem or original_filename or vault_path or "Unknown Program"
    program_id = omega_db.create_program(
        title=title,
        original_filename=original_filename,
        video_path=vault_path or (str(video_path) if video_path else None),
        client=client,
        due_date=due_date,
        default_style=default_style,
        meta=program_meta,
    )
    return program_id


def _ensure_track(
    *,
    program_id: str,
    job_id: str,
    target_language: str,
    subtitle_style: str,
    subtitle_style_explicit: bool,
    program_profile: str,
    program_profile_explicit: bool,
    meta: dict,
) -> dict:
    track = omega_db.get_track_by_job(job_id)
    if track:
        return track

    tracks = omega_db.get_tracks_for_program(program_id)
    lang_code = (target_language or "is").lower()
    empty_job_track = None
    conflict_track = None
    for item in tracks:
        if (item.get("language_code") or "").lower() != lang_code:
            continue
        if not item.get("job_id"):
            empty_job_track = item
            break
        conflict_track = item

    if empty_job_track:
        omega_db.update_track(empty_job_track["id"], job_id=job_id)
        return omega_db.get_track(empty_job_track["id"]) or empty_job_track

    if conflict_track:
        LOG.warning(
            "Existing track for program %s language %s has job_id=%s; creating new track for %s",
            program_id,
            lang_code,
            conflict_track.get("job_id"),
            job_id,
        )

    track_meta = _clean_meta({
        **(meta or {}),
        "target_language": lang_code,
        "source": "cloud_sync",
    })
    if program_profile_explicit:
        track_meta["program_profile"] = program_profile
    if subtitle_style_explicit:
        track_meta["subtitle_style"] = subtitle_style

    track_id = omega_db.create_track(
        program_id=program_id,
        type="subtitle",
        language_code=lang_code,
        stage="QUEUED",
        status="Pending",
        job_id=job_id,
        meta=track_meta,
    )
    return omega_db.get_track(track_id) or {"id": track_id}


def _sync_approved_blob(
    *,
    storage_client: storage.Client,
    bucket: str,
    blob_name: str,
    job_id: str,
) -> Optional[Path]:
    local_path = config.TRANSLATED_DONE_DIR / f"{job_id}_APPROVED.json"
    sync_state = omega_db.get_sync_state(job_id, "approved_json")
    local_exists = local_path.exists()
    artifact_matches = sync_state and sync_state.get("artifact_path") == blob_name
    if sync_state and sync_state.get("state") == "SYNCED" and local_exists and artifact_matches:
        return local_path

    gcs_path = blob_name
    if not sync_state:
        omega_db.create_sync_state(job_id, "approved_json", gcs_path, str(local_path))
    else:
        # Reset stuck DOWNLOADING state if it's been stuck for more than 5 minutes
        current_state = sync_state.get("state") or "PENDING"
        if current_state == "DOWNLOADING":
            updated_at = sync_state.get("updated_at")
            if updated_at:
                try:
                    # Parse the timestamp (naive timestamps are usually local time)
                    if isinstance(updated_at, str):
                        # Remove timezone info for comparison with naive datetime
                        updated_str = updated_at.replace("Z", "").replace("+00:00", "")
                        # Handle various formats
                        if "T" in updated_str:
                            updated_dt = datetime.fromisoformat(updated_str.split("+")[0].split("Z")[0])
                        else:
                            updated_dt = datetime.strptime(updated_str[:19], "%Y-%m-%d %H:%M:%S")
                    else:
                        updated_dt = updated_at
                    stuck_threshold = datetime.now() - timedelta(minutes=5)
                    if updated_dt < stuck_threshold:
                        LOG.warning("Resetting stuck DOWNLOADING state for %s (stuck since %s)", job_id, updated_at)
                        current_state = "PENDING"
                except Exception as e:
                    LOG.warning("Failed to parse updated_at for %s: %s, resetting state", job_id, e)
                    current_state = "PENDING"
        omega_db.update_sync_state(job_id, "approved_json", current_state, artifact_path=gcs_path)

    omega_db.update_sync_state(job_id, "approved_json", "DOWNLOADING", artifact_path=gcs_path)
    try:
        payload = download_json(storage_client, bucket=bucket, blob_name=blob_name)
    except Exception as e:
        LOG.error("Failed to download approved.json for %s: %s", job_id, e)
        omega_db.update_sync_state(job_id, "approved_json", "PENDING", artifact_path=gcs_path, error_message=str(e))
        raise

    tmp_dir = config.TRANSLATED_DONE_DIR
    tmp_path = tmp_dir / f"{job_id}_APPROVED.tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
    os.replace(tmp_path, local_path)

    omega_db.update_sync_state(job_id, "approved_json", "FILE_SAVED", local_path=str(local_path), artifact_path=gcs_path)
    omega_db.update_sync_state(job_id, "approved_json", "SYNCED", local_path=str(local_path), artifact_path=gcs_path)
    return local_path


def _advance_track(
    *,
    job_id: str,
    track: dict,
    target_language: str,
    program_profile: Optional[str],
    subtitle_style: Optional[str],
    meta_updates: dict,
    client: Optional[str],
    due_date: Optional[str],
    title: Optional[str],
):
    current_stage = track.get("stage") or ""
    should_update = _stage_rank(current_stage) < _stage_rank("REVIEWED")
    if should_update:
        current_progress = float(track.get("progress") or 0.0)
        progress = max(current_progress, 70.0)
        omega_db.update_job_via_track(
            job_id,
            stage="REVIEWED",
            status="Approved (cloud)",
            progress=progress,
            meta=meta_updates,
            target_language=target_language,
            program_profile=program_profile,
            subtitle_style=subtitle_style,
            client=client,
            due_date=due_date,
            title=title,
        )
    else:
        omega_db.update_job_via_track(
            job_id,
            meta=meta_updates,
            target_language=target_language,
            program_profile=program_profile,
            subtitle_style=subtitle_style,
            client=client,
            due_date=due_date,
            title=title,
        )


def _sync_job(
    *,
    storage_client: storage.Client,
    bucket: str,
    prefix: str,
    job_id: str,
    blob_name: str,
) -> bool:
    payload = _load_job_payload(storage_client, bucket, prefix, job_id)
    context = _extract_job_context(job_id, payload)
    meta = context.get("meta") or {}

    station_id = str(meta.get("station_id") or (payload.get("station_id") if payload else None) or "").strip().lower()
    if config.OMEGA_STATION_ID:
        if station_id and station_id != config.OMEGA_STATION_ID:
            return False
        if not station_id and not config.OMEGA_STATION_CLAIM_UNASSIGNED:
            return False
        if not station_id and config.OMEGA_STATION_CLAIM_UNASSIGNED:
            meta["station_id"] = config.OMEGA_STATION_ID

    original_filename = meta.get("original_filename")
    vault_path = meta.get("vault_path")
    program_stem = context.get("program_stem")
    target_language = context.get("target_language")
    program_profile = context.get("program_profile")
    program_profile_explicit = bool(context.get("program_profile_explicit"))
    subtitle_style = context.get("subtitle_style")
    subtitle_style_explicit = bool(context.get("subtitle_style_explicit"))

    video_path = None
    if vault_path:
        video_path = Path(str(vault_path))
    elif original_filename:
        video_path = config.VAULT_VIDEOS / original_filename
    elif program_stem:
        video_path = _find_vault_video(program_stem)

    client = meta.get("client")
    due_date = meta.get("due_date")
    program_title = meta.get("program_title")

    program_id = _ensure_program(
        program_stem=program_stem,
        original_filename=original_filename,
        vault_path=str(vault_path) if vault_path else None,
        video_path=video_path,
        client=client,
        due_date=due_date,
        subtitle_style=subtitle_style,
        subtitle_style_explicit=subtitle_style_explicit,
        meta=meta,
    )

    if not program_id:
        # Program was soft-deleted or couldn't be created. Skip.
        return False

    track = _ensure_track(
        program_id=program_id,
        job_id=job_id,
        target_language=target_language,
        subtitle_style=subtitle_style,
        subtitle_style_explicit=subtitle_style_explicit,
        program_profile=program_profile,
        program_profile_explicit=program_profile_explicit,
        meta=meta,
    )

    # If this is a progress-only update, sync progress and exit
    if blob_name.endswith("progress.json"):
        progress_data = try_download_json(storage_client, bucket=bucket, blob_name=blob_name)
        if progress_data and isinstance(progress_data, dict):
            stage = progress_data.get("stage")
            status = progress_data.get("status")
            progress_val = progress_data.get("progress")
            
            # Map cloud stages to local DB stages if possible, or just update status/progress
            # This allows the UI to show "CLOUD_TRANSLATING: Loading skeleton (40%)"
            if stage and status:
                omega_db.update_track(
                    track["id"], 
                    status=f"{stage}: {status}", 
                    progress=progress_val
                )
                LOG.info(f"Updated progress for {job_id}: {stage} - {status} ({progress_val}%)")
        return True

    approved_path = _sync_approved_blob(
        storage_client=storage_client,
        bucket=bucket,
        blob_name=blob_name,
        job_id=job_id,
    )

    meta_updates = _clean_meta({
        "cloud_job_id": job_id,
        "cloud_bucket": bucket,
        "cloud_prefix": prefix,
        "target_language": target_language,
        "approved_json": str(approved_path) if approved_path else None,
        "cloud_synced_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": "cloud_sync",
    })
    if program_profile_explicit:
        meta_updates["program_profile"] = program_profile
    if subtitle_style_explicit:
        meta_updates["subtitle_style"] = subtitle_style

    _advance_track(
        job_id=job_id,
        track=track,
        target_language=target_language,
        program_profile=program_profile if program_profile_explicit else None,
        subtitle_style=subtitle_style if subtitle_style_explicit else None,
        meta_updates=meta_updates,
        client=client,
        due_date=due_date,
        title=program_title,
    )

    return True


def sync_once(storage_client: storage.Client) -> int:
    bucket = config.OMEGA_JOBS_BUCKET
    prefix = config.OMEGA_JOBS_PREFIX
    if not bucket:
        LOG.warning("OMEGA_JOBS_BUCKET not set; skipping sync")
        return 0

    if not config.critical_paths_ready(require_write=True):
        LOG.warning("Critical paths not writable; skipping sync")
        return 0

    batch_limit = int(getattr(config, "OMEGA_CLOUD_SYNC_BATCH_LIMIT", 50))
    processed = 0
    jobs = _list_completed_jobs(storage_client, bucket, prefix)
    if jobs:
        for job_id, info in sorted(jobs.items(), key=lambda item: item[1].get("updated") or 0, reverse=True):
            if processed >= batch_limit:
                break
            try:
                _sync_job(
                    storage_client=storage_client,
                    bucket=bucket,
                    prefix=prefix,
                    job_id=job_id,
                    blob_name=info["blob"],
                )
                processed += 1
            except Exception as exc:
                LOG.error("Sync failed for %s: %s", job_id, exc)
                try:
                    omega_db.update_sync_state(job_id, "approved_json", "PENDING", error_message=str(exc))
                except Exception:
                    pass

    _apply_deadman(storage_client)
    return processed


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [cloud_sync] %(message)s",
    )
    LOG.info("Cloud sync service starting")

    if not getattr(config, "OMEGA_CLOUD_SYNC_ENABLED", True):
        LOG.warning("OMEGA_CLOUD_SYNC_ENABLED is false; exiting")
        return

    omega_db.ensure_schema()
    ensure_google_application_credentials()

    try:
        storage_client = storage.Client()
    except Exception as exc:
        LOG.error("Failed to initialize GCS client: %s", exc)
        return

    poll_seconds = float(getattr(config, "OMEGA_CLOUD_SYNC_POLL_SECONDS", 60.0))

    while True:
        system_health.update_heartbeat("cloud_sync_service")
        try:
            synced = sync_once(storage_client)
            if synced:
                LOG.info("Synced %d cloud job(s)", synced)
        except Exception as exc:
            LOG.error("Cloud sync loop error: %s", exc)
        time.sleep(max(5.0, poll_seconds))


if __name__ == "__main__":
    with ProcessLock("omega_cloud_sync"):
        main()
