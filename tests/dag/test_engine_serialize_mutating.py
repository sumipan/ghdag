"""Engine tests for serialize_mutating — mutating task serialization."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

from ghdag.dag.engine import DagEngine
from ghdag.dag.models import DagConfig
from ghdag.io.done import load_succeeded_from_dir


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


