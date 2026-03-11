import json
import logging
import shutil
import asyncio
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, update
from sqlalchemy.orm import selectinload

import config
from db import get_db
from models import Program, Track, MasterScript, TrackDelivery, ScriptEdit, Delivery
from routers import admin_required

logger = logging.getLogger("OmegaFastAPI")

router = APIRouter(prefix="/api/v2", tags=["Programs"])

_VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".mpg", ".mpeg", ".m4v", ".wmv", ".flv", ".mts", ".m2ts", ".ts", ".mxf"}


def _secure_filename(filename: str) -> str:
    return re.sub(r"[^\w\s\-.]", "", filename).strip()


_BOUNDARY_RE = re.compile(r'boundary=(?:"([^"]+)"|([^;]+))', re.IGNORECASE)


class _MemoryUpload:
    def __init__(self, filename: str, data: bytes):
        self.filename = filename
        self._data = data

    async def read(self) -> bytes:
        return self._data


class _FallbackForm:
    def __init__(self, entries: list[tuple[str, Any]]):
        self._entries = entries

    def multi_items(self) -> list[tuple[str, Any]]:
        return list(self._entries)

    def get(self, key: str, default: Any = None) -> Any:
        for k, v in self._entries:
            if k == key:
                return v
        return default

    def __iter__(self):
        seen = set()
        for key, _ in self._entries:
            if key in seen:
                continue
            seen.add(key)
            yield key


def _extract_multipart_boundary(content_type: str) -> bytes | None:
    match = _BOUNDARY_RE.search(content_type or "")
    if not match:
        return None
    boundary = (match.group(1) or match.group(2) or "").strip()
    if not boundary:
        return None
    return boundary.encode("utf-8")


async def _parse_multipart_fallback(request: Request) -> _FallbackForm:
    content_type = request.headers.get("content-type", "")
    boundary = _extract_multipart_boundary(content_type)
    if not boundary:
        raise HTTPException(status_code=400, detail="Invalid multipart request: missing boundary")

    body = await request.body()
    marker = b"--" + boundary
    entries: list[tuple[str, Any]] = []

    for raw_part in body.split(marker)[1:]:
        if raw_part.startswith(b"--"):
            break
        part = raw_part.lstrip(b"\r\n")
        if not part:
            continue
        try:
            header_blob, payload = part.split(b"\r\n\r\n", 1)
        except ValueError:
            continue
        payload = payload.rstrip(b"\r\n")

        headers: dict[str, str] = {}
        for line in header_blob.split(b"\r\n"):
            if b":" not in line:
                continue
            key, value = line.split(b":", 1)
            headers[key.decode("latin-1").strip().lower()] = value.decode("latin-1").strip()

        disposition = headers.get("content-disposition", "")
        if "form-data" not in disposition.lower():
            continue

        name_match = re.search(r'name="([^"]+)"', disposition)
        if not name_match:
            continue
        field_name = name_match.group(1)

        filename_match = re.search(r'filename="([^"]*)"', disposition)
        if filename_match is not None:
            filename = filename_match.group(1)
            entries.append((field_name, _MemoryUpload(filename, payload)))
            continue

        entries.append((field_name, payload.decode("utf-8", errors="replace")))

    return _FallbackForm(entries)


async def _parse_form_data(request: Request) -> Any:
    try:
        return await request.form()
    except AssertionError as exc:
        # FastAPI/Starlette requires python-multipart for request.form().
        # Fallback parser keeps uploads working when that dependency is unavailable.
        if "python-multipart" not in str(exc):
            raise
        logger.warning("python-multipart unavailable; using fallback multipart parser")
        return await _parse_multipart_fallback(request)


def _extract_form_files(form_data: Any) -> list[Any]:
    files: list[Any] = []
    if hasattr(form_data, "multi_items"):
        for _, value in form_data.multi_items():
            if hasattr(value, "read") and hasattr(value, "filename"):
                files.append(value)
        return files

    for key in form_data:
        value = form_data.get(key)
        if hasattr(value, "read") and hasattr(value, "filename"):
            files.append(value)
    return files


async def _save_upload(upload: Any, destination: Path) -> None:
    if isinstance(upload, _MemoryUpload):
        destination.write_bytes(await upload.read())
    else:
        loop = asyncio.get_running_loop()
        await upload.seek(0)
        with destination.open("wb") as out_file:
            await loop.run_in_executor(None, shutil.copyfileobj, upload.file, out_file)


