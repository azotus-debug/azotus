"""
Event Handlers - Bridge between Events and Workers
===================================================

This module subscribes to events and triggers appropriate actions.
When a job stage changes, it determines what should happen next.

The event handler system supports:
1. Pub/Sub subscriber for production use
2. Local event bus for development/testing
3. Event replay for debugging and recovery

Usage:
    # Production: Start Pub/Sub subscriber
    from event_handlers import start_subscriber
    start_subscriber("omega-events-subscription")

    # Development: Use local event bus
    from event_handlers import LocalEventBus, dispatch_event
    bus = LocalEventBus()
    dispatch_event({"type": "job.stage_changed", "job_id": "abc123", ...}, bus=bus)
    bus.process_events()

    # Replay events for debugging
    from event_handlers import replay_events
    replay_events("job-123", from_timestamp=datetime(2026, 1, 1), dry_run=True)

Author: Omega System
Last Updated: February 2026
"""

import json
import logging
import os
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from functools import wraps
from queue import Queue, Empty
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# =============================================================================
# EVENT TYPES
# =============================================================================

class EventType:
    """Standard event types emitted by the system."""

    # Job lifecycle events
    JOB_CREATED = "job.created"
    JOB_STAGE_CHANGED = "job.stage_changed"
    JOB_STATUS_UPDATED = "job.status_updated"
    JOB_FAILED = "job.failed"
    JOB_COMPLETED = "job.completed"

    # Processing events
    TRANSCRIPTION_STARTED = "transcription.started"
    TRANSCRIPTION_COMPLETED = "transcription.completed"
    TRANSLATION_STARTED = "translation.started"
    TRANSLATION_COMPLETED = "translation.completed"
    BURN_STARTED = "burn.started"
    BURN_COMPLETED = "burn.completed"

    # Review events
    REVIEW_REQUESTED = "review.requested"
    REVIEW_SUBMITTED = "review.submitted"
    REVIEW_APPROVED = "review.approved"

    # System events
    WORKER_HEALTH_CHECK = "worker.health_check"
    SYSTEM_ALERT = "system.alert"


# =============================================================================
# EVENT HANDLER REGISTRY
# =============================================================================

# Global registry of event handlers
_handlers: Dict[str, List[Callable]] = defaultdict(list)
_handler_lock = threading.Lock()


def event_handler(event_type: str):
    """
    Decorator to register a function as an event handler.

    Example:
        @event_handler("job.stage_changed")
        def on_stage_changed(event: Dict[str, Any]) -> None:
            job_id = event["job_id"]
            to_stage = event["data"]["to_stage"]
            # Handle the event...
    """
    def decorator(func: Callable):
        @wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)

        with _handler_lock:
            _handlers[event_type].append(wrapper)
            logger.debug(f"Registered handler '{func.__name__}' for event type '{event_type}'")

        return wrapper
    return decorator


def get_handlers(event_type: str) -> List[Callable]:
    """Get all registered handlers for an event type."""
    with _handler_lock:
        # Return direct handlers plus wildcard handlers
        handlers = list(_handlers.get(event_type, []))
        handlers.extend(_handlers.get("*", []))  # Wildcard handlers
        return handlers


def clear_handlers():
    """Clear all registered handlers. Useful for testing."""
    with _handler_lock:
        _handlers.clear()


# =============================================================================
# STAGE -> ACTION MAPPING
# =============================================================================

def enqueue_ingest(job_id: str, event: Dict[str, Any]) -> None:
    """Enqueue a job for ingest processing."""
    logger.info(f"[{job_id}] Enqueuing for ingest")
    # Ingest is typically handled by file watcher in omega_manager
    # This is for programmatic triggers
    try:
        import omega_db
        omega_db.update_job_via_track(
            job_id,
            status="Queued for ingest",
            progress=5.0,
        )
    except Exception as e:
        logger.error(f"[{job_id}] Failed to enqueue ingest: {e}")


