"""
Event Publisher for Google Cloud Pub/Sub

This module provides event publishing capabilities for the Omega TV Subtitle Workflow system.
Events are published to Google Cloud Pub/Sub for external integrations, monitoring dashboards,
and workflow orchestration.

Event Types:
    - job.stage_changed: When a job transitions between stages
    - job.created: When a new job is created
    - job.completed: When a job reaches DELIVERED/COMPLETED stage
    - job.failed: When a job fails (enters DEAD stage or exceeds retry limit)
    - track.claimed: When a worker claims a track for processing
    - track.released: When a worker releases a track (success or failure)

Configuration Environment Variables:
    - OMEGA_PUBSUB_ENABLED: Enable/disable Pub/Sub publishing (default: 0/disabled)
    - OMEGA_PUBSUB_TOPIC: Topic name (default: omega-events)
    - OMEGA_PUBSUB_PROJECT: GCP project ID (defaults to OMEGA_CLOUD_PROJECT)
    - OMEGA_PUBSUB_DLQ_TOPIC: Dead letter queue topic (default: omega-events-dlq)
    - PUBSUB_EMULATOR_HOST: Set to use local emulator (e.g., localhost:8085)

Usage:
    from event_publisher import publish_stage_change, publish_job_created

    # Publish a stage change event
    publish_stage_change(
        job_id="cbnjd011326cc_is",
        from_stage="TRANSLATING",
        to_stage="REVIEWING",
        worker_id="cloud_worker_1"
    )

    # Publish a job created event
    publish_job_created(
        job_id="cbnjd011326cc_is",
        language_code="is",
        source_file="cbnjd011326cc.mp4"
    )
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from queue import Queue, Empty
from typing import Any, Callable, Dict, List, Optional
from concurrent.futures import Future

logger = logging.getLogger("OmegaEventPublisher")

# ============================================================================
# Configuration
# ============================================================================

def _bool_env(key: str, default: str = "0") -> bool:
    """Parse boolean environment variable."""
    return os.environ.get(key, default).strip().lower() in {"1", "true", "yes", "on"}


# Pub/Sub configuration
PUBSUB_ENABLED = _bool_env("OMEGA_PUBSUB_ENABLED", "0")
PUBSUB_TOPIC = os.environ.get("OMEGA_PUBSUB_TOPIC", "omega-events").strip()
PUBSUB_PROJECT = os.environ.get(
    "OMEGA_PUBSUB_PROJECT",
    os.environ.get("OMEGA_CLOUD_PROJECT", "sermon-translator-system")
).strip()
PUBSUB_DLQ_TOPIC = os.environ.get("OMEGA_PUBSUB_DLQ_TOPIC", "omega-events-dlq").strip()

# Batching configuration
PUBSUB_BATCH_MAX_MESSAGES = int(os.environ.get("OMEGA_PUBSUB_BATCH_MAX_MESSAGES", "100"))
PUBSUB_BATCH_MAX_LATENCY = float(os.environ.get("OMEGA_PUBSUB_BATCH_MAX_LATENCY", "0.5"))  # seconds
PUBSUB_BATCH_MAX_BYTES = int(os.environ.get("OMEGA_PUBSUB_BATCH_MAX_BYTES", "1048576"))  # 1MB

# Retry configuration
PUBSUB_MAX_RETRIES = int(os.environ.get("OMEGA_PUBSUB_MAX_RETRIES", "3"))
PUBSUB_RETRY_DELAY = float(os.environ.get("OMEGA_PUBSUB_RETRY_DELAY", "1.0"))


# ============================================================================
# Event Types
# ============================================================================

class EventType:
    """Constants for event type names."""
    JOB_STAGE_CHANGED = "job.stage_changed"
    JOB_CREATED = "job.created"
    JOB_COMPLETED = "job.completed"
    JOB_FAILED = "job.failed"
    TRACK_CLAIMED = "track.claimed"
    TRACK_RELEASED = "track.released"


# ============================================================================
# Event Schema
# ============================================================================

@dataclass
class Event:
    """
    Event data structure for Pub/Sub messages.

    Attributes:
        event_type: The type of event (e.g., "job.stage_changed")
        job_id: The job identifier this event relates to
        timestamp: ISO 8601 timestamp when the event occurred
        data: Additional event-specific data
    """
    event_type: str
    job_id: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert event to dictionary for JSON serialization."""
        return {
            "event_type": self.event_type,
            "job_id": self.job_id,
            "timestamp": self.timestamp,
            "data": self.data
        }

    def to_json(self) -> str:
        """Serialize event to JSON string."""
        return json.dumps(self.to_dict(), ensure_ascii=False)

    def to_bytes(self) -> bytes:
        """Serialize event to UTF-8 encoded bytes for Pub/Sub."""
        return self.to_json().encode("utf-8")