def _normalize_meta(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return {}
    return value if isinstance(value, dict) else {}


def _hydrate_track_file_paths(tracks: list[dict]) -> None:
    for track in tracks:
        job_id = track.get("job_id")
        if job_id:
            srt_path = config.SRT_DIR / f"DONE_{job_id}.srt"
            if not srt_path.exists():
                srt_path = config.SRT_DIR / f"{job_id}.srt"
            video_path = config.VIDEO_DIR / f"{job_id}_SUBBED.mp4"
            track["srt_path"] = str(srt_path) if srt_path.exists() else None
            track["video_path"] = str(video_path) if video_path.exists() else None
            track["files_ready"] = srt_path.exists() and video_path.exists()
        else:
            track["srt_path"] = None
            track["video_path"] = None
            track["files_ready"] = False


def _hydrate_track_job_ids(tracks: list[dict]) -> None:
    job_map = {t["id"]: t["job_id"] for t in tracks if t.get("id") and t.get("job_id")}
    for track in tracks:
        if track.get("job_id"):
            continue
        depends_on = track.get("depends_on")
        if depends_on and depends_on in job_map:
            track["job_id"] = job_map[depends_on]


def _track_to_dict(track: Track) -> dict:
    d = {c.name: getattr(track, c.name) for c in track.__table__.columns}
    if isinstance(d.get("meta"), str):
        try:
            d["meta"] = json.loads(d["meta"])
        except Exception:
            d["meta"] = {}
    if isinstance(d.get("created_at"), datetime):
        d["created_at"] = d["created_at"].isoformat()
    if isinstance(d.get("updated_at"), datetime):
        d["updated_at"] = d["updated_at"].isoformat()
    if isinstance(d.get("override_timestamp"), datetime):
        d["override_timestamp"] = d["override_timestamp"].isoformat()
    if isinstance(d.get("locked_at"), datetime):
        d["locked_at"] = d["locked_at"].isoformat()
    return d


def _program_to_dict(program: Program) -> dict:
    d = {c.name: getattr(program, c.name) for c in program.__table__.columns}
    if isinstance(d.get("meta"), str):
        try:
            d["meta"] = json.loads(d["meta"])
        except Exception:
            d["meta"] = {}
    if isinstance(d.get("created_at"), datetime):
        d["created_at"] = d["created_at"].isoformat()
    if isinstance(d.get("updated_at"), datetime):
        d["updated_at"] = d["updated_at"].isoformat()
    if isinstance(d.get("deleted_at"), datetime):
        d["deleted_at"] = d["deleted_at"].isoformat()
    return d


@router.post("/programs/upload")
async def upload_program_media(
    request: Request,
    _admin=Depends(admin_required),
):
    """
    Canonical media-ingest endpoint for the frontend import modal.
    Supports:
      - full_pipeline (default)
      - quick_burn (video + srt)
      - skip_transcription (video + optional transcript)
      - srt_update (srt only)
    """
    import omega_db

    form = await _parse_form_data(request)
    mode = str(form.get("mode") or "full_pipeline").strip().lower()
    review_mode = str(form.get("review_mode") or "automatic").strip().lower()

    raw_languages = form.get("languages")
    languages: list[str] = ["is"]
    if isinstance(raw_languages, str) and raw_languages.strip():
        try:
            parsed = json.loads(raw_languages)
            if isinstance(parsed, list):
                languages = [str(lang).strip().lower() for lang in parsed if str(lang).strip()]
        except json.JSONDecodeError:
            parts = [p.strip().lower() for p in raw_languages.split(",")]
            languages = [p for p in parts if p]

    files = _extract_form_files(form)
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")

    video_file = None
    srt_file = None
    transcript_file = None
    for upload in files:
        filename = _secure_filename(upload.filename or "unknown")
        lower = filename.lower()
        if lower.endswith(".srt") and srt_file is None:
            srt_file = upload
        elif lower.endswith((".txt", ".json")) and transcript_file is None:
            transcript_file = upload
        elif Path(lower).suffix in _VIDEO_EXTS and video_file is None:
            video_file = upload

    if mode == "quick_burn":
        if not video_file or not srt_file:
            raise HTTPException(status_code=400, detail="quick_burn requires video + srt")

        video_name = _secure_filename(video_file.filename or "video.mp4")
        stem = Path(video_name).stem
        vault_dir = config.VAULT_DIR / datetime.utcnow().strftime("%Y-%m") / stem
        vault_dir.mkdir(parents=True, exist_ok=True)
        video_dest = vault_dir / video_name
        await _save_upload(video_file, video_dest)

        srt_dest = config.SRT_DIR / f"DONE_{stem}.srt"
        await _save_upload(srt_file, srt_dest)

        await asyncio.to_thread(
            omega_db.update_job_via_track,
            stem,
            stage="FINALIZED",
            status="Quick burn ready",
            progress=90.0,
            subtitle_style="RUV_BOX",
            meta={
                "vault_path": str(vault_dir),
                "review_mode": review_mode,
                "languages": languages,
            },
        )
        return {"ok": True, "mode": "quick_burn", "stem": stem}

    if mode == "srt_update":
        if not srt_file:
            raise HTTPException(status_code=400, detail="srt_update requires an srt file")
        srt_name = _secure_filename(srt_file.filename or "subtitles.srt")
        stem = Path(srt_name).stem
        if stem.startswith("DONE_"):
            stem = stem[5:]
        srt_dest = config.SRT_DIR / f"DONE_{stem}.srt"
        await _save_upload(srt_file, srt_dest)
        await asyncio.to_thread(
            omega_db.update_job_via_track,
            stem,
            stage="FINALIZED",
            status="SRT updated",
            subtitle_style="RUV_BOX",
        )
        return {"ok": True, "mode": "srt_update", "stem": stem}

    if mode == "skip_transcription":
        if not video_file:
            raise HTTPException(status_code=400, detail="skip_transcription requires a video file")

        video_name = _secure_filename(video_file.filename or "video.mp4")
        stem = Path(video_name).stem
        vault_dir = config.VAULT_DIR / datetime.utcnow().strftime("%Y-%m") / stem
        vault_dir.mkdir(parents=True, exist_ok=True)
        video_dest = vault_dir / video_name
        await _save_upload(video_file, video_dest)

        if transcript_file:
            transcript_name = _secure_filename(transcript_file.filename or "transcript.txt")
            transcript_dest = vault_dir / transcript_name
            await _save_upload(transcript_file, transcript_dest)

        await asyncio.to_thread(
            omega_db.update_job_via_track,
            stem,
            stage="TRANSCRIBED",
            status="Skipped transcription",
            progress=30.0,
            subtitle_style="RUV_BOX",
            meta={
                "vault_path": str(vault_dir),
                "review_mode": review_mode,
                "languages": languages,
            },
        )
        return {"ok": True, "mode": "skip_transcription", "stem": stem}

    if not video_file:
        raise HTTPException(status_code=400, detail="full_pipeline requires a video file")

    video_name = _secure_filename(video_file.filename or "video.mp4")
    if not video_name:
        raise HTTPException(status_code=400, detail="Invalid filename")

    destination = config.INBOX_DIR / video_name
    await _save_upload(video_file, destination)

    # Sidecar metadata consumed by omega_manager ingest.
    sidecar_data: dict[str, Any] = {
        "subtitle_style": "RUV_BOX",
        "review_mode": review_mode,
        "target_language": languages[0] if languages else "is",
        "languages": languages,
    }
    for key in ("client", "due_date", "profile"):
        value = form.get(key)
        if value:
            sidecar_data[key] = value
    sidecar_data["mode"] = "REVIEW" if review_mode == "human" else "AUTO"

    sidecar_path = destination.with_suffix(".json")
    sidecar_path.write_text(json.dumps(sidecar_data, indent=2), encoding="utf-8")

    return {"ok": True, "mode": "full_pipeline", "filename": video_name}


@router.get("/programs")
async def list_programs(
    client: str = Query(None),
    station: str = Query(None, description="Filter by station_id. Use 'all' to see all stations."),
    limit: int = Query(100),
    db: AsyncSession = Depends(get_db),
):
    """Get all programs with their tracks."""
    stmt = select(Program).where(Program.status != "DELETED").order_by(Program.updated_at.desc()).limit(limit)
    if client:
        stmt = stmt.where(Program.client == client)

    result = await db.execute(stmt)
    programs = result.scalars().all()

    # Determine station filter: explicit param > OMEGA_UI_SCOPE config > show all
    station_filter = None
    if station and station != "all":
        station_filter = station
    elif not station and getattr(config, "OMEGA_UI_SCOPE", "all") == "station":
        station_filter = config.OMEGA_STATION_ID

    output = []
    for program in programs:
        p_dict = _program_to_dict(program)

        # Fetch tracks
        tracks_result = await db.execute(
            select(Track).where(Track.program_id == program.id)
        )
        tracks = [_track_to_dict(t) for t in tracks_result.scalars().all()]
        _hydrate_track_job_ids(tracks)
        _hydrate_track_file_paths(tracks)
        p_dict["tracks"] = tracks

        # Station filtering: skip programs that have no tracks belonging to this station
        if station_filter:
            has_station_track = False
            for t in tracks:
                t_meta = t.get("meta") or {}
                if isinstance(t_meta, str):
                    try:
                        t_meta = json.loads(t_meta)
                    except Exception:
                        t_meta = {}
                t_station = t_meta.get("station_id", "")
                if t_station == station_filter or not t_station:
                    # Match this station, or unassigned jobs (visible to all)
                    has_station_track = True
                    break
            if not has_station_track:
                continue

        total_tracks = len(tracks)
        complete_tracks = sum(
            1 for t in tracks if t["stage"] in ("COMPLETE", "COMPLETED", "DELIVERED")
        )
        p_dict["track_completion"] = f"{complete_tracks}/{total_tracks}" if total_tracks > 0 else "0/0"
        p_dict["needs_attention"] = any(
            t["stage"] in ("AWAITING_REVIEW", "AWAITING_APPROVAL", "FAILED", "DEAD")
            for t in tracks
        )
        output.append(p_dict)

    return output


@router.get("/programs/{program_id}")
async def get_program(program_id: str, db: AsyncSession = Depends(get_db)):
    """Get a single program with all details."""
    result = await db.execute(select(Program).where(Program.id == program_id))
    program = result.scalar_one_or_none()
    if not program:
        raise HTTPException(status_code=404, detail="Program not found")

    p_dict = _program_to_dict(program)

    # Tracks
    tracks_result = await db.execute(select(Track).where(Track.program_id == program_id))
    tracks = [_track_to_dict(t) for t in tracks_result.scalars().all()]
    _hydrate_track_job_ids(tracks)
    _hydrate_track_file_paths(tracks)
    p_dict["tracks"] = tracks

    # Deliveries
    track_ids = [t["id"] for t in tracks]
    deliveries = []
    if track_ids:
        del_result = await db.execute(
            select(TrackDelivery).where(TrackDelivery.track_id.in_(track_ids))
        )
        for d in del_result.scalars().all():
            deliveries.append({
                "id": d.id, "track_id": d.track_id,
                "destination": d.destination, "recipient": d.recipient,
                "delivered_at": d.delivered_at.isoformat() if d.delivered_at else None,
                "notes": d.notes,
            })
    p_dict["deliveries"] = deliveries

    return p_dict


@router.delete("/programs/{program_id}")
async def delete_program(
    program_id: str,
    db: AsyncSession = Depends(get_db),
    _admin=Depends(admin_required),
):
    """Soft-delete a program and all associated data."""
    result = await db.execute(select(Program).where(Program.id == program_id))
    program = result.scalar_one_or_none()
    if not program:
        raise HTTPException(status_code=404, detail="Program not found")

    if program.status == "DELETED":
        return {"success": True, "message": f"Program '{program.title}' already deleted", "program_id": program_id}

    original_filename = program.original_filename or ""
    video_path = program.video_path or ""
    title = program.title or "Unknown"

    if not original_filename and video_path:
        try:
            original_filename = Path(video_path).name
        except Exception:
            original_filename = ""

    # Delete filesystem artifacts
    deleted_paths = []
    if original_filename:
        stem = Path(original_filename).stem

        project_dir = config.find_project_folder(original_filename)
        if project_dir and project_dir.exists():
            try:
                await asyncio.to_thread(shutil.rmtree, str(project_dir))
                deleted_paths.append(str(project_dir))
            except Exception as e:
                logger.warning(f"Failed to delete project folder: {e}")

        directories_to_clean = [
            config.VAULT_DATA, config.VAULT_VIDEOS,
            config.VAULT_DIR / "Audio", config.VAULT_DIR / "Proxies",
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
                    logger.warning(f"Failed to delete {f}: {e}")

    # Delete DB records
    tracks_result = await db.execute(select(Track).where(Track.program_id == program_id))
    track_rows = tracks_result.scalars().all()
    track_ids = [t.id for t in track_rows]
    job_ids = [t.job_id for t in track_rows if t.job_id]

    ms_result = await db.execute(select(MasterScript).where(MasterScript.program_id == program_id))
    master_script_ids = [m.id for m in ms_result.scalars().all()]
    master_script_ids.extend([t.master_script_id for t in track_rows if t.master_script_id])
    master_script_ids = list(set(master_script_ids))

    td_deleted = se_deleted = del_deleted = jobs_deleted = 0
    if track_ids:
        r = await db.execute(delete(TrackDelivery).where(TrackDelivery.track_id.in_(track_ids)))
        td_deleted = r.rowcount
        r = await db.execute(delete(ScriptEdit).where(ScriptEdit.track_id.in_(track_ids)))
        se_deleted = r.rowcount
    if master_script_ids:
        r = await db.execute(delete(ScriptEdit).where(ScriptEdit.master_script_id.in_(master_script_ids)))
        se_deleted += r.rowcount

    await db.execute(delete(Track).where(Track.program_id == program_id))
    tracks_deleted = len(track_ids)
    await db.execute(delete(MasterScript).where(MasterScript.program_id == program_id))

    # Soft-delete program
    now = datetime.now()
    program.status = "DELETED"
    program.deleted_at = now
    program.deleted_original_filename = original_filename or None
    program.deleted_video_path = video_path or None
    program.original_filename = None
    program.video_path = None
    program.thumbnail_path = None
    program.updated_at = now

    await db.commit()

    return {
        "success": True,
        "message": f"Deleted program '{title}'",
        "tracks_deleted": tracks_deleted,
        "track_deliveries_deleted": td_deleted,
        "script_edits_deleted": se_deleted,
        "files_deleted": len(deleted_paths),
        "deleted_paths": deleted_paths,
    }


@router.post("/programs")
async def create_program(
    data: dict,
    db: AsyncSession = Depends(get_db),
    _admin=Depends(admin_required),
):
    """Create a new program manually."""
    import uuid

    meta = data.get("meta", {}) or {}
    if "station_id" not in meta:
        meta["station_id"] = config.OMEGA_STATION_ID

    program = Program(
        id=str(uuid.uuid4()),
        title=data.get("title", "Untitled"),
        original_filename=data.get("original_filename"),
        video_path=data.get("video_path"),
        client=data.get("client"),
        due_date=data.get("due_date"),
        default_style=data.get("default_style", "RUV_BOX"),
        meta=meta,
    )
    db.add(program)
    await db.commit()

    return {"success": True, "program_id": program.id}


@router.get("/programs/staged")
async def get_staged_programs(db: AsyncSession = Depends(get_db)):
    """Get programs waiting for configuration."""
    result = await db.execute(
        select(Program)
        .where(Program.status != "DELETED")
        .order_by(Program.created_at.desc())
    )
    programs = result.scalars().all()

    # Filter for ingest_mode='staged' via meta (since ingest_mode isn't a column)
    output = []
    for p in programs:
        meta = _normalize_meta(p.meta)
        if meta.get("ingest_mode") == "staged":
            d = _program_to_dict(p)
            output.append(d)

    return output


@router.post("/programs/{program_id}/configure")
async def configure_staged_program(
    program_id: str,
    data: dict,
    db: AsyncSession = Depends(get_db),
    _admin=Depends(admin_required),
):
    """Configure a staged program before starting translation."""
    result = await db.execute(select(Program).where(Program.id == program_id))
    program = result.scalar_one_or_none()
    if not program:
        raise HTTPException(status_code=404, detail="Program not found")

    meta = _normalize_meta(program.meta)
    if meta.get("ingest_mode") != "staged":
        raise HTTPException(status_code=400, detail="Program is not in staged mode")

    languages = data.get("languages", [])
    if not languages:
        raise HTTPException(status_code=400, detail="At least one target language is required")

    if data.get("style"):
        program.default_style = data["style"]

    meta["configured_languages"] = languages
    meta["configured_at"] = datetime.now().isoformat()
    program.meta = meta
    program.updated_at = datetime.now()

    await db.commit()

    return {
        "success": True,
        "program_id": program_id,
        "configured_languages": languages,
        "message": "Program configured. Call /start to begin translation.",
    }


@router.post("/programs/{program_id}/start")
async def start_staged_program(
    program_id: str,
    db: AsyncSession = Depends(get_db),
    _admin=Depends(admin_required),
):
    """Start translation for a staged program."""
    result = await db.execute(select(Program).where(Program.id == program_id))
    program = result.scalar_one_or_none()
    if not program:
        raise HTTPException(status_code=404, detail="Program not found")

    meta = _normalize_meta(program.meta)
    if meta.get("ingest_mode") != "staged":
        raise HTTPException(status_code=400, detail="Program is not in staged mode")

    languages = meta.get("configured_languages", [])
    if not languages:
        raise HTTPException(status_code=400, detail="No languages configured. Call /configure first.")

    skeleton_path = meta.get("skeleton_path")
    if not skeleton_path or not Path(skeleton_path).exists():
        raise HTTPException(status_code=400, detail="Skeleton file not found. Re-run transcription.")

    import uuid
    from gcs_jobs import GcsJobPaths, new_job_id, upload_json
    from workers import publisher

    created_tracks = []
    for lang_code in languages:
        original_stem = meta.get("original_stem", program.title or "unknown")
        job_id = new_job_id(f"{original_stem}_{lang_code}")

        language_name = None
        try:
            from profiles import LANGUAGES
            language_name = (LANGUAGES.get(lang_code) or {}).get("name")
        except Exception:
            pass

        # Create master script
        ms_result = await db.execute(
            select(MasterScript).where(
                MasterScript.program_id == program_id,
                MasterScript.language_code == lang_code,
            )
        )
        ms = ms_result.scalar_one_or_none()
        if not ms:
            ms = MasterScript(
                id=str(uuid.uuid4()),
                program_id=program_id,
                language_code=lang_code,
                language_name=language_name,
            )
            db.add(ms)
            await db.flush()

        track_meta = {**meta, "target_language": lang_code, "cloud_job_id": job_id}
        track = Track(
            id=str(uuid.uuid4()),
            program_id=program_id,
            type="subtitle",
            language_code=lang_code,
            language_name=language_name,
            stage="TRANSCRIBED",
            status="Ready for Translation",
            job_id=job_id,
            master_script_id=ms.id,
            meta=track_meta,
        )
        db.add(track)

        # Upload to cloud
        bucket_name = meta.get("cloud_bucket") or config.OMEGA_JOBS_BUCKET
        prefix = meta.get("cloud_prefix") or config.OMEGA_JOBS_PREFIX
        paths = GcsJobPaths(bucket=bucket_name, prefix=prefix, job_id=job_id)
        await asyncio.to_thread(upload_json, Path(skeleton_path), paths.skeleton_blob)

        job_payload = {
            "id": job_id,
            "file_stem": original_stem,
            "target_language": lang_code,
            "program_profile": meta.get("program_profile") or "standard",
            "glossary_terms": meta.get("glossary_terms") or [],
            "meta": track_meta,
            "created_at": publisher.iso_now(),
        }
        temp_job_json = config.VAULT_DATA / f"{job_id}_cloud_job.json"
        with open(temp_job_json, "w") as f:
            json.dump(job_payload, f, indent=2)
        await asyncio.to_thread(upload_json, temp_job_json, paths.job_blob)
        temp_job_json.unlink(missing_ok=True)

        created_tracks.append({
            "track_id": track.id, "language_code": lang_code, "job_id": job_id,
        })

    # Update program mode
    meta["ingest_mode"] = "auto"
    program.meta = meta
    program.updated_at = datetime.now()
    await db.commit()

    return {
        "success": True,
        "program_id": program_id,
        "tracks_created": len(created_tracks),
        "tracks": created_tracks,
        "message": f"Started translation for {len(created_tracks)} language(s)",
    }


@router.get("/programs/{program_id}/tracks")
async def get_program_tracks(program_id: str, db: AsyncSession = Depends(get_db)):
    """Get all tracks for a program."""
    result = await db.execute(select(Track).where(Track.program_id == program_id))
    tracks = [_track_to_dict(t) for t in result.scalars().all()]
    _hydrate_track_job_ids(tracks)
    _hydrate_track_file_paths(tracks)
    return tracks


@router.post("/programs/{program_id}/tracks")
async def add_track(
    program_id: str,
    data: dict,
    db: AsyncSession = Depends(get_db),
    _admin=Depends(admin_required),
):
    """Add a new track to a program."""
    import uuid
    from gcs_jobs import new_job_id

    result = await db.execute(select(Program).where(Program.id == program_id))
    program = result.scalar_one_or_none()
    if not program:
        raise HTTPException(status_code=404, detail="Program not found")

    track_type = str(data.get("type", "subtitle") or "subtitle").strip().lower() or "subtitle"
    language_code = str(data.get("language_code", "is") or "is").strip().lower() or "is"
    voice_id = data.get("voice_id")
    depends_on = data.get("depends_on")

    # Check for duplicate
    existing = await db.execute(
        select(Track).where(
            Track.program_id == program_id,
            Track.language_code == language_code,
            Track.type == track_type,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"{language_code} {track_type} track already exists")

    original_stem = Path(program.original_filename or "").stem or program_id[:8]
    job_id = new_job_id(f"{original_stem}_{language_code}")

    if track_type == "subtitle":
        # Ensure master script
        ms_result = await db.execute(
            select(MasterScript).where(
                MasterScript.program_id == program_id,
                MasterScript.language_code == language_code,
            )
        )
        ms = ms_result.scalar_one_or_none()
        if not ms:
            ms = MasterScript(
                id=str(uuid.uuid4()),
                program_id=program_id,
                language_code=language_code,
            )
            db.add(ms)
            await db.flush()

        track = Track(
            id=str(uuid.uuid4()),
            program_id=program_id,
            type="subtitle",
            language_code=language_code,
            stage="TRANSCRIBED",
            status="Ready for translation",
            job_id=job_id,
            master_script_id=ms.id,
            meta={"original_filename": program.original_filename, "created_at": datetime.now().isoformat()},
        )
    else:
        # Dub track
        if not depends_on:
            sub_result = await db.execute(
                select(Track).where(
                    Track.program_id == program_id,
                    Track.type == "subtitle",
                    Track.language_code == language_code,
                    Track.stage.in_(["COMPLETE", "COMPLETED", "DELIVERED"]),
                )
            )
            subtitle_track = sub_result.scalar_one_or_none()
            if subtitle_track:
                depends_on = subtitle_track.id
            else:
                raise HTTPException(status_code=400, detail=f"No completed subtitle track in {language_code}")

        track = Track(
            id=str(uuid.uuid4()),
            program_id=program_id,
            type="dub",
            language_code=language_code,
            stage="PENDING_DUB",
            status="Waiting for dubbing",
            job_id=job_id,
            voice_id=voice_id,
            depends_on=depends_on,
            meta={"original_filename": program.original_filename, "depends_on": depends_on, "voice_id": voice_id},
        )

    db.add(track)
    await db.commit()

    return {
        "success": True,
        "track_id": track.id,
        "job_id": job_id,
        "message": f"Created {language_code} {track_type} track",
    }


@router.get("/thumbnails/{program_id}")
async def get_thumbnail(program_id: str, db: AsyncSession = Depends(get_db)):
    """Serve program thumbnail."""
    result = await db.execute(select(Program).where(Program.id == program_id))
    program = result.scalar_one_or_none()
    if not program:
        raise HTTPException(status_code=404, detail="Program not found")

    thumbnail_path = program.thumbnail_path
    if thumbnail_path and Path(thumbnail_path).exists():
        return FileResponse(thumbnail_path, media_type="image/jpeg")

    raise HTTPException(status_code=404, detail="No thumbnail")


# ---- Master-script detail (consumed by ProgramDetailView.tsx) ----

@router.get("/master-scripts/{script_id}")
async def get_master_script(script_id: str, db: AsyncSession = Depends(get_db)):
    """Return a single master-script by ID."""
    result = await db.execute(select(MasterScript).where(MasterScript.id == script_id))
    ms = result.scalar_one_or_none()
    if not ms:
        raise HTTPException(status_code=404, detail="Master script not found")

    meta = _normalize_meta(ms.meta) if ms.meta else {}
    return {
        "id": ms.id,
        "program_id": ms.program_id,
        "language_code": ms.language_code,
        "language_name": ms.language_name,
        "state": ms.state,
        "version": ms.version,
        "locked_at": ms.locked_at.isoformat() if ms.locked_at else None,
        "locked_by": ms.locked_by,
        "created_at": ms.created_at.isoformat() if ms.created_at else None,
        "updated_at": ms.updated_at.isoformat() if ms.updated_at else None,
        "meta": meta,
    }
