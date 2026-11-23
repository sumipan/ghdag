"""Tests for ghdag.dag.engine — §5.4 acceptance criteria."""

import json
import threading
from pathlib import Path
from unittest.mock import MagicMock

from ghdag_dag_conftest_helpers import (
    FakeClock,
    _make_config,
    _read_done_status,
    _run_engine_with_timeout,
)

from ghdag.core.models.metrics import TaskMetrics
from ghdag.dag.audit_hooks import AuditHooks
from ghdag.dag.engine import DagEngine
from ghdag.dag.models import DagConfig, Task
from ghdag.io.done import is_done, load_succeeded_from_dir


class TestDependencyResolution:
    """§5.4 dependency resolution"""


    def test_dep_resolved_after_success(self, tmp_path):
        """uuid-b starts after uuid-a succeeds"""
        config = _make_config(
            tmp_path,
            "uuid-a: echo ok\n"
            "uuid-b[depends:uuid-a]: echo done\n",
        )
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        engine = DagEngine(config, hooks)

        _run_engine_with_timeout(engine, timeout=5.0)

        assert is_done(config.exec_done_dir, "uuid-a")
        assert is_done(config.exec_done_dir, "uuid-b")
        succeeded = load_succeeded_from_dir(config.exec_done_dir)
        assert "uuid-a" in succeeded
        assert "uuid-b" in succeeded


class TestAppendTask:
    """§5.4 append_task exclusivity"""


    def test_append_task_concurrent(self, tmp_path):
        """Concurrent append_task() from 2 threads must not interleave lines"""
        config = _make_config(tmp_path, "")
        engine = DagEngine(config, hooks=MagicMock())
        # Don't run the engine loop — just test append_task
        engine._lock_fh = open(str(config.lock_file), "w")

        errors = []

        def appender(prefix: str, count: int):
            try:
                for i in range(count):
                    line = json.dumps({"uuid": f"{prefix}-{i}", "command": f"echo {prefix}-{i}", "depends": []})
                    engine.append_task(line)
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=appender, args=("thread1", 20))
        t2 = threading.Thread(target=appender, args=("thread2", 20))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert not errors

        lines = Path(config.exec_jsonl_path).read_text().strip().split("\n")
        # Should have 40 non-empty lines
        non_empty = [line for line in lines if line.strip()]
        assert len(non_empty) == 40

        # Each line should be a complete JSONL record
        for line in non_empty:
            data = json.loads(line)
            assert "echo " in data["command"]


class TestValidateDependenciesEngine:
    """AC 3: validate_dependencies is integrated into the engine"""

    def test_orphan_dep_marks_dep_failed(self, tmp_path):
        """Orphan-dependency tasks are marked DEP_FAILED"""
        config = _make_config(
            tmp_path,
            "uuid-b[depends:nonexistent-uuid]: echo should-not-run\n",
        )
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        engine = DagEngine(config, hooks)

        _run_engine_with_timeout(engine, timeout=3.0)

        assert is_done(config.exec_done_dir, "uuid-b")
        succeeded = load_succeeded_from_dir(config.exec_done_dir)
        assert "uuid-b" not in succeeded


class TestStdinMissingInputSkip:
    """AC-1–AC-5: skip behavior when stdin input file is missing"""


    def test_existing_stdin_file_launches_normally(self, tmp_path):
        """AC-2: when stdin file exists, starts and completes as before"""
        stdin_file = tmp_path / "input.txt"
        stdin_file.write_text("hello", encoding="utf-8")
        config = _make_config(
            tmp_path,
            f"uuid-a: cat < {stdin_file}\n",
        )
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        engine = DagEngine(config, hooks)

        _run_engine_with_timeout(engine, timeout=5.0)

        assert _read_done_status(config.exec_done_dir, "uuid-a") == "0"


    def test_relative_stdin_uses_cwd(self, tmp_path):
        """AC-5: relative path resolved against cwd; missing → SKIPPED_MISSING_INPUT"""
        work_dir = tmp_path / "work"
        work_dir.mkdir()
        # orders/task.md does not exist
        config = _make_config(
            tmp_path,
            "uuid-a: agent < orders/task.md\n",
            cwd=str(work_dir),
        )
        hooks = MagicMock()
        engine = DagEngine(config, hooks)

        _run_engine_with_timeout(engine, timeout=3.0)

        assert _read_done_status(config.exec_done_dir, "uuid-a") == "SKIPPED_MISSING_INPUT"


class TestEngineModelFromStructuredFields:
    """AC3: task.engine/model priority and parse_engine_model fallback"""

    def _make_jsonl_config(self, tmp_path, jsonl_content: str, **overrides) -> DagConfig:
        exec_jsonl = tmp_path / "exec.jsonl"
        exec_jsonl.parent.mkdir(parents=True, exist_ok=True)
        exec_jsonl.write_text(jsonl_content, encoding="utf-8")
        defaults = dict(
            exec_jsonl_path=str(exec_jsonl),
            exec_done_dir=str(tmp_path / "jobs" / "done"),
            poll_interval=0.1,
            launch_stagger=0.0,
            lock_file=str(tmp_path / "lock"),
        )
        defaults.update(overrides)
        return DagConfig(**defaults)


_TZ_UUID = "tz-test-uuid-0000-0000-0000-000000000001"


def _make_tz_task() -> Task:
    return Task(uuid=_TZ_UUID, command="echo hello")


def _make_tz_metrics() -> TaskMetrics:
    started = 1_735_800_000.0
    return TaskMetrics(
        uuid=_TZ_UUID,
        engine="claude",
        model="claude-sonnet-4-6",
        wall_time_sec=1.0,
        token_count=10,
        status="success",
        started_at=started,
        finished_at=started + 1.0,
    )


def test_fake_clock_advances_without_wallclock_wait() -> None:
    """Refs sumipan/nexus#7833 AC1."""
    clock = FakeClock(100.0)
    clock.sleep(2.5)
    clock.advance(1.5)
    assert clock.time() == 104.0
    assert clock.monotonic() == 104.0
    assert clock.sleeps == [2.5]


def test_audit_hooks_none_uses_machine_timezone(tmp_path, monkeypatch) -> None:
    """Refs sumipan/nexus#6872."""
    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(
        "ghdag.io.audit.now_ts",
        lambda tz_name=None: "2026-01-15T12:00:00+13:00",
    )
    hooks = AuditHooks(audit_path=audit_path, tz_name=None)
    hooks.on_task_success(_TZ_UUID, _make_tz_task(), _make_tz_metrics())

    record = json.loads(audit_path.read_text(encoding="utf-8").strip())
    assert record["timestamp"].endswith("+13:00")
