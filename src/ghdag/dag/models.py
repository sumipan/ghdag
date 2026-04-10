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


@dataclass
class RunningTask:
    uuid: str
    task: Task
    proc: subprocess.Popen
    started_at: float
    stderr_buf: io.BytesIO
    retry_depth: int = 0


@dataclass
class DagConfig:
    exec_md_path: str | Path
    exec_done_dir: str | Path = "exec-done"
    poll_interval: float = 1.0
    launch_stagger: float = 0.5
    max_retry: int = 1
    lock_file: str | Path = "/tmp/ghdag.lock"
    timezone: str = "UTC"
