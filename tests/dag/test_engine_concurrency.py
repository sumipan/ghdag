"""Tests for ghdag.dag.engine — §5.4 acceptance criteria."""

from unittest.mock import MagicMock

from ghdag_dag_conftest_helpers import (
    _make_config,
    _run_engine_with_timeout,
)

from ghdag.dag.engine import DagEngine
from ghdag.io.done import load_done_from_dir, load_succeeded_from_dir


class TestMaxConcurrency:
    """AC2: max_concurrency concurrent-execution cap"""


    def test_dep_failed_detection_with_limit(self, tmp_path):
        """DEP_FAILED is still detected correctly when at the concurrency cap"""
        config = _make_config(
            tmp_path,
            "uuid-a: exit 1\nuuid-b[depends:uuid-a]: echo b\nuuid-c: sleep 0.2\n",
            max_concurrency=1,
        )
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        engine = DagEngine(config, hooks)

        _run_engine_with_timeout(engine, timeout=8.0)

        done = load_done_from_dir(config.exec_done_dir)
        succeeded = load_succeeded_from_dir(config.exec_done_dir)

        assert "uuid-a" in done
        assert "uuid-b" in done
        assert "uuid-c" in done

        assert "uuid-a" not in succeeded
        assert "uuid-b" not in succeeded
        assert "uuid-c" in succeeded