# ============================================================================
# Publisher Client (Lazy Singleton)
# ============================================================================

_publisher_client = None
_publisher_lock = threading.Lock()
_background_queue: Optional[Queue] = None
_background_thread: Optional[threading.Thread] = None
_shutdown_flag = threading.Event()


def _get_publisher_client():
    """
    Get or create the Pub/Sub publisher client.

    Returns a singleton publisher client with batching configured.
    Uses lazy initialization to avoid import errors if google-cloud-pubsub
    is not installed.

    Returns:
        PublisherClient instance or None if Pub/Sub is not available
    """
    global _publisher_client

    if not PUBSUB_ENABLED:
        return None

    if _publisher_client is not None:
        return _publisher_client

    with _publisher_lock:
        # Double-check pattern
        if _publisher_client is not None:
            return _publisher_client

        try:
            from google.cloud import pubsub_v1
            from google.cloud.pubsub_v1 import types

            # Configure batching settings for performance
            batch_settings = types.BatchSettings(
                max_messages=PUBSUB_BATCH_MAX_MESSAGES,
                max_latency=PUBSUB_BATCH_MAX_LATENCY,
                max_bytes=PUBSUB_BATCH_MAX_BYTES,
            )

            # Create publisher with batching
            _publisher_client = pubsub_v1.PublisherClient(
                batch_settings=batch_settings
            )

            logger.info(
                "Pub/Sub publisher initialized: project=%s, topic=%s",
                PUBSUB_PROJECT, PUBSUB_TOPIC
            )

            return _publisher_client

        except ImportError:
            logger.warning(
                "google-cloud-pubsub not installed. Event publishing disabled. "
                "Install with: pip install google-cloud-pubsub"
            )
            return None
        except Exception as e:
            logger.error("Failed to initialize Pub/Sub publisher: %s", e)
            return None


def _get_topic_path(topic_name: str = None) -> str:
    """Get the full topic path for Pub/Sub."""
    topic = topic_name or PUBSUB_TOPIC
    return f"projects/{PUBSUB_PROJECT}/topics/{topic}"


# ============================================================================
# Background Publishing Thread
# ============================================================================

def _start_background_publisher():
    """Start the background publisher thread if not already running."""
    global _background_queue, _background_thread

    if not PUBSUB_ENABLED:
        return

    with _publisher_lock:
        if _background_queue is None:
            _background_queue = Queue()

        if _background_thread is None or not _background_thread.is_alive():
            _shutdown_flag.clear()
            _background_thread = threading.Thread(
                target=_background_publish_loop,
                daemon=True,
                name="PubSubPublisher"
            )
            _background_thread.start()
            logger.debug("Background publisher thread started")


def _background_publish_loop():
    """Background thread loop for async message publishing."""
    global _background_queue

    while not _shutdown_flag.is_set():
        try:
            # Wait for messages with timeout for graceful shutdown
            try:
                event = _background_queue.get(timeout=1.0)
            except Empty:
                continue

            # Attempt to publish with retries
            _publish_with_retry(event)
            _background_queue.task_done()

        except Exception as e:
            logger.error("Background publisher error: %s", e)


