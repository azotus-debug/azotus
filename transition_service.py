"""
Transition Service - Unified Stage Transition Management
=========================================================

This module provides a single entry point for ALL stage transitions in the
Omega Subtitle Workflow system. It combines:

1. Validation (from state_machine.py) - Ensures transitions follow valid paths
2. Audit Logging (from omega_db.py) - Records all transitions for traceability
3. Error Handling - Graceful degradation when audit logging fails

Usage:
    from transition_service import execute_transition, get_transition_history

    # Execute a validated transition with audit logging
    result = execute_transition(
        job_id="track-uuid-123",
        job_stem="cbnjd011326cc_is",
        from_stage="TRANSCRIBED",
        to_stage="CLOUD_TRANSLATING",
        processing_step="translate_cloud",
        worker_id="cloud_worker_1",
        reason="Starting cloud translation"
    )

    # Get transition history for a job
    history = get_transition_history(job_stem="cbnjd011326cc_is")

Author: Omega System
Last Updated: January 2026
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

# Import validation from state_machine
from state_machine import (
    InvalidTransitionError,
    JobStage,
    ProcessingStep,
    record_transition,
    can_transition,
    legacy_to_new,
    new_to_legacy,
    get_valid_transitions as _get_valid_transitions,
)

# Import audit logging from omega_db
import omega_db

# Import event publisher (optional - graceful fallback if not available)
try:
    from event_publisher import publish_stage_change, publish_job_failed, publish_job_completed, is_enabled as events_enabled
    _EVENTS_AVAILABLE = True
except ImportError:
    _EVENTS_AVAILABLE = False
    def publish_stage_change(*args, **kwargs): return False
    def publish_job_failed(*args, **kwargs): return False
    def publish_job_completed(*args, **kwargs): return False
    def events_enabled(): return False

logger = logging.getLogger(__name__)


class TransitionError(Exception):
    """Raised when a transition fails for any reason."""

    def __init__(self, message: str, job_id: str = None, from_stage: str = None,
                 to_stage: str = None, cause: Exception = None):
        self.job_id = job_id
        self.from_stage = from_stage
        self.to_stage = to_stage
        self.cause = cause
        super().__init__(message)


def _resolve_track_id(job_id: Optional[str], job_stem: Optional[str]) -> Optional[str]:
    """
    Resolve the canonical track UUID for audit logging.

    execute_transition() callers may pass either a track ID or a job stem as
    `job_id`; this helper normalizes to the actual track id used by the DB.
    """
    candidates = []
    if job_id:
        candidates.append(str(job_id))
    if job_stem and str(job_stem) not in candidates:
        candidates.append(str(job_stem))

    for candidate in candidates:
        try:
            direct_track = omega_db.get_track(candidate)
            if direct_track and direct_track.get("id"):
                return str(direct_track["id"])
        except Exception as exc:
            logger.debug("Track lookup by id failed for %s: %s", candidate, exc)

        try:
            by_job = omega_db.get_track_by_job(candidate)
            if by_job and by_job.get("id"):
                return str(by_job["id"])
        except Exception as exc:
            logger.debug("Track lookup by job_id failed for %s: %s", candidate, exc)

    return None


def execute_transition(
    job_id: str,
    job_stem: str,
    from_stage: str,
    to_stage: str,
    processing_step: Optional[str] = None,
    worker_id: Optional[str] = None,
    reason: Optional[str] = None,
    skip_validation: bool = False,
) -> Dict[str, Any]:
    """
    Execute and record a stage transition.

    This is the single entry point for ALL stage transitions in the system.
    It performs validation (unless skipped) and records the transition to
    the audit log.

    Args:
        job_id: The job/track identifier (e.g., track UUID or job ID)
        job_stem: The job stem/file identifier (e.g., "cbnjd011326cc_is")
        from_stage: The current/previous stage (legacy string like "TRANSCRIBED"
                    or new JobStage value)
        to_stage: The target stage to transition to
        processing_step: Optional sub-step within PROCESSING stage
                        (e.g., "translate_cloud", "transcribe", "burn")
        worker_id: Optional identifier for the worker/process performing the
                   transition (for debugging and audit)
        reason: Optional human-readable reason for the transition
        skip_validation: If True, skips state machine validation. Use only for
                        self-healing paths or emergency overrides. Default False.

    Returns:
        Dict containing transition details:
        {
            "success": bool,
            "job_id": str,
            "job_stem": str,
            "from_stage": str,           # Normalized stage
            "to_stage": str,             # Normalized stage
            "from_stage_legacy": str,    # Original input
            "to_stage_legacy": str,      # Original input
            "processing_step": str|None,
            "worker_id": str|None,
            "reason": str|None,
            "timestamp": str,            # ISO format
            "validation_skipped": bool,
            "audit_logged": bool,
            "audit_error": str|None,     # Error message if audit failed
        }

    Raises:
        InvalidTransitionError: If validation is enabled and the transition
                               is not allowed by the state machine
        TransitionError: If a critical error occurs during transition

    Examples:
        # Standard transition with full validation
        >>> result = execute_transition(
        ...     job_id="track-uuid-123",
        ...     job_stem="cbnjd011326cc_is",
        ...     from_stage="TRANSCRIBED",
        ...     to_stage="CLOUD_TRANSLATING",
        ...     processing_step="translate_cloud",
        ...     worker_id="omega_manager",
        ...     reason="Starting cloud translation pipeline"
        ... )

        # Self-healing transition (skips validation)
        >>> result = execute_transition(
        ...     job_id="track-uuid-456",
        ...     job_stem="cbnjd011326cc_nl",
        ...     from_stage="DEAD",
        ...     to_stage="QUEUED",
        ...     skip_validation=True,
        ...     reason="Self-healing: resetting stuck job"
        ... )
    """
    timestamp = datetime.utcnow().isoformat() + "Z"

    # Initialize result structure
    result = {
        "success": False,
        "job_id": job_id,
        "job_stem": job_stem,
        "from_stage": from_stage,
        "to_stage": to_stage,
        "from_stage_legacy": from_stage if isinstance(from_stage, str) else None,
        "to_stage_legacy": to_stage if isinstance(to_stage, str) else None,
        "processing_step": processing_step,
        "worker_id": worker_id,
        "reason": reason,
        "timestamp": timestamp,
        "validation_skipped": skip_validation,
        "audit_logged": False,
        "audit_error": None,
    }

    # Convert processing_step string to ProcessingStep enum if provided
    processing_step_enum = None
    if processing_step:
        try:
            processing_step_enum = ProcessingStep(processing_step)
        except ValueError:
            # Not a valid ProcessingStep enum value, keep as string for audit
            logger.debug(f"Processing step '{processing_step}' is not a standard ProcessingStep enum value")

    # Step 1: Validate transition (unless skipped)
    if not skip_validation:
        try:
            transition_record = record_transition(
                job_id=job_id,
                from_stage=from_stage,
                to_stage=to_stage,
                processing_step=processing_step_enum,
                worker_id=worker_id,
                reason=reason,
            )

            # Update result with normalized values from validation
            result["from_stage"] = transition_record["from_stage"]
            result["to_stage"] = transition_record["to_stage"]
            result["timestamp"] = transition_record["timestamp"]

        except InvalidTransitionError as e:
            logger.error(
                f"Invalid transition for {job_stem}: {from_stage} -> {to_stage}. "
                f"Valid targets: {e.valid_targets}"
            )
            raise
        except ValueError as e:
            logger.error(f"Invalid stage value for {job_stem}: {e}")
            raise TransitionError(
                f"Invalid stage value: {e}",
                job_id=job_id,
                from_stage=from_stage,
                to_stage=to_stage,
                cause=e,
            )
    else:
        # When validation is skipped, we still normalize stages for logging
        logger.warning(
            f"Skipping validation for transition {job_stem}: {from_stage} -> {to_stage} "
            f"(reason: {reason or 'not provided'})"
        )
        try:
            job_stage_from, _ = legacy_to_new(from_stage) if isinstance(from_stage, str) else (from_stage, None)
            job_stage_to, _ = legacy_to_new(to_stage) if isinstance(to_stage, str) else (to_stage, None)
            result["from_stage"] = job_stage_from.value if hasattr(job_stage_from, 'value') else str(job_stage_from)
            result["to_stage"] = job_stage_to.value if hasattr(job_stage_to, 'value') else str(job_stage_to)
        except Exception:
            # Keep original values if normalization fails
            pass

    # Step 2: Write to audit log (always attempted, errors are non-fatal)
    try:
        track_id = _resolve_track_id(job_id=job_id, job_stem=job_stem)
        if not track_id:
            error_msg = (
                f"Skipped audit log for {job_stem}: unable to resolve track_id "
                f"(job_id={job_id})"
            )
            logger.warning(error_msg)
            result["audit_error"] = error_msg
        else:
            omega_db.log_stage_transition(
                track_id=track_id,
                job_stem=job_stem,
                from_stage=from_stage,
                to_stage=to_stage,
                processing_step=processing_step,
                worker_id=worker_id,
                reason=reason,
            )
            result["audit_logged"] = True
            logger.debug(f"Audit log recorded for {job_stem}: {from_stage} -> {to_stage}")

    except Exception as e:
        # Audit logging failure is non-fatal - log the error but continue
        error_msg = f"Failed to write audit log for {job_stem}: {e}"
        logger.warning(error_msg)
        result["audit_error"] = str(e)
        # Don't raise - this is a graceful degradation

    # Step 3: Publish event (optional, non-fatal)
    result["event_published"] = False
    if _EVENTS_AVAILABLE and events_enabled():
        try:
            # Determine which event to publish based on target stage
            normalized_to = result["to_stage"].lower() if result.get("to_stage") else to_stage.lower()

            if normalized_to in ("failed", "dead"):
                publish_job_failed(
                    job_id=job_stem,
                    error_message=reason or "Unknown error",
                    from_stage=result.get("from_stage", from_stage),
                    worker_id=worker_id,
                )
            elif normalized_to in ("delivered", "completed"):
                publish_job_completed(
                    job_id=job_stem,
                    from_stage=result.get("from_stage", from_stage),
                    worker_id=worker_id,
                )
            else:
                publish_stage_change(
                    job_id=job_stem,
                    from_stage=result.get("from_stage", from_stage),
                    to_stage=result.get("to_stage", to_stage),
                    processing_step=processing_step,
                    worker_id=worker_id,
                    reason=reason,
                )
            result["event_published"] = True
            logger.debug(f"Event published for {job_stem}: {from_stage} -> {to_stage}")
        except Exception as e:
            logger.warning(f"Failed to publish event for {job_stem}: {e}")
            # Non-fatal - continue without event

    # Mark as successful
    result["success"] = True

    logger.info(
        f"Transition executed: {job_stem} [{from_stage} -> {to_stage}]"
        f"{' (validation skipped)' if skip_validation else ''}"
        f"{' (audit failed)' if result['audit_error'] else ''}"
        f"{' (event published)' if result.get('event_published') else ''}"
    )

    return result


def apply_transition(
    job_id: str,
    job_stem: str,
    from_stage: str,
    to_stage: str,
    processing_step: Optional[str] = None,
    worker_id: Optional[str] = None,
    reason: Optional[str] = None,
    skip_validation: bool = False,
    # DB fields to also update via omega_db.update_job_via_track:
    status: Optional[str] = None,
    progress: Optional[float] = None,
    meta: Optional[dict] = None,
    **db_kwargs,
) -> Dict[str, Any]:
    """
    Validate a stage transition AND write to DB in one atomic call.

    This is the recommended entry point for ALL stage transitions that also
    need to persist to the database. It combines:
    1. execute_transition() — validates, audits, publishes events
    2. omega_db.update_job_via_track() — persists the stage change to DB

    Use this instead of calling execute_transition() + update_job_via_track()
    separately, which risks the DB write succeeding without validation.

    Args:
        job_id: The job/track identifier (UUID or stem)
        job_stem: The job stem/file identifier (e.g., "cbnjd011326cc_is")
        from_stage: The current stage (legacy string or JobStage value)
        to_stage: The target stage to transition to
        processing_step: Optional sub-step within PROCESSING stage
        worker_id: Optional identifier for the worker performing the transition
        reason: Optional human-readable reason for the transition
        skip_validation: If True, skips state machine validation
        status: Optional status string for the DB record
        progress: Optional progress percentage (0-100)
        meta: Optional meta dict to merge into the track's meta
        **db_kwargs: Additional keyword args passed to update_job_via_track
                     (e.g., target_language, subtitle_style, output_path)

    Returns:
        Dict containing transition details (same as execute_transition)

    Raises:
        InvalidTransitionError: If validation is enabled and transition is invalid
        TransitionError: If a critical error occurs
    """
    # Step 1: Validate + audit + publish event
    result = execute_transition(
        job_id=job_id,
        job_stem=job_stem,
        from_stage=from_stage,
        to_stage=to_stage,
        processing_step=processing_step,
        worker_id=worker_id,
        reason=reason,
        skip_validation=skip_validation,
    )

    # Step 2: Persist to DB
    try:
        omega_db.update_job_via_track(
            job_stem,
            stage=to_stage,
            status=status,
            progress=progress,
            meta=meta,
            **db_kwargs,
        )
        result["db_written"] = True
    except Exception as e:
        logger.error(f"DB write failed for {job_stem} ({from_stage} -> {to_stage}): {e}")
        result["db_written"] = False
        result["db_error"] = str(e)
        raise

    return result


def get_transition_history(
    job_id: Optional[str] = None,
    job_stem: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """
    Get stage transition history from the audit log.

    Wraps omega_db.get_stage_transitions() with additional error handling
    and result normalization.

    Args:
        job_id: Filter by job/track ID (the UUID)
        job_stem: Filter by job stem (e.g., "cbnjd011326cc_is")
        limit: Maximum number of records to return (default 100)

    Returns:
        List of transition records as dictionaries, ordered by created_at DESC.
        Each record contains:
        {
            "id": int,
            "track_id": str,
            "job_stem": str,
            "from_stage": str|None,
            "to_stage": str,
            "processing_step": str|None,
            "worker_id": str|None,
            "reason": str|None,
            "created_at": str,
        }

        Returns empty list if no records found or on error.

    Examples:
        # Get all transitions for a job stem
        >>> history = get_transition_history(job_stem="cbnjd011326cc_is")
        >>> for transition in history:
        ...     print(f"{transition['from_stage']} -> {transition['to_stage']}")

        # Get recent transitions (all jobs)
        >>> recent = get_transition_history(limit=10)
    """
    try:
        # Use track_id parameter for job_id (that's how omega_db names it)
        rows = omega_db.get_stage_transitions(
            track_id=job_id,
            job_stem=job_stem,
            limit=limit,
        )

        # Normalize results to ensure consistent dict format
        result = []
        for row in rows:
            if isinstance(row, dict):
                result.append(row)
            elif hasattr(row, 'keys'):
                # Row object (dict-like)
                result.append(dict(row))
            elif isinstance(row, (list, tuple)):
                # Plain tuple/list - convert to dict with expected keys
                result.append({
                    "id": row[0] if len(row) > 0 else None,
                    "track_id": row[1] if len(row) > 1 else None,
                    "job_stem": row[2] if len(row) > 2 else None,
                    "from_stage": row[3] if len(row) > 3 else None,
                    "to_stage": row[4] if len(row) > 4 else None,
                    "processing_step": row[5] if len(row) > 5 else None,
                    "worker_id": row[6] if len(row) > 6 else None,
                    "reason": row[7] if len(row) > 7 else None,
                    "created_at": row[8] if len(row) > 8 else None,
                })
            else:
                logger.warning(f"Unexpected row type in transition history: {type(row)}")
                continue

        return result

    except Exception as e:
        logger.error(f"Failed to get transition history: {e}")
        return []


def validate_transition_path(from_stage: str, to_stage: str) -> bool:
    """
    Check if a transition is valid without executing it.

    Useful for UI validation or pre-flight checks.

    Args:
        from_stage: The current stage (legacy or new format)
        to_stage: The target stage (legacy or new format)

    Returns:
        True if the transition is valid, False otherwise

    Examples:
        >>> validate_transition_path("TRANSCRIBED", "CLOUD_TRANSLATING")
        True
        >>> validate_transition_path("COMPLETED", "QUEUED")
        False
    """
    try:
        job_stage_from, _ = legacy_to_new(from_stage) if isinstance(from_stage, str) else (from_stage, None)
        job_stage_to, _ = legacy_to_new(to_stage) if isinstance(to_stage, str) else (to_stage, None)

        # Handle case where from_stage is already a JobStage enum
        if isinstance(job_stage_from, str):
            job_stage_from = JobStage(job_stage_from.lower())
        if isinstance(job_stage_to, str):
            job_stage_to = JobStage(job_stage_to.lower())

        return can_transition(job_stage_from, job_stage_to)
    except (ValueError, KeyError):
        return False


def get_valid_next_stages(current_stage: str) -> List[str]:
    """
    Get list of valid target stages from the current stage.

    Args:
        current_stage: The current stage (legacy or new format)

    Returns:
        List of valid target stage names (in new format)

    Examples:
        >>> get_valid_next_stages("QUEUED")
        ['processing', 'failed']
        >>> get_valid_next_stages("COMPLETED")
        []
    """
    try:
        job_stage, _ = legacy_to_new(current_stage) if isinstance(current_stage, str) else (current_stage, None)

        if isinstance(job_stage, str):
            job_stage = JobStage(job_stage.lower())

        valid_stages = _get_valid_transitions(job_stage)
        return [s.value for s in valid_stages]
    except (ValueError, KeyError):
        return []


# =============================================================================
# CONVENIENCE FUNCTIONS FOR COMMON TRANSITIONS
# =============================================================================

def transition_to_processing(
    job_id: str,
    job_stem: str,
    from_stage: str,
    processing_step: str,
    worker_id: Optional[str] = None,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Convenience function for transitioning to PROCESSING stage.

    Automatically sets the processing_step parameter.
    """
    return execute_transition(
        job_id=job_id,
        job_stem=job_stem,
        from_stage=from_stage,
        to_stage="processing",
        processing_step=processing_step,
        worker_id=worker_id,
        reason=reason,
    )


