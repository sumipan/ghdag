"""tests for QueueTaskStore and QueueTask."""

from __future__ import annotations

from pathlib import Path

import pytest

from ghdag import QueueTask as QueueTaskTop
from ghdag import QueueTaskStore as QueueTaskStoreTop
from ghdag.pipeline.result import QueueTask, QueueTaskStore


def test_top_level_exports_are_same_classes() -> None:
    assert QueueTaskTop is QueueTask
    assert QueueTaskStoreTop is QueueTaskStore

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
    def test_returns_content_when_result_exists(self, dirs: tuple[Path, Path]) -> None:
        queue_dir, done_dir = dirs
        result_file = queue_dir / _result_name(TS, ENGINE, UUID1)
        result_file.write_text("result content", encoding="utf-8")

        store = QueueTaskStore(queue_dir, done_dir)
        assert store.read_result(UUID1) == "result content"

    def test_returns_none_when_result_missing(self, dirs: tuple[Path, Path]) -> None:
        queue_dir, done_dir = dirs
        # only order exists, no result
        (queue_dir / _order_name(TS, ENGINE, UUID1)).write_text("order", encoding="utf-8")

        store = QueueTaskStore(queue_dir, done_dir)
        assert store.read_result(UUID1) is None

    def test_returns_none_when_uuid_not_in_queue(self, dirs: tuple[Path, Path]) -> None:
        queue_dir, done_dir = dirs
        store = QueueTaskStore(queue_dir, done_dir)
        assert store.read_result(UUID1) is None


class TestGetResultPath:
    def test_returns_path_when_result_exists(self, dirs: tuple[Path, Path]) -> None:
        queue_dir, done_dir = dirs
        result_file = queue_dir / _result_name(TS, ENGINE, UUID1)
        result_file.write_text("content", encoding="utf-8")

        store = QueueTaskStore(queue_dir, done_dir)
        path = store.get_result_path(UUID1)
        assert path == result_file.resolve()

    def test_returns_none_when_result_missing(self, dirs: tuple[Path, Path]) -> None:
        queue_dir, done_dir = dirs
        store = QueueTaskStore(queue_dir, done_dir)
        assert store.get_result_path(UUID1) is None


class TestListTasks:
    def test_empty_queue_returns_empty_list(self, dirs: tuple[Path, Path]) -> None:
        queue_dir, done_dir = dirs
        store = QueueTaskStore(queue_dir, done_dir)
        assert store.list_tasks() == []

    def test_groups_order_result_stderr_by_uuid(self, dirs: tuple[Path, Path]) -> None:
        queue_dir, done_dir = dirs
        order_file = queue_dir / _order_name(TS, ENGINE, UUID1)
        result_file = queue_dir / _result_name(TS, ENGINE, UUID1)
        stderr_file = queue_dir / _stderr_name(TS, ENGINE, UUID1)
        order_file.write_text("order", encoding="utf-8")
        result_file.write_text("result", encoding="utf-8")
        stderr_file.write_text("stderr", encoding="utf-8")

        store = QueueTaskStore(queue_dir, done_dir)
        tasks = store.list_tasks()
        assert len(tasks) == 1
        t = tasks[0]
        assert t.uuid == UUID1.lower()
        assert t.timestamp == TS
        assert t.engine == ENGINE
        assert t.order_path == order_file.resolve()
        assert t.result_path == result_file.resolve()
        assert t.stderr_path == stderr_file.resolve()

    def test_order_only_task_has_none_result_and_not_done(self, dirs: tuple[Path, Path]) -> None:
        queue_dir, done_dir = dirs
        order_file = queue_dir / _order_name(TS, ENGINE, UUID1)
        order_file.write_text("order", encoding="utf-8")

        store = QueueTaskStore(queue_dir, done_dir)
        tasks = store.list_tasks()
        assert len(tasks) == 1
        t = tasks[0]
        assert t.result_path is None
        assert t.stderr_path is None
        assert t.is_done is False

    def test_done_marker_sets_is_done_true(self, dirs: tuple[Path, Path]) -> None:
        queue_dir, done_dir = dirs
        order_file = queue_dir / _order_name(TS, ENGINE, UUID1)
        result_file = queue_dir / _result_name(TS, ENGINE, UUID1)
        order_file.write_text("order", encoding="utf-8")
        result_file.write_text("result", encoding="utf-8")
        (done_dir / UUID1).write_text("", encoding="utf-8")

        store = QueueTaskStore(queue_dir, done_dir)
        tasks = store.list_tasks()
        assert len(tasks) == 1
        assert tasks[0].is_done is True

    def test_no_done_marker_sets_is_done_false(self, dirs: tuple[Path, Path]) -> None:
        queue_dir, done_dir = dirs
        order_file = queue_dir / _order_name(TS, ENGINE, UUID1)
        result_file = queue_dir / _result_name(TS, ENGINE, UUID1)
        order_file.write_text("order", encoding="utf-8")
        result_file.write_text("result", encoding="utf-8")

        store = QueueTaskStore(queue_dir, done_dir)
        tasks = store.list_tasks()
        assert len(tasks) == 1
        assert tasks[0].is_done is False

    def test_multiple_uuids_returned_separately(self, dirs: tuple[Path, Path]) -> None:
        queue_dir, done_dir = dirs
        (queue_dir / _order_name(TS, ENGINE, UUID1)).write_text("o1", encoding="utf-8")
        (queue_dir / _result_name(TS, ENGINE, UUID2)).write_text("r2", encoding="utf-8")

        store = QueueTaskStore(queue_dir, done_dir)
        tasks = store.list_tasks()
        assert len(tasks) == 2
        uuids = {t.uuid for t in tasks}
        assert uuids == {UUID1.lower(), UUID2.lower()}

    def test_non_matching_files_are_ignored(self, dirs: tuple[Path, Path]) -> None:
        queue_dir, done_dir = dirs
        (queue_dir / "audit.jsonl").write_text("", encoding="utf-8")
        (queue_dir / "exec.jsonl").write_text("", encoding="utf-8")
        (queue_dir / "README.md").write_text("", encoding="utf-8")

        store = QueueTaskStore(queue_dir, done_dir)
        assert store.list_tasks() == []
