"""Tests for result_finalize policy (AC1-AC3 from issue #1140)."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from unittest.mock import MagicMock

from ghdag.dag.engine import DagEngine
from ghdag.dag.models import DagConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(tmp_path: Path, records: list[dict], **overrides) -> DagConfig:
    exec_jsonl = tmp_path / "exec.jsonl"
    exec_jsonl.write_text(
        "\n".join(json.dumps(r) for r in records) + "\n",
        encoding="utf-8",
    )
    defaults = dict(
        exec_jsonl_path=str(exec_jsonl),
        exec_done_dir=str(tmp_path / "jobs" / "done"),
        poll_interval=0.1,
        launch_stagger=0.0,
        lock_file=str(tmp_path / "lock"),
    )
    defaults.update(overrides)
    return DagConfig(**defaults)


def _run_engine(engine: DagEngine) -> None:
    finished = threading.Event()
    hooks = engine._hooks

    prior_rejected = hooks.on_task_rejected.side_effect

    def mark_done_rejected(*args, **kwargs) -> None:
        finished.set()
        if callable(prior_rejected):
            prior_rejected(*args, **kwargs)

    if hooks.check_rejected.return_value:
        hooks.on_task_rejected.side_effect = mark_done_rejected
    else:
        hooks.on_task_success.side_effect = lambda *_a, **_k: finished.set()

    thread = threading.Thread(target=engine.run, daemon=True)
    thread.start()
    finished.wait()
    engine._shutdown = True
    thread.join()


def _make_hooks(*, rejected: bool = False) -> MagicMock:
    hooks = MagicMock()
    hooks.check_rejected.return_value = rejected
    hooks.check_pipeline_status.return_value = None
    hooks.check_promote_target.return_value = None
    return hooks


# ---------------------------------------------------------------------------
# AC1: result_finalize policy branch tests
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# AC2: parser can parse result_finalize
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# AC3: result_path deletion on retry
# ---------------------------------------------------------------------------

class TestRetryResultPathCleanup:
    """AC3: result_path is deleted before a rejected task is retried"""


    def test_final_rejected_does_not_clear_result_path(self, tmp_path):
        """On REJECTED_FINAL, do not delete result_path"""
        result_path = tmp_path / "result.md"
        original_content = "REJECTED: final\nold content"
        result_path.write_text(original_content, encoding="utf-8")

        result_path_at_callback: list[bool] = []

        def on_rejected(uuid, task, retry_depth, is_final, metrics):
            result_path_at_callback.append(result_path.exists())

        config = _make_config(tmp_path, [
            {
                "uuid": "uuid-a",
                "command": "printf 'REJECTED: final'",
                "depends": [],
                "result_path": str(result_path),
                "retry": 1,  # already retried
                "annotations": {},
            }
        ], max_retry=1)

        hooks = _make_hooks(rejected=True)
        hooks.on_task_rejected.side_effect = on_rejected
        engine = DagEngine(config, hooks)
        _run_engine(engine)

        hooks.on_task_rejected.assert_called_once()
        # REJECTED_FINAL so the file should still exist
        assert result_path_at_callback == [True]
