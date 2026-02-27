"""
Async transition helpers for FastAPI routers.

Wraps transition_service.execute_transition() for use with async SQLAlchemy
ORM objects (Track model instances managed by FastAPI's async session).

Usage in a router:
    from routers._transition_helpers import validate_and_set_stage

    @router.post("/tracks/{track_id}/approve")
    async def approve_track(track_id: str, db: AsyncSession = Depends(get_db)):
        track = ...  # fetch track
        await validate_and_set_stage(
            track, db,
            from_stage=track.stage,
            to_stage="FINALIZED",
            status="Approved",
            worker_id="api:approve",
            reason="User approved track",
        )
        return {"ok": True}
"""

import logging
from datetime import datetime
from typing import Optional

from fastapi import HTTPException

from state_machine import InvalidTransitionError
from transition_service import execute_transition, TransitionError

logger = logging.getLogger("OmegaFastAPI")


async def validate_and_set_stage(
    track,
    db,
    from_stage: str,
    to_stage: str,
    status: Optional[str] = None,
    progress: Optional[float] = None,
    worker_id: str = "api",
    reason: Optional[str] = None,
    skip_validation: bool = False,
    processing_step: Optional[str] = None,
):
    """
    Validate a stage transition via the transition service, then apply it
    to an ORM Track object and commit.

    Raises HTTPException(409) if the transition is invalid.
    Raises HTTPException(500) on unexpected errors.
    """
    job_id = track.job_id or str(track.id)
    job_stem = track.job_id or str(track.id)

    try:
        execute_transition(
            job_id=job_id,
            job_stem=job_stem,
            from_stage=from_stage,
            to_stage=to_stage,
            processing_step=processing_step,
            worker_id=worker_id,
            reason=reason,
            skip_validation=skip_validation,
        )
    except InvalidTransitionError as e:
        raise HTTPException(
            status_code=409,
            detail=f"Invalid transition: {from_stage} -> {to_stage}. "
                   f"Valid targets: {e.valid_targets}",
        )
    except TransitionError as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Apply to ORM object
    track.stage = to_stage
    if status is not None:
        track.status = status
    if progress is not None:
        track.progress = progress
    track.updated_at = datetime.utcnow()
    await db.commit()
