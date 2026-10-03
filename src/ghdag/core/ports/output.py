"""EngineOutputAdapter Protocol."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol, runtime_checkable

from ghdag.core.models.metrics import FailureClass, TokenUsage


class EngineErrorKind(str, Enum):
    CAPACITY = "CAPACITY"
    RATE_LIMIT = "RATE_LIMIT"
    AUTH = "AUTH"
    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class EngineError:
    kind: EngineErrorKind
    message: str
    retryable: bool
    resume_at: datetime | None = None


@runtime_checkable
class EngineOutputAdapter(Protocol):
    """Extract body text and usage from an engine-specific stdout format."""

    def extract_result_text(self, stdout: bytes, stderr: bytes) -> bytes:
        """Return the text bytes to write to result_path from stdout."""
        ...

    def extract_token_usage(self, stdout: bytes, stderr: bytes) -> TokenUsage | None:
        """Extract TokenUsage from stdout/stderr. Return None if unavailable."""
        ...

    def extract_session_id(self, stdout: bytes, stderr: bytes) -> str | None:
        """Extract a resumable session_id from stdout/stderr. Return None if unavailable."""
        ...

    def extract_error(self, stdout: bytes, stderr: bytes) -> EngineError | None:
        """Extract an engine error from stdout/stderr. Return None if no error is detected."""
        ...

    def classify_failure(
        self,
        returncode: int,
        stdout: bytes,
        stderr: bytes,
    ) -> FailureClass | None:
        """Infer a FailureClass from stdout/stderr on abnormal exit. Return None if unknown."""
        ...
