"""Tests for task_status / interpret_done — AC-3 (Issue #678)."""

from __future__ import annotations

from ghdag.pipeline.status import (
    STATE_DEFERRED,
    STATE_EMPTY,
    STATE_ENGINE_ERROR,
    STATE_FAIL,
    STATE_OK,
    STATE_PENDING_DEPS,
    STATE_PENDING_RUN,
    STATE_REJECTED,
    STATE_RUNNING,
    interpret_done,
    task_status,
)


class TestInterpretDone:
    def test_none_returns_none(self):
        assert interpret_done(None) is None

    def test_exit_zero_returns_success(self):
        assert interpret_done("0\n") == "success"

    def test_empty_string_returns_success(self):
        assert interpret_done("") == "success"

    def test_rejected_returns_rejected(self):
        assert interpret_done("REJECTED\n") == "rejected"

    def test_rejected_final_returns_rejected(self):
        assert interpret_done("REJECTED_FINAL\n") == "rejected"

    def test_empty_result_returns_empty_result(self):
        assert interpret_done("EMPTY_RESULT\n") == "empty_result"

    def test_engine_error_returns_engine_error(self):
        assert interpret_done("ENGINE_ERROR\n") == "engine_error"
        assert interpret_done("ENGINE_ERROR_FINAL\n") == "engine_error"

    def test_nonzero_exit_returns_failed_exit(self):
        assert interpret_done("1\n") == "failed_exit"
        assert interpret_done("127\n") == "failed_exit"

    def test_unknown_string_returns_other(self):
        assert interpret_done("SOMETHING_ELSE\n") == "other"


class TestTaskStatus:
    def test_completed_success(self, tmp_path):
        exec_done = tmp_path / "jobs" / "done"
        exec_done.mkdir(parents=True)
        (exec_done / "uuid-1").write_text("0\n", encoding="utf-8")
        assert task_status("uuid-1", exec_done) == STATE_OK

    def test_completed_failed(self, tmp_path):
        exec_done = tmp_path / "jobs" / "done"
        exec_done.mkdir(parents=True)
        (exec_done / "uuid-1").write_text("1\n", encoding="utf-8")
        assert task_status("uuid-1", exec_done) == STATE_FAIL

    def test_completed_rejected(self, tmp_path):
        exec_done = tmp_path / "jobs" / "done"
        exec_done.mkdir(parents=True)
        (exec_done / "uuid-1").write_text("REJECTED\n", encoding="utf-8")
        assert task_status("uuid-1", exec_done) == STATE_REJECTED

    def test_completed_empty_result(self, tmp_path):
        exec_done = tmp_path / "jobs" / "done"
        exec_done.mkdir(parents=True)
        (exec_done / "uuid-1").write_text("EMPTY_RESULT\n", encoding="utf-8")
        assert task_status("uuid-1", exec_done) == STATE_EMPTY

    def test_completed_engine_error(self, tmp_path):
        exec_done = tmp_path / "jobs" / "done"
        exec_done.mkdir(parents=True)
        (exec_done / "uuid-1").write_text("ENGINE_ERROR\n", encoding="utf-8")
        assert task_status("uuid-1", exec_done) == STATE_ENGINE_ERROR

    def test_pending_deps_when_dep_not_done(self, tmp_path):
        exec_done = tmp_path / "jobs" / "done"
        exec_done.mkdir(parents=True)
        assert task_status(
            "uuid-1",
            exec_done,
            task_depends={"dep-uuid"},
        ) == STATE_PENDING_DEPS

    def test_running(self, tmp_path):
        exec_done = tmp_path / "jobs" / "done"
        exec_done.mkdir(parents=True)
        assert task_status(
            "uuid-1",
            exec_done,
            running_uuids={"uuid-1"},
        ) == STATE_RUNNING

    def test_deferred_when_not_done_or_running(self, tmp_path):
        exec_done = tmp_path / "jobs" / "done"
        exec_done.mkdir(parents=True)
        assert task_status(
            "uuid-1",
            exec_done,
            deferred_uuids={"uuid-1"},
        ) == STATE_DEFERRED

    def test_running_precedes_dependency_pending(self, tmp_path):
        exec_done = tmp_path / "jobs" / "done"
        exec_done.mkdir(parents=True)
        assert task_status(
            "uuid-1",
            exec_done,
            task_depends={"dep-uuid"},
            running_uuids={"uuid-1"},
        ) == STATE_RUNNING

    def test_pending_run_when_no_deps_and_not_running(self, tmp_path):
        exec_done = tmp_path / "jobs" / "done"
        exec_done.mkdir(parents=True)
        assert task_status("uuid-1", exec_done) == STATE_PENDING_RUN

    def test_deps_succeeded_not_pending(self, tmp_path):
        exec_done = tmp_path / "jobs" / "done"
        exec_done.mkdir(parents=True)
        (exec_done / "dep-uuid").write_text("0\n", encoding="utf-8")
        assert task_status(
            "uuid-1",
            exec_done,
            task_depends={"dep-uuid"},
        ) == STATE_PENDING_RUN