def _publish_with_retry(event: Event, max_retries: int = None) -> bool:
    """
    Publish an event with retry logic.

    Args:
        event: The event to publish
        max_retries: Maximum retry attempts (defaults to PUBSUB_MAX_RETRIES)

    Returns:
        True if published successfully, False otherwise
    """
    if max_retries is None:
        max_retries = PUBSUB_MAX_RETRIES

    client = _get_publisher_client()
    if client is None:
        return False

    topic_path = _get_topic_path()
    message_data = event.to_bytes()

    # Message attributes for filtering and routing
    attributes = {
        "event_type": event.event_type,
        "job_id": event.job_id,
    }

    for attempt in range(max_retries + 1):
        try:
            future = client.publish(
                topic_path,
                message_data,
                ordering_key=event.job_id,  # Ensure ordering by job_id
                **attributes
            )
            # Wait for publish to complete (with timeout)
            message_id = future.result(timeout=30)

            logger.debug(
                "Published event: type=%s, job_id=%s, message_id=%s",
                event.event_type, event.job_id, message_id
            )
            return True

        except Exception as e:
            if attempt < max_retries:
                delay = PUBSUB_RETRY_DELAY * (2 ** attempt)  # Exponential backoff
                logger.warning(
                    "Pub/Sub publish failed (attempt %d/%d), retrying in %.1fs: %s",
                    attempt + 1, max_retries + 1, delay, e
                )
                time.sleep(delay)
            else:
                logger.error(
                    "Pub/Sub publish failed after %d attempts: %s",
                    max_retries + 1, e
                )
                # Attempt to publish to DLQ
                _publish_to_dlq(event, str(e))
                return False

    return False


def _publish_to_dlq(event: Event, error_message: str):
    """
    Attempt to publish failed event to Dead Letter Queue.

    Args:
        event: The event that failed to publish
        error_message: The error that caused the failure
    """
    client = _get_publisher_client()
    if client is None:
        return

    try:
        dlq_topic_path = _get_topic_path(PUBSUB_DLQ_TOPIC)

        # Wrap original event with error info
        dlq_payload = {
            "original_event": event.to_dict(),
            "error": error_message,
            "failed_at": datetime.now(timezone.utc).isoformat()
        }

        message_data = json.dumps(dlq_payload, ensure_ascii=False).encode("utf-8")

        future = client.publish(
            dlq_topic_path,
            message_data,
            event_type=event.event_type,
            job_id=event.job_id,
            is_dlq="true"
        )
        future.result(timeout=10)

        logger.info(
            "Published failed event to DLQ: type=%s, job_id=%s",
            event.event_type, event.job_id
        )

    except Exception as e:
        # Last resort: just log it
        logger.error(
            "Failed to publish to DLQ (event lost): type=%s, job_id=%s, error=%s",
            event.event_type, event.job_id, e
        )


# ============================================================================
# Public API: Generic Event Publishing
# ============================================================================

def publish_event(
    event_type: str,
    job_id: str,
    data: Dict[str, Any] = None,
    *,
    sync: bool = False
) -> bool:
    """
    Publish an event to Pub/Sub.

    This is the core publishing function. Events are published asynchronously
    by default for better performance. Use sync=True for critical events that
    must be delivered before proceeding.

    Args:
        event_type: The type of event (e.g., "job.stage_changed")
        job_id: The job identifier this event relates to
        data: Additional event-specific data (optional)
        sync: If True, block until publish completes (default: False)

    Returns:
        True if event was published (or queued) successfully, False otherwise.
        Note: For async publishes, True means queued, not necessarily delivered.

    Example:
        >>> publish_event(
        ...     event_type="job.stage_changed",
        ...     job_id="cbnjd011326cc_is",
        ...     data={"from_stage": "TRANSLATING", "to_stage": "REVIEWING"}
        ... )
        True
    """
    if not PUBSUB_ENABLED:
        logger.debug(
            "Pub/Sub disabled, skipping event: type=%s, job_id=%s",
            event_type, job_id
        )
        return True  # Not an error, just disabled

    event = Event(
        event_type=event_type,
        job_id=job_id,
        data=data or {}
    )

    if sync:
        return _publish_with_retry(event)
    else:
        # Queue for async publishing
        _start_background_publisher()
        try:
            _background_queue.put_nowait(event)
            return True
        except Exception as e:
            logger.error("Failed to queue event for publishing: %s", e)
            return False


