"""TaskMetrics dataclass for recording task execution metrics."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class FailureClass(Enum):
    cause: str
    retry_policy: str

    def __new__(cls, value: str, cause: str, retry_policy: str) -> FailureClass:
        obj = object.__new__(cls)
        obj._value_ = value
        obj.cause = cause
        obj.retry_policy = retry_policy
        return obj

    TIMEOUT = ("TIMEOUT", "transient", "safe")
    REJECTED = ("REJECTED", "permanent", "forbidden")
    ENGINE_ERROR = ("ENGINE_ERROR", "transient", "safe")
    QUOTA_EXHAUSTED = ("QUOTA_EXHAUSTED", "transient", "forbidden")
    AUTH = ("AUTH", "permanent", "forbidden")
    ENGINE_ENVIRONMENT_ERROR = ("ENGINE_ENVIRONMENT_ERROR", "permanent", "forbidden")
    PROCESS_ERROR = ("PROCESS_ERROR", "permanent", "requires_review")
    PIPELINE_FAILED = ("PIPELINE_FAILED", "permanent", "requires_review")
    EMPTY_RESULT = ("EMPTY_RESULT", "unknown", "requires_review")
    FANOUT_CHILD_FAILED = ("FANOUT_CHILD_FAILED", "permanent", "forbidden")
    FANOUT_PARSE_FAILED = ("FANOUT_PARSE_FAILED", "permanent", "requires_review")
    DEP_FAILED = ("DEP_FAILED", "permanent", "forbidden")
    UNKNOWN_FAILURE = ("UNKNOWN_FAILURE", "unknown", "requires_review")


@dataclass(frozen=True)
class TokenUsage:
    token_count: int | None = None
    cost_usd: float | None = None
    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = None


@dataclass(frozen=True)
class TaskMetrics:
    uuid: str
    engine: str | None
    model: str | None
    wall_time_sec: float
    token_count: int | None
    status: str
    started_at: float
    finished_at: float
    correlation_id: str | None = None
    failure_class: FailureClass | None = None
    request_id: str | None = None
    cost_usd: float | None = None
    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = None
    additional_tags: dict[str, str] | None = None
