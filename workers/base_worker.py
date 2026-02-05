"""
base_worker.py - Stateless Worker Base Class for Omega Pipeline

This module provides the foundation for event-driven, stateless workers.
Workers are pure functions: they claim a job, process it, and release it.
No in-memory state between jobs. Can run multiple instances for scaling.

Design Principles:
1. Stateless: Each job is fully self-contained; no cross-job memory
2. Idempotent: Safe to retry any job; partial progress is recoverable
3. Observable: Comprehensive logging, metrics, and event emission
4. Resilient: Automatic retry for transient errors, clear error classification
5. Compatible: Existing workers can subclass without breaking

Usage:
    from workers.base_worker import StatelessWorker, ProcessResult

    class TranscriberWorker(StatelessWorker):
        def process(self, track: Dict[str, Any]) -> ProcessResult:
            # Do transcription work
            return ProcessResult(success=True, next_stage="TRANSCRIBED")

    # Run continuously
    worker = TranscriberWorker(worker_type="transcriber")
    worker.run_forever()

    # Or run once (for Cloud Run Jobs)
    result = worker.run_once()

Author: Omega TV Engineering
Created: 2026-02-01
"""

from __future__ import annotations

import logging
import os
import signal
import threading
import time
import uuid
from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, TypeVar

# Local imports (will be available when used within the Omega project)
try:
    import omega_db
except ImportError:
    omega_db = None  # type: ignore

try:
    import omega_db_pg
except ImportError:
    omega_db_pg = None  # type: ignore

logger = logging.getLogger("OmegaWorker")

# Type variable for generic worker typing
T = TypeVar("T", bound="StatelessWorker")


# =============================================================================
# Error Classification
# =============================================================================


class TransientError(Exception):
    """
    Retry-able error that may succeed on subsequent attempts.

    Examples:
    - Network timeout
    - Rate limit exceeded
    - Temporary service unavailable
    - Database connection lost
    - Cloud API throttling

    Workers should raise this for errors that are expected to be temporary.
    The base class will handle retry logic with exponential backoff.
    """

    def __init__(
        self,
        message: str,
        retry_after: Optional[float] = None,
        original_error: Optional[Exception] = None,
    ):
        """
        Initialize a transient error.

        Args:
            message: Human-readable error description
            retry_after: Optional hint for how long to wait before retry (seconds)
            original_error: The underlying exception that caused this error
        """
        super().__init__(message)
        self.retry_after = retry_after
        self.original_error = original_error


class PermanentError(Exception):
    """
    Non-retryable error that will fail consistently.

    Examples:
    - Invalid input data
    - Authentication failure (wrong credentials)
    - Missing required configuration
    - Unsupported language/format
    - File not found (after verification)

    Workers should raise this for errors that cannot be fixed by retrying.
    The job will be marked as failed immediately.
    """

    def __init__(
        self,
        message: str,
        error_code: Optional[str] = None,
        original_error: Optional[Exception] = None,
    ):
        """
        Initialize a permanent error.

        Args:
            message: Human-readable error description
            error_code: Optional machine-readable error code for categorization
            original_error: The underlying exception that caused this error
        """
        super().__init__(message)
        self.error_code = error_code
        self.original_error = original_error


class WorkerShutdownRequested(Exception):
    """Raised when the worker should gracefully shut down."""

    pass


# =============================================================================
# Data Classes
# =============================================================================


class ResultStatus(Enum):
    """Status codes for process results."""

    SUCCESS = "success"
    FAILED = "failed"
    RETRY = "retry"
    SKIPPED = "skipped"