# ============================================================================
# Public API: Convenience Functions
# ============================================================================

def publish_stage_change(
    job_id: str,
    from_stage: str,
    to_stage: str,
    *,
    processing_step: str = None,
    worker_id: str = None,
    duration_seconds: float = None,
    metadata: Dict[str, Any] = None,
    sync: bool = False
) -> bool:
    """
    Publish a job stage change event.

    Use this when a job transitions from one stage to another (e.g., TRANSLATING -> REVIEWING).

    Args:
        job_id: The job identifier
        from_stage: The previous stage (e.g., "TRANSLATING")
        to_stage: The new stage (e.g., "REVIEWING")
        processing_step: Optional processing step name (e.g., "translate_cloud")
        worker_id: Optional worker identifier that triggered the change
        duration_seconds: Optional time spent in the previous stage
        metadata: Optional additional metadata
        sync: If True, block until publish completes

    Returns:
        True if event was published/queued successfully

    Example:
        >>> publish_stage_change(
        ...     job_id="cbnjd011326cc_is",
        ...     from_stage="TRANSLATING",
        ...     to_stage="REVIEWED",
        ...     processing_step="translate_cloud",
        ...     worker_id="cloud_worker_1",
        ...     duration_seconds=245.3
        ... )
        True
    """
    data = {
        "from_stage": from_stage,
        "to_stage": to_stage,
    }

    if processing_step:
        data["processing_step"] = processing_step
    if worker_id:
        data["worker_id"] = worker_id
    if duration_seconds is not None:
        data["duration_seconds"] = duration_seconds
    if metadata:
        data["metadata"] = metadata

    return publish_event(
        event_type=EventType.JOB_STAGE_CHANGED,
        job_id=job_id,
        data=data,
        sync=sync
    )


def publish_job_created(
    job_id: str,
    language_code: str,
    *,
    source_file: str = None,
    client: str = None,
    style: str = None,
    priority: int = None,
    due_date: str = None,
    metadata: Dict[str, Any] = None,
    sync: bool = False
) -> bool:
    """
    Publish a job created event.

    Use this when a new job is created/ingested into the system.

    Args:
        job_id: The job identifier
        language_code: Target language code (e.g., "is", "nl", "es")
        source_file: Optional source video filename
        client: Optional client name
        style: Optional subtitle style
        priority: Optional job priority (higher = more urgent)
        due_date: Optional due date (ISO 8601 format)
        metadata: Optional additional metadata
        sync: If True, block until publish completes

    Returns:
        True if event was published/queued successfully

    Example:
        >>> publish_job_created(
        ...     job_id="cbnjd011326cc_is",
        ...     language_code="is",
        ...     source_file="cbnjd011326cc.mp4",
        ...     client="CBN Europe"
        ... )
        True
    """
    data = {
        "language_code": language_code,
    }

    if source_file:
        data["source_file"] = source_file
    if client:
        data["client"] = client
    if style:
        data["style"] = style
    if priority is not None:
        data["priority"] = priority
    if due_date:
        data["due_date"] = due_date
    if metadata:
        data["metadata"] = metadata

    return publish_event(
        event_type=EventType.JOB_CREATED,
        job_id=job_id,
        data=data,
        sync=sync
    )


