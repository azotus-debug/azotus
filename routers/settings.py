import json
import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete

import config
from db import get_db
from models import MinistryProfile, DeliveryProfile, DropzoneRecipe, SystemState, Track
from routers import admin_required

logger = logging.getLogger("OmegaFastAPI")

router = APIRouter(tags=["Settings"])


# ---- helpers ----

def _ministry_to_dict(m: MinistryProfile) -> dict:
    languages = []
    if m.languages:
        try:
            languages = json.loads(m.languages)
        except Exception:
            languages = m.languages if isinstance(m.languages, list) else []
    return {
        "id": m.id,
        "name": m.name,
        "slug": m.slug,
        "languages": languages,
        "default_delivery_id": m.default_delivery_id,
        "terminology": m.terminology,
        "style": m.style,
        "watch_folder": m.watch_folder,
        "workflow": m.workflow,
        "created_at": m.created_at.isoformat() if m.created_at else None,
        "updated_at": m.updated_at.isoformat() if m.updated_at else None,
    }


def _delivery_profile_to_dict(p: DeliveryProfile) -> dict:
    outputs = []
    if p.outputs:
        try:
            outputs = json.loads(p.outputs)
        except Exception:
            outputs = p.outputs if isinstance(p.outputs, list) else []
    return {
        "id": p.id,
        "name": p.name,
        "slug": p.slug,
        "outputs": outputs,
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }


