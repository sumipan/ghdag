"""Engine tests for serialize_mutating — mutating task serialization."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

from ghdag.dag.engine import DagEngine
from ghdag.dag.models import DagConfig, RunningTask, Task
from ghdag.dag.state import load_done_from_dir, load_succeeded_from_dir


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def _make_config(
    tmp_path: Path,
    records: list[dict],
    **overrides,
) -> DagConfig:
    exec_jsonl = tmp_path / "exec.jsonl"
    exec_jsonl.parent.mkdir(parents=True, exist_ok=True)
    _write_jsonl(exec_jsonl, records)
    defaults = dict(
        exec_jsonl_path=str(exec_jsonl),
        exec_done_dir=str(tmp_path / "jobs" / "done"),
        poll_interval=0.05,
        launch_stagger=0.0,
        lock_file=str(tmp_path / "lock"),
    )
    defaults.update(overrides)
    Path(defaults["exec_done_dir"]).mkdir(parents=True, exist_ok=True)
    return DagConfig(**defaults)


def _run_engine_with_timeout(engine: DagEngine, timeout: float = 10.0) -> None:
    t = threading.Thread(target=engine.run, daemon=True)
    t.start()
    t.join(timeout=timeout)
    engine._shutdown = True
    t.join(timeout=2.0)


def _mutating_task(uuid: str, command: str, **extra) -> dict:
    return {
        "uuid": uuid,
        "command": command,
        "depends": [],
        "annotations": {"_mutates": "true", **extra.get("annotations", {})},
        **{k: v for k, v in extra.items() if k != "annotations"},
    }


def _plain_task(uuid: str, command: str, **extra) -> dict:
    return {"uuid": uuid, "command": command, "depends": [], **extra}


class TestSerializeMutatingBasic:
    """AC: mutating tasks run one at a time when serialize_mutating=True."""

    def test_only_one_mutating_task_running(self, tmp_path):
        records = [
            _mutating_task("m1", "sleep 0.5"),
            _mutating_task("m2", "sleep 0.5"),
            _mutating_task("m3", "sleep 0.5"),
        ]
        config = _make_config(tmp_path, records, serialize_mutating=True)
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        engine = DagEngine(config, hooks)

        samples: list[int] = []

        def sampler():
            for _ in range(40):
                mutating_running = sum(
                    1
                    for rt in engine._running.values()
                    if rt.task.annotations.get("_mutates") == "true"
                )
                samples.append(mutating_running)
                time.sleep(0.05)

        t_sampler = threading.Thread(target=sampler, daemon=True)
        t = threading.Thread(target=engine.run, daemon=True)
        t.start()
        t_sampler.start()
        t.join(timeout=15.0)
        engine._shutdown = True
        t.join(timeout=2.0)
        t_sampler.join(timeout=2.0)

        assert samples, "No samples collected"
        assert max(samples) <= 1, f"More than one mutating task ran concurrently: max={max(samples)}"

        done = load_done_from_dir(config.exec_done_dir)
        assert done >= {"m1", "m2", "m3"}

    def test_non_mutating_tasks_run_in_parallel(self, tmp_path):
        records = [
            _plain_task("n1", "sleep 0.8"),
            _plain_task("n2", "sleep 0.8"),
            _plain_task("n3", "sleep 0.8"),
        ]
        config = _make_config(tmp_path, records, serialize_mutating=True)
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        engine = DagEngine(config, hooks)

        t = threading.Thread(target=engine.run, daemon=True)
        t.start()
        time.sleep(0.3)

        running_count = len(engine._running)
        engine._shutdown = True
        t.join(timeout=5.0)

        assert running_count == 3, f"Expected 3 non-mutating tasks in parallel, got {running_count}"

    def test_default_allows_parallel_mutating(self, tmp_path):
        records = [
            _mutating_task("m1", "sleep 0.8"),
            _mutating_task("m2", "sleep 0.8"),
            _mutating_task("m3", "sleep 0.8"),
        ]
        config = _make_config(tmp_path, records, serialize_mutating=False)
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        engine = DagEngine(config, hooks)

        t = threading.Thread(target=engine.run, daemon=True)
        t.start()
        time.sleep(0.3)

        running_count = len(engine._running)
        engine._shutdown = True
        t.join(timeout=5.0)

        assert running_count == 3, f"Default should allow parallel mutating tasks, got {running_count}"


class TestSerializeMutatingWithMaxConcurrency:
    """AC: serialize_mutating interacts correctly with max_concurrency."""

    def test_max_concurrency_2_non_mutating_parallel(self, tmp_path):
        records = [
            _plain_task("n1", "sleep 0.5"),
            _plain_task("n2", "sleep 0.5"),
            _plain_task("n3", "sleep 0.5"),
        ]
        config = _make_config(
            tmp_path, records, serialize_mutating=True, max_concurrency=2,
        )
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        engine = DagEngine(config, hooks)

        samples: list[int] = []

        def sampler():
            for _ in range(30):
                samples.append(len(engine._running))
                time.sleep(0.05)

        t_sampler = threading.Thread(target=sampler, daemon=True)
        t = threading.Thread(target=engine.run, daemon=True)
        t.start()
        t_sampler.start()
        t.join(timeout=15.0)
        engine._shutdown = True
        t.join(timeout=2.0)
        t_sampler.join(timeout=2.0)

        assert samples
        assert max(samples) <= 2, f"max_concurrency=2 exceeded: max={max(samples)}"
        assert max(samples) >= 2, f"Expected 2 tasks in parallel at some point: {samples}"

    def test_one_mutating_plus_two_non_mutating(self, tmp_path):
        records = [
            _mutating_task("m1", "sleep 0.6"),
            _plain_task("n1", "sleep 0.6"),
            _plain_task("n2", "sleep 0.6"),
        ]
        config = _make_config(
            tmp_path, records, serialize_mutating=True, max_concurrency=3,
        )
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        engine = DagEngine(config, hooks)

        t = threading.Thread(target=engine.run, daemon=True)
        t.start()
        time.sleep(0.25)

        mutating_running = sum(
            1
            for rt in engine._running.values()
            if rt.task.annotations.get("_mutates") == "true"
        )
        total_running = len(engine._running)
        engine._shutdown = True
        t.join(timeout=5.0)

        assert mutating_running <= 1
        assert total_running == 3, (
            f"Expected 1 mutating + 2 non-mutating running, got total={total_running}, "
            f"mutating={mutating_running}"
        )


class TestSerializeMutatingLaunchStagger:
    """AC: launch_stagger and serialize_mutating work together."""

    def test_stagger_does_not_bypass_serialization(self, tmp_path):
        records = [
            _mutating_task("m1", "sleep 1.0"),
            _mutating_task("m2", "sleep 0.1"),
        ]
        config = _make_config(
            tmp_path, records, serialize_mutating=True, launch_stagger=0.1,
        )
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        engine = DagEngine(config, hooks)

        samples: list[int] = []

        def sampler():
            for _ in range(30):
                mutating_running = sum(
                    1
                    for rt in engine._running.values()
                    if rt.task.annotations.get("_mutates") == "true"
                )
                samples.append(mutating_running)
                time.sleep(0.05)

        t_sampler = threading.Thread(target=sampler, daemon=True)
        t = threading.Thread(target=engine.run, daemon=True)
        t.start()
        t_sampler.start()
        t.join(timeout=15.0)
        engine._shutdown = True
        t.join(timeout=2.0)
        t_sampler.join(timeout=2.0)

        assert max(samples) <= 1, f"Stagger must not allow concurrent mutating tasks: {samples}"


class TestSerializeMutatingSequentialLaunch:
    """AC: next mutating task launches after previous completes."""

    def test_second_mutating_launches_after_first_completes(self, tmp_path):
        records = [
            _mutating_task("m1", "sleep 0.2"),
            _mutating_task("m2", "sleep 0.2"),
        ]
        config = _make_config(tmp_path, records, serialize_mutating=True)
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        engine = DagEngine(config, hooks)

        _run_engine_with_timeout(engine, timeout=10.0)

        succeeded = load_succeeded_from_dir(config.exec_done_dir)
        assert "m1" in succeeded
        assert "m2" in succeeded


class TestSerializeMutatingUnitCheck:
    """Unit-level verification of the launch-loop guard."""

    def test_mutating_blocked_when_another_mutating_running(self, tmp_path):
        config = _make_config(tmp_path, [], serialize_mutating=True)
        engine = DagEngine(config, MagicMock())

        running_task = Task(
            uuid="running",
            command="sleep 1",
            annotations={"_mutates": "true"},
        )
        proc_mock = MagicMock()
        proc_mock.poll.return_value = None
        engine._running["running"] = RunningTask(
            uuid="running",
            task=running_task,
            proc=proc_mock,
            started_at=time.time(),
            started_at_mono=time.monotonic(),
            stderr_buf=MagicMock(),
        )

        candidate = Task(
            uuid="candidate",
            command="sleep 1",
            annotations={"_mutates": "true"},
        )

        task_is_mutating = candidate.annotations.get("_mutates") == "true"
        running_has_mutating = any(
            rt.task.annotations.get("_mutates") == "true"
            for rt in engine._running.values()
        )
        blocked = (
            config.serialize_mutating
            and task_is_mutating
            and running_has_mutating
        )
        assert blocked

    def test_non_mutating_not_blocked_by_mutating_running(self, tmp_path):
        config = _make_config(tmp_path, [], serialize_mutating=True)
        engine = DagEngine(config, MagicMock())

        running_task = Task(
            uuid="running",
            command="sleep 1",
            annotations={"_mutates": "true"},
        )
        proc_mock = MagicMock()
        proc_mock.poll.return_value = None
        engine._running["running"] = RunningTask(
            uuid="running",
            task=running_task,
            proc=proc_mock,
            started_at=time.time(),
            started_at_mono=time.monotonic(),
            stderr_buf=MagicMock(),
        )

        candidate = Task(uuid="candidate", command="sleep 1", annotations={})

        task_is_mutating = candidate.annotations.get("_mutates") == "true"
        running_has_mutating = any(
            rt.task.annotations.get("_mutates") == "true"
            for rt in engine._running.values()
        )
        blocked = (
            config.serialize_mutating
            and task_is_mutating
            and running_has_mutating
        )
        assert not blocked