def enqueue_transcription(job_id: str, event: Dict[str, Any]) -> None:
    """Enqueue a job for transcription."""
    logger.info(f"[{job_id}] Enqueuing for transcription")
    try:
        import omega_db
        omega_db.update_job_via_track(
            job_id,
            stage="INGEST",
            status="Starting transcription",
            progress=15.0,
        )
    except Exception as e:
        logger.error(f"[{job_id}] Failed to enqueue transcription: {e}")


def submit_cloud_translation(job_id: str, event: Dict[str, Any]) -> None:
    """Submit a job for cloud translation."""
    logger.info(f"[{job_id}] Submitting to cloud translation")
    try:
        import omega_db
        import config
        from gcs_jobs import GcsJobPaths

        job = omega_db.get_job_via_track(job_id)
        if not job:
            logger.error(f"[{job_id}] Job not found for cloud translation")
            return

        meta = job.get("meta", {})
        if isinstance(meta, str):
            meta = json.loads(meta)

        omega_db.update_job_via_track(
            job_id,
            stage="TRANSLATING_CLOUD_SUBMITTED",
            status="Submitted to cloud worker",
            progress=35.0,
        )
    except Exception as e:
        logger.error(f"[{job_id}] Failed to submit cloud translation: {e}")


def enqueue_burn(job_id: str, event: Dict[str, Any]) -> None:
    """Enqueue a job for video burning."""
    logger.info(f"[{job_id}] Enqueuing for burn")
    try:
        import omega_db
        omega_db.update_job_via_track(
            job_id,
            stage="BURNING",
            status="Starting video burn",
            progress=90.0,
            meta={"burn_started_at": datetime.now().isoformat()},
        )
    except Exception as e:
        logger.error(f"[{job_id}] Failed to enqueue burn: {e}")


def enqueue_finalization(job_id: str, event: Dict[str, Any]) -> None:
    """Enqueue a job for finalization (SRT/ASS generation)."""
    logger.info(f"[{job_id}] Enqueuing for finalization")
    try:
        import omega_db
        omega_db.update_job_via_track(
            job_id,
            stage="FINALIZING",
            status="Generating subtitles",
            progress=75.0,
        )
    except Exception as e:
        logger.error(f"[{job_id}] Failed to enqueue finalization: {e}")


def send_review_notification(job_id: str, event: Dict[str, Any]) -> None:
    """Send review notification to appropriate reviewer."""
    logger.info(f"[{job_id}] Sending review notification")
    try:
        import omega_db
        from workers import review_notifier

        job = omega_db.get_job_via_track(job_id)
        if not job:
            logger.error(f"[{job_id}] Job not found for review notification")
            return

        meta = job.get("meta", {})
        if isinstance(meta, str):
            meta = json.loads(meta)

        target_language = job.get("target_language", "is")
        reviewer_email = review_notifier.get_reviewer_for_language(target_language.upper())

        if reviewer_email:
            cloud_job_id = meta.get("cloud_job_id") or job_id
            program_name = meta.get("original_stem") or job_id

            # Get quality rating if available
            quality_rating = None
            report = job.get("editor_report")
            if report:
                try:
                    report_data = json.loads(report) if isinstance(report, str) else report
                    quality_rating = report_data.get("rating")
                except Exception:
                    pass

            review_notifier.send_review_notification(
                job_id=cloud_job_id,
                program_name=program_name,
                target_language=target_language,
                reviewer_email=reviewer_email,
                quality_rating=quality_rating,
            )

            omega_db.update_job_via_track(
                job_id,
                status="Review notification sent",
                meta={"review_notification_sent_at": datetime.now().isoformat()},
            )
        else:
            logger.warning(f"[{job_id}] No reviewer configured for language: {target_language}")

    except Exception as e:
        logger.error(f"[{job_id}] Failed to send review notification: {e}")


