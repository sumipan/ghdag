"""Tests for ghdag.dag.engine — §5.4 acceptance criteria."""

import logging
import threading
from unittest.mock import MagicMock

from ghdag_dag_conftest_helpers import (
    _make_config,
)

from ghdag.dag.engine import DagEngine


class TestTimeoutReaderJoin:
    """Thread join after timeout"""


    def test_join_warning_when_reader_thread_hangs(self, tmp_path, caplog):
        """`_join_reader_threads` emits a warning if threads do not finish within 2.0s"""
        stop_event = threading.Event()

        def hanging_reader():
            stop_event.wait(timeout=30.0)

        hanging_thread = threading.Thread(target=hanging_reader, daemon=True)
        hanging_thread.start()
        try:
            config = _make_config(tmp_path, "")
            engine = DagEngine(config, MagicMock())

            class FakeRT:
                uuid = "test-uuid"
                stderr_thread = hanging_thread
                stdout_thread = None

            with caplog.at_level(logging.WARNING, logger="ghdag.dag.task_launcher"):
                engine._launcher._join_reader_threads(FakeRT())

            assert any(
                "reader thread did not terminate within 2.0s" in r.message
                for r in caplog.records
                if r.levelno == logging.WARNING
            )
        finally:
            stop_event.set()
            hanging_thread.join(timeout=1.0)
