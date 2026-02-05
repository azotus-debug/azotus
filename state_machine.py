import enum
import logging
from datetime import datetime
from typing import Optional, Dict, Any, Tuple, List, Set

logger = logging.getLogger(__name__)

# =============================================================================
# EXCEPTIONS
# =============================================================================

class InvalidTransitionError(Exception):
    """Raised when an invalid state transition is attempted."""
    def __init__(self, from_stage: str, to_stage: str, valid_targets: List[str] = None):
        self.from_stage = from_stage
        self.to_stage = to_stage
        self.valid_targets = valid_targets or []
        msg = f"Invalid transition: {from_stage} → {to_stage}"
        if valid_targets:
            msg += f". Valid targets: {valid_targets}"
        super().__init__(msg)


class StaleStateError(Exception):
    """Raised when the expected state doesn't match the current state (optimistic lock failure)."""
    def __init__(self, job_id: str, expected: str, actual: str):
        self.job_id = job_id
        self.expected = expected
        self.actual = actual
        super().__init__(f"Stale state for job {job_id}: expected {expected}, found {actual}")


# =============================================================================
# PIPELINE STAGE ENUMS (Job Processing Flow)
# =============================================================================

class JobStage(str, enum.Enum):
    """
    Core pipeline stages for job processing.

    This is the PRIMARY state machine for the Omega workflow.
    All stage transitions MUST go through the transition() function.
    """
    QUEUED = "queued"           # Job created, waiting for processing
    PROCESSING = "processing"   # Active work in progress (see ProcessingStep for details)
    REVIEWING = "reviewing"     # Human review required
    FINALIZING = "finalizing"   # Generating final outputs (SRT, burned video)
    DELIVERED = "delivered"     # Successfully completed
    FAILED = "failed"           # Terminal error state

    @classmethod
    def from_legacy(cls, legacy_stage: str) -> "JobStage":
        """Map legacy stage strings to new JobStage enum."""
        mapping = {
            # Direct mappings
            "QUEUED": cls.QUEUED,
            "queued": cls.QUEUED,
            "DELIVERED": cls.DELIVERED,
            "delivered": cls.DELIVERED,
            "FAILED": cls.FAILED,
            "failed": cls.FAILED,
            "DEAD": cls.FAILED,

            # Processing stages (all map to PROCESSING with different steps)
            "INGEST": cls.PROCESSING,
            "TRANSCRIBED": cls.PROCESSING,
            "TRANSLATING": cls.PROCESSING,
            "TRANSLATING_CLOUD_SUBMITTED": cls.PROCESSING,
            "CLOUD_TRANSLATING": cls.PROCESSING,
            "CLOUD_DETECTING_MUSIC": cls.PROCESSING,
            "CLOUD_REVIEWING": cls.PROCESSING,
            "CLOUD_DONE": cls.PROCESSING,
            "BURNING": cls.PROCESSING,

            # Review stages
            "REVIEWING": cls.REVIEWING,
            "REVIEWED": cls.REVIEWING,
            "AWAITING_REVIEW": cls.REVIEWING,

            # Finalize stages
            "FINALIZING": cls.FINALIZING,
            "FINALIZED": cls.FINALIZING,

            # Completed
            "COMPLETED": cls.DELIVERED,
            "COMPLETE": cls.DELIVERED,
        }
        return mapping.get(legacy_stage, cls.QUEUED)


