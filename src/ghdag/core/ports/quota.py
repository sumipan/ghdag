"""QuotaAdmissionPort — admission boundary for io without importing ghdag.quota."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Literal, Protocol

from ghdag.core.models.metrics import QuotaWindow

AdmissionPhase = Literal["enqueue", "launch", "runtime"]

QuotaStatus = Literal["available", "paused"]
PauseKind = Literal["budget", "failure", "manual", "drain"]


class QuotaAdmissionPort(Protocol):
    """Structural port for enqueue-time quota admission (exec_jsonl.append)."""

    def admit(
        self,
        *,
        task_uuid: str,
        engine: str | None,
        phase: AdmissionPhase,
    ) -> object: ...


class QuotaEngineStateView(Protocol):
    @property
    def status(self) -> str: ...


class QuotaSnapshotView(Protocol):
    @property
    def engines(self) -> Mapping[str, QuotaEngineStateView]: ...


class QuotaCallPort(Protocol):
    """Structural port for quota operations used by ``ghdag.llm.engines.call``."""

    def snapshot(self, *, now: datetime | None = None) -> QuotaSnapshotView: ...

    def report(
        self,
        *,
        engine: str,
        status: QuotaStatus,
        observed_at: datetime,
        resume_at: datetime | None = None,
        reason: str | None = None,
        kind: PauseKind = "failure",
        set_by: str = "ghdag",
    ) -> object: ...

    def increment_auth_strike(self, *, engine: str, observed_at: datetime) -> int: ...

    def reset_auth_strike(self, *, engine: str) -> None: ...

    def observe(
        self,
        *,
        engine: str,
        windows: Sequence[QuotaWindow],
        observed_at: datetime,
    ) -> None: ...
