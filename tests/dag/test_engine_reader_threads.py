"""Tests for ghdag.dag.engine — §5.4 acceptance criteria."""

import logging
from unittest.mock import MagicMock

from ghdag_dag_conftest_helpers import (
    _make_config,
)

from ghdag.dag.engine import DagEngine


class _FakeHangingThread:
    """Records join(timeout) and always reports alive for reader join warning tests."""

    def __init__(self) -> None:
        self.join_timeout: float | None = None

    def join(self, timeout=None) -> None:
        self.join_timeout = timeout

    def is_alive(self) -> bool:
        return True


class TestTimeoutReaderJoin:
    """Thread join after timeout"""


    def test_join_warning_when_reader_thread_hangs(self, tmp_path, caplog):
        """`_join_reader_threads` emits a warning if threads do not finish within 2.0s"""
        config = _make_config(tmp_path, "")
        engine = DagEngine(config, MagicMock())
        fake_thread = _FakeHangingThread()

        class FakeRT:
            uuid = "test-uuid"
            stderr_thread = fake_thread
            stdout_thread = None

        with caplog.at_level(logging.WARNING, logger="ghdag.dag.task_launcher"):
            engine._launcher._join_reader_threads(FakeRT())

        assert fake_thread.join_timeout == 2.0
        assert any(
            "reader thread did not terminate within 2.0s" in r.message
            for r in caplog.records
            if r.levelno == logging.WARNING
        )
