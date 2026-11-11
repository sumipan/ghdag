"""Shared helpers for ghdag.dag.engine tests."""

import json
import re as _re
import sys
import threading
import types
from pathlib import Path

from ghdag.core.models.metrics import TaskMetrics
from ghdag.dag.engine import DagEngine
from ghdag.dag.hooks import DefaultHooks
from ghdag.dag.models import DagConfig


class MemoryHooks(DefaultHooks):
    def __init__(self) -> None:
        super().__init__()
        self.rejected = False
        self.pipeline_status = None
        self.starts: list[tuple] = []
        self.successes: list[tuple] = []
        self.failures: list[tuple] = []
        self.rejected_events: list[tuple] = []
        self.empty_results: list[tuple] = []
        self.dep_failed: list[tuple] = []
        self.shutdown_signum = None
        self._on_task_start_extra = None

    def on_task_start(self, uuid, task):
        self.starts.append((uuid, task))
        if self._on_task_start_extra:
            self._on_task_start_extra(uuid, task)

    def on_task_success(self, uuid, task, metrics: TaskMetrics):
        self.successes.append((uuid, task, metrics))

    def on_task_failure(self, uuid, task, returncode, stderr_text, metrics):
        self.failures.append((uuid, task, returncode, stderr_text, metrics))

    def on_task_rejected(self, uuid, task, retry_depth, is_final, metrics):
        self.rejected_events.append((uuid, task, retry_depth, is_final, metrics))

    def on_task_empty_result(self, uuid, task, stderr_text, metrics):
        self.empty_results.append((uuid, task, stderr_text, metrics))

    def on_task_dep_failed(self, uuid, task, failed_dep):
        self.dep_failed.append((uuid, task, failed_dep))

    def on_shutdown(self, signum):
        self.shutdown_signum = signum

    def check_rejected(self, result_path: str) -> bool:
        return self.rejected

    def check_pipeline_status(self, result_path: str):
        return self.pipeline_status


_MD_LINE_RE = _re.compile(r"^([a-zA-Z0-9\-]+)((?:\[[^\]]+\])*)\s*:\s*(.+)$")
_MD_DEPENDS_RE = _re.compile(r"\[depends:([^\]]+)\]")


def _read_done_status(exec_done_dir: str, uuid: str) -> str:
    return (Path(exec_done_dir) / uuid).read_text().strip()


def _md_to_jsonl(content: str) -> str:
    """Convert exec.md text format to JSONL — test helper only."""
    lines = []
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = _MD_LINE_RE.match(line)
        if not m:
            continue
        uid = m.group(1)
        annotation_str = m.group(2)
        command = m.group(3)
        depends_m = _MD_DEPENDS_RE.search(annotation_str)
        depends = [d.strip() for d in depends_m.group(1).split(",")] if depends_m else []
        lines.append(json.dumps({"uuid": uid, "command": command, "depends": depends}))
    return "\n".join(lines) + ("\n" if lines else "")


def _make_config(tmp_path, exec_md_content: str, **overrides) -> DagConfig:
    exec_jsonl = tmp_path / "exec.jsonl"
    exec_jsonl.parent.mkdir(parents=True, exist_ok=True)
    exec_jsonl.write_text(_md_to_jsonl(exec_md_content), encoding="utf-8")
    defaults = dict(
        exec_jsonl_path=str(exec_jsonl),
        exec_done_dir=str(tmp_path / "jobs" / "done"),
        poll_interval=0.1,
        launch_stagger=0.0,
        lock_file=str(tmp_path / "lock"),
    )
    defaults.update(overrides)
    return DagConfig(**defaults)


def _run_engine_with_timeout(engine: DagEngine, timeout: float = 5.0) -> None:
    """Run engine in a thread and stop it after timeout or all tasks done."""
    t = threading.Thread(target=engine.run, daemon=True)
    t.start()
    t.join(timeout=timeout)
    engine._shutdown = True
    t.join(timeout=2.0)


def _make_jsonl_config(tmp_path, tasks: list[dict], **overrides) -> DagConfig:
    exec_jsonl = tmp_path / "exec.jsonl"
    exec_jsonl.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(t) for t in tasks]
    exec_jsonl.write_text("\n".join(lines), encoding="utf-8")
    defaults = dict(
        exec_jsonl_path=str(exec_jsonl),
        exec_done_dir=str(tmp_path / "jobs" / "done"),
        poll_interval=0.1,
        launch_stagger=0.0,
        lock_file=str(tmp_path / "lock"),
    )
    defaults.update(overrides)
    return DagConfig(**defaults)


def _jsonl_task(uuid: str, command: str, result_path: str) -> dict:
    return {
        "uuid": uuid,
        "command": command,
        "depends": [],
        "result_path": result_path,
        "retry": 0,
        "annotations": {},
    }


_helpers = types.ModuleType("ghdag_dag_conftest_helpers")
sys.modules["ghdag_dag_conftest_helpers"] = _helpers
for _name in (
    "MemoryHooks",
    "_read_done_status",
    "_md_to_jsonl",
    "_make_config",
    "_run_engine_with_timeout",
    "_make_jsonl_config",
    "_jsonl_task",
):
    setattr(_helpers, _name, globals()[_name])