def publish_job_completed(
    job_id: str,
    *,
    output_path: str = None,
    total_duration_seconds: float = None,
    quality_score: float = None,
    segment_count: int = None,
    metadata: Dict[str, Any] = None,
    sync: bool = False
) -> bool:
    """
    Publish a job completed event.

    Use this when a job reaches DELIVERED or COMPLETED stage.

    Args:
        job_id: The job identifier
        output_path: Optional path to the final output file
        total_duration_seconds: Optional total processing time
        quality_score: Optional quality assessment score
        segment_count: Optional number of subtitle segments
        metadata: Optional additional metadata
        sync: If True, block until publish completes

    Returns:
        True if event was published/queued successfully

    Example:
        >>> publish_job_completed(
        ...     job_id="cbnjd011326cc_is",
        ...     output_path="/4_DELIVERY/VIDEO/cbnjd011326cc_is_SUBBED.mp4",
        ...     total_duration_seconds=1847.5,
        ...     quality_score=8.7
        ... )
        True
    """
    data = {}

    if output_path:
        data["output_path"] = output_path
    if total_duration_seconds is not None:
        data["total_duration_seconds"] = total_duration_seconds
    if quality_score is not None:
        data["quality_score"] = quality_score
    if segment_count is not None:
        data["segment_count"] = segment_count
    if metadata:
        data["metadata"] = metadata

    return publish_event(
        event_type=EventType.JOB_COMPLETED,
        job_id=job_id,
        data=data,
        sync=sync
    )


def publish_job_failed(
    job_id: str,
    error_message: str,
    *,
    stage: str = None,
    error_code: str = None,
    retry_count: int = None,
    is_permanent: bool = False,
    metadata: Dict[str, Any] = None,
    sync: bool = True  # Failures should be sync by default
) -> bool:
    """
    Publish a job failed event.

    Use this when a job fails (enters DEAD stage or exceeds retry limit).

    Args:
        job_id: The job identifier
        error_message: Human-readable error description
        stage: Optional stage where failure occurred
        error_code: Optional machine-readable error code
        retry_count: Optional number of retries attempted
        is_permanent: If True, indicates the job will not be retried
        metadata: Optional additional metadata
        sync: If True, block until publish completes (default: True for failures)

    Returns:
        True if event was published/queued successfully

    Example:
        >>> publish_job_failed(
        ...     job_id="cbnjd011326cc_is",
        ...     error_message="Translation API quota exceeded",
        ...     stage="TRANSLATING",
        ...     error_code="QUOTA_EXCEEDED",
        ...     retry_count=3,
        ...     is_permanent=True
        ... )
        True
    """
    data = {
        "error_message": error_message,
        "is_permanent": is_permanent,
    }

    if stage:
        data["stage"] = stage
    if error_code:
        data["error_code"] = error_code
    if retry_count is not None:
        data["retry_count"] = retry_count
    if metadata:
        data["metadata"] = metadata

    return publish_event(
        event_type=EventType.JOB_FAILED,
        job_id=job_id,
        data=data,
        sync=sync
    )


def publish_track_claimed(
    job_id: str,
    worker_id: str,
    *,
    track_type: str = None,
    language_code: str = None,
    processing_step: str = None,
    metadata: Dict[str, Any] = None,
    sync: bool = False
) -> bool:
    """
    Publish a track claimed event.

    Use this when a worker claims a track for processing.

    Args:
        job_id: The job identifier
        worker_id: The worker claiming the track
        track_type: Optional track type (e.g., "subtitle", "dub")
        language_code: Optional language code
        processing_step: Optional processing step being claimed
        metadata: Optional additional metadata
        sync: If True, block until publish completes

    Returns:
        True if event was published/queued successfully

    Example:
        >>> publish_track_claimed(
        ...     job_id="cbnjd011326cc_is",
        ...     worker_id="finalizer_worker_1",
        ...     track_type="subtitle",
        ...     language_code="is",
        ...     processing_step="finalize"
        ... )
        True
    """
    data = {
        "worker_id": worker_id,
    }

    if track_type:
        data["track_type"] = track_type
    if language_code:
        data["language_code"] = language_code
    if processing_step:
        data["processing_step"] = processing_step
    if metadata:
        data["metadata"] = metadata

    return publish_event(
        event_type=EventType.TRACK_CLAIMED,
        job_id=job_id,
        data=data,
        sync=sync
    )


