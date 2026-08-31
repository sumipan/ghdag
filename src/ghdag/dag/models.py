"""Data definitions for the DAG execution engine."""

from __future__ import annotations

import io
import subprocess
import threading
from dataclasses import dataclass

from ghdag.core.models.dag import DagConfig, Task

__all__ = ["Task", "DagConfig", "RunningTask"]


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
    stderr_thread: threading.Thread | None = None
    stdout_thread: threading.Thread | None = None
