"""
Editor routes — ported from dashboard.py.

Covers:
  /api/surgical/segments   (GET)
  /api/surgical/save       (POST)
  /api/assistant/chat      (POST)
  /api/editor/{job_id}     (GET / POST)
  /api/stream/{job_id}     (GET)
"""

import asyncio
import json
import logging
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, FileResponse

import config
import omega_db
from routers import admin_required

logger = logging.getLogger("OmegaFastAPI")

router = APIRouter(tags=["Editor"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
    return f"{master_version}.0" if master_version else "1.0"


def _read_json(path: Path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Path, data, **kwargs) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, **kwargs)


def _normalize_segments(data):
    """Unwrap common container formats into a plain list of segments."""
    if isinstance(data, dict):
        if "translated_data" in data:
            return data["translated_data"]
        if "segments" in data:
            return data["segments"]
        if "events" in data:
            raw_events = data["events"]
            return [
                {
                    "id": idx + 1,
                    "start": ev["start"],
                    "end": ev["end"],
                    "text": "\n".join(ev.get("lines", [])),
                }
                for idx, ev in enumerate(raw_events)
            ]
    return data  # already a list (or unknown)


def _populate_source_text(segments: list, stem: str) -> None:
    """Merge source_text from SKELETON files into segments (in-place)."""
    try:
        skeleton_path = config.VAULT_DATA / f"{stem}_SKELETON_DONE.json"
        if not skeleton_path.exists():
            skeleton_path = config.VAULT_DATA / f"{stem}_SKELETON.json"
        if not skeleton_path.exists():
            return

        with open(skeleton_path, "r", encoding="utf-8") as f:
            skel_data = json.load(f)

        skel_segs = skel_data.get("segments", []) if isinstance(skel_data, dict) else skel_data
        source_map = {s["id"]: s["text"] for s in skel_segs if "id" in s}

        for seg in segments:
            if "id" in seg and seg["id"] in source_map:
                seg.setdefault("source_text", source_map[seg["id"]])
    except Exception as e:
        logger.warning("Failed to load source text for %s: %s", stem, e)


def _populate_source_text_by_timing(segments: list, stem: str) -> None:
    """Merge source_text from SKELETON using loose timing match (for editor)."""
    try:
        skeleton_path = config.VAULT_DATA / f"{stem}_SKELETON_DONE.json"
        if not skeleton_path.exists():
            skeleton_path = config.VAULT_DATA / f"{stem}_SKELETON.json"
        if not skeleton_path.exists():
            return

        with open(skeleton_path, "r", encoding="utf-8") as f:
            skel_data = json.load(f)

        skel_segs = skel_data.get("segments", []) if isinstance(skel_data, dict) else skel_data
        skel_map = {}
        for s in skel_segs:
            key = round(float(s.get("start", 0)), 1)
            skel_map[key] = s.get("text", "")

        for seg in segments:
            if not seg.get("source_text"):
                start_key = round(float(seg.get("start", 0)), 1)
                if start_key in skel_map:
                    seg["source_text"] = skel_map[start_key]
    except Exception as e:
        logger.warning("Failed to populate source_text from skeleton: %s", e)


# ---------------------------------------------------------------------------
# 1. GET /api/surgical/segments
# ---------------------------------------------------------------------------

@router.get("/api/surgical/segments")
async def get_segments(stem: str = Query(...), _=Depends(admin_required)):
    if not stem:
        raise HTTPException(status_code=400, detail="Missing stem")

    paths = [
        config.SRT_DIR / f"{stem}_normalized.json",
        config.SRT_DIR / f"DONE_{stem}_normalized.json",
        config.TRANSLATED_DONE_DIR / f"{stem}_APPROVED.json",
        config.TRANSLATED_DONE_DIR / f"{stem}_ICELANDIC.json",
        config.TRANSLATED_DONE_DIR / f"{stem}_is.json",
    ]

    for p in paths:
        if not await asyncio.to_thread(p.exists):
            continue
        try:
            data = await asyncio.to_thread(_read_json, p)
            data = _normalize_segments(data)
            _populate_source_text(data, stem)
            return {"segments": data, "source": p.name}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    raise HTTPException(status_code=404, detail="No editable file found")


# ---------------------------------------------------------------------------
# 2. POST /api/surgical/save
# ---------------------------------------------------------------------------

@router.post("/api/surgical/save")
async def save_segments(request: Request, _=Depends(admin_required)):
    body = await request.json()
    stem = body.get("stem")
    segments = body.get("segments")

    if not stem or not segments:
        raise HTTPException(status_code=400, detail="Missing data")

    try:
        output_path = config.TRANSLATED_DONE_DIR / f"{stem}_APPROVED.json"

        # Backup
        if await asyncio.to_thread(output_path.exists):
            backup_path = output_path.with_suffix(f".json.bak_{int(time.time())}")
            await asyncio.to_thread(shutil.copy, output_path, backup_path)

        # Save
        payload = {
            "segments": segments,
            "meta": {
                "edited_via": "dashboard",
                "edited_at": datetime.now().isoformat(),
            },
        }
        await asyncio.to_thread(_write_json, output_path, payload)

        # Auto-finalize
        from workers import finalizer

        job = await asyncio.to_thread(omega_db.get_job_via_track, stem)
        lang = job.get("target_language", "is") if job else "is"

        srt_path, normalized_path = await asyncio.to_thread(
            finalizer.finalize, output_path, target_language=lang
        )

        await asyncio.to_thread(
            omega_db.update_job_via_track,
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

        return {"success": True}

    except Exception as e:
        logger.error("Surgical Save Failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# 3. POST /api/assistant/chat
# ---------------------------------------------------------------------------

@router.post("/api/assistant/chat")
async def assistant_chat(request: Request, _=Depends(admin_required)):
    try:
        body = await request.json()
        job_id = body.get("job_id")
        message = body.get("message")
        history = body.get("history", [])

        if not job_id or not message:
            raise HTTPException(status_code=400, detail="Missing job_id or message")

        from workers import assistant

        result = await asyncio.to_thread(
            assistant.chat_with_job, job_id, message, history
        )

        if result.get("edits_performed"):
            from workers import finalizer

            stem = job_id
            approved_path = config.VAULT_DATA / f"{stem}_APPROVED.json"

            if await asyncio.to_thread(approved_path.exists):
                job = await asyncio.to_thread(omega_db.get_job_via_track, stem)
                lang = job.get("target_language", "is") if job else "is"

                srt_path, normalized_path = await asyncio.to_thread(
                    finalizer.finalize, approved_path, target_language=lang
                )

                await asyncio.to_thread(
                    omega_db.update_job_via_track,
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

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Assistant Endpoint Failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# 4. GET/POST /api/editor/{job_id}
# ---------------------------------------------------------------------------

@router.get("/api/editor/{job_id}")
async def editor_get(job_id: str, _=Depends(admin_required)):
    try:
        from workers import assistant

        file_path, data = await asyncio.to_thread(assistant._load_job_file, job_id)

        if not file_path or not data:
            raise HTTPException(status_code=404, detail="Job file not found")

        # Normalize segments
        segments = []
        if isinstance(data, dict):
            if "segments" in data:
                segments = data["segments"]
            elif "events" in data:
                segments = data["events"]
            elif "translated_data" in data:
                segments = data.get("translated_data", [])
        elif isinstance(data, list):
            segments = data

        # Convert lines -> text
        for seg in segments:
            if "lines" in seg and "text" not in seg:
                seg["text"] = "\n".join(seg["lines"])

        # Populate source_text from skeleton (timing-based)
        _populate_source_text_by_timing(segments, job_id)

        track = await asyncio.to_thread(omega_db.get_track_by_job, job_id)

        return {
            "job_id": job_id,
            "file_path": str(file_path),
            "segments": segments,
            "graphic_zones": data.get("graphic_zones", []) if isinstance(data, dict) else [],
            "history": data.get("history", []) if isinstance(data, dict) else [],
            "track": track,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Editor GET Failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/editor/{job_id}")
async def editor_post(job_id: str, request: Request, _=Depends(admin_required)):
    try:
        from workers import assistant, finalizer

        file_path, _existing = await asyncio.to_thread(assistant._load_job_file, job_id)
        if not file_path:
            raise HTTPException(status_code=404, detail="Original file not found, cannot save")

        payload = await request.json()
        new_segments = payload.get("segments")
        if not isinstance(new_segments, list):
            raise HTTPException(status_code=400, detail="Invalid segments format")

        # 1. Backup
        await asyncio.to_thread(assistant._backup_file, file_path)

        # 2. Read current full data
        current_full_data = await asyncio.to_thread(_read_json, file_path)

        original_segments = []
        if isinstance(current_full_data, dict):
            original_segments = current_full_data.get("segments") or []
        elif isinstance(current_full_data, list):
            original_segments = current_full_data

        # -- timing helpers --
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
            if (
                not isinstance(target.get("words"), list) or not target.get("words")
            ) and isinstance(source.get("words"), list):
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
                seg.pop("words", None)
                seg.pop("source_text", None)

        # 3. Merge back into full data structure
        now_iso = datetime.now().isoformat()
        if isinstance(current_full_data, dict):
            current_full_data["segments"] = new_segments
            current_full_data["graphic_zones"] = payload.get("graphic_zones", [])
            current_full_data["history"] = payload.get("history", [])
            current_full_data["normalized_for_review"] = True
            current_full_data["normalized_at"] = now_iso
            if "meta" not in current_full_data:
                current_full_data["meta"] = {}
            current_full_data["meta"]["last_manual_edit"] = now_iso
        else:
            current_full_data = {
                "segments": new_segments,
                "graphic_zones": payload.get("graphic_zones", []),
                "history": payload.get("history", []),
                "normalized_for_review": True,
                "normalized_at": now_iso,
                "meta": {"last_manual_edit": now_iso},
            }

        await asyncio.to_thread(_write_json, file_path, current_full_data)

        # 4. Generate SRT
        job = await asyncio.to_thread(omega_db.get_job_via_track, job_id)
        lang = job.get("target_language", "is") if job else "is"

        srt_path = config.SRT_DIR / f"{job_id}.srt"
        await asyncio.to_thread(
            finalizer.segments_to_srt, new_segments, srt_path, target_language=lang
        )

        # 5. Create normalized JSON
        normalized_path = config.SRT_DIR / f"{job_id}_normalized.json"
        normalized_payload = {
            "events": [
                {
                    "start": seg.get("start"),
                    "end": seg.get("end"),
                    "lines": (
                        seg.get("text", "").split("\n")
                        if isinstance(seg.get("text"), str)
                        else seg.get("lines", [])
                    ),
                }
                for seg in new_segments
            ],
            "language": lang,
        }
        await asyncio.to_thread(_write_json, normalized_path, normalized_payload)

        # 6. Update DB
        await asyncio.to_thread(
            omega_db.update_job_via_track,
            job_id,
            stage="FINALIZED",
            status="Manual Edit Saved",
            progress=90.0,
            meta={
                "manual_edit_at": now_iso,
                "srt_path": str(srt_path),
                "normalized_path": str(normalized_path),
            },
        )

        # 7. Handle change_type (versioning + script edit logging)
        change_type = payload.get("change_type")
        if change_type:
            change_type = str(change_type).strip()
            change_summary = payload.get("change_summary") or payload.get("summary")
            change_author = payload.get("change_author") or payload.get("author")

            track = await asyncio.to_thread(omega_db.get_track_by_job, job_id)
            master_script_id = track.get("master_script_id") if track else None
            new_version = None

            if master_script_id:
                await asyncio.to_thread(
                    omega_db.log_script_edit,
                    master_script_id=master_script_id,
                    track_id=track.get("id") if track else None,
                    change_type=str(change_type),
                    summary=change_summary,
                    author=change_author,
                )

                if change_type in {"text_change_minor", "text_change_material"}:
                    master = await asyncio.to_thread(
                        omega_db.get_master_script, master_script_id
                    )
                    new_version = (master.get("version", 1) if master else 1) + 1
                    await asyncio.to_thread(
                        omega_db.update_master_script,
                        master_script_id,
                        version=new_version,
                    )

                    if change_type == "text_change_minor" and track:
                        program_id = track.get("program_id")
                        language_code = track.get("language_code")
                        if program_id and language_code:
                            all_tracks = await asyncio.to_thread(
                                omega_db.get_tracks_for_program, program_id
                            )
                            for output in all_tracks:
                                if output.get("language_code") != language_code:
                                    continue
                                if output.get("type") == "dub":
                                    await asyncio.to_thread(
                                        omega_db.update_track,
                                        output.get("id"),
                                        pending_resync=True,
                                    )

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

                await asyncio.to_thread(
                    omega_db.update_track, track.get("id"), **track_updates
                )

        return {"success": True}

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Editor POST Failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# 5. GET /api/stream/{job_id}
# ---------------------------------------------------------------------------

@router.get("/api/stream/{job_id}")
async def stream_video(job_id: str):
    """
    Stream the video file for a job.
    Supports range requests via FileResponse (starlette).

    Priority:
      0. Proxy file ({job_id}_PROXY.mp4, original_stem_PROXY, derived_stem_PROXY)
      1. meta.vault_path
      2. meta.source_path
      3. VAULT_VIDEOS / {job_id}.{ext}
    """
    try:
        job = await asyncio.to_thread(omega_db.get_job_via_track, job_id)
        meta = job.get("meta", {}) if job else {}
        video_path: Optional[Path] = None

        # Priority 0: Proxy files
        candidates = [
            config.PROXIES_DIR / f"{job_id}_PROXY.mp4",
        ]
        if meta and meta.get("original_stem"):
            candidates.append(
                config.PROXIES_DIR / f"{meta['original_stem']}_PROXY.mp4"
            )
        # Derived stem (STEM-TIMESTAMP -> STEM)
        try:
            derived_stem = job_id.rsplit("-", 1)[0].upper()
            candidates.append(config.PROXIES_DIR / f"{derived_stem}_PROXY.mp4")
        except Exception:
            pass
        # Case-insensitive variants
        candidates.append(config.PROXIES_DIR / f"{job_id.upper()}_PROXY.mp4")
        if meta and meta.get("original_stem"):
            candidates.append(
                config.PROXIES_DIR / f"{meta['original_stem'].upper()}_PROXY.mp4"
            )

        logger.info(
            "Stream candidates for %s: %s",
            job_id,
            [str(p) for p in candidates],
        )

        for c in candidates:
            if await asyncio.to_thread(c.exists):
                video_path = c
                break

        # Priority 1: vault_path
        if not video_path:
            vault_path = meta.get("vault_path")
            if vault_path:
                candidate = Path(vault_path)
                if await asyncio.to_thread(candidate.exists):
                    video_path = candidate

        # Priority 2: source_path
        if not video_path:
            source_path = meta.get("source_path")
            if source_path:
                candidate = Path(source_path)
                if await asyncio.to_thread(candidate.exists):
                    video_path = candidate

        # Priority 3: VAULT_VIDEOS by extension
        if not video_path:
            for ext in (".mp4", ".mov", ".mkv", ".avi", ".m4v"):
                candidate = config.VAULT_VIDEOS / f"{job_id}{ext}"
                if await asyncio.to_thread(candidate.exists):
                    video_path = candidate
                    break

        if not video_path:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "Video file not found",
                    "checked": [str(p) for p in candidates],
                },
            )

        # Determine media type
        suffix = video_path.suffix.lower()
        media_types = {
            ".mp4": "video/mp4",
            ".mov": "video/quicktime",
            ".mkv": "video/x-matroska",
            ".avi": "video/x-msvideo",
            ".m4v": "video/mp4",
        }
        media_type = media_types.get(suffix, "video/mp4")

        return FileResponse(
            path=str(video_path),
            media_type=media_type,
            filename=video_path.name,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Stream Failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
