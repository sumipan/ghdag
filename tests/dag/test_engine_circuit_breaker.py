"""Circuit breaker tests for DagEngine consecutive failure handling."""

from __future__ import annotations

import io
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from ghdag.dag.engine import DagEngine
from ghdag.dag.hooks import DagHooks
from ghdag.dag.models import DagConfig, RunningTask, Task


def _make_config(tmp_path, **overrides) -> DagConfig:
    defaults = dict(
        exec_jsonl_path=str(tmp_path / "exec.jsonl"),
        exec_done_dir=str(tmp_path / "done"),
        max_consecutive_failures=3,
        failure_window_sec=60.0,
    )
    defaults.update(overrides)
    return DagConfig(**defaults)


def _make_engine(tmp_path, **config_overrides) -> tuple[DagEngine, MagicMock]:
    hooks = MagicMock(spec=DagHooks)
    hooks.check_rejected.return_value = False
    hooks.check_pipeline_status.return_value = None
    engine = DagEngine(_make_config(tmp_path, **config_overrides), hooks)
    return engine, hooks


def _make_running_task(
    uuid: str = "test-uuid",
    command: str = "echo hello",
    returncode: int = 0,
    retry: int = 0,
) -> RunningTask:
    proc = MagicMock()
    proc.poll.return_value = returncode
    proc.returncode = returncode
    task = Task(uuid=uuid, command=command, retry=retry)
    return RunningTask(
        uuid=uuid,
        task=task,
        proc=proc,
        started_at=time.time() - 0.1,
        started_at_mono=time.monotonic() - 0.1,
        stderr_buf=io.BytesIO(b""),
        retry_depth=retry,
    )


@patch("ghdag.dag.engine.state_mark_done")
@patch("ghdag.dag.engine._extract_tee_target", return_value=None)
def test_shutdown_after_max_consecutive_failures(mock_tee, mock_mark_done, tmp_path):
    """max_consecutive_failures=3 で 3 回連続失敗すると _shutdown が True になる。"""
    engine, _hooks = _make_engine(tmp_path, max_consecutive_failures=3)

    for i in range(3):
        rt = _make_running_task(uuid=f"fail-{i}", returncode=1)
        engine._running[rt.uuid] = rt
        engine._check_completions()

    assert engine._consecutive_failures == 3
    assert engine._shutdown is True


@patch("ghdag.dag.engine.state_mark_done")
@patch("ghdag.dag.engine._extract_tee_target", return_value=None)
def test_success_resets_consecutive_failures(mock_tee, mock_mark_done, tmp_path):
    """連続失敗中に 1 回成功が挟まるとカウンタが 0 にリセットされる。"""
    engine, _hooks = _make_engine(tmp_path, max_consecutive_failures=3)

    for i in range(2):
        rt = _make_running_task(uuid=f"fail-{i}", returncode=1)
        engine._running[rt.uuid] = rt
        engine._check_completions()

    assert engine._consecutive_failures == 2
    assert engine._shutdown is False

    ok = _make_running_task(uuid="ok-1", returncode=0)
    engine._running[ok.uuid] = ok
    engine._check_completions()

    assert engine._consecutive_failures == 0
    assert engine._shutdown is False

    rt = _make_running_task(uuid="fail-after-ok", returncode=1)
    engine._running[rt.uuid] = rt
    engine._check_completions()

    assert engine._consecutive_failures == 1
    assert engine._shutdown is False


@patch("ghdag.dag.engine.state_mark_done")
@patch("ghdag.dag.engine._extract_tee_target", return_value=None)
def test_failure_window_resets_counter(mock_tee, mock_mark_done, tmp_path):
    """failure_window_sec 以上経過後の失敗は 1 回目としてカウントされる。"""
    engine, _hooks = _make_engine(tmp_path, max_consecutive_failures=3, failure_window_sec=60.0)

    rt1 = _make_running_task(uuid="fail-1", returncode=1)
    engine._running[rt1.uuid] = rt1

    base_time = 1000.0
    with patch("ghdag.dag.engine.time.time", return_value=base_time):
        engine._check_completions()

    assert engine._consecutive_failures == 1

    rt2 = _make_running_task(uuid="fail-2", returncode=1)
    engine._running[rt2.uuid] = rt2

    with patch("ghdag.dag.engine.time.time", return_value=base_time + 61.0):
        engine._check_completions()

    assert engine._consecutive_failures == 1
    assert engine._shutdown is False


@patch("ghdag.dag.engine.state_mark_done")
def test_timeout_records_failure(mock_mark_done, tmp_path):
    """TIMEOUT 失敗も連続失敗カウンタに含まれる。"""
    engine, _hooks = _make_engine(tmp_path, max_consecutive_failures=1)

    rt = _make_running_task()
    rt.term_sent_at = time.monotonic() - 1.0
    engine._running[rt.uuid] = rt
    engine._check_completions()

    assert engine._consecutive_failures == 1
    assert engine._shutdown is True


@patch("ghdag.dag.engine.state_mark_done")
@patch("ghdag.dag.engine._extract_tee_target")
def test_rejected_final_records_failure(mock_tee, mock_mark_done, tmp_path):
    """REJECTED (final) 失敗も連続失敗カウンタに含まれる。"""
    engine, hooks = _make_engine(tmp_path, max_consecutive_failures=1, max_retry=0)
    hooks.check_rejected.return_value = True
    mock_tee.return_value = str(tmp_path / "result.md")
    (tmp_path / "result.md").write_text("REJECTED")

    rt = _make_running_task(retry=0)
    engine._running[rt.uuid] = rt
    engine._check_completions()

    assert engine._consecutive_failures == 1
    assert engine._shutdown is True


@patch("ghdag.dag.engine.state_mark_done")
@patch("ghdag.dag.engine._extract_tee_target")
def test_pipeline_failed_records_failure(mock_tee, mock_mark_done, tmp_path):
    """PIPELINE_FAILED 失敗も連続失敗カウンタに含まれる。"""
    engine, hooks = _make_engine(tmp_path, max_consecutive_failures=1)
    hooks.check_pipeline_status.return_value = "IMPL_FAILED"
    mock_tee.return_value = str(tmp_path / "result.md")
    (tmp_path / "result.md").write_text("PIPELINE_STATUS: IMPL_FAILED")

    rt = _make_running_task()
    engine._running[rt.uuid] = rt
    engine._check_completions()

    assert engine._consecutive_failures == 1
    assert engine._shutdown is True


@patch("ghdag.dag.engine.state_mark_done")
@patch("ghdag.dag.engine._extract_tee_target")
def test_empty_result_records_failure(mock_tee, mock_mark_done, tmp_path):
    """EMPTY_RESULT 失敗も連続失敗カウンタに含まれる。"""
    engine, _hooks = _make_engine(tmp_path, max_consecutive_failures=1)
    result_file = tmp_path / "result.md"
    result_file.write_text("")
    mock_tee.return_value = str(result_file)

    rt = _make_running_task()
    engine._running[rt.uuid] = rt
    engine._check_completions()

    assert engine._consecutive_failures == 1
    assert engine._shutdown is True
