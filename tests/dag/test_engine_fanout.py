"""Engine-level integration tests for fan-out/join semantics."""

from __future__ import annotations

import io
import json
import subprocess
import time
from pathlib import Path

from ghdag.core.models.metrics import FailureClass, TaskMetrics
from ghdag.dag.engine import DagEngine
from ghdag.dag.fanout import FanOutItem, FanOutSpec
from ghdag.dag.models import DagConfig, Task, _RunningProcess


class _CapturingHooks:
    def __init__(self):
        self.success: list[tuple] = []
        self.failure: list[tuple] = []
        self.dep_failed: list[tuple] = []

    def on_task_success(self, uuid, task, metrics):
        self.success.append((uuid, task, metrics))

    def on_task_failure(self, uuid, task, returncode, stderr_text, metrics):
        self.failure.append((uuid, task, returncode, stderr_text, metrics))

    def on_task_rejected(self, uuid, task, retry_depth, is_final, metrics):
        pass

    def on_task_dep_failed(self, uuid, task, failed_dep):
        self.dep_failed.append((uuid, task, failed_dep))

    def on_task_empty_result(self, uuid, task, stderr_text, metrics):
        pass

    def on_shutdown(self, signum):
        pass

    def check_rejected(self, result_path):
        return False

    def check_pipeline_status(self, result_path):
        return None

    def on_task_start(self, uuid, task):
        pass

    def check_promote_target(self, result_path):
        return None


def _make_engine(tmp_path: Path) -> tuple[DagEngine, _CapturingHooks, Path]:
    exec_file = tmp_path / "exec.jsonl"
    exec_file.write_text("")
    done_dir = tmp_path / "done"
    done_dir.mkdir(exist_ok=True)
    hooks = _CapturingHooks()
    config = DagConfig(
        exec_jsonl_path=exec_file,
        exec_done_dir=done_dir,
        lock_file=tmp_path / ".lock",
    )
    engine = DagEngine(config, hooks)
    return engine, hooks, exec_file


def _base_metrics(uuid: str) -> TaskMetrics:
    t = time.time()
    return TaskMetrics(
        uuid=uuid, engine=None, model=None,
        wall_time_sec=1.0, token_count=None, status="success",
        started_at=t, finished_at=t,
    )


