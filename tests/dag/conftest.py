"""Shared helpers for ghdag.dag.engine tests."""

import json
import re as _re
import sys
import threading
import types
from collections.abc import Callable
from pathlib import Path

import pytest

import ghdag.dag.circuit_breaker as _circuit_breaker_mod
import ghdag.dag.engine as _engine_mod
import ghdag.dag.task_launcher as _task_launcher_mod
from ghdag.core.models.metrics import TaskMetrics
from ghdag.dag.engine import DagEngine
from ghdag.dag.hooks import DefaultHooks
from ghdag.dag.models import DagConfig

RUNNING_WALL_START = 1_700_000_000.0
RUNNING_MONO_START = 9_999.9


class FakeClock:
    """Deterministic clock for engine tests (docs/TEST.md T13)."""

    def __init__(self, initial: float = 1_000_000.0) -> None:
        self._now = initial
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self._now

    def time(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.advance(seconds)


def _make_bound_time(clock: FakeClock) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        monotonic=clock.monotonic,
        time=clock.time,
        sleep=clock.sleep,
    )


@pytest.fixture
def fake_clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    clock = FakeClock()
    bound = _make_bound_time(clock)
    for module in (_engine_mod, _task_launcher_mod, _circuit_breaker_mod):
        monkeypatch.setattr(module, "time", bound)
    return clock


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
        self._cond = threading.Condition()

    def wait_for(self, predicate: Callable[[], bool]) -> bool:
        with self._cond:
            return self._cond.wait_for(predicate)

    def on_task_start(self, uuid, task):
        with self._cond:
            self.starts.append((uuid, task))
            if self._on_task_start_extra:
                self._on_task_start_extra(uuid, task)
            self._cond.notify_all()

    def on_task_success(self, uuid, task, metrics: TaskMetrics):
        with self._cond:
            self.successes.append((uuid, task, metrics))
            self._cond.notify_all()

    def on_task_failure(self, uuid, task, returncode, stderr_text, metrics):
        with self._cond:
            self.failures.append((uuid, task, returncode, stderr_text, metrics))
            self._cond.notify_all()

    def on_task_rejected(self, uuid, task, retry_depth, is_final, metrics):
        with self._cond:
            self.rejected_events.append((uuid, task, retry_depth, is_final, metrics))
            self._cond.notify_all()

    def on_task_empty_result(self, uuid, task, stderr_text, metrics):
        with self._cond:
            self.empty_results.append((uuid, task, stderr_text, metrics))
            self._cond.notify_all()

    def on_task_dep_failed(self, uuid, task, failed_dep):
        with self._cond:
            self.dep_failed.append((uuid, task, failed_dep))
            self._cond.notify_all()

    def on_shutdown(self, signum):
        with self._cond:
            self.shutdown_signum = signum
            self._cond.notify_all()

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


def _expected_uuids(config: DagConfig) -> list[str]:
    exec_path = Path(config.exec_jsonl_path)
    if not exec_path.exists():
        return []
    uuids: list[str] = []
    for line in exec_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            uuids.append(json.loads(line)["uuid"])
    return uuids


def _all_tasks_settled(engine: DagEngine) -> bool:
    uuids = _expected_uuids(engine._config)
    done_dir = Path(engine._config.exec_done_dir)
    if not uuids:
        return engine._launcher.running_count == 0
    for uuid in uuids:
        if not (done_dir / uuid).exists() and engine._launcher.is_running(uuid):
            return False
        if not (done_dir / uuid).exists():
            return False
    return engine._launcher.running_count == 0


def _run_engine_with_timeout(engine: DagEngine, timeout: float = 5.0) -> None:
    """Run engine until tasks settle, then stop without wall-clock joins."""
    del timeout
    state_changed = threading.Condition()
    check_completions = engine._launcher.check_completions

    def check_completions_and_notify() -> None:
        try:
            check_completions()
        finally:
            with state_changed:
                state_changed.notify_all()

    def run_and_notify() -> None:
        try:
            engine.run()
        finally:
            with state_changed:
                state_changed.notify_all()

    engine._launcher.check_completions = check_completions_and_notify
    t = threading.Thread(target=run_and_notify, daemon=True)
    t.start()
    with state_changed:
        state_changed.wait_for(lambda: not t.is_alive() or _all_tasks_settled(engine))
    engine._shutdown = True
    t.join()


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
    "FakeClock",
    "MemoryHooks",
    "RUNNING_WALL_START",
    "RUNNING_MONO_START",
    "_read_done_status",
    "_md_to_jsonl",
    "_make_config",
    "_run_engine_with_timeout",
    "_make_jsonl_config",
    "_jsonl_task",
):
    setattr(_helpers, _name, globals()[_name])
