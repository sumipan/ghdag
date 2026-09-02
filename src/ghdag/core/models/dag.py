"""Data definitions for the DAG execution engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Task:
    uuid: str
    command: str
    depends: list[str] = field(default_factory=list)
    retry: int = 0
    annotations: dict[str, str] = field(default_factory=dict)
    result_path: str | None = None
    idempotency_key: str | None = None
    engine: str | None = None
    model: str | None = None
    result_finalize: str | None = None  # "preserve_nonempty" | "stdout_only"


@dataclass
class DagConfig:
    exec_jsonl_path: str | Path
    exec_done_dir: str | Path = "jobs/done"
    poll_interval: float = 1.0
    launch_stagger: float = 0.5
    max_retry: int = 1
    lock_file: str | Path | None = None
    timezone: str = "UTC"
    cwd: str | Path | None = None
    task_timeout: float | None = None
    kill_grace: float = 10.0
    max_concurrency: int | None = None
    serialize_mutating: bool = False
    max_consecutive_failures: int = 5
    failure_window_sec: float = 60.0
    quota_state_path: str | Path | None = None
    quota_audit_path: str | Path | None = None

    def __post_init__(self) -> None:
        queue_dir = Path(self.exec_jsonl_path).parent
        if self.lock_file is None:
            self.lock_file = queue_dir / ".ghdag.lock"
        else:
            self.lock_file = Path(self.lock_file)
        if self.quota_state_path is None:
            self.quota_state_path = queue_dir / "quota-gate.json"
        else:
            self.quota_state_path = Path(self.quota_state_path)
        if self.quota_audit_path is None:
            self.quota_audit_path = queue_dir / "audit.jsonl"
        else:
            self.quota_audit_path = Path(self.quota_audit_path)