class ProcessingStep(str, enum.Enum):
    """
    Sub-steps within the PROCESSING stage.

    Provides granular visibility into what the system is doing
    while keeping the core state machine simple.
    """
    INGEST = "ingest"               # Moving file, extracting audio
    # Multimodal (Azotus) pipeline steps
    PROXY_GEN = "proxy_gen"         # Generate 360p vision proxy
    VISION_SCAN = "vision_scan"     # Gemini Flash video analysis
    OCR_LAYOUT = "ocr_layout"       # Danger zone detection
    # Standard pipeline steps
    TRANSCRIBE = "transcribe"       # ElevenLabs transcription
    TRANSLATE_SUBMIT = "translate_submit"   # Submitting to cloud
    TRANSLATE_CLOUD = "translate_cloud"     # Cloud translation in progress
    MUSIC_DETECT = "music_detect"   # Detecting music segments
    EDIT = "edit"                   # AI editing/review
    BURN = "burn"                   # FFmpeg video burning
    DELIVER = "deliver"             # Final delivery/upload

    @classmethod
    def from_legacy(cls, legacy_stage: str) -> Optional["ProcessingStep"]:
        """Map legacy stage strings to ProcessingStep."""
        mapping = {
            "INGEST": cls.INGEST,
            # Multimodal (Azotus) steps
            "PROXY_GEN": cls.PROXY_GEN,
            "VISION_SCAN": cls.VISION_SCAN,
            "OCR_LAYOUT": cls.OCR_LAYOUT,
            # Standard pipeline
            "TRANSCRIBED": cls.TRANSCRIBE,  # Just finished transcription
            "TRANSLATING": cls.TRANSLATE_SUBMIT,
            "TRANSLATING_CLOUD_SUBMITTED": cls.TRANSLATE_SUBMIT,
            "CLOUD_TRANSLATING": cls.TRANSLATE_CLOUD,
            "CLOUD_DETECTING_MUSIC": cls.MUSIC_DETECT,
            "CLOUD_REVIEWING": cls.EDIT,
            "BURNING": cls.BURN,
        }
        return mapping.get(legacy_stage)


# =============================================================================
# PIPELINE STATE MACHINE (Enforced Transitions)
# =============================================================================

# Valid transitions between JobStage values
VALID_TRANSITIONS: Dict[JobStage, Set[JobStage]] = {
    JobStage.QUEUED: {JobStage.PROCESSING, JobStage.FAILED},
    JobStage.PROCESSING: {JobStage.REVIEWING, JobStage.FINALIZING, JobStage.FAILED},
    JobStage.REVIEWING: {JobStage.PROCESSING, JobStage.FINALIZING, JobStage.FAILED},
    JobStage.FINALIZING: {JobStage.DELIVERED, JobStage.FAILED},
    JobStage.DELIVERED: set(),  # Terminal state - no transitions out
    JobStage.FAILED: {JobStage.QUEUED},  # Can retry failed jobs
}


def can_transition(from_stage: JobStage, to_stage: JobStage) -> bool:
    """Check if a transition is valid without throwing an exception."""
    if from_stage == to_stage:
        return True  # No-op transitions are allowed
    valid_targets = VALID_TRANSITIONS.get(from_stage, set())
    return to_stage in valid_targets


def validate_transition(from_stage: JobStage, to_stage: JobStage) -> None:
    """
    Validate that a state transition is allowed.

    Raises:
        InvalidTransitionError: If the transition is not valid
    """
    if from_stage == to_stage:
        return  # No-op, always allowed

    valid_targets = VALID_TRANSITIONS.get(from_stage, set())
    if to_stage not in valid_targets:
        raise InvalidTransitionError(
            from_stage.value,
            to_stage.value,
            [s.value for s in valid_targets]
        )


def get_valid_transitions(from_stage: JobStage) -> List[JobStage]:
    """Get list of valid target stages from the current stage."""
    return list(VALID_TRANSITIONS.get(from_stage, set()))


def _normalize_stage(stage: Any) -> JobStage:
    """
    Convert a stage value to JobStage enum.

    Handles:
    - JobStage enum values (passed through)
    - Legacy stage strings (e.g., "TRANSCRIBED", "CLOUD_TRANSLATING")
    - New stage strings (e.g., "processing", "reviewing")
    """
    if isinstance(stage, JobStage):
        return stage
    if isinstance(stage, str):
        # Try direct enum value match first (new format)
        try:
            return JobStage(stage.lower())
        except ValueError:
            pass
        # Fall back to legacy mapping
        return JobStage.from_legacy(stage)
    raise ValueError(f"Cannot convert {type(stage).__name__} to JobStage: {stage}")