@dataclass
class ProcessResult:
    """
    Result of processing a single track/job.

    This is the return type for the `process()` method. It captures the outcome
    and any artifacts or metadata produced during processing.

    Attributes:
        success: Whether processing completed successfully
        next_stage: The stage to transition the track to (e.g., "TRANSCRIBED")
        next_step: The processing step to transition to (e.g., "finalize")
        error: Error message if processing failed
        error_code: Machine-readable error code for categorization
        artifacts: Dict of artifact name -> path/URL produced
        metrics: Dict of metric name -> value for observability
        should_retry: Whether this failure should be retried
        retry_after: Seconds to wait before retry (for transient errors)
    """

    success: bool
    next_stage: Optional[str] = None
    next_step: Optional[str] = None
    error: Optional[str] = None
    error_code: Optional[str] = None
    artifacts: Dict[str, str] = field(default_factory=dict)
    metrics: Dict[str, Any] = field(default_factory=dict)
    should_retry: bool = False
    retry_after: Optional[float] = None

    @classmethod
    def succeeded(
        cls,
        next_stage: Optional[str] = None,
        next_step: Optional[str] = None,
        artifacts: Optional[Dict[str, str]] = None,
        metrics: Optional[Dict[str, Any]] = None,
    ) -> "ProcessResult":
        """Create a successful result."""
        return cls(
            success=True,
            next_stage=next_stage,
            next_step=next_step,
            artifacts=artifacts or {},
            metrics=metrics or {},
        )

    @classmethod
    def failed(
        cls,
        error: str,
        error_code: Optional[str] = None,
        should_retry: bool = False,
        retry_after: Optional[float] = None,
        metrics: Optional[Dict[str, Any]] = None,
    ) -> "ProcessResult":
        """Create a failed result."""
        return cls(
            success=False,
            error=error,
            error_code=error_code,
            should_retry=should_retry,
            retry_after=retry_after,
            metrics=metrics or {},
        )

    @classmethod
    def skipped(
        cls,
        reason: str,
        metrics: Optional[Dict[str, Any]] = None,
    ) -> "ProcessResult":
        """Create a skipped result (job not processed but not failed)."""
        return cls(
            success=True,  # Not a failure
            error=reason,
            error_code="SKIPPED",
            metrics=metrics or {},
        )

    @property
    def status(self) -> ResultStatus:
        """Get the status enum for this result."""
        if self.error_code == "SKIPPED":
            return ResultStatus.SKIPPED
        if self.success:
            return ResultStatus.SUCCESS
        if self.should_retry:
            return ResultStatus.RETRY
        return ResultStatus.FAILED


