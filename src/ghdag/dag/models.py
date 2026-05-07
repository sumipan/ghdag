"""Data definitions for the DAG execution engine."""

from __future__ import annotations

import io
import subprocess
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


@dataclass
class RunningTask:
    uuid: str
    task: Task
    proc: subprocess.Popen
    started_at: float
    started_at_mono: float
    stderr_buf: io.BytesIO
    retry_depth: int = 0
    term_sent_at: float | None = None
    stdout_buf: io.BytesIO | None = None


@dataclass
class DagConfig:
    exec_md_path: str | Path
    exec_done_dir: str | Path = "jobs/done"
    poll_interval: float = 1.0
    launch_stagger: float = 0.5
    max_retry: int = 1
    lock_file: str | Path | None = None
    timezone: str = "UTC"
    cwd: str | Path | None = None
    task_timeout: float | None = None
    kill_grace: float = 10.0

    def __post_init__(self) -> None:
        if self.lock_file is None:
            self.lock_file = Path(self.exec_md_path).parent / ".ghdag.lock"
        else:
            self.lock_file = Path(self.lock_file)
