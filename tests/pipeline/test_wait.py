"""Tests for wait_for_result — AC-2 (Issue #678)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from ghdag.pipeline.wait import wait_for_result


class TestWaitForResult:
    def test_immediate_success_exit_zero(self, tmp_path):
        exec_done = tmp_path / "jobs" / "done"
        exec_done.mkdir(parents=True)
        (exec_done / "uuid-1").write_text("0\n", encoding="utf-8")
        result = wait_for_result(exec_done, "uuid-1", timeout=1.0)
        assert result == ("success", "0")

    def test_empty_content(self, tmp_path):
        exec_done = tmp_path / "jobs" / "done"
        exec_done.mkdir(parents=True)
        (exec_done / "uuid-1").write_text("", encoding="utf-8")
        result = wait_for_result(exec_done, "uuid-1", timeout=1.0)
        assert result == ("other", "")

    def test_rejected(self, tmp_path):
        exec_done = tmp_path / "jobs" / "done"
        exec_done.mkdir(parents=True)
        (exec_done / "uuid-1").write_text("REJECTED\n", encoding="utf-8")
        result = wait_for_result(exec_done, "uuid-1", timeout=1.0)
        assert result == ("rejected", "REJECTED")

    def test_rejected_final(self, tmp_path):
        exec_done = tmp_path / "jobs" / "done"
        exec_done.mkdir(parents=True)
        (exec_done / "uuid-1").write_text("REJECTED_FINAL\n", encoding="utf-8")
        result = wait_for_result(exec_done, "uuid-1", timeout=1.0)
        assert result == ("rejected", "REJECTED_FINAL")

    def test_empty_result(self, tmp_path):
        exec_done = tmp_path / "jobs" / "done"
        exec_done.mkdir(parents=True)
        (exec_done / "uuid-1").write_text("EMPTY_RESULT\n", encoding="utf-8")
        result = wait_for_result(exec_done, "uuid-1", timeout=1.0)
        assert result == ("empty_result", "EMPTY_RESULT")

    def test_engine_error(self, tmp_path):
        exec_done = tmp_path / "jobs" / "done"
        exec_done.mkdir(parents=True)
        (exec_done / "uuid-1").write_text("ENGINE_ERROR\n", encoding="utf-8")
        result = wait_for_result(exec_done, "uuid-1", timeout=1.0)
        assert result == ("engine_error", "ENGINE_ERROR")

    def test_engine_environment_error(self, tmp_path):
        exec_done = tmp_path / "jobs" / "done"
        exec_done.mkdir(parents=True)
        (exec_done / "uuid-1").write_text("ENGINE_ENVIRONMENT_ERROR\n", encoding="utf-8")
        result = wait_for_result(exec_done, "uuid-1", timeout=1.0)
        assert result == ("engine_error", "ENGINE_ENVIRONMENT_ERROR")

    def test_nonzero_exit(self, tmp_path):
        exec_done = tmp_path / "jobs" / "done"
        exec_done.mkdir(parents=True)
        (exec_done / "uuid-1").write_text("1\n", encoding="utf-8")
        result = wait_for_result(exec_done, "uuid-1", timeout=1.0)
        assert result == ("failed_exit", "1")

    def test_polling_detects_file_created_later(self, tmp_path):
        exec_done = tmp_path / "jobs" / "done"
        exec_done.mkdir(parents=True)
        uuid = "uuid-poll"
        done_path = exec_done / uuid

        def create_done_on_poll(_seconds: float) -> None:
            done_path.write_text("0\n", encoding="utf-8")

        with patch("ghdag.pipeline.wait.time.sleep", side_effect=create_done_on_poll):
            result = wait_for_result(exec_done, uuid, timeout=2.0, poll_interval=0.05)
        assert result == ("success", "0")

    def test_timeout_raises(self, tmp_path):
        exec_done = tmp_path / "jobs" / "done"
        exec_done.mkdir(parents=True)
        monotonic_values = iter([0.0, 1.0])
        with (
            patch("ghdag.pipeline.wait.time.monotonic", side_effect=lambda: next(monotonic_values)),
            patch("ghdag.pipeline.wait.time.sleep"),
            pytest.raises(TimeoutError, match="uuid-missing"),
        ):
            wait_for_result(exec_done, "uuid-missing", timeout=0.5, poll_interval=0.1)