def send_completion_notification(job_id: str, event: Dict[str, Any]) -> None:
    """Send completion notification when job is delivered."""
    logger.info(f"[{job_id}] Sending completion notification")
    try:
        import omega_db
        from notification_manager import NotificationManager

        job = omega_db.get_job_via_track(job_id)
        if not job:
            logger.error(f"[{job_id}] Job not found for completion notification")
            return

        meta = job.get("meta", {})
        if isinstance(meta, str):
            meta = json.loads(meta)

        # Get client email from job or environment
        client_email = meta.get("client_email") or os.environ.get("OMEGA_CLIENT_EMAIL", "")
        if not client_email:
            logger.debug(f"[{job_id}] No client email configured for completion notification")
            return

        program_title = meta.get("original_stem") or job_id
        final_output = meta.get("final_output", "")

        NotificationManager.notify_final_delivery(
            client_email=client_email,
            program_title=program_title,
            download_link=final_output,
            version="1.0",
        )

    except Exception as e:
        logger.error(f"[{job_id}] Failed to send completion notification: {e}")


def send_failure_alert(job_id: str, event: Dict[str, Any]) -> None:
    """Send alert when job fails."""
    logger.info(f"[{job_id}] Sending failure alert")
    try:
        import omega_db
        from email_utils import send_email

        job = omega_db.get_job_via_track(job_id)
        if not job:
            return

        meta = job.get("meta", {})
        if isinstance(meta, str):
            meta = json.loads(meta)

        operator_email = os.environ.get("OMEGA_OPERATOR_EMAIL", "")
        if not operator_email:
            return

        error_message = meta.get("last_error", "Unknown error")
        program_title = meta.get("original_stem") or job_id
        from_stage = event.get("data", {}).get("from_stage", "unknown")

        subject = f"Job Failed: {program_title}"
        body = (
            f"Job {job_id} failed during stage: {from_stage}\n\n"
            f"Error: {error_message}\n\n"
            f"Please investigate and retry if appropriate."
        )

        send_email(subject=subject, body=body, to_addrs=operator_email)

    except Exception as e:
        logger.error(f"[{job_id}] Failed to send failure alert: {e}")


# Stage action mapping table
# Key: (to_stage, processing_step) -> action function
# None for action means stage is handled externally (e.g., cloud worker)
STAGE_ACTIONS: Dict[tuple, Optional[Callable]] = {
    # Processing stages
    ("processing", "ingest"): enqueue_ingest,
    ("processing", "transcribe"): enqueue_transcription,
    ("processing", "translate_submit"): submit_cloud_translation,
    ("processing", "translate_cloud"): None,  # Cloud worker handles this
    ("processing", "translate_local"): None,  # Local worker handles this
    ("processing", "music_detect"): None,  # Cloud worker handles this
    ("processing", "edit"): None,  # Cloud worker handles this
    ("processing", "polish"): None,  # Cloud worker handles this
    ("processing", "burn"): enqueue_burn,
    ("processing", "deliver"): None,  # Final delivery handled separately

    # Stage transitions
    ("finalizing", None): enqueue_finalization,
    ("reviewing", None): send_review_notification,
    ("delivered", None): send_completion_notification,
    ("failed", None): send_failure_alert,

    # Legacy stage mappings
    ("TRANSCRIBED", None): submit_cloud_translation,
    ("TRANSLATING_CLOUD_SUBMITTED", None): None,  # Cloud triggers itself
    ("CLOUD_TRANSLATING", None): None,  # Cloud worker handles this
    ("REVIEWED", None): enqueue_finalization,
    ("FINALIZED", None): enqueue_burn,
    ("BURNING", None): None,  # Worker handles this
    ("COMPLETED", None): send_completion_notification,
    ("DEAD", None): send_failure_alert,
}


def get_action_for_stage(
    to_stage: str,
    processing_step: Optional[str] = None,
) -> Optional[Callable]:
    """
    Get the action function for a stage transition.

    Args:
        to_stage: The target stage
        processing_step: Optional processing sub-step

    Returns:
        Action function or None if no action needed
    """
    # Try exact match first
    action = STAGE_ACTIONS.get((to_stage, processing_step))
    if action is not None:
        return action

    # Try with None processing_step
    action = STAGE_ACTIONS.get((to_stage, None))
    if action is not None:
        return action

    # Try lowercase version
    action = STAGE_ACTIONS.get((to_stage.lower(), processing_step))
    if action is not None:
        return action

    action = STAGE_ACTIONS.get((to_stage.lower(), None))
    return action