def publish_track_released(
    job_id: str,
    worker_id: str,
    success: bool,
    *,
    track_type: str = None,
    language_code: str = None,
    processing_step: str = None,
    duration_seconds: float = None,
    error_message: str = None,
    metadata: Dict[str, Any] = None,
    sync: bool = False
) -> bool:
    """
    Publish a track released event.

    Use this when a worker releases a track after processing (success or failure).

    Args:
        job_id: The job identifier
        worker_id: The worker releasing the track
        success: Whether processing completed successfully
        track_type: Optional track type (e.g., "subtitle", "dub")
        language_code: Optional language code
        processing_step: Optional processing step that was performed
        duration_seconds: Optional processing duration
        error_message: Optional error message if success=False
        metadata: Optional additional metadata
        sync: If True, block until publish completes

    Returns:
        True if event was published/queued successfully

    Example:
        >>> publish_track_released(
        ...     job_id="cbnjd011326cc_is",
        ...     worker_id="finalizer_worker_1",
        ...     success=True,
        ...     processing_step="finalize",
        ...     duration_seconds=45.2
        ... )
        True
    """
    data = {
        "worker_id": worker_id,
        "success": success,
    }

    if track_type:
        data["track_type"] = track_type
    if language_code:
        data["language_code"] = language_code
    if processing_step:
        data["processing_step"] = processing_step
    if duration_seconds is not None:
        data["duration_seconds"] = duration_seconds
    if error_message:
        data["error_message"] = error_message
    if metadata:
        data["metadata"] = metadata

    return publish_event(
        event_type=EventType.TRACK_RELEASED,
        job_id=job_id,
        data=data,
        sync=sync
    )


# ============================================================================
# Lifecycle Management
# ============================================================================

def flush_events(timeout: float = 30.0) -> bool:
    """
    Flush all pending events in the background queue.

    Call this before shutdown to ensure all events are published.

    Args:
        timeout: Maximum time to wait for queue to empty

    Returns:
        True if all events were flushed, False if timeout occurred
    """
    global _background_queue

    if _background_queue is None:
        return True

    try:
        _background_queue.join()
        return True
    except Exception:
        return False


def shutdown_publisher():
    """
    Gracefully shutdown the event publisher.

    This flushes pending events and stops the background thread.
    Call this during application shutdown.
    """
    global _publisher_client, _background_thread, _background_queue

    logger.info("Shutting down event publisher...")

    # Signal shutdown
    _shutdown_flag.set()

    # Flush pending events
    flush_events(timeout=10.0)

    # Wait for background thread
    if _background_thread is not None and _background_thread.is_alive():
        _background_thread.join(timeout=5.0)

    # Close publisher client
    with _publisher_lock:
        if _publisher_client is not None:
            try:
                _publisher_client.stop()
            except Exception:
                pass
            _publisher_client = None

    logger.info("Event publisher shutdown complete")


# ============================================================================
# Status and Health
# ============================================================================

def is_enabled() -> bool:
    """Check if event publishing is enabled."""
    return PUBSUB_ENABLED


def get_status() -> Dict[str, Any]:
    """
    Get the current status of the event publisher.

    Returns:
        Dictionary with status information
    """
    global _background_queue, _background_thread

    queue_size = 0
    if _background_queue is not None:
        try:
            queue_size = _background_queue.qsize()
        except Exception:
            pass

    return {
        "enabled": PUBSUB_ENABLED,
        "project": PUBSUB_PROJECT,
        "topic": PUBSUB_TOPIC,
        "dlq_topic": PUBSUB_DLQ_TOPIC,
        "queue_size": queue_size,
        "background_thread_alive": (
            _background_thread is not None and _background_thread.is_alive()
        ),
        "publisher_initialized": _publisher_client is not None,
    }


# ============================================================================
# Testing / Main
# ============================================================================

