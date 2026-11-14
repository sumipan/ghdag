"""QuotaAdmissionPort — admission boundary for io without importing ghdag.quota."""

from __future__ import annotations

from typing import Literal, Protocol

AdmissionPhase = Literal["enqueue", "launch", "runtime"]


class QuotaAdmissionPort(Protocol):
    """Structural port for enqueue-time quota admission (exec_jsonl.append)."""

    def admit(
        self,
        *,
        task_uuid: str,
        engine: str | None,
        phase: AdmissionPhase,
    ) -> object: ...
