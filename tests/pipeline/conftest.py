"""Shared helpers for ghdag.pipeline task queue tests."""

from __future__ import annotations

import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import MagicMock

from ghdag.io.audit import AuditContext
from ghdag.pipeline.task_queue import TaskQueue

_TEST_AUDIT_CTX = AuditContext(source="test")


def _track_build_order(order_builder: MagicMock) -> list[tuple[str, dict]]:
    """Record build_order(template, ctx) calls via side_effect."""
    captured: list[tuple[str, dict]] = []

    def _side(template: str, ctx: dict) -> str:
        captured.append((template, ctx))
        return order_builder.build_order.return_value or "order content"

    order_builder.build_order.side_effect = _side
    return captured


def _make_api(
    queue_dir: str = "queue",
) -> tuple[TaskQueue, TaskQueue, MagicMock]:
    """TaskQueue with a mocked order builder under a temp directory."""
    tmp = Path(tempfile.mkdtemp())
    qdir = tmp / queue_dir
    qdir.mkdir(parents=True, exist_ok=True)
    order_builder = MagicMock()
    order_builder.build_order.return_value = "order content"
    task_queue = TaskQueue(
        state_dir=tmp / "state",
        exec_jsonl_path=tmp / "exec.jsonl",
        order_builder=order_builder,
        queue_dir=str(qdir),
    )
    return task_queue, task_queue, order_builder


_helpers = types.ModuleType("ghdag_pipeline_conftest_helpers")
sys.modules["ghdag_pipeline_conftest_helpers"] = _helpers
for _name in ("_TEST_AUDIT_CTX", "_track_build_order", "_make_api"):
    setattr(_helpers, _name, globals()[_name])