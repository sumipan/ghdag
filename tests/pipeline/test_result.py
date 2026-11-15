"""tests for QueueTaskStore and QueueTask."""

from __future__ import annotations

from pathlib import Path

import pytest

from ghdag.io.queue import QueueTaskStore

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


class TestQueueTaskStoreInit:
    def test_instantiate(self, dirs: tuple[Path, Path]) -> None:
        queue_dir, done_dir = dirs
        store = QueueTaskStore(queue_dir, done_dir)
        assert store is not None


class TestReadResult:

    def test_returns_none_when_uuid_not_in_queue(self, dirs: tuple[Path, Path]) -> None:
        queue_dir, done_dir = dirs
        store = QueueTaskStore(queue_dir, done_dir)
        assert store.read_result(UUID1) is None


class TestGetResultPath:

    def test_returns_none_when_result_missing(self, dirs: tuple[Path, Path]) -> None:
        queue_dir, done_dir = dirs
        store = QueueTaskStore(queue_dir, done_dir)
        assert store.get_result_path(UUID1) is None


class TestListTasks:
    def test_empty_queue_returns_empty_list(self, dirs: tuple[Path, Path]) -> None:
        queue_dir, done_dir = dirs
        store = QueueTaskStore(queue_dir, done_dir)
        assert store.list_tasks() == []