def transition_to_reviewing(
    job_id: str,
    job_stem: str,
    from_stage: str,
    worker_id: Optional[str] = None,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Convenience function for transitioning to REVIEWING stage.
    """
    return execute_transition(
        job_id=job_id,
        job_stem=job_stem,
        from_stage=from_stage,
        to_stage="reviewing",
        worker_id=worker_id,
        reason=reason or "Ready for human review",
    )


def transition_to_failed(
    job_id: str,
    job_stem: str,
    from_stage: str,
    error_message: str,
    worker_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Convenience function for transitioning to FAILED stage.

    Always includes the error message as the reason.
    """
    return execute_transition(
        job_id=job_id,
        job_stem=job_stem,
        from_stage=from_stage,
        to_stage="failed",
        worker_id=worker_id,
        reason=f"Error: {error_message}",
    )


def reset_failed_job(
    job_id: str,
    job_stem: str,
    worker_id: Optional[str] = None,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Reset a failed job back to QUEUED for retry.

    This is a valid transition in the state machine (FAILED -> QUEUED).
    """
    return execute_transition(
        job_id=job_id,
        job_stem=job_stem,
        from_stage="failed",
        to_stage="queued",
        worker_id=worker_id,
        reason=reason or "Resetting failed job for retry",
    )


def record_error_transition(
    job_stem: str,
    from_stage: str,
    to_stage: str,
    worker_id: str,
    error_message: str,
    error_type: str = "permanent",
) -> Dict[str, Any]:
    """
    Convenience function for recording error transitions (DEAD/FAILED).

    This is a shortcut for execute_transition with skip_validation=True
    and appropriate reason formatting.

    Args:
        job_stem: The job stem identifier
        from_stage: Current stage
        to_stage: Target stage (usually "DEAD" or "FAILED")
        worker_id: Worker performing the transition
        error_message: The error message
        error_type: Type of error ("permanent", "transient", "stall")

    Returns:
        Transition record dict
    """
    reason = f"{error_type} error: {error_message}"

    return execute_transition(
        job_id=job_stem,
        job_stem=job_stem,
        from_stage=from_stage,
        to_stage=to_stage,
        worker_id=worker_id,
        reason=reason,
        skip_validation=True,  # Error paths bypass normal validation
    )


# =============================================================================
# MODULE INITIALIZATION
# =============================================================================

# Ensure logging is configured when module is imported
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    ))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
