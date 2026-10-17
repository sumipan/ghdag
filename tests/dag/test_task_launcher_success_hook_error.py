"""Regression tests for success postprocess isolation (nexus #5326)."""

from __future__ import annotations

import io
import json
import time
from unittest.mock import MagicMock, patch

from ghdag.core.vocabulary import DONE_UNKNOWN_FAILURE
from ghdag.dag.circuit_breaker import CircuitBreakerPolicy
from ghdag.dag.engine import DagEngine
from ghdag.dag.hooks import DagHooks
from ghdag.dag.models import DagConfig, RunningTask, Task


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
) -> RunningTask:
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
    return RunningTask(
        uuid=uuid,
        task=task,
        proc=proc,
        started_at=time.time() - 0.1,
        started_at_mono=time.monotonic() - 0.1,
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


def test_hook_exception_keeps_success_done_and_runs_followups(tmp_path) -> None:
    engine, hooks = _make_engine(tmp_path)
    promote = MagicMock()
    engine._launcher._promote_fn = promote
    hooks.on_task_success.side_effect = RuntimeError("hook boom")

    rt = _make_running_task(tmp_path)
    engine._launcher._running[rt.uuid] = rt
    cb = engine._launcher._circuit_breaker
    with patch.object(cb, "reset", wraps=cb.reset) as reset_spy:
        engine._launcher.check_completions()

    assert _done_value(tmp_path, rt.uuid) == "0"
    hooks.on_task_failure.assert_not_called()
    reset_spy.assert_called_once()
    promote.assert_called_once()


def test_hook_exception_writes_audit_record(tmp_path) -> None:
    engine, hooks = _make_engine(tmp_path)
    engine._launcher._promote_fn = MagicMock()
    hooks.on_task_success.side_effect = RuntimeError("hook boom")

    rt = _make_running_task(tmp_path, uuid="audit-uuid")
    engine._launcher._running[rt.uuid] = rt
    engine._launcher.check_completions()

    failures = _postprocess_failures(tmp_path)
    assert len(failures) == 1
    record = failures[0]
    assert record["schema_version"] == 3
    assert record["status"] == "success"
    assert record["component"] == "on_task_success"
    assert record["exception_type"] == "RuntimeError"
    assert record["error_message"] == "hook boom"
    assert record["uuid"] == "audit-uuid"
    assert record["correlation_id"] == "corr-1"
    assert record["request_id"] == "req-1"


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


def test_promote_exception_does_not_call_failure_hook(tmp_path) -> None:
    engine, hooks = _make_engine(tmp_path)
    promote = MagicMock(side_effect=RuntimeError("promote boom"))
    engine._launcher._promote_fn = promote

    rt = _make_running_task(tmp_path)
    engine._launcher._running[rt.uuid] = rt
    engine._launcher.check_completions()

    assert _done_value(tmp_path, rt.uuid) == "0"
    hooks.on_task_failure.assert_not_called()
    failures = _postprocess_failures(tmp_path)
    assert len(failures) == 1
    assert failures[0]["component"] == "promote"


def test_multiple_postprocess_failures_each_audited_and_continue(tmp_path) -> None:
    engine, hooks = _make_engine(tmp_path)
    promote = MagicMock(side_effect=RuntimeError("promote boom"))
    engine._launcher._promote_fn = promote
    hooks.on_task_success.side_effect = RuntimeError("hook boom")
    engine._launcher._circuit_breaker = MagicMock(spec=CircuitBreakerPolicy)
    engine._launcher._circuit_breaker.reset.side_effect = RuntimeError("cb boom")

    rt = _make_running_task(tmp_path)
    engine._launcher._running[rt.uuid] = rt
    engine._launcher.check_completions()

    assert _done_value(tmp_path, rt.uuid) == "0"
    hooks.on_task_failure.assert_not_called()
    components = {r["component"] for r in _postprocess_failures(tmp_path)}
    assert components == {"on_task_success", "circuit_breaker.reset", "promote"}


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


@patch("ghdag.dag.task_launcher._extract_tee_target", return_value=None)
def test_nonzero_exit_does_not_emit_postprocess_audit(mock_tee, tmp_path) -> None:
    engine, hooks = _make_engine(tmp_path)
    rt = _make_running_task(tmp_path, returncode=2)
    engine._launcher._running[rt.uuid] = rt
    engine._launcher.check_completions()

    assert _postprocess_failures(tmp_path) == []
    hooks.on_task_failure.assert_called_once()


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
