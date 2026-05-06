"""TaskMetrics dataclass for recording task execution metrics."""

from __future__ import annotations

from dataclasses import dataclass


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
