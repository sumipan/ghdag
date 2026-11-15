"""Tests for wait_for_result — AC-2 (Issue #678)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from ghdag.pipeline.wait import wait_for_result


class TestWaitForResult:

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