# =============================================================================
# CORE EVENT HANDLERS
# =============================================================================

@event_handler(EventType.JOB_STAGE_CHANGED)
def on_stage_changed(event: Dict[str, Any]) -> None:
    """
    Determine what to do when a job changes stage.

    This is the primary handler that bridges events to worker actions.
    """
    job_id = event.get("job_id")
    if not job_id:
        logger.warning("Received stage_changed event without job_id")
        return

    data = event.get("data", {})
    to_stage = data.get("to_stage")
    from_stage = data.get("from_stage")
    processing_step = data.get("processing_step")

    if not to_stage:
        logger.warning(f"[{job_id}] Received stage_changed event without to_stage")
        return

    logger.info(
        f"[{job_id}] Stage changed: {from_stage} -> {to_stage}"
        f"{f' (step: {processing_step})' if processing_step else ''}"
    )

    # Get and execute the action for this stage
    action = get_action_for_stage(to_stage, processing_step)

    if action is not None:
        try:
            action(job_id, event)
        except Exception as e:
            logger.error(f"[{job_id}] Action failed for stage {to_stage}: {e}")
    else:
        logger.debug(f"[{job_id}] No action defined for stage {to_stage}")


@event_handler(EventType.JOB_FAILED)
def on_job_failed(event: Dict[str, Any]) -> None:
    """Handle job failure events."""
    job_id = event.get("job_id")
    if not job_id:
        return

    data = event.get("data", {})
    error_message = data.get("error_message", "Unknown error")

    logger.error(f"[{job_id}] Job failed: {error_message}")
    send_failure_alert(job_id, event)


@event_handler(EventType.JOB_COMPLETED)
def on_job_completed(event: Dict[str, Any]) -> None:
    """Handle job completion events."""
    job_id = event.get("job_id")
    if not job_id:
        return

    logger.info(f"[{job_id}] Job completed successfully")
    send_completion_notification(job_id, event)


@event_handler(EventType.REVIEW_SUBMITTED)
def on_review_submitted(event: Dict[str, Any]) -> None:
    """Handle review submission events."""
    job_id = event.get("job_id")
    if not job_id:
        return

    data = event.get("data", {})
    reviewer = data.get("reviewer", "unknown")
    corrections_count = data.get("corrections_count", 0)

    logger.info(f"[{job_id}] Review submitted by {reviewer} with {corrections_count} corrections")

    try:
        import omega_db
        omega_db.update_job_via_track(
            job_id,
            stage="REVIEWED",
            status=f"Review complete ({corrections_count} corrections)",
            progress=72.0,
            meta={
                "review_submitted_at": datetime.now().isoformat(),
                "reviewer": reviewer,
                "corrections_count": corrections_count,
            },
        )
    except Exception as e:
        logger.error(f"[{job_id}] Failed to update job after review: {e}")


# =============================================================================
# EVENT DISPATCH
# =============================================================================

def dispatch_event(event: Dict[str, Any], bus: Optional["LocalEventBus"] = None) -> None:
    """
    Dispatch an event to all registered handlers.

    Args:
        event: The event to dispatch
        bus: Optional LocalEventBus for queuing (if None, executes directly)
    """
    event_type = event.get("type")
    if not event_type:
        logger.warning("Received event without type field")
        return

    # Add timestamp if not present
    if "timestamp" not in event:
        event["timestamp"] = datetime.utcnow().isoformat() + "Z"

    if bus is not None:
        # Queue for later processing
        bus.publish(event)
    else:
        # Execute immediately
        handlers = get_handlers(event_type)
        for handler in handlers:
            try:
                handler(event)
            except Exception as e:
                logger.error(
                    f"Handler {handler.__name__} failed for event {event_type}: {e}"
                )


