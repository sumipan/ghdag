"""Tests for ghdag.io.queue — queue directory scan consolidation (nexus Issue #2675)."""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from ghdag import QueueTask as QueueTaskTop
from ghdag import QueueTaskStore as QueueTaskStoreTop
from ghdag.io.queue import QueueTask, QueueTaskStore
from ghdag.pipeline.result import QueueTask as QueueTaskShim
from ghdag.pipeline.result import QueueTaskStore as QueueTaskStoreShim

UUID1 = "14036ae7-f47f-4669-8a80-679a5e87c147"
UUID2 = "75d11b22-75ca-4754-8768-765857d3ac9d"
TS = "20260510194041"
ENGINE = "claude"


def _order_name(ts: str, engine: str, uuid: str) -> str:
    return f"{ts}-{engine}-order-{uuid}.md"


def _result_name(ts: str, engine: str, uuid: str) -> str:
    return f"{ts}-{engine}-result-{uuid}.md"


def _stderr_name(ts: str, engine: str, uuid: str) -> str:
    return f"{ts}-{engine}-stderr-{uuid}.md"


@pytest.fixture
def dirs(tmp_path: Path) -> tuple[Path, Path]:
    queue_dir = tmp_path / "jobs"
    done_dir = tmp_path / "jobs" / "done"
    queue_dir.mkdir()
    done_dir.mkdir()
    return queue_dir, done_dir


class TestExports:
    def test_top_level_and_shim_are_same_classes(self) -> None:
        assert QueueTaskTop is QueueTask
        assert QueueTaskStoreTop is QueueTaskStore
        assert QueueTaskShim is QueueTask
        assert QueueTaskStoreShim is QueueTaskStore

    def test_canonical_source_is_io_queue(self) -> None:
        src = inspect.getsourcefile(QueueTaskStore)
        assert src is not None
        assert src.endswith("io/queue.py") or src.endswith("io\\queue.py")


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

    def test_get_result_path(self, dirs: tuple[Path, Path]) -> None:
        queue_dir, done_dir = dirs
        result_file = queue_dir / _result_name(TS, ENGINE, UUID1)
        result_file.write_text("content", encoding="utf-8")
        assert QueueTaskStore(queue_dir, done_dir).get_result_path(UUID1) == result_file.resolve()

    def test_list_tasks_groups_by_uuid(self, dirs: tuple[Path, Path]) -> None:
        queue_dir, done_dir = dirs
        order_file = queue_dir / _order_name(TS, ENGINE, UUID1)
        result_file = queue_dir / _result_name(TS, ENGINE, UUID1)
        stderr_file = queue_dir / _stderr_name(TS, ENGINE, UUID1)
        order_file.write_text("order", encoding="utf-8")
        result_file.write_text("result", encoding="utf-8")
        stderr_file.write_text("stderr", encoding="utf-8")

        tasks = QueueTaskStore(queue_dir, done_dir).list_tasks()
        assert len(tasks) == 1
        t = tasks[0]
        assert t.uuid == UUID1.lower()
        assert t.timestamp == TS
        assert t.engine == ENGINE
        assert t.order_path == order_file.resolve()
        assert t.result_path == result_file.resolve()
        assert t.stderr_path == stderr_file.resolve()
        assert t.is_done is False

    def test_done_marker_sets_is_done(self, dirs: tuple[Path, Path]) -> None:
        queue_dir, done_dir = dirs
        (queue_dir / _order_name(TS, ENGINE, UUID1)).write_text("o", encoding="utf-8")
        (done_dir / UUID1).write_text("", encoding="utf-8")
        tasks = QueueTaskStore(queue_dir, done_dir).list_tasks()
        assert len(tasks) == 1
        assert tasks[0].is_done is True

    def test_multiple_uuids(self, dirs: tuple[Path, Path]) -> None:
        queue_dir, done_dir = dirs
        (queue_dir / _order_name(TS, ENGINE, UUID1)).write_text("o1", encoding="utf-8")
        (queue_dir / _result_name(TS, ENGINE, UUID2)).write_text("r2", encoding="utf-8")
        uuids = {t.uuid for t in QueueTaskStore(queue_dir, done_dir).list_tasks()}
        assert uuids == {UUID1.lower(), UUID2.lower()}

    def test_non_matching_files_ignored(self, dirs: tuple[Path, Path]) -> None:
        queue_dir, done_dir = dirs
        (queue_dir / "audit.jsonl").write_text("", encoding="utf-8")
        (queue_dir / "README.md").write_text("", encoding="utf-8")
        assert QueueTaskStore(queue_dir, done_dir).list_tasks() == []
