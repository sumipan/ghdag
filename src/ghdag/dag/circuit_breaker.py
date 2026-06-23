"""CircuitBreakerPolicy — consecutive failure tracking and breaker logic."""

from __future__ import annotations

import time


class CircuitBreakerPolicy:
    """Track consecutive task failures and trip when a threshold is reached."""

    def __init__(self, failure_window_sec: float, max_consecutive_failures: int) -> None:
        self._failure_window_sec = failure_window_sec
        self._max_consecutive_failures = max_consecutive_failures
        self._consecutive_failures: int = 0
        self._last_failure_time: float | None = None
        self._tripped: bool = False

    def record_failure(self) -> bool:
        """Increment consecutive failure counter.

        Resets the counter if the last failure was outside failure_window_sec.
        Returns True if the breaker has just tripped (threshold reached).
        """
        now = time.monotonic()
        if (
            self._last_failure_time is not None
            and (now - self._last_failure_time) > self._failure_window_sec
        ):
            self._consecutive_failures = 0

        self._last_failure_time = now
        self._consecutive_failures += 1

        if self._consecutive_failures >= self._max_consecutive_failures:
            self._tripped = True
            return True
        return False

    def reset(self) -> None:
        """Reset consecutive failure counter on task success."""
        self._consecutive_failures = 0
        self._last_failure_time = None

    @property
    def tripped(self) -> bool:
        """True when the failure threshold has been reached."""
        return self._tripped
