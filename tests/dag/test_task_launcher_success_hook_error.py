"""Regression tests for success postprocess isolation (nexus #5326)."""

from __future__ import annotations

import io
import json
from unittest.mock import MagicMock, patch

from ghdag.core.vocabulary import DONE_UNKNOWN_FAILURE
from ghdag.dag.circuit_breaker import CircuitBreakerPolicy
from ghdag.dag.engine import DagEngine
from ghdag.dag.hooks import DagHooks
from ghdag.dag.models import DagConfig, Task, _RunningProcess


def _make_config(tmp_path) -> DagConfig:
    (tmp_path / "done").mkdir(parents=True, exist_ok=True)
    return DagConfig(
        exec_jsonl_path=str(tmp_path / "exec.jsonl"),
        exec_done_dir=str(tmp_path / "done"),
    )


def _make_running_task(
    tmp_path,
    uuid: str = "task-uuid",
    *,
    returncode: int = 0,
) -> _RunningProcess:
    result_file = tmp_path / "result.md"
    result_file.write_text("ok\n", encoding="utf-8")
    proc = MagicMock()
    proc.poll.return_value = returncode
    proc.returncode = returncode
    task = Task(
        uuid=uuid,
        command="echo ok",
        result_path=str(result_file),
        idempotency_key="corr-1",
        annotations={"_request_id": "req-1"},
    )
    return _RunningProcess(
        uuid=uuid,
        task=task,
        proc=proc,
        started_at=1_700_000_000.0 - 0.1,
        started_at_mono=10_000.0 - 0.1,
        stderr_buf=io.BytesIO(b""),
        retry_depth=0,
        stdout_buf=io.BytesIO(b""),
    )


def _make_engine(tmp_path) -> tuple[DagEngine, MagicMock]:
    hooks = MagicMock(spec=DagHooks)
    hooks.check_rejected.return_value = False
    hooks.check_pipeline_status.return_value = None
    engine = DagEngine(_make_config(tmp_path), hooks)
    return engine, hooks


def _done_value(tmp_path, uuid: str) -> str:
    return (tmp_path / "done" / uuid).read_text(encoding="utf-8").strip()


def _audit_records(tmp_path) -> list[dict]:
    audit_path = tmp_path / "audit.jsonl"
    if not audit_path.exists():
        return []
    return [json.loads(line) for line in audit_path.read_text().splitlines() if line.strip()]


def _postprocess_failures(tmp_path) -> list[dict]:
    return [
        r
        for r in _audit_records(tmp_path)
        if r.get("event_type") == "task_success_postprocess_failed"
    ]






def test_circuit_breaker_reset_exception_still_promotes(tmp_path) -> None:
    engine, hooks = _make_engine(tmp_path)
    promote = MagicMock()
    engine._launcher._promote_fn = promote
    engine._launcher._circuit_breaker = MagicMock(spec=CircuitBreakerPolicy)
    engine._launcher._circuit_breaker.reset.side_effect = RuntimeError("cb boom")

    rt = _make_running_task(tmp_path)
    engine._launcher._running[rt.uuid] = rt
    engine._launcher.check_completions()

    assert _done_value(tmp_path, rt.uuid) == "0"
    promote.assert_called_once()
    failures = _postprocess_failures(tmp_path)
    assert len(failures) == 1
    assert failures[0]["component"] == "circuit_breaker.reset"






@patch("ghdag.dag.task_launcher.append_audit_record", side_effect=OSError("disk full"))
def test_audit_write_failure_keeps_success(mock_append, tmp_path) -> None:
    engine, hooks = _make_engine(tmp_path)
    engine._launcher._promote_fn = MagicMock()
    hooks.on_task_success.side_effect = RuntimeError("hook boom")

    rt = _make_running_task(tmp_path)
    engine._launcher._running[rt.uuid] = rt
    engine._launcher.check_completions()

    assert _done_value(tmp_path, rt.uuid) == "0"
    hooks.on_task_failure.assert_not_called()


@patch("ghdag.dag.task_launcher.get_output_adapter")
@patch("ghdag.dag.task_launcher._extract_tee_target", return_value=None)
def test_pre_success_exception_marks_unknown_failure(
    mock_tee, mock_get_adapter, tmp_path
) -> None:
    engine, hooks = _make_engine(tmp_path)
    adapter = MagicMock()
    adapter.extract_error.return_value = None
    adapter.extract_token_usage.return_value = None
    adapter.extract_result_text.side_effect = RuntimeError("extract fail")
    mock_get_adapter.return_value = adapter

    rt = _make_running_task(tmp_path)
    engine._launcher._running[rt.uuid] = rt
    engine._launcher.check_completions()

    assert _done_value(tmp_path, rt.uuid) == DONE_UNKNOWN_FAILURE
    hooks.on_task_failure.assert_called_once()
    assert _postprocess_failures(tmp_path) == []