@dataclass
class WorkerMetrics:
    """
    Metrics collected during worker operation.

    These can be exported to monitoring systems or logged for observability.
    """

    worker_id: str
    worker_type: str
    jobs_processed: int = 0
    jobs_succeeded: int = 0
    jobs_failed: int = 0
    jobs_retried: int = 0
    total_processing_time_ms: float = 0.0
    last_job_id: Optional[str] = None
    last_job_duration_ms: Optional[float] = None
    started_at: datetime = field(default_factory=datetime.utcnow)
    last_heartbeat: Optional[datetime] = None

    def record_job(
        self,
        job_id: str,
        duration_ms: float,
        success: bool,
        retried: bool = False,
    ) -> None:
        """Record metrics for a completed job."""
        self.jobs_processed += 1
        if success:
            self.jobs_succeeded += 1
        else:
            self.jobs_failed += 1
        if retried:
            self.jobs_retried += 1
        self.total_processing_time_ms += duration_ms
        self.last_job_id = job_id
        self.last_job_duration_ms = duration_ms

    @property
    def average_processing_time_ms(self) -> float:
        """Calculate average processing time per job."""
        if self.jobs_processed == 0:
            return 0.0
        return self.total_processing_time_ms / self.jobs_processed

    @property
    def success_rate(self) -> float:
        """Calculate success rate as a percentage."""
        if self.jobs_processed == 0:
            return 100.0
        return (self.jobs_succeeded / self.jobs_processed) * 100.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert metrics to a dictionary for serialization."""
        return {
            "worker_id": self.worker_id,
            "worker_type": self.worker_type,
            "jobs_processed": self.jobs_processed,
            "jobs_succeeded": self.jobs_succeeded,
            "jobs_failed": self.jobs_failed,
            "jobs_retried": self.jobs_retried,
            "total_processing_time_ms": self.total_processing_time_ms,
            "average_processing_time_ms": self.average_processing_time_ms,
            "success_rate": round(self.success_rate, 2),
            "last_job_id": self.last_job_id,
            "last_job_duration_ms": self.last_job_duration_ms,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "last_heartbeat": self.last_heartbeat.isoformat() if self.last_heartbeat else None,
            "uptime_seconds": (datetime.utcnow() - self.started_at).total_seconds() if self.started_at else 0,
        }


@dataclass
class ClaimResult:
    """Result of attempting to claim a track for processing."""

    claimed: bool
    track: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


# =============================================================================
# Stateless Worker Base Class
# =============================================================================


class StatelessWorker(ABC):
    """
    Base class for stateless workers in the Omega pipeline.

    Workers are pure functions: they claim a job, process it, and release it.
    No in-memory state between jobs. Can run multiple instances for scaling.

    Subclasses must implement:
    - process(track) -> ProcessResult: The main processing logic

    Optional hooks:
    - before_process(track): Called before processing starts
    - after_process(track, result): Called after processing completes
    - on_error(track, error): Called when an error occurs

    Thread Safety:
    - The base class is thread-safe for single-threaded operation
    - For multi-threaded workers, subclasses should handle their own locking

    Example:
        class TranscriberWorker(StatelessWorker):
            def process(self, track: Dict[str, Any]) -> ProcessResult:
                audio_path = track.get("audio_path")
                # ... do transcription ...
                return ProcessResult.succeeded(
                    next_stage="TRANSCRIBED",
                    artifacts={"skeleton": skeleton_path}
                )
    """

    def __init__(
        self,
        worker_type: str,
        worker_id: Optional[str] = None,
        lease_minutes: int = 30,
        heartbeat_interval: float = 60.0,
        max_retries: int = 3,
        retry_base_delay: float = 5.0,
        location_id: Optional[str] = None,
    ):
        """
        Initialize the stateless worker.

        Args:
            worker_type: Type identifier for this worker (e.g., "transcriber", "finalizer")
            worker_id: Unique identifier for this worker instance. Auto-generated if not provided.
            lease_minutes: How long to hold a job lease (for heartbeat extension)
            heartbeat_interval: How often to send heartbeats during long operations (seconds)
            max_retries: Maximum number of retries for transient errors
            retry_base_delay: Base delay between retries (will be exponentially increased)
            location_id: Optional location identifier (e.g., "iceland", "virginia")
        """
        self.worker_type = worker_type
        self.worker_id = worker_id or f"{worker_type}-{uuid.uuid4().hex[:8]}"
        self.lease_minutes = lease_minutes
        self.heartbeat_interval = heartbeat_interval
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay
        self.location_id = location_id or os.environ.get("OMEGA_LOCATION", "local")

        # Internal state
        self._shutdown_requested = False
        self._current_track: Optional[Dict[str, Any]] = None
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._heartbeat_stop_event = threading.Event()
        self._metrics = WorkerMetrics(worker_id=self.worker_id, worker_type=self.worker_type)

        # Setup signal handlers for graceful shutdown
        self._setup_signal_handlers()

        logger.info(
            "Worker initialized: %s (type=%s, location=%s, lease=%dm)",
            self.worker_id,
            self.worker_type,
            self.location_id,
            self.lease_minutes,
        )

    def _setup_signal_handlers(self) -> None:
        """Setup signal handlers for graceful shutdown."""
        try:
            signal.signal(signal.SIGTERM, self._signal_handler)
            signal.signal(signal.SIGINT, self._signal_handler)
        except (ValueError, OSError):
            # Signal handlers can only be set in main thread
            pass

    def _signal_handler(self, signum: int, frame: Any) -> None:
        """Handle shutdown signals gracefully."""
        sig_name = signal.Signals(signum).name if hasattr(signal, "Signals") else str(signum)
        logger.info("Received signal %s, requesting graceful shutdown", sig_name)
        self._shutdown_requested = True

    # =========================================================================
    # Abstract Methods (Must be implemented by subclasses)
    # =========================================================================

    @abstractmethod
    def process(self, track: Dict[str, Any]) -> ProcessResult:
        """
        Process a single track. This is the main work method.

        This method should be a pure function: given the same track data,
        it should produce the same result. Side effects (file creation, API calls)
        are expected but should be idempotent where possible.

        Args:
            track: Dictionary containing track/job data. Typical keys:
                - id: Track UUID
                - program_id: Parent program UUID
                - file_stem: Base filename
                - language_code: Target language (e.g., "is", "nl")
                - stage: Current pipeline stage
                - processing_step: Current step within stage
                - meta: Additional metadata dict

        Returns:
            ProcessResult indicating success/failure and next state

        Raises:
            TransientError: For retry-able failures
            PermanentError: For non-retryable failures
        """
        raise NotImplementedError("Subclasses must implement process()")

    # =========================================================================
    # Lifecycle Hooks (Optional, can be overridden)
    # =========================================================================

    def before_process(self, track: Dict[str, Any]) -> None:
        """
        Hook called before processing starts.

        Override this to perform setup actions like:
        - Logging
        - Resource allocation
        - Validation
        - Metrics initialization

        Args:
            track: The track about to be processed
        """
        logger.info(
            "[%s] Starting: track=%s stage=%s",
            self.worker_id,
            track.get("id") or track.get("file_stem"),
            track.get("stage"),
        )

    def after_process(self, track: Dict[str, Any], result: ProcessResult) -> None:
        """
        Hook called after processing completes (success or failure).

        Override this to perform cleanup actions like:
        - Logging results
        - Metrics recording
        - Resource cleanup
        - Notifications

        Args:
            track: The track that was processed
            result: The processing result
        """
        duration_ms = result.metrics.get("duration_ms", 0)
        if result.success:
            logger.info(
                "[%s] Completed: track=%s next_stage=%s duration=%.2fs",
                self.worker_id,
                track.get("id") or track.get("file_stem"),
                result.next_stage,
                duration_ms / 1000,
            )
        else:
            logger.warning(
                "[%s] Failed: track=%s error=%s retry=%s",
                self.worker_id,
                track.get("id") or track.get("file_stem"),
                result.error,
                result.should_retry,
            )

    def on_error(self, track: Dict[str, Any], error: Exception) -> None:
        """
        Hook called when an unhandled error occurs.

        Override this to implement custom error handling like:
        - Error reporting to external services
        - Alerting
        - Custom logging

        Args:
            track: The track being processed when error occurred
            error: The exception that was raised
        """
        logger.exception(
            "[%s] Unhandled error processing track=%s: %s",
            self.worker_id,
            track.get("id") or track.get("file_stem"),
            error,
        )

    # =========================================================================
    # Job Claiming
    # =========================================================================

    def _claim_track_pg(
        self,
        stages: Optional[List[str]] = None,
        processing_steps: Optional[List[str]] = None,
        language_codes: Optional[List[str]] = None,
    ) -> ClaimResult:
        """
        Claim a track using PostgreSQL with optimistic locking.

        This uses the claim_track_atomic() function in PostgreSQL for
        safe concurrent claiming across multiple workers.

        Args:
            stages: List of stages to claim from
            processing_steps: List of processing steps to claim
            language_codes: Optional filter for specific languages

        Returns:
            ClaimResult with claimed track or error
        """
        if omega_db_pg is None:
            return ClaimResult(claimed=False, error="omega_db_pg not available")

        try:
            # First, find an unclaimed track
            conn = omega_db_pg.get_connection()
            try:
                with conn.cursor() as cur:
                    # Build query to find claimable tracks
                    conditions = ["(claimed_by IS NULL OR lease_until < NOW())"]
                    params: List[Any] = []

                    if stages:
                        placeholders = ", ".join(["%s"] * len(stages))
                        conditions.append(f"stage IN ({placeholders})")
                        params.extend(stages)

                    if processing_steps:
                        placeholders = ", ".join(["%s"] * len(processing_steps))
                        conditions.append(f"processing_step IN ({placeholders})")
                        params.extend(processing_steps)

                    if language_codes:
                        placeholders = ", ".join(["%s"] * len(language_codes))
                        conditions.append(f"language_code IN ({placeholders})")
                        params.extend(language_codes)

                    # Add location filter if applicable
                    if self.location_id:
                        conditions.append(
                            "(processing_location IS NULL OR processing_location = %s)"
                        )
                        params.append(self.location_id)

                    where_clause = " AND ".join(conditions)
                    query = f"""
                        SELECT id, version FROM tracks
                        WHERE {where_clause}
                        ORDER BY priority DESC, created_at ASC
                        LIMIT 1
                        FOR UPDATE SKIP LOCKED
                    """

                    cur.execute(query, params)
                    row = cur.fetchone()

                    if not row:
                        return ClaimResult(claimed=False)

                    track_id, version = row

                    # Attempt atomic claim
                    cur.execute(
                        "SELECT claim_track_atomic(%s, %s, %s, %s)",
                        (track_id, self.worker_id, version, self.lease_minutes),
                    )
                    claimed = cur.fetchone()[0]

                    if not claimed:
                        conn.rollback()
                        return ClaimResult(claimed=False, error="Claim failed (version conflict)")

                    conn.commit()

                    # Fetch full track data
                    cur.execute("SELECT * FROM tracks WHERE id = %s", (track_id,))
                    track_row = cur.fetchone()
                    if track_row:
                        columns = [desc[0] for desc in cur.description]
                        track = dict(zip(columns, track_row))
                        return ClaimResult(claimed=True, track=track)

                    return ClaimResult(claimed=False, error="Track not found after claim")

            finally:
                omega_db_pg.release_connection(conn)

        except Exception as e:
            logger.error("PostgreSQL claim error: %s", e)
            return ClaimResult(claimed=False, error=str(e))

    def claim_track(
        self,
        stages: Optional[List[str]] = None,
        processing_steps: Optional[List[str]] = None,
        language_codes: Optional[List[str]] = None,
    ) -> ClaimResult:
        """
        Claim a track for processing with optimistic locking.

        This method attempts to atomically claim a track that is ready for
        processing using PostgreSQL.

        Args:
            stages: List of pipeline stages to claim from (e.g., ["TRANSCRIBED"])
            processing_steps: List of processing steps to claim (e.g., ["translate"])
            language_codes: Optional list of languages to process (e.g., ["is", "nl"])

        Returns:
            ClaimResult containing the claimed track if successful
        """
        if omega_db_pg is None:
            return ClaimResult(claimed=False, error="PostgreSQL backend not available")

        return self._claim_track_pg(stages, processing_steps, language_codes)

    def release_track(
        self,
        track: Dict[str, Any],
        result: ProcessResult,
    ) -> bool:
        """
        Release a track after processing.

        Updates the track state based on the processing result:
        - On success: Transitions to next_stage/next_step
        - On failure: Updates error info and retry count

        Args:
            track: The track that was processed
            result: The processing result

        Returns:
            True if release was successful
        """
        track_id = track.get("id") or track.get("file_stem")

        try:
            update_data: Dict[str, Any] = {
                "claimed_by": None,
                "lease_until": None,
                "worker_id": None,
            }

            if result.success:
                if result.next_stage:
                    update_data["stage"] = result.next_stage
                if result.next_step:
                    update_data["processing_step"] = result.next_step
                update_data["status"] = f"Completed by {self.worker_id}"
                update_data["last_error"] = None
            else:
                update_data["last_error"] = result.error
                update_data["status"] = f"Failed: {result.error}"

                if result.should_retry:
                    # Increment retry count
                    retry_count = (track.get("retry_count") or 0) + 1
                    update_data["retry_count"] = retry_count

                    if result.retry_after:
                        retry_after = datetime.utcnow() + timedelta(seconds=result.retry_after)
                        update_data["retry_after"] = retry_after.isoformat()

            # Update via appropriate backend
            if omega_db is not None:
                omega_db.update_track(track_id, **update_data)
                return True

            return False

        except Exception as e:
            logger.error("Failed to release track %s: %s", track_id, e)
            return False

    # =========================================================================
    # Heartbeat Management
    # =========================================================================

    def _start_heartbeat(self, track: Dict[str, Any]) -> None:
        """Start heartbeat thread for long-running operations."""
        self._heartbeat_stop_event.clear()
        self._current_track = track

        def heartbeat_loop():
            while not self._heartbeat_stop_event.wait(self.heartbeat_interval):
                if self._shutdown_requested:
                    break
                try:
                    self._extend_lease(track)
                    self._metrics.last_heartbeat = datetime.utcnow()
                except Exception as e:
                    logger.warning("Heartbeat failed: %s", e)

        self._heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
        self._heartbeat_thread.start()

    def _stop_heartbeat(self) -> None:
        """Stop the heartbeat thread."""
        self._heartbeat_stop_event.set()
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            self._heartbeat_thread.join(timeout=5.0)
        self._heartbeat_thread = None
        self._current_track = None

    def _extend_lease(self, track: Dict[str, Any]) -> None:
        """Extend the lease on a claimed track."""
        track_id = track.get("id") or track.get("file_stem")
        new_lease = datetime.utcnow() + timedelta(minutes=self.lease_minutes)

        try:
            if omega_db is not None:
                omega_db.update_track(
                    track_id,
                    lease_until=new_lease.isoformat(),
                )
            logger.debug("Extended lease for track %s", track_id)
        except Exception as e:
            logger.warning("Failed to extend lease for %s: %s", track_id, e)

    # =========================================================================
    # Main Execution Methods
    # =========================================================================

    def run_once(
        self,
        stages: Optional[List[str]] = None,
        processing_steps: Optional[List[str]] = None,
        language_codes: Optional[List[str]] = None,
    ) -> Optional[ProcessResult]:
        """
        Process one job and return.

        This is the preferred method for Cloud Run Jobs and similar
        serverless environments where the worker should process a single
        job and then exit.

        Args:
            stages: Pipeline stages to process
            processing_steps: Processing steps to handle
            language_codes: Languages to process

        Returns:
            ProcessResult if a job was processed, None if no job was available
        """
        # Attempt to claim a track
        claim_result = self.claim_track(
            stages=stages,
            processing_steps=processing_steps,
            language_codes=language_codes,
        )

        if not claim_result.claimed or claim_result.track is None:
            logger.debug("No tracks available to process")
            return None

        track = claim_result.track
        return self._process_track(track)

    def run_forever(
        self,
        poll_interval: float = 5.0,
        stages: Optional[List[str]] = None,
        processing_steps: Optional[List[str]] = None,
        language_codes: Optional[List[str]] = None,
        max_iterations: Optional[int] = None,
    ) -> None:
        """
        Main loop: claim -> process -> release -> repeat.

        This is the preferred method for long-running workers that
        continuously process jobs.

        Args:
            poll_interval: Seconds to wait between poll attempts when no work is available
            stages: Pipeline stages to process
            processing_steps: Processing steps to handle
            language_codes: Languages to process
            max_iterations: Optional limit on iterations (for testing)
        """
        logger.info(
            "[%s] Starting continuous processing (poll_interval=%.1fs)",
            self.worker_id,
            poll_interval,
        )

        iterations = 0

        while not self._shutdown_requested:
            if max_iterations is not None and iterations >= max_iterations:
                logger.info("Reached max iterations (%d), stopping", max_iterations)
                break

            try:
                result = self.run_once(
                    stages=stages,
                    processing_steps=processing_steps,
                    language_codes=language_codes,
                )

                if result is None:
                    # No work available, wait before polling again
                    time.sleep(poll_interval)
                else:
                    # Work was done, immediately check for more
                    iterations += 1

            except WorkerShutdownRequested:
                logger.info("Shutdown requested, exiting run loop")
                break
            except Exception as e:
                logger.exception("Unexpected error in run loop: %s", e)
                time.sleep(poll_interval)

        logger.info(
            "[%s] Stopped after %d iterations. Metrics: %s",
            self.worker_id,
            iterations,
            self._metrics.to_dict(),
        )

    def _process_track(self, track: Dict[str, Any]) -> ProcessResult:
        """
        Internal method to process a single track with full lifecycle management.

        Handles:
        - Lifecycle hooks (before/after/error)
        - Heartbeat management
        - Retry logic for transient errors
        - Metrics collection
        - Track release

        Args:
            track: The track to process

        Returns:
            ProcessResult from processing
        """
        track_id = track.get("id") or track.get("file_stem")
        start_time = time.time()
        result: Optional[ProcessResult] = None
        retries = 0

        try:
            # Start heartbeat for long operations
            self._start_heartbeat(track)

            # Before hook
            self.before_process(track)

            # Process with retry logic
            while retries <= self.max_retries:
                try:
                    result = self.process(track)
                    break  # Success

                except TransientError as e:
                    retries += 1
                    if retries > self.max_retries:
                        result = ProcessResult.failed(
                            error=f"Max retries exceeded: {e}",
                            error_code="MAX_RETRIES",
                            should_retry=False,
                        )
                        break

                    # Calculate backoff delay
                    delay = e.retry_after or (self.retry_base_delay * (2 ** (retries - 1)))
                    logger.warning(
                        "[%s] Transient error (attempt %d/%d), retrying in %.1fs: %s",
                        self.worker_id,
                        retries,
                        self.max_retries,
                        delay,
                        e,
                    )
                    time.sleep(delay)

                except PermanentError as e:
                    result = ProcessResult.failed(
                        error=str(e),
                        error_code=e.error_code or "PERMANENT_ERROR",
                        should_retry=False,
                    )
                    break

            # Calculate duration
            duration_ms = (time.time() - start_time) * 1000
            if result is not None:
                result.metrics["duration_ms"] = duration_ms
                result.metrics["retries"] = retries

                # Record metrics
                self._metrics.record_job(
                    job_id=track_id,
                    duration_ms=duration_ms,
                    success=result.success,
                    retried=retries > 0,
                )

                # After hook
                self.after_process(track, result)

        except Exception as e:
            # Unhandled error
            duration_ms = (time.time() - start_time) * 1000
            self.on_error(track, e)
            result = ProcessResult.failed(
                error=f"Unhandled error: {e}",
                error_code="UNHANDLED_ERROR",
                should_retry=True,  # Assume transient unless proven otherwise
            )
            result.metrics["duration_ms"] = duration_ms
            self._metrics.record_job(
                job_id=track_id,
                duration_ms=duration_ms,
                success=False,
            )

        finally:
            # Stop heartbeat
            self._stop_heartbeat()

            # Release the track
            if result is not None:
                self.release_track(track, result)

        return result or ProcessResult.failed(error="No result produced")

    # =========================================================================
    # Utility Methods
    # =========================================================================

    @property
    def metrics(self) -> WorkerMetrics:
        """Get current worker metrics."""
        return self._metrics

    def request_shutdown(self) -> None:
        """Request graceful shutdown of the worker."""
        logger.info("[%s] Shutdown requested", self.worker_id)
        self._shutdown_requested = True

    @contextmanager
    def track_context(self, track: Dict[str, Any]):
        """
        Context manager for processing a track with automatic cleanup.

        Usage:
            with self.track_context(track) as t:
                # do processing
                pass  # Heartbeat runs automatically
        """
        self._start_heartbeat(track)
        try:
            yield track
        finally:
            self._stop_heartbeat()


# =============================================================================
# Utility Functions
# =============================================================================


def classify_exception(exc: Exception) -> type:
    """
    Classify an exception as transient or permanent.

    This helper can be used to convert standard exceptions into the
    appropriate worker exception type.

    Args:
        exc: The exception to classify

    Returns:
        TransientError or PermanentError class
    """
    # Network and timeout errors are typically transient
    transient_types = (
        ConnectionError,
        TimeoutError,
        OSError,
    )

    # Check exception type
    if isinstance(exc, transient_types):
        return TransientError

    # Check error message for transient patterns
    msg = str(exc).lower()
    transient_patterns = [
        "timeout",
        "connection",
        "rate limit",
        "throttl",
        "retry",
        "temporary",
        "unavailable",
        "503",
        "429",
        "502",
        "504",
    ]

    for pattern in transient_patterns:
        if pattern in msg:
            return TransientError

    # Default to permanent
    return PermanentError


def wrap_exception(exc: Exception, message: Optional[str] = None) -> Exception:
    """
    Wrap an exception in the appropriate worker exception type.

    Args:
        exc: The exception to wrap
        message: Optional custom message

    Returns:
        TransientError or PermanentError wrapping the original
    """
    exc_class = classify_exception(exc)
    msg = message or str(exc)
    return exc_class(msg, original_error=exc)
