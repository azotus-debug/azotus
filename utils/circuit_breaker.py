"""
Simple circuit breaker for external dependencies.

Use per-service breakers to prevent repeated hammering when a dependency fails.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional
from enum import Enum
import threading


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    name: str
    failure_threshold: int = 5
    reset_timeout: int = 300  # seconds
    _state: CircuitState = CircuitState.CLOSED
    _failure_count: int = 0
    _last_failure_time: Optional[datetime] = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def is_open(self) -> bool:
        with self._lock:
            if self._state == CircuitState.OPEN:
                if self._last_failure_time is None:
                    return True
                if datetime.utcnow() - self._last_failure_time > timedelta(seconds=self.reset_timeout):
                    self._state = CircuitState.HALF_OPEN
                    return False
                return True
            return False

    def record_success(self) -> None:
        with self._lock:
            self._failure_count = 0
            self._state = CircuitState.CLOSED
            self._last_failure_time = None

    def record_failure(self) -> None:
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = datetime.utcnow()
            if self._failure_count >= self.failure_threshold:
                self._state = CircuitState.OPEN


BREAKERS: dict[str, CircuitBreaker] = {
    "elevenlabs": CircuitBreaker("elevenlabs", failure_threshold=3, reset_timeout=300),
    "vertex_ai": CircuitBreaker("vertex_ai", failure_threshold=5, reset_timeout=180),
    "gcs": CircuitBreaker("gcs", failure_threshold=5, reset_timeout=120),
    "smtp": CircuitBreaker("smtp", failure_threshold=2, reset_timeout=600),
    "pubsub": CircuitBreaker("pubsub", failure_threshold=5, reset_timeout=120),
}


def get_breaker(name: str) -> CircuitBreaker:
    breaker = BREAKERS.get(name)
    if breaker is None:
        breaker = CircuitBreaker(name)
        BREAKERS[name] = breaker
    return breaker