def _dropzone_to_dict(r: DropzoneRecipe) -> dict:
    languages = []
    if r.languages:
        try:
            languages = json.loads(r.languages)
        except Exception:
            languages = r.languages if isinstance(r.languages, list) else []
    return {
        "id": r.id,
        "name": r.name,
        "folder_name": r.folder_name,
        "ministry_id": r.ministry_id,
        "languages": languages,
        "delivery_id": r.delivery_id,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


# =========================================================================
# Ministries
# =========================================================================

@router.get("/api/v2/ministries")
async def list_ministries(db: AsyncSession = Depends(get_db)):
    try:
        result = await db.execute(select(MinistryProfile))
        return [_ministry_to_dict(m) for m in result.scalars().all()]
    except Exception as e:
        logger.error(f"Failed to list ministries: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/v2/ministries")
async def create_ministry(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _admin=Depends(admin_required),
):
    try:
        data = await request.json()
        if not data.get("name") or not data.get("slug"):
            return JSONResponse({"error": "name and slug are required"}, status_code=400)

        profile_id = str(uuid.uuid4())
        languages = data.get("languages", [])
        m = MinistryProfile(
            id=profile_id,
            name=data["name"],
            slug=data["slug"],
            languages=json.dumps(languages) if isinstance(languages, list) else str(languages),
            default_delivery_id=data.get("default_delivery_id"),
            terminology=data.get("terminology"),
            style=data.get("style"),
            watch_folder=data.get("watch_folder"),
            workflow=data.get("workflow", "standard"),
            created_at=datetime.utcnow(),
        )
        db.add(m)
        await db.commit()
        return {"id": profile_id, "ok": True}
    except Exception as e:
        logger.error(f"Failed to create ministry: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.put("/api/v2/ministries/{profile_id}")
async def update_ministry(
    profile_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _admin=Depends(admin_required),
):
    try:
        data = await request.json()
        result = await db.execute(select(MinistryProfile).where(MinistryProfile.id == profile_id))
        m = result.scalar_one_or_none()
        if not m:
            return JSONResponse({"error": "Ministry not found"}, status_code=404)

        for key, val in data.items():
            if key == "languages" and isinstance(val, list):
                val = json.dumps(val)
            if hasattr(m, key):
                setattr(m, key, val)
        m.updated_at = datetime.utcnow()
        await db.commit()
        return {"ok": True}
    except Exception as e:
        logger.error(f"Failed to update ministry: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.delete("/api/v2/ministries/{profile_id}")
async def delete_ministry(
    profile_id: str,
    db: AsyncSession = Depends(get_db),
    _admin=Depends(admin_required),
):
    try:
        await db.execute(delete(MinistryProfile).where(MinistryProfile.id == profile_id))
        await db.commit()
        return {"ok": True}
    except Exception as e:
        logger.error(f"Failed to delete ministry: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


# =========================================================================
# Delivery Profiles
# =========================================================================

@router.get("/api/v2/deliveries/profiles")
async def list_delivery_profiles(db: AsyncSession = Depends(get_db)):
    try:
        result = await db.execute(select(DeliveryProfile))
        return [_delivery_profile_to_dict(p) for p in result.scalars().all()]
    except Exception as e:
        logger.error(f"Failed to list delivery profiles: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/v2/deliveries/profiles")
async def create_delivery_profile(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _admin=Depends(admin_required),
):
    try:
        data = await request.json()
        if not data.get("name") or not data.get("slug"):
            return JSONResponse({"error": "name and slug are required"}, status_code=400)

        profile_id = str(uuid.uuid4())
        outputs = data.get("outputs", [])
        p = DeliveryProfile(
            id=profile_id,
            name=data["name"],
            slug=data["slug"],
            outputs=json.dumps(outputs) if isinstance(outputs, list) else str(outputs),
            created_at=datetime.utcnow(),
        )
        db.add(p)
        await db.commit()
        return {"id": profile_id, "ok": True}
    except Exception as e:
        logger.error(f"Failed to create delivery profile: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.put("/api/v2/deliveries/profiles/{profile_id}")
async def update_delivery_profile(
    profile_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _admin=Depends(admin_required),
):
    try:
        data = await request.json()
        result = await db.execute(select(DeliveryProfile).where(DeliveryProfile.id == profile_id))
        p = result.scalar_one_or_none()
        if not p:
            return JSONResponse({"error": "Delivery profile not found"}, status_code=404)

        for key, val in data.items():
            if key == "outputs" and isinstance(val, list):
                val = json.dumps(val)
            if hasattr(p, key):
                setattr(p, key, val)
        await db.commit()
        return {"ok": True}
    except Exception as e:
        logger.error(f"Failed to update delivery profile: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.delete("/api/v2/deliveries/profiles/{profile_id}")
async def delete_delivery_profile(
    profile_id: str,
    db: AsyncSession = Depends(get_db),
    _admin=Depends(admin_required),
):
    try:
        await db.execute(delete(DeliveryProfile).where(DeliveryProfile.id == profile_id))
        await db.commit()
        return {"ok": True}
    except Exception as e:
        logger.error(f"Failed to delete delivery profile: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


# =========================================================================
# Dropzones
# =========================================================================

@router.get("/api/v2/dropzones")
async def list_dropzones(db: AsyncSession = Depends(get_db)):
    try:
        result = await db.execute(select(DropzoneRecipe))
        return [_dropzone_to_dict(r) for r in result.scalars().all()]
    except Exception as e:
        logger.error(f"Failed to list drop zone recipes: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/v2/dropzones")
async def create_dropzone(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _admin=Depends(admin_required),
):
    try:
        data = await request.json()
        if not data.get("name") or not data.get("folder_name") or not data.get("ministry_id"):
            return JSONResponse(
                {"error": "name, folder_name, and ministry_id are required"}, status_code=400
            )

        recipe_id = str(uuid.uuid4())
        languages = data.get("languages", [])
        r = DropzoneRecipe(
            id=recipe_id,
            name=data["name"],
            folder_name=data["folder_name"],
            ministry_id=data["ministry_id"],
            languages=json.dumps(languages) if isinstance(languages, list) else str(languages),
            delivery_id=data.get("delivery_id"),
            created_at=datetime.utcnow(),
        )
        db.add(r)
        await db.commit()
        return {"id": recipe_id, "ok": True}
    except Exception as e:
        logger.error(f"Failed to create drop zone recipe: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.put("/api/v2/dropzones/{recipe_id}")
async def update_dropzone(
    recipe_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _admin=Depends(admin_required),
):
    try:
        data = await request.json()
        result = await db.execute(select(DropzoneRecipe).where(DropzoneRecipe.id == recipe_id))
        r = result.scalar_one_or_none()
        if not r:
            return JSONResponse({"error": "Drop zone recipe not found"}, status_code=404)

        for key, val in data.items():
            if key == "languages" and isinstance(val, list):
                val = json.dumps(val)
            if hasattr(r, key):
                setattr(r, key, val)
        await db.commit()
        return {"ok": True}
    except Exception as e:
        logger.error(f"Failed to update drop zone recipe: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.delete("/api/v2/dropzones/{recipe_id}")
async def delete_dropzone(
    recipe_id: str,
    db: AsyncSession = Depends(get_db),
    _admin=Depends(admin_required),
):
    try:
        await db.execute(delete(DropzoneRecipe).where(DropzoneRecipe.id == recipe_id))
        await db.commit()
        return {"ok": True}
    except Exception as e:
        logger.error(f"Failed to delete drop zone recipe: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


# =========================================================================
# Pipeline Stats, Languages, Voices
# =========================================================================

@router.get("/api/v2/pipeline/stats")
async def pipeline_stats(db: AsyncSession = Depends(get_db)):
    try:
        result = await db.execute(select(Track))
        all_tracks = result.scalars().all()

        stage_counts: dict[str, int] = {}
        blocked_count = 0
        active_count = 0
        complete_stages = {"COMPLETE", "COMPLETED", "DELIVERED"}
        blocked_stages = {"AWAITING_REVIEW", "AWAITING_APPROVAL", "FAILED", "DEAD"}

        for t in all_tracks:
            stage = (t.stage or "UNKNOWN").upper()
            stage_counts[stage] = stage_counts.get(stage, 0) + 1
            if stage in blocked_stages:
                blocked_count += 1
            elif stage not in complete_stages:
                active_count += 1

        completed_today = sum(1 for t in all_tracks if (t.stage or "").upper() in complete_stages)
        needs_attention = (
            stage_counts.get("AWAITING_REVIEW", 0)
            + stage_counts.get("AWAITING_APPROVAL", 0)
            + stage_counts.get("FAILED", 0)
            + stage_counts.get("DEAD", 0)
        )

        return {
            "total_active": len(all_tracks),
            "blocked": blocked_count,
            "active": active_count,
            "needs_attention": needs_attention,
            "stage_counts": stage_counts,
            "stages": [{"stage": s, "count": c} for s, c in stage_counts.items()],
        }
    except Exception as e:
        logger.error(f"Pipeline stats error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/v2/languages")
async def list_languages():
    from profiles import LANGUAGES, LANGUAGE_POLICIES

    languages = []
    for code, lang in LANGUAGES.items():
        policy = LANGUAGE_POLICIES.get(code, {"mode": "sub", "voice": "alloy"})
        languages.append({
            "code": code,
            "name": lang["name"],
            "default_mode": policy["mode"],
            "default_voice": policy["voice"],
        })

    languages.sort(key=lambda x: x["name"])
    return {"languages": languages}


@router.get("/api/v2/voices")
async def list_voices():
    voices = [
        {"id": "alloy", "name": "Alloy", "description": "Neutral, balanced voice"},
        {"id": "echo", "name": "Echo", "description": "Male, clear and articulate"},
        {"id": "fable", "name": "Fable", "description": "Warm, expressive British accent"},
        {"id": "onyx", "name": "Onyx", "description": "Deep, authoritative male voice"},
        {"id": "nova", "name": "Nova", "description": "Friendly, conversational female"},
        {"id": "shimmer", "name": "Shimmer", "description": "Soft, gentle female voice"},
    ]
    return {"voices": voices}


# =========================================================================
# App Settings (key-value in system_state)
# =========================================================================

@router.get("/api/v2/settings")
async def get_settings(db: AsyncSession = Depends(get_db)):
    try:
        result = await db.execute(
            select(SystemState).where(SystemState.key.like("setting_%"))
        )
        rows = result.scalars().all()
        settings = {}
        for row in rows:
            key = row.key.replace("setting_", "", 1)
            try:
                settings[key] = json.loads(row.value) if row.value else None
            except (json.JSONDecodeError, TypeError):
                settings[key] = row.value
        return settings
    except Exception as e:
        logger.error(f"Failed to get settings: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.patch("/api/v2/settings")
async def update_settings(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _admin=Depends(admin_required),
):
    try:
        data = await request.json()
        if not data or not isinstance(data, dict):
            return JSONResponse({"error": "Request body must be a JSON object"}, status_code=400)

        for key, val in data.items():
            db_key = f"setting_{key}"
            db_val = json.dumps(val) if not isinstance(val, str) else val
            result = await db.execute(select(SystemState).where(SystemState.key == db_key))
            existing = result.scalar_one_or_none()
            if existing:
                existing.value = db_val
            else:
                db.add(SystemState(key=db_key, value=db_val))

        await db.commit()

        # Return updated settings
        result = await db.execute(
            select(SystemState).where(SystemState.key.like("setting_%"))
        )
        rows = result.scalars().all()
        updated = {}
        for row in rows:
            k = row.key.replace("setting_", "", 1)
            try:
                updated[k] = json.loads(row.value) if row.value else None
            except (json.JSONDecodeError, TypeError):
                updated[k] = row.value
        return updated
    except Exception as e:
        logger.error(f"Failed to update settings: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)