def record_transition(
    job_id: str,
    from_stage: Any,
    to_stage: Any,
    processing_step: Optional[ProcessingStep] = None,
    worker_id: Optional[str] = None,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Validate and record a state transition.

    This is the primary entry point for transitioning job stages. It:
    1. Normalizes legacy string stages to JobStage enum
    2. Validates the transition is allowed
    3. Logs the transition for debugging
    4. Returns a record suitable for audit logging

    Args:
        job_id: The job identifier (e.g., "cbnjd011326cc_is")
        from_stage: Current stage (JobStage enum or legacy string like "TRANSCRIBED")
        to_stage: Target stage (JobStage enum or legacy string like "CLOUD_TRANSLATING")
        processing_step: Optional sub-step within PROCESSING stage
        worker_id: Optional identifier for the worker performing the transition
        reason: Optional human-readable reason for the transition

    Returns:
        Dict with transition details for audit logging:
        {
            "job_id": str,
            "from_stage": str,
            "to_stage": str,
            "from_stage_legacy": str,  # Original input if it was a string
            "to_stage_legacy": str,    # Original input if it was a string
            "processing_step": str or None,
            "worker_id": str or None,
            "reason": str or None,
            "timestamp": str (ISO format),
            "valid": bool
        }

    Raises:
        InvalidTransitionError: If the transition is not allowed
        ValueError: If stage values cannot be converted to JobStage

    Example:
        >>> transition_record = record_transition(
        ...     job_id="cbnjd011326cc_is",
        ...     from_stage="TRANSCRIBED",  # legacy string
        ...     to_stage=JobStage.PROCESSING,  # or "CLOUD_TRANSLATING"
        ...     processing_step=ProcessingStep.TRANSLATE_CLOUD,
        ...     worker_id="cloud_worker_1",
        ...     reason="Starting cloud translation"
        ... )
    """
    timestamp = datetime.utcnow().isoformat() + "Z"

    # Preserve original inputs for audit trail
    from_stage_legacy = from_stage if isinstance(from_stage, str) else None
    to_stage_legacy = to_stage if isinstance(to_stage, str) else None

    # Normalize to JobStage enum
    from_stage_enum = _normalize_stage(from_stage)
    to_stage_enum = _normalize_stage(to_stage)

    # Build the transition record
    record = {
        "job_id": job_id,
        "from_stage": from_stage_enum.value,
        "to_stage": to_stage_enum.value,
        "from_stage_legacy": from_stage_legacy,
        "to_stage_legacy": to_stage_legacy,
        "processing_step": processing_step.value if processing_step else None,
        "worker_id": worker_id,
        "reason": reason,
        "timestamp": timestamp,
        "valid": False,  # Will be set to True if validation passes
    }

    # Validate the transition (raises InvalidTransitionError if invalid)
    validate_transition(from_stage_enum, to_stage_enum)
    record["valid"] = True

    # Log the successful transition
    step_info = f" (step={processing_step.value})" if processing_step else ""
    worker_info = f" by {worker_id}" if worker_id else ""
    reason_info = f": {reason}" if reason else ""

    logger.info(
        f"Transition [{job_id}] {from_stage_enum.value} -> {to_stage_enum.value}"
        f"{step_info}{worker_info}{reason_info}"
    )

    return record


# =============================================================================
# LEGACY STAGE MAPPING (for backward compatibility during migration)
# =============================================================================

def legacy_to_new(legacy_stage: str) -> Tuple[JobStage, Optional[ProcessingStep]]:
    """
    Convert a legacy stage string to (JobStage, ProcessingStep) tuple.

    Used during migration to maintain compatibility with existing data.
    """
    job_stage = JobStage.from_legacy(legacy_stage)
    processing_step = ProcessingStep.from_legacy(legacy_stage)
    return (job_stage, processing_step)


def new_to_legacy(job_stage: JobStage, processing_step: Optional[ProcessingStep] = None) -> str:
    """
    Convert new (JobStage, ProcessingStep) to legacy stage string.

    Used during migration for backward compatibility with code that
    expects legacy stage strings.
    """
    if job_stage == JobStage.QUEUED:
        return "QUEUED"
    elif job_stage == JobStage.FAILED:
        return "DEAD"
    elif job_stage == JobStage.DELIVERED:
        return "COMPLETED"
    elif job_stage == JobStage.REVIEWING:
        return "REVIEWED"
    elif job_stage == JobStage.FINALIZING:
        return "FINALIZING"
    elif job_stage == JobStage.PROCESSING:
        if processing_step:
            step_to_legacy = {
                ProcessingStep.INGEST: "INGEST",
                # Multimodal (Azotus) steps
                ProcessingStep.PROXY_GEN: "PROXY_GEN",
                ProcessingStep.VISION_SCAN: "VISION_SCAN",
                ProcessingStep.OCR_LAYOUT: "OCR_LAYOUT",
                # Standard pipeline
                ProcessingStep.TRANSCRIBE: "TRANSCRIBED",
                ProcessingStep.TRANSLATE_SUBMIT: "TRANSLATING_CLOUD_SUBMITTED",
                ProcessingStep.TRANSLATE_CLOUD: "CLOUD_TRANSLATING",
                ProcessingStep.MUSIC_DETECT: "CLOUD_DETECTING_MUSIC",
                ProcessingStep.EDIT: "CLOUD_REVIEWING",
                ProcessingStep.BURN: "BURNING",
                ProcessingStep.DELIVER: "COMPLETED",
            }
            return step_to_legacy.get(processing_step, "PROCESSING")
        return "PROCESSING"
    return "QUEUED"


# =============================================================================
# CONTENT STATE ENUMS (Master Script / Output Track Quality)
# =============================================================================

class MasterState(str, enum.Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    LOCKED = "locked"

class TrackState(str, enum.Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    RENDERED = "rendered"
    DELIVERED = "delivered"
    LOCKED = "locked"

class ChangeType(str, enum.Enum):
    FORMATTING_ONLY = "formatting_only"
    TEXT_CHANGE_MINOR = "text_change_minor"
    TEXT_CHANGE_MATERIAL = "text_change_material"
    OUTPUT_OVERRIDE = "output_override"

class ChangeImpact(str, enum.Enum):
    SUBTITLE_RERENDER = "subtitle_re-render"
    DUB_RERENDER = "dub_re-render"
    PENDING_RESYNC = "pending_resync"
    OUTPUT_ONLY = "output_only"

# =============================================================================
# STATE MACHINES
# =============================================================================

class MasterScriptStateMachine:
    """
    Manages state transitions for the Master Script.
    States: draft -> approved -> locked
    """
    
    TRANSITIONS = {
        MasterState.DRAFT: {MasterState.APPROVED},
        MasterState.APPROVED: {MasterState.DRAFT, MasterState.LOCKED},
        MasterState.LOCKED: {MasterState.DRAFT},  # Creates new version
    }

    @staticmethod
    def can_transition(current: str, next_state: str) -> bool:
        current_state = MasterState(current)
        try:
            target = MasterState(next_state)
        except ValueError:
            return False
            
        allowed = MasterScriptStateMachine.TRANSITIONS.get(current_state, set())
        return target in allowed

    @staticmethod
    def get_trigger_description(current: str, next_state: str) -> str:
        pair = (MasterState(current), MasterState(next_state))
        if pair == (MasterState.DRAFT, MasterState.APPROVED):
            return "Reviewer/Editor approval"
        if pair == (MasterState.APPROVED, MasterState.DRAFT):
            return "Reviewer requests changes"
        if pair == (MasterState.APPROVED, MasterState.LOCKED):
            return "Operator/System lock (after delivery/TTL)"
        if pair == (MasterState.LOCKED, MasterState.DRAFT):
            return "Client revision request (New Version)"
        return "Unknown transition"


class OutputTrackStateMachine:
    """
    Manages state transitions for Output Tracks (Subtitles/Dubs).
    States: draft -> approved -> rendered -> delivered -> locked
    """
    
    TRANSITIONS = {
        TrackState.DRAFT: {TrackState.APPROVED},
        TrackState.APPROVED: {TrackState.RENDERED},
        TrackState.RENDERED: {TrackState.DELIVERED, TrackState.APPROVED}, # Approved = QC Fail -> Retry
        TrackState.DELIVERED: {TrackState.LOCKED, TrackState.RENDERED},  # Rendered = Revision pre-lock
        TrackState.LOCKED: {TrackState.RENDERED},  # Output-only override
    }

    @staticmethod
    def can_transition(current: str, next_state: str) -> bool:
        current_state = TrackState(current)
        try:
            target = TrackState(next_state)
        except ValueError:
            return False

        allowed = OutputTrackStateMachine.TRANSITIONS.get(current_state, set())
        return target in allowed

# =============================================================================
# DECISION ENGINE
# =============================================================================

class ChangeClassifier:
    """
    Implements the Change Classification Decision Table.
    """
    
    @staticmethod
    def classify_formatting_only() -> Dict[str, Any]:
        """
        Policy: formatting_only
        - Master Ver: No Change
        - Subs: Re-render
        - Dubs: No
        - Approval: Operator
        """
        return {
            "master_version_increment": False,
            "subtitle_rerender": True,
            "dub_rerender": False,
            "pending_resync": False,
            "approval_role": "operator"
        }

    @staticmethod
    def classify_text_change_minor() -> Dict[str, Any]:
        """
        Policy: text_change_minor
        - Master Ver: +1
        - Subs: Re-render
        - Dubs: No (PENDING_RESYNC)
        - Approval: Reviewer
        """
        return {
            "master_version_increment": True,
            "subtitle_rerender": True,
            "dub_rerender": False,
            "pending_resync": True,  # Flag for dubs
            "approval_role": "reviewer"
        }

    @staticmethod
    def classify_text_change_material() -> Dict[str, Any]:
        """
        Policy: text_change_material
        - Master Ver: +1
        - Subs: Re-render
        - Dubs: Re-render
        - Approval: Reviewer
        """
        return {
            "master_version_increment": True,
            "subtitle_rerender": True,
            "dub_rerender": True,
            "pending_resync": False,
            "approval_role": "reviewer"
        }

    @staticmethod
    def classify_output_override() -> Dict[str, Any]:
        """
        Policy: output_override
        - Master Ver: No Change
        - Subs/Dubs: Output Only
        - Approval: Operator
        """
        return {
            "master_version_increment": False,
            "subtitle_rerender": True, # Technically "Output Only" logic applies
            "dub_rerender": True,      # "Output Only"
            "pending_resync": False,
            "approval_role": "operator",
            "output_only": True
        }

    @staticmethod
    def evaluate_change(change_type: str) -> Dict[str, Any]:
        if change_type == ChangeType.FORMATTING_ONLY:
            return ChangeClassifier.classify_formatting_only()
        elif change_type == ChangeType.TEXT_CHANGE_MINOR:
            return ChangeClassifier.classify_text_change_minor()
        elif change_type == ChangeType.TEXT_CHANGE_MATERIAL:
            return ChangeClassifier.classify_text_change_material()
        elif change_type == ChangeType.OUTPUT_OVERRIDE:
            return ChangeClassifier.classify_output_override()
        else:
            # Default to material change for safety
            return ChangeClassifier.classify_text_change_material()

# =============================================================================
# VERSIONING UTILS
# =============================================================================

def calculate_next_output_version(current_output_ver: str, master_ver: int, is_override: bool) -> str:
    """
    Calculates the next output version string.
    Rule: Master version is major, override index is minor.
    Example: Master v1 -> "1.0"
             Override -> "1.1", "1.2"
    """
    try:
        current_major, current_minor = map(int, current_output_ver.split('.'))
    except ValueError:
        current_major, current_minor = 1, 0

    if is_override:
        # If modifying existing output without master change, increment minor
        # Ensure major matches master (though override keeps master same)
        return f"{current_major}.{current_minor + 1}"
    else:
        # If master changed, output matches master major, reset minor
        return f"{master_ver}.0"