def _make_completed_running_task(uuid: str, task: Task, returncode: int = 0,
                                  stdout_content: bytes = b"") -> _RunningProcess:
    proc = subprocess.Popen(["true"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    proc.wait()
    stdout_buf = io.BytesIO(stdout_content) if stdout_content else None
    if task.result_path is not None and stdout_buf is None:
        stdout_buf = io.BytesIO(b"")
    return _RunningProcess(
        uuid=uuid,
        task=task,
        proc=proc,
        started_at=time.time(),
        started_at_mono=time.monotonic(),
        stderr_buf=io.BytesIO(b""),
        retry_depth=task.retry,
        stdout_buf=stdout_buf,
    )


FANOUT_REPLY_BLOCK = b"""\
Some output.

```ghdag
status: ok
children:
  - id: item-001
    command: "echo 1"
  - id: item-002
    command: "echo 2"
  - id: item-003
    command: "echo 3"
```
"""


class TestAC1FanoutSpawnChildren:


    def test_reply_block_prompt_child_is_enqueued_as_llm_task(self, tmp_path):
        engine, hooks, exec_file = _make_engine(tmp_path)
        parent_uuid = "parent-block"
        result_file = tmp_path / "result-block.md"
        task = Task(
            uuid=parent_uuid,
            command="claude -p < order.md",
            engine="claude",
            result_path=str(result_file),
        )
        reply = b"""\
```ghdag
status: ok
children:
  - id: child
    prompt: do child
```
"""
        engine._launcher._running[parent_uuid] = _make_completed_running_task(
            parent_uuid, task, stdout_content=reply
        )

        engine._launcher.check_completions()

        records = [json.loads(line) for line in exec_file.read_text().splitlines()]
        assert len(records) == 1
        assert records[0]["uuid"] == f"{parent_uuid}--fo--child"
        assert records[0]["engine"] == "claude"
        assert records[0]["annotations"]["fanout_parent"] == parent_uuid
        order_path = Path(records[0]["command"].rsplit(" < ", 1)[1])
        assert order_path.read_text(encoding="utf-8") == "do child"


class TestAC3JoinAllChildrenSuccess:
    def test_all_children_succeed_marks_parent_done(self, tmp_path):
        engine, hooks, exec_file = _make_engine(tmp_path)
        parent_uuid = "parent-join"
        task = Task(uuid=parent_uuid, command="true")
        metrics = _base_metrics(parent_uuid)
        child_uuids = {f"{parent_uuid}--fo--c1", f"{parent_uuid}--fo--c2"}

        engine._fanout_manager._pending[parent_uuid] = set(child_uuids)
        engine._fanout_manager._tasks[parent_uuid] = task
        engine._fanout_manager._metrics[parent_uuid] = metrics

        known_done = set(child_uuids)
        known_succeeded = set(child_uuids)
        engine._fanout_manager.check_completions(known_done, known_succeeded)

        assert parent_uuid in known_done
        assert parent_uuid in known_succeeded
        assert (tmp_path / "done" / parent_uuid).read_text() == "0"
        assert len(hooks.success) == 1
        assert hooks.success[0][0] == parent_uuid
        assert parent_uuid not in engine._fanout_manager._pending

    def test_partial_done_does_not_trigger(self, tmp_path):
        engine, hooks, exec_file = _make_engine(tmp_path)
        parent_uuid = "parent-partial"
        task = Task(uuid=parent_uuid, command="true")
        child_uuids = {f"{parent_uuid}--fo--c1", f"{parent_uuid}--fo--c2"}

        engine._fanout_manager._pending[parent_uuid] = set(child_uuids)
        engine._fanout_manager._tasks[parent_uuid] = task
        engine._fanout_manager._metrics[parent_uuid] = _base_metrics(parent_uuid)

        known_done = {f"{parent_uuid}--fo--c1"}
        known_succeeded = {f"{parent_uuid}--fo--c1"}
        engine._fanout_manager.check_completions(known_done, known_succeeded)

        assert parent_uuid not in known_done
        assert not hooks.success
        assert parent_uuid in engine._fanout_manager._pending


class TestAC4ChildFailurePropagates:
    def test_failed_child_marks_parent_fanout_failed(self, tmp_path):
        engine, hooks, exec_file = _make_engine(tmp_path)
        parent_uuid = "parent-fail"
        task = Task(uuid=parent_uuid, command="true")
        child_uuids = {f"{parent_uuid}--fo--c1", f"{parent_uuid}--fo--c2"}

        engine._fanout_manager._pending[parent_uuid] = set(child_uuids)
        engine._fanout_manager._tasks[parent_uuid] = task
        engine._fanout_manager._metrics[parent_uuid] = _base_metrics(parent_uuid)

        known_done = set(child_uuids)
        known_succeeded = {f"{parent_uuid}--fo--c2"}
        engine._fanout_manager.check_completions(known_done, known_succeeded)

        assert parent_uuid in known_done
        assert parent_uuid not in known_succeeded
        status = (tmp_path / "done" / parent_uuid).read_text()
        assert status == "FANOUT_CHILD_FAILED"
        assert len(hooks.failure) == 1
        assert hooks.failure[0][0] == parent_uuid
        assert hooks.failure[0][3] == "FANOUT_CHILD_FAILED"
        assert hooks.failure[0][4].failure_class == FailureClass.FANOUT_CHILD_FAILED
        assert parent_uuid not in engine._fanout_manager._pending


class TestAC2FanoutParseFailed:

    def test_invalid_fanout_child_marks_parent_fanout_parse_failed(self, tmp_path):
        engine, hooks, exec_file = _make_engine(tmp_path)
        uuid = "parent-fanout-fail"
        result_file = tmp_path / "result_fanout.md"
        task = Task(uuid=uuid, command="true", result_path=str(result_file))

        invalid_fanout = b"""\
Some output.

```ghdag
status: ok
children:
  - id: bad
```
"""
        rt = _make_completed_running_task(uuid, task, stdout_content=invalid_fanout)
        engine._launcher._running[uuid] = rt

        engine._launcher.check_completions()

        done_file = tmp_path / "done" / uuid
        assert done_file.exists()
        assert done_file.read_text() == "FANOUT_PARSE_FAILED"

        assert len(hooks.failure) == 1
        assert hooks.failure[0][0] == uuid
        assert hooks.failure[0][4].failure_class == FailureClass.FANOUT_PARSE_FAILED
        assert not hooks.success

        lines = [ln for ln in exec_file.read_text().splitlines() if ln.strip()]
        assert len(lines) == 0


def _spawn_command_children(engine: DagEngine, exec_file: Path, parent_uuid: str) -> set[str]:
    """Refs sumipan/nexus#6936 AC1."""
    appended: list[str] = []

    def capture(line: str, _parent: str) -> None:
        exec_file.write_text(
            (exec_file.read_text() if exec_file.exists() else "") + line + "\n",
            encoding="utf-8",
        )
        appended.append(line)

    engine._fanout_manager._append_task_fn = capture
    task = Task(uuid=parent_uuid, command="true")
    spec = FanOutSpec(
        children=[
            FanOutItem(id="c1", command="echo 1"),
            FanOutItem(id="c2", command="echo 2"),
        ]
    )
    metrics = _base_metrics(parent_uuid)
    engine._fanout_manager.spawn(parent_uuid, task, spec, metrics)
    return {f"{parent_uuid}--fo--c1", f"{parent_uuid}--fo--c2"}


class TestPersistedFanoutPending:
    def test_restore_after_spawn_marks_parent_pending(self, tmp_path):
        """Refs sumipan/nexus#6936 AC1."""
        engine1, _, exec_file = _make_engine(tmp_path)
        parent_uuid = "parent-restore"
        _spawn_command_children(engine1, exec_file, parent_uuid)

        engine2, _, _ = _make_engine(tmp_path)
        tasks = {parent_uuid: Task(uuid=parent_uuid, command="true")}
        restored = engine2._fanout_manager.restore(tasks, set())

        assert restored == [parent_uuid]
        assert engine2._fanout_manager.is_pending(parent_uuid)

    def test_restored_parent_completes_when_children_done(self, tmp_path):
        """Refs sumipan/nexus#6936 AC2."""
        engine1, hooks, exec_file = _make_engine(tmp_path)
        parent_uuid = "parent-join-restore"
        child_uuids = _spawn_command_children(engine1, exec_file, parent_uuid)

        engine2, hooks2, _ = _make_engine(tmp_path)
        tasks = {parent_uuid: Task(uuid=parent_uuid, command="true")}
        engine2._fanout_manager.restore(tasks, set())

        for child in child_uuids:
            (tmp_path / "done" / child).write_text("0", encoding="utf-8")
        known_done = set(child_uuids)
        known_succeeded = set(child_uuids)
        engine2._fanout_manager.check_completions(known_done, known_succeeded)

        assert (tmp_path / "done" / parent_uuid).read_text() == "0"
        assert len(hooks2.success) == 1
        assert hooks2.success[0][0] == parent_uuid
        pending_path = tmp_path / "fanout-pending" / f"{parent_uuid}.json"
        assert not pending_path.exists()

    def test_restore_drops_file_when_parent_already_done(self, tmp_path):
        """Refs sumipan/nexus#6936 AC6."""
        engine1, _, _ = _make_engine(tmp_path)
        parent_uuid = "parent-stale"
        task = Task(uuid=parent_uuid, command="true")
        metrics = _base_metrics(parent_uuid)
        payload = {
            "children": [f"{parent_uuid}--fo--c1"],
            "metrics": {
                "uuid": parent_uuid,
                "engine": None,
                "model": None,
                "wall_time_sec": 1.0,
                "token_count": None,
                "status": "success",
                "started_at": metrics.started_at,
                "finished_at": metrics.finished_at,
                "correlation_id": None,
                "failure_class": None,
                "failure_class_reason": None,
                "request_id": None,
                "cost_usd": None,
                "cache_read_tokens": None,
                "cache_creation_tokens": None,
                "additional_tags": None,
                "stderr_path": None,
            },
        }
        pending_dir = tmp_path / "fanout-pending"
        pending_dir.mkdir(parents=True)
        pending_file = pending_dir / f"{parent_uuid}.json"
        pending_file.write_text(json.dumps(payload), encoding="utf-8")
        (tmp_path / "done" / parent_uuid).write_text("0", encoding="utf-8")

        restored = engine1._fanout_manager.restore({parent_uuid: task}, {parent_uuid})

        assert restored == []
        assert not engine1._fanout_manager.is_pending(parent_uuid)
        assert not pending_file.exists()


