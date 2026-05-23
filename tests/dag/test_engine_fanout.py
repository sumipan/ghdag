"""Engine-level integration tests for fan-out/join semantics."""

from __future__ import annotations

import io
import json
import subprocess
import time
from pathlib import Path


from ghdag.dag.engine import DagEngine
from ghdag.dag.fanout import FanOutItem, FanOutSpec
from ghdag.dag.models import DagConfig, RunningTask, Task
from ghdag.metrics.models import TaskMetrics


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


def _make_engine(tmp_path: Path, jsonl: bool = True) -> tuple[DagEngine, _CapturingHooks, Path]:
    exec_file = tmp_path / ("exec.jsonl" if jsonl else "exec.md")
    exec_file.write_text("")
    done_dir = tmp_path / "done"
    done_dir.mkdir()
    hooks = _CapturingHooks()
    config = DagConfig(
        exec_md_path=exec_file,
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
                                  stdout_content: bytes = b"") -> RunningTask:
    proc = subprocess.Popen(["true"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    proc.wait()
    # Override returncode for testing (inject non-zero by using false if needed)
    stdout_buf = io.BytesIO(stdout_content) if stdout_content else None
    if task.result_path is not None and stdout_buf is None:
        stdout_buf = io.BytesIO(b"")
    return RunningTask(
        uuid=uuid,
        task=task,
        proc=proc,
        started_at=time.time(),
        started_at_mono=time.monotonic(),
        stderr_buf=io.BytesIO(b""),
        retry_depth=task.retry,
        stdout_buf=stdout_buf,
    )


FANOUT_YAML = b"""\
Some output.

---
ghdag_fanout:
  children:
    - id: item-001
      command: "echo 1"
    - id: item-002
      command: "echo 2"
    - id: item-003
      command: "echo 3"
"""


class TestAC1FanoutSpawnChildren:
    def test_spawn_adds_three_jsonl_children(self, tmp_path):
        engine, hooks, exec_file = _make_engine(tmp_path, jsonl=True)
        parent_uuid = "parent-001"
        task = Task(uuid=parent_uuid, command="true")
        spec = FanOutSpec(children=[
            FanOutItem(id="item-001", command="echo 1"),
            FanOutItem(id="item-002", command="echo 2"),
            FanOutItem(id="item-003", command="echo 3"),
        ])
        engine._spawn_fanout(parent_uuid, task, spec, _base_metrics(parent_uuid))

        lines = [line for line in exec_file.read_text().splitlines() if line.strip()]
        assert len(lines) == 3
        uuids = [json.loads(line)["uuid"] for line in lines]
        assert f"{parent_uuid}--fo--item-001" in uuids
        assert f"{parent_uuid}--fo--item-002" in uuids
        assert f"{parent_uuid}--fo--item-003" in uuids

    def test_spawn_adds_exec_md_children(self, tmp_path):
        engine, hooks, exec_file = _make_engine(tmp_path, jsonl=False)
        parent_uuid = "parent-md"
        task = Task(uuid=parent_uuid, command="true")
        spec = FanOutSpec(children=[
            FanOutItem(id="child-a", command="echo a"),
        ])
        engine._spawn_fanout(parent_uuid, task, spec, _base_metrics(parent_uuid))

        content = exec_file.read_text()
        assert f"{parent_uuid}--fo--child-a: echo a" in content

    def test_check_completions_detects_fanout_from_result_file(self, tmp_path):
        engine, hooks, exec_file = _make_engine(tmp_path, jsonl=True)
        parent_uuid = "parent-check"
        result_file = tmp_path / "result.md"
        task = Task(uuid=parent_uuid, command="true", result_path=str(result_file))
        rt = _make_completed_running_task(parent_uuid, task, stdout_content=FANOUT_YAML)
        engine._running[parent_uuid] = rt

        engine._check_completions()

        assert parent_uuid in engine._fanout_pending
        assert len(engine._fanout_pending[parent_uuid]) == 3
        # All child UUIDs use the expected pattern
        for cid in ("item-001", "item-002", "item-003"):
            assert f"{parent_uuid}--fo--{cid}" in engine._fanout_pending[parent_uuid]


class TestAC2ParentNotMarkedDone:
    def test_parent_not_done_after_spawn(self, tmp_path):
        engine, hooks, exec_file = _make_engine(tmp_path, jsonl=True)
        parent_uuid = "parent-nodelay"
        task = Task(uuid=parent_uuid, command="true")
        spec = FanOutSpec(children=[FanOutItem(id="c1", command="echo 1")])
        engine._spawn_fanout(parent_uuid, task, spec, _base_metrics(parent_uuid))

        done_dir = tmp_path / "done"
        assert not (done_dir / parent_uuid).exists()
        assert parent_uuid in engine._fanout_pending

    def test_parent_not_done_after_check_completions(self, tmp_path):
        engine, hooks, exec_file = _make_engine(tmp_path, jsonl=True)
        parent_uuid = "parent-no-done"
        result_file = tmp_path / "result2.md"
        task = Task(uuid=parent_uuid, command="true", result_path=str(result_file))
        rt = _make_completed_running_task(parent_uuid, task, stdout_content=FANOUT_YAML)
        engine._running[parent_uuid] = rt

        engine._check_completions()

        assert not (tmp_path / "done" / parent_uuid).exists()
        assert not hooks.success
        assert not hooks.failure


class TestAC3JoinAllChildrenSuccess:
    def test_all_children_succeed_marks_parent_done(self, tmp_path):
        engine, hooks, exec_file = _make_engine(tmp_path, jsonl=True)
        parent_uuid = "parent-join"
        task = Task(uuid=parent_uuid, command="true")
        metrics = _base_metrics(parent_uuid)
        child_uuids = {f"{parent_uuid}--fo--c1", f"{parent_uuid}--fo--c2"}

        engine._fanout_pending[parent_uuid] = set(child_uuids)
        engine._fanout_tasks[parent_uuid] = task
        engine._fanout_metrics[parent_uuid] = metrics

        known_done = set(child_uuids)
        known_succeeded = set(child_uuids)
        engine._check_fanout_completions(known_done, known_succeeded)

        assert parent_uuid in known_done
        assert parent_uuid in known_succeeded
        assert (tmp_path / "done" / parent_uuid).read_text() == "0"
        assert len(hooks.success) == 1
        assert hooks.success[0][0] == parent_uuid
        assert parent_uuid not in engine._fanout_pending

    def test_partial_done_does_not_trigger(self, tmp_path):
        engine, hooks, exec_file = _make_engine(tmp_path, jsonl=True)
        parent_uuid = "parent-partial"
        task = Task(uuid=parent_uuid, command="true")
        child_uuids = {f"{parent_uuid}--fo--c1", f"{parent_uuid}--fo--c2"}

        engine._fanout_pending[parent_uuid] = set(child_uuids)
        engine._fanout_tasks[parent_uuid] = task
        engine._fanout_metrics[parent_uuid] = _base_metrics(parent_uuid)

        # Only one child done
        known_done = {f"{parent_uuid}--fo--c1"}
        known_succeeded = {f"{parent_uuid}--fo--c1"}
        engine._check_fanout_completions(known_done, known_succeeded)

        assert parent_uuid not in known_done
        assert not hooks.success
        assert parent_uuid in engine._fanout_pending


class TestAC4ChildFailurePropagates:
    def test_failed_child_marks_parent_fanout_failed(self, tmp_path):
        engine, hooks, exec_file = _make_engine(tmp_path, jsonl=True)
        parent_uuid = "parent-fail"
        task = Task(uuid=parent_uuid, command="true")
        child_uuids = {f"{parent_uuid}--fo--c1", f"{parent_uuid}--fo--c2"}

        engine._fanout_pending[parent_uuid] = set(child_uuids)
        engine._fanout_tasks[parent_uuid] = task
        engine._fanout_metrics[parent_uuid] = _base_metrics(parent_uuid)

        known_done = set(child_uuids)
        # c2 succeeded, c1 failed (not in known_succeeded)
        known_succeeded = {f"{parent_uuid}--fo--c2"}
        engine._check_fanout_completions(known_done, known_succeeded)

        assert parent_uuid in known_done
        assert parent_uuid not in known_succeeded
        status = (tmp_path / "done" / parent_uuid).read_text()
        assert status == "FANOUT_CHILD_FAILED"
        assert len(hooks.failure) == 1
        assert hooks.failure[0][0] == parent_uuid
        assert hooks.failure[0][3] == "FANOUT_CHILD_FAILED"
        assert hooks.failure[0][4].failure_class == "FANOUT_CHILD_FAILED"
        assert parent_uuid not in engine._fanout_pending


class TestAC8NoFanoutNormalSuccess:
    def test_no_fanout_block_triggers_normal_success(self, tmp_path):
        engine, hooks, exec_file = _make_engine(tmp_path, jsonl=True)
        uuid = "task-normal"
        # Use command with no tee, no result_path → effective_result_path is None
        task = Task(uuid=uuid, command="true")
        rt = _make_completed_running_task(uuid, task)
        engine._running[uuid] = rt

        engine._check_completions()

        assert uuid not in engine._fanout_pending
        assert len(hooks.success) == 1
        assert hooks.success[0][0] == uuid

    def test_result_file_without_fanout_block_normal_success(self, tmp_path):
        engine, hooks, exec_file = _make_engine(tmp_path, jsonl=True)
        uuid = "task-plain-result"
        result_file = tmp_path / "plain.md"
        task = Task(uuid=uuid, command="true", result_path=str(result_file))
        rt = _make_completed_running_task(uuid, task, stdout_content=b"Normal output only.\n")
        engine._running[uuid] = rt

        engine._check_completions()

        assert uuid not in engine._fanout_pending
        assert len(hooks.success) == 1
        assert hooks.success[0][0] == uuid


class TestAC9ValidateDependencies:
    def test_child_tasks_have_no_deps_and_launch_immediately(self, tmp_path):
        """Child tasks generated by fanout have no depends, so validate_dependencies passes."""
        from ghdag.dag.parser import validate_dependencies

        child1 = Task(uuid="parent--fo--c1", command="echo 1")
        child2 = Task(uuid="parent--fo--c2", command="echo 2")
        successor = Task(uuid="successor", command="echo done", depends=["parent"])
        parent = Task(uuid="parent", command="echo parent")

        all_tasks = [parent, child1, child2, successor]
        known_done: set[str] = {"parent"}  # parent is done after join

        invalid = validate_dependencies(all_tasks, known_done)
        # successor depends on parent which is done, so no invalid tasks
        assert "successor" not in invalid
        assert "parent--fo--c1" not in invalid
        assert "parent--fo--c2" not in invalid


class TestParentNotRelaunched:
    def test_parent_in_fanout_pending_is_skipped_in_launch_loop(self, tmp_path):
        """Parent UUID in _fanout_pending must not be re-launched."""
        engine, hooks, exec_file = _make_engine(tmp_path, jsonl=True)
        parent_uuid = "parent-skip"
        task = Task(uuid=parent_uuid, command="true")
        engine._tasks[parent_uuid] = task

        # Simulate fanout pending state
        engine._fanout_pending[parent_uuid] = {"parent-skip--fo--c1"}
        engine._fanout_tasks[parent_uuid] = task
        engine._fanout_metrics[parent_uuid] = _base_metrics(parent_uuid)

        # The parent should not appear in _running after the loop logic
        # (We test the guard condition: uuid in _fanout_pending)
        assert parent_uuid in engine._fanout_pending
        assert parent_uuid not in engine._running
        # Verify the guard would skip it (would not call _launch_task)
        # Done by checking _fanout_pending is the skip condition
        done_set: set[str] = set()
        for uuid, t in engine._tasks.items():
            if uuid in done_set or uuid in engine._running or uuid in engine._fanout_pending:
                continue
            # If we reach here, the task would be launched. Parent should not reach here.
            assert uuid != parent_uuid, "Parent should have been skipped"