if __name__ == "__main__":
    """
    Test the event publisher.

    Usage:
        # Test with emulator
        export PUBSUB_EMULATOR_HOST=localhost:8085
        export OMEGA_PUBSUB_ENABLED=1
        python event_publisher.py

        # Test with real Pub/Sub (requires auth)
        export OMEGA_PUBSUB_ENABLED=1
        export OMEGA_PUBSUB_PROJECT=your-project
        python event_publisher.py
    """
    import sys

    # Set up logging for testing
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    print("=" * 60)
    print("Event Publisher Test")
    print("=" * 60)

    # Check configuration
    status = get_status()
    print(f"\nConfiguration:")
    print(f"  Enabled: {status['enabled']}")
    print(f"  Project: {status['project']}")
    print(f"  Topic: {status['topic']}")
    print(f"  DLQ Topic: {status['dlq_topic']}")

    emulator_host = os.environ.get("PUBSUB_EMULATOR_HOST")
    if emulator_host:
        print(f"  Emulator: {emulator_host}")

    if not PUBSUB_ENABLED:
        print("\nPub/Sub is disabled. Set OMEGA_PUBSUB_ENABLED=1 to enable.")
        print("Exiting.")
        sys.exit(0)

    print("\nRunning tests...")

    # Test 1: Job created event
    print("\n1. Testing job.created event...")
    result = publish_job_created(
        job_id="test_job_001",
        language_code="is",
        source_file="test_video.mp4",
        client="Test Client",
        sync=True
    )
    print(f"   Result: {'SUCCESS' if result else 'FAILED'}")

    # Test 2: Stage change event
    print("\n2. Testing job.stage_changed event...")
    result = publish_stage_change(
        job_id="test_job_001",
        from_stage="QUEUED",
        to_stage="TRANSCRIBED",
        worker_id="test_worker_1",
        duration_seconds=120.5,
        sync=True
    )
    print(f"   Result: {'SUCCESS' if result else 'FAILED'}")

    # Test 3: Track claimed event
    print("\n3. Testing track.claimed event...")
    result = publish_track_claimed(
        job_id="test_job_001",
        worker_id="test_worker_1",
        track_type="subtitle",
        language_code="is",
        processing_step="translate",
        sync=True
    )
    print(f"   Result: {'SUCCESS' if result else 'FAILED'}")

    # Test 4: Track released event
    print("\n4. Testing track.released event...")
    result = publish_track_released(
        job_id="test_job_001",
        worker_id="test_worker_1",
        success=True,
        processing_step="translate",
        duration_seconds=245.3,
        sync=True
    )
    print(f"   Result: {'SUCCESS' if result else 'FAILED'}")

    # Test 5: Job completed event
    print("\n5. Testing job.completed event...")
    result = publish_job_completed(
        job_id="test_job_001",
        output_path="/4_DELIVERY/VIDEO/test_job_001_SUBBED.mp4",
        total_duration_seconds=1847.5,
        quality_score=8.7,
        segment_count=342,
        sync=True
    )
    print(f"   Result: {'SUCCESS' if result else 'FAILED'}")

    # Test 6: Job failed event
    print("\n6. Testing job.failed event...")
    result = publish_job_failed(
        job_id="test_job_002",
        error_message="Test error message",
        stage="TRANSLATING",
        error_code="TEST_ERROR",
        retry_count=3,
        is_permanent=True,
        sync=True
    )
    print(f"   Result: {'SUCCESS' if result else 'FAILED'}")

    # Test 7: Async batch publishing
    print("\n7. Testing async batch publishing...")
    for i in range(10):
        publish_stage_change(
            job_id=f"batch_test_{i:03d}",
            from_stage="QUEUED",
            to_stage="TRANSCRIBED",
            sync=False  # Async
        )
    print("   Queued 10 events...")

    # Flush and shutdown
    print("\n8. Flushing pending events...")
    flush_events(timeout=30.0)
    print("   Done")

    print("\n9. Shutting down publisher...")
    shutdown_publisher()
    print("   Done")

    print("\n" + "=" * 60)
    print("All tests completed!")
    print("=" * 60)
