"""Tests for ghdag.io.queue — queue directory scan consolidation (nexus Issue #2675)."""

from __future__ import annotations

from pathlib import Path

import pytest

from ghdag.io.queue import QueueTaskStore

UUID1 = "14036ae7-f47f-4669-8a80-679a5e87c147"
TS = "20260510194041"
ENGINE = "claude"


def _order_name(ts: str, engine: str, uuid: str) -> str:
    return f"{ts}-{engine}-order-{uuid}.md"


def _result_name(ts: str, engine: str, uuid: str) -> str:
    return f"{ts}-{engine}-result-{uuid}.md"


@pytest.fixture
def dirs(tmp_path: Path) -> tuple[Path, Path]:
    queue_dir = tmp_path / "jobs"
    done_dir = tmp_path / "jobs" / "done"
    queue_dir.mkdir()
    done_dir.mkdir()
    return queue_dir, done_dir


class TestQueueTaskStore:
    def test_instantiate(self, dirs: tuple[Path, Path]) -> None:
        queue_dir, done_dir = dirs
        assert QueueTaskStore(queue_dir, done_dir) is not None

    def test_read_result_returns_content(self, dirs: tuple[Path, Path]) -> None:
        queue_dir, done_dir = dirs
        (queue_dir / _result_name(TS, ENGINE, UUID1)).write_text("result content", encoding="utf-8")
        assert QueueTaskStore(queue_dir, done_dir).read_result(UUID1) == "result content"

    def test_read_result_none_when_missing(self, dirs: tuple[Path, Path]) -> None:
        queue_dir, done_dir = dirs
        (queue_dir / _order_name(TS, ENGINE, UUID1)).write_text("order", encoding="utf-8")
        assert QueueTaskStore(queue_dir, done_dir).read_result(UUID1) is None

    def test_done_marker_sets_is_done(self, dirs: tuple[Path, Path]) -> None:
        queue_dir, done_dir = dirs
        (queue_dir / _order_name(TS, ENGINE, UUID1)).write_text("o", encoding="utf-8")
        (done_dir / UUID1).write_text("", encoding="utf-8")
        tasks = QueueTaskStore(queue_dir, done_dir).list_tasks()
        assert len(tasks) == 1
        assert tasks[0].is_done is True

    def test_non_matching_files_ignored(self, dirs: tuple[Path, Path]) -> None:
        queue_dir, done_dir = dirs
        (queue_dir / "audit.jsonl").write_text("", encoding="utf-8")
        (queue_dir / "README.md").write_text("", encoding="utf-8")
        assert QueueTaskStore(queue_dir, done_dir).list_tasks() == []
