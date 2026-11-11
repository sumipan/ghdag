"""Tests for ghdag.dag.engine — §5.4 acceptance criteria."""

import threading
import time
from unittest.mock import MagicMock

from ghdag_dag_conftest_helpers import (
    _make_config,
    _run_engine_with_timeout,
)

from ghdag.dag.engine import DagEngine
from ghdag.io.done import load_done_from_dir, load_succeeded_from_dir


class TestMaxConcurrency:
    """AC2: max_concurrency concurrent-execution cap"""

    def test_concurrency_limited(self, tmp_path):
        """With max_concurrency=2, concurrency never exceeds 2"""
        tasks = "\n".join(f"uuid-{i}: sleep 1" for i in range(4))
        config = _make_config(tmp_path, tasks + "\n", max_concurrency=2)
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        engine = DagEngine(config, hooks)

        samples = []

        def sampler():
            for _ in range(30):
                samples.append(len(engine._launcher._running))
                time.sleep(0.1)

        t_sampler = threading.Thread(target=sampler, daemon=True)
        t = threading.Thread(target=engine.run, daemon=True)
        t.start()
        t_sampler.start()
        t.join(timeout=10.0)
        engine._shutdown = True
        t.join(timeout=2.0)
        t_sampler.join(timeout=2.0)

        assert samples, "No samples collected"
        assert max(samples) <= 2, f"Concurrency exceeded 2: max={max(samples)}, samples={samples}"

    def test_none_means_unlimited(self, tmp_path):
        """With max_concurrency=None, all tasks start concurrently"""
        tasks = "\n".join(f"uuid-{i}: sleep 3" for i in range(3))
        config = _make_config(tmp_path, tasks + "\n", max_concurrency=None)
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        engine = DagEngine(config, hooks)

        t = threading.Thread(target=engine.run, daemon=True)
        t.start()
        time.sleep(0.8)

        running_count = len(engine._launcher._running)
        engine._shutdown = True
        t.join(timeout=5.0)

        assert running_count == 3, f"Expected 3 tasks running simultaneously, got {running_count}"

    def test_concurrency_1_serializes_tasks(self, tmp_path):
        """With max_concurrency=1, independent tasks A and B run one at a time"""
        config = _make_config(
            tmp_path,
            "uuid-a: sleep 0.5\nuuid-b: sleep 0.5\n",
            max_concurrency=1,
        )
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        engine = DagEngine(config, hooks)

        samples = []

        def sampler():
            for _ in range(20):
                samples.append(len(engine._launcher._running))
                time.sleep(0.05)

        t_sampler = threading.Thread(target=sampler, daemon=True)
        t = threading.Thread(target=engine.run, daemon=True)
        t.start()
        t_sampler.start()
        t.join(timeout=8.0)
        engine._shutdown = True
        t.join(timeout=2.0)
        t_sampler.join(timeout=2.0)

        assert all(s <= 1 for s in samples), f"Concurrency exceeded 1: {samples}"

        done = load_done_from_dir(config.exec_done_dir)
        assert "uuid-a" in done
        assert "uuid-b" in done

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