def publish_stage_change(
    job_id: str,
    from_stage: str,
    to_stage: str,
    processing_step: Optional[str] = None,
    worker_id: Optional[str] = None,
    reason: Optional[str] = None,
    bus: Optional["LocalEventBus"] = None,
) -> Dict[str, Any]:
    """
    Publish a stage change event.

    This is the primary way to trigger stage-based actions.
    Should be called from transition_service after successful transition.

    Args:
        job_id: The job identifier
        from_stage: Previous stage
        to_stage: New stage
        processing_step: Optional sub-step within processing stage
        worker_id: Optional worker identifier
        reason: Optional reason for transition
        bus: Optional LocalEventBus for development

    Returns:
        The published event
    """
    event = {
        "type": EventType.JOB_STAGE_CHANGED,
        "job_id": job_id,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "data": {
            "from_stage": from_stage,
            "to_stage": to_stage,
            "processing_step": processing_step,
            "worker_id": worker_id,
            "reason": reason,
        },
    }

    dispatch_event(event, bus=bus)
    return event


# =============================================================================
# LOCAL EVENT BUS (For Development)
# =============================================================================

@dataclass
class LocalEventBus:
    """
    In-memory event bus for local development without Pub/Sub.
    Useful for testing and single-machine deployments.

    Usage:
        bus = LocalEventBus()
        bus.publish({"type": "job.stage_changed", "job_id": "abc123", ...})
        bus.process_events()  # Process all queued events
    """

    _queue: Queue = field(default_factory=Queue)
    _subscribers: Dict[str, List[Callable]] = field(default_factory=lambda: defaultdict(list))
    _processed_count: int = field(default=0)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _history: List[Dict[str, Any]] = field(default_factory=list)
    max_history: int = 1000

    def publish(self, event: Dict[str, Any]) -> None:
        """
        Add an event to the queue.

        Args:
            event: Event dictionary with at least "type" field
        """
        if "timestamp" not in event:
            event["timestamp"] = datetime.utcnow().isoformat() + "Z"

        self._queue.put(event)

        # Store in history for replay
        with self._lock:
            self._history.append(event)
            if len(self._history) > self.max_history:
                self._history = self._history[-self.max_history:]

    def subscribe(self, event_type: str, handler: Callable) -> None:
        """
        Subscribe a handler to an event type.

        Args:
            event_type: Event type to subscribe to (or "*" for all)
            handler: Callable that takes an event dict
        """
        with self._lock:
            self._subscribers[event_type].append(handler)

    def unsubscribe(self, event_type: str, handler: Callable) -> None:
        """Unsubscribe a handler from an event type."""
        with self._lock:
            if handler in self._subscribers[event_type]:
                self._subscribers[event_type].remove(handler)

    def process_events(self, max_events: int = 100, timeout: float = 0.1) -> int:
        """
        Process queued events.

        Args:
            max_events: Maximum number of events to process
            timeout: Timeout for queue.get() in seconds

        Returns:
            Number of events processed
        """
        processed = 0

        while processed < max_events:
            try:
                event = self._queue.get(timeout=timeout)
            except Empty:
                break

            event_type = event.get("type")
            if not event_type:
                continue

            # Get handlers from both local subscribers and global registry
            handlers = []
            with self._lock:
                handlers.extend(self._subscribers.get(event_type, []))
                handlers.extend(self._subscribers.get("*", []))
            handlers.extend(get_handlers(event_type))

            for handler in handlers:
                try:
                    handler(event)
                except Exception as e:
                    logger.error(
                        f"Handler {handler.__name__} failed for event {event_type}: {e}"
                    )

            processed += 1
            self._processed_count += 1
            self._queue.task_done()

        return processed

    def get_history(
        self,
        job_id: Optional[str] = None,
        event_type: Optional[str] = None,
        from_timestamp: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get event history with optional filtering.

        Args:
            job_id: Filter by job ID
            event_type: Filter by event type
            from_timestamp: Only events after this timestamp

        Returns:
            Filtered list of events
        """
        with self._lock:
            history = list(self._history)

        if job_id:
            history = [e for e in history if e.get("job_id") == job_id]

        if event_type:
            history = [e for e in history if e.get("type") == event_type]

        if from_timestamp:
            from_ts = from_timestamp.isoformat()
            history = [e for e in history if e.get("timestamp", "") >= from_ts]

        return history

    def clear(self) -> None:
        """Clear the queue and history."""
        with self._lock:
            # Clear queue
            while not self._queue.empty():
                try:
                    self._queue.get_nowait()
                    self._queue.task_done()
                except Empty:
                    break

            self._history.clear()
            self._processed_count = 0

    @property
    def pending_count(self) -> int:
        """Number of events waiting to be processed."""
        return self._queue.qsize()

    @property
    def processed_count(self) -> int:
        """Total number of events processed."""
        return self._processed_count


# =============================================================================
# PUB/SUB SUBSCRIBER
# =============================================================================

_subscriber_thread: Optional[threading.Thread] = None
_subscriber_stop_event = threading.Event()


def start_subscriber(
    subscription: str,
    project: Optional[str] = None,
    max_messages: int = 10,
    timeout: float = 300,
    daemon: bool = True,
) -> threading.Thread:
    """
    Start listening for events from Pub/Sub.

    This runs in a dedicated thread using streaming pull for low-latency
    event handling.

    Args:
        subscription: Pub/Sub subscription name (e.g., "omega-events-sub")
        project: GCP project ID (defaults to OMEGA_CLOUD_PROJECT env var)
        max_messages: Maximum number of messages to pull at once
        timeout: Ack deadline in seconds
        daemon: Whether to run as daemon thread

    Returns:
        The subscriber thread
    """
    global _subscriber_thread, _subscriber_stop_event

    if _subscriber_thread is not None and _subscriber_thread.is_alive():
        logger.warning("Subscriber already running")
        return _subscriber_thread

    _subscriber_stop_event.clear()

    project = project or os.environ.get("OMEGA_CLOUD_PROJECT", "sermon-translator-system")

    def subscriber_loop():
        try:
            from google.cloud import pubsub_v1
        except ImportError:
            logger.error("google-cloud-pubsub not installed. Run: pip install google-cloud-pubsub")
            return

        subscriber = pubsub_v1.SubscriberClient()
        subscription_path = subscriber.subscription_path(project, subscription)

        def callback(message):
            try:
                event = json.loads(message.data.decode("utf-8"))
                logger.debug(f"Received event: {event.get('type')}")

                dispatch_event(event)
                message.ack()

            except json.JSONDecodeError as e:
                logger.error(f"Invalid JSON in message: {e}")
                message.nack()
            except Exception as e:
                logger.error(f"Error processing message: {e}")
                message.nack()

        streaming_pull_future = subscriber.subscribe(
            subscription_path,
            callback=callback,
            flow_control=pubsub_v1.types.FlowControl(max_messages=max_messages),
        )

        logger.info(f"Started Pub/Sub subscriber on {subscription_path}")

        try:
            while not _subscriber_stop_event.is_set():
                try:
                    streaming_pull_future.result(timeout=10)
                except Exception as e:
                    if not _subscriber_stop_event.is_set():
                        logger.warning(f"Subscriber interrupted: {e}")
                        time.sleep(5)  # Backoff before reconnect
        finally:
            streaming_pull_future.cancel()
            streaming_pull_future.result()  # Wait for clean shutdown
            subscriber.close()
            logger.info("Pub/Sub subscriber stopped")

    _subscriber_thread = threading.Thread(target=subscriber_loop, daemon=daemon)
    _subscriber_thread.start()

    return _subscriber_thread


def stop_subscriber(timeout: float = 30) -> None:
    """
    Stop the Pub/Sub subscriber.

    Args:
        timeout: Maximum time to wait for clean shutdown
    """
    global _subscriber_thread

    if _subscriber_thread is None or not _subscriber_thread.is_alive():
        return

    _subscriber_stop_event.set()
    _subscriber_thread.join(timeout=timeout)

    if _subscriber_thread.is_alive():
        logger.warning("Subscriber thread did not stop cleanly")
    else:
        logger.info("Subscriber stopped")

    _subscriber_thread = None


# =============================================================================
# EVENT REPLAY
# =============================================================================

def replay_events(
    job_id: str,
    from_timestamp: datetime,
    dry_run: bool = True,
    bus: Optional[LocalEventBus] = None,
    source: str = "database",
) -> List[Dict[str, Any]]:
    """
    Replay events for a job from a specific time.
    Useful for debugging and recovery.

    Args:
        job_id: Job ID to replay events for
        from_timestamp: Only replay events after this timestamp
        dry_run: If True, only return events without executing handlers
        bus: Optional LocalEventBus to use (creates temporary one if None)
        source: Event source - "database" (audit log) or "bus" (LocalEventBus history)

    Returns:
        List of events that were (or would be) replayed
    """
    events: List[Dict[str, Any]] = []

    if source == "bus" and bus is not None:
        # Get events from bus history
        events = bus.get_history(job_id=job_id, from_timestamp=from_timestamp)

    elif source == "database":
        # Get events from transition audit log
        try:
            from transition_service import get_transition_history

            history = get_transition_history(job_stem=job_id, limit=1000)

            # Convert transitions to events
            from_ts = from_timestamp.isoformat()
            for transition in history:
                created_at = transition.get("created_at", "")
                if created_at < from_ts:
                    continue

                event = {
                    "type": EventType.JOB_STAGE_CHANGED,
                    "job_id": job_id,
                    "timestamp": created_at,
                    "data": {
                        "from_stage": transition.get("from_stage"),
                        "to_stage": transition.get("to_stage"),
                        "processing_step": transition.get("processing_step"),
                        "worker_id": transition.get("worker_id"),
                        "reason": transition.get("reason"),
                    },
                    "_source": "replay",
                    "_original_id": transition.get("id"),
                }
                events.append(event)

            # Sort by timestamp
            events.sort(key=lambda e: e.get("timestamp", ""))

        except Exception as e:
            logger.error(f"Failed to get transition history for replay: {e}")
            return []

    if dry_run:
        logger.info(f"DRY RUN: Would replay {len(events)} events for job {job_id}")
        for event in events:
            logger.info(
                f"  [{event.get('timestamp')}] {event.get('type')}: "
                f"{event.get('data', {}).get('from_stage')} -> "
                f"{event.get('data', {}).get('to_stage')}"
            )
        return events

    # Actually replay events
    replay_bus = bus or LocalEventBus()

    for event in events:
        event["_replayed_at"] = datetime.utcnow().isoformat() + "Z"
        dispatch_event(event, bus=replay_bus)

    if bus is None:
        # Process events if we created a temporary bus
        replay_bus.process_events()

    logger.info(f"Replayed {len(events)} events for job {job_id}")
    return events


# =============================================================================
# INTEGRATION WITH TRANSITION SERVICE
# =============================================================================

def integrate_with_transition_service():
    """
    Integrate event publishing into the transition service.

    Call this at application startup to automatically publish events
    when transitions are executed.

    Note: This modifies the transition_service module to add event publishing.
    """
    try:
        import transition_service

        original_execute = transition_service.execute_transition

        def execute_with_events(
            job_id: str,
            job_stem: str,
            from_stage: str,
            to_stage: str,
            processing_step: Optional[str] = None,
            worker_id: Optional[str] = None,
            reason: Optional[str] = None,
            skip_validation: bool = False,
        ) -> Dict[str, Any]:
            # Execute original transition
            result = original_execute(
                job_id=job_id,
                job_stem=job_stem,
                from_stage=from_stage,
                to_stage=to_stage,
                processing_step=processing_step,
                worker_id=worker_id,
                reason=reason,
                skip_validation=skip_validation,
            )

            # Publish event if transition succeeded
            if result.get("success"):
                publish_stage_change(
                    job_id=job_stem,
                    from_stage=from_stage,
                    to_stage=to_stage,
                    processing_step=processing_step,
                    worker_id=worker_id,
                    reason=reason,
                )

            return result

        transition_service.execute_transition = execute_with_events
        logger.info("Integrated event publishing with transition_service")

    except ImportError:
        logger.warning("transition_service not available for integration")
    except Exception as e:
        logger.error(f"Failed to integrate with transition_service: {e}")


# =============================================================================
# MODULE INITIALIZATION
# =============================================================================

# Ensure logging is configured
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    ))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
