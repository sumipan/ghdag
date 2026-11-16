"""Tests for pure completion decisions (#6876)."""

from __future__ import annotations

import ast
import inspect
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ghdag.core.models.metrics import FailureClass
from ghdag.core.vocabulary import (
    DONE_CANCELLED,
    DONE_EMPTY_RESULT,
    DONE_ENGINE_ENV_ERROR,
    DONE_ENGINE_ERROR,
    DONE_ENGINE_ERROR_FINAL,
    DONE_FANOUT_PARSE_FAILED,
    DONE_INTERACTIVE_PROMPT,
    DONE_PIPELINE_FAILED_PREFIX,
    DONE_REJECTED,
    DONE_REJECTED_FINAL,
    DONE_TIMEOUT,
    PIPELINE_REPLY_BLOCK_FAILED,
    PIPELINE_WAIT_QUOTA,
    PIPELINE_WAIT_QUOTA_INVALID,
)
from ghdag.dag.completion import (
    CircuitAction,
    CompletionFacts,
    ContractErrorFact,
    EngineErrorFact,
    FanoutFact,
    FanoutState,
    HookKind,
    ResultActionKind,
    WaitQuotaFact,
    decide_completion,
)
from ghdag.dag.task_launcher import TaskLauncher

_ALLOWED_IMPORT_ROOTS = frozenset(
    {
        "__future__",
        "dataclasses",
        "datetime",
        "enum",
        "typing",
        "ghdag.core",
    }
)


def _base_facts(**overrides) -> CompletionFacts:
    base = CompletionFacts(
        returncode=0,
        cancelled=False,
        timed_out=False,
        retry=0,
        engine="cursor",
        stderr_text="",
    )
    return replace(base, **overrides)


def test_success() -> None:
    decision = decide_completion(_base_facts(), max_retry=2, task_timeout=60.0)
    assert decision.done == 0
    assert decision.metrics_status == "success"
    assert decision.hook is not None and decision.hook.kind == HookKind.SUCCESS
    assert decision.circuit == CircuitAction.RESET
    assert decision.promote is True


def test_rejected() -> None:
    open_decision = decide_completion(
        _base_facts(rejected=True, retry=0),
        max_retry=2,
        task_timeout=60.0,
    )
    assert open_decision.done == DONE_REJECTED
    assert open_decision.result_action == ResultActionKind.UNLINK
    assert open_decision.hook is not None
    assert open_decision.hook.kind == HookKind.REJECTED
    assert open_decision.hook.is_final is False
    assert open_decision.failure_class == FailureClass.REJECTED
    assert open_decision.circuit == CircuitAction.NONE

    final_decision = decide_completion(
        _base_facts(rejected=True, retry=2),
        max_retry=2,
        task_timeout=60.0,
    )
    assert final_decision.done == DONE_REJECTED_FINAL
    assert final_decision.result_action == ResultActionKind.KEEP


@pytest.mark.parametrize(
    ("wait_quota", "expected_done", "circuit"),
    [
        (
            WaitQuotaFact(engines=("cursor",), after=datetime(2026, 1, 1, tzinfo=timezone.utc)),
            None,
            CircuitAction.NONE,
        ),
        (
            WaitQuotaFact(invalid_reason="bad engines line"),
            f"{DONE_PIPELINE_FAILED_PREFIX}{PIPELINE_WAIT_QUOTA_INVALID}",
            CircuitAction.RECORD_FAILURE,
        ),
    ],
)
def test_wait_quota(wait_quota, expected_done, circuit) -> None:
    decision = decide_completion(
        _base_facts(
            pipeline_status=PIPELINE_WAIT_QUOTA,
            wait_quota=wait_quota,
        ),
        max_retry=2,
        task_timeout=60.0,
    )
    assert decision.done == expected_done
    assert decision.circuit == circuit


def test_reply_block_failed() -> None:
    violation = ContractErrorFact("RB", "message", "fix prompt")
    with_repair = decide_completion(
        _base_facts(
            pipeline_status=PIPELINE_REPLY_BLOCK_FAILED,
            reply_block_error=violation,
        ),
        max_retry=2,
        task_timeout=60.0,
    )
    assert with_repair.repair == violation
    assert with_repair.fallback is not None
    assert with_repair.fallback.done == (
        f"{DONE_PIPELINE_FAILED_PREFIX}{PIPELINE_REPLY_BLOCK_FAILED}"
    )

    direct = decide_completion(
        _base_facts(pipeline_status=PIPELINE_REPLY_BLOCK_FAILED),
        max_retry=2,
        task_timeout=60.0,
    )
    assert direct.repair is None
    assert direct.done == f"{DONE_PIPELINE_FAILED_PREFIX}{PIPELINE_REPLY_BLOCK_FAILED}"


def test_pipeline_x_failed() -> None:
    decision = decide_completion(
        _base_facts(pipeline_status="X_FAILED"),
        max_retry=2,
        task_timeout=60.0,
    )
    assert decision.done == f"{DONE_PIPELINE_FAILED_PREFIX}X_FAILED"
    assert decision.failure_class == FailureClass.PIPELINE_FAILED
    assert decision.circuit == CircuitAction.RECORD_FAILURE


@pytest.mark.parametrize(
    (
        "failure_class",
        "retry",
        "returncode",
        "expected_done",
        "circuit",
        "quarantine",
        "retry_audit",
    ),
    [
        (FailureClass.QUOTA_EXHAUSTED, 0, 9, None, CircuitAction.NONE, False, False),
        (
            FailureClass.ENGINE_ENVIRONMENT_ERROR,
            0,
            9,
            DONE_ENGINE_ENV_ERROR,
            CircuitAction.RECORD_FAILURE,
            True,
            False,
        ),
        (
            FailureClass.INTERACTIVE_PROMPT,
            0,
            9,
            DONE_INTERACTIVE_PROMPT,
            CircuitAction.RECORD_FAILURE,
            False,
            False,
        ),
        (FailureClass.ENGINE_ERROR, 0, 9, DONE_ENGINE_ERROR, CircuitAction.RECORD_FAILURE, False, True),
        (
            FailureClass.ENGINE_ERROR,
            2,
            9,
            DONE_ENGINE_ERROR_FINAL,
            CircuitAction.RECORD_FAILURE,
            False,
            False,
        ),
        (FailureClass.AUTH, 0, 17, 17, CircuitAction.RECORD_FAILURE, False, False),
        (FailureClass.PROCESS_ERROR, 0, 9, 9, CircuitAction.RECORD_FAILURE, False, False),
    ],
)
def test_nonzero_by_failure_class(
    failure_class,
    retry,
    returncode,
    expected_done,
    circuit,
    quarantine,
    retry_audit,
) -> None:
    decision = decide_completion(
        CompletionFacts(
            returncode=returncode,
            cancelled=False,
            timed_out=False,
            retry=retry,
            engine="cursor",
            stderr_text="stderr",
            failure_class=failure_class,
            failure_output="question?\n",
            failure_class_reason="tail",
        ),
        max_retry=2,
        task_timeout=60.0,
    )
    assert decision.done == expected_done
    assert decision.circuit == circuit
    assert decision.quarantine is quarantine
    assert decision.retry_audit is retry_audit


def test_cancel_and_timeout() -> None:
    cancelled = decide_completion(
        _base_facts(cancelled=True),
        max_retry=2,
        task_timeout=60.0,
    )
    assert cancelled.done == DONE_CANCELLED
    assert cancelled.hook is not None and cancelled.hook.kind == HookKind.CANCELLED
    assert cancelled.metrics_status is None

    timeout = decide_completion(
        _base_facts(timed_out=True, returncode=137),
        max_retry=2,
        task_timeout=42.0,
    )
    assert timeout.done == DONE_TIMEOUT
    assert timeout.circuit == CircuitAction.RECORD_FAILURE

    both = decide_completion(
        _base_facts(cancelled=True, timed_out=True),
        max_retry=2,
        task_timeout=42.0,
    )
    assert both.done == DONE_CANCELLED


def test_engine_error() -> None:
    quota = decide_completion(
        _base_facts(
            returncode=0,
            engine_error=EngineErrorFact(
                kind="QUOTA_EXHAUSTED",
                message="limit",
                retryable=False,
                resume_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
            ),
        ),
        max_retry=2,
        task_timeout=60.0,
    )
    assert quota.done is None
    assert quota.quota_pause is not None
    assert quota.result_action == ResultActionKind.UNLINK

    retryable = decide_completion(
        _base_facts(
            engine_error=EngineErrorFact(
                kind="RATE_LIMIT",
                message="slow down",
                retryable=True,
            ),
        ),
        max_retry=2,
        task_timeout=60.0,
    )
    assert retryable.done == DONE_ENGINE_ERROR

    final = decide_completion(
        _base_facts(
            engine_error=EngineErrorFact(
                kind="RATE_LIMIT",
                message="slow down",
                retryable=False,
            ),
        ),
        max_retry=2,
        task_timeout=60.0,
    )
    assert final.done == DONE_ENGINE_ERROR_FINAL


def test_empty_result_and_fanout() -> None:
    empty = decide_completion(
        _base_facts(result_empty=True),
        max_retry=2,
        task_timeout=60.0,
    )
    assert empty.done == DONE_EMPTY_RESULT
    assert empty.hook is not None and empty.hook.kind == HookKind.EMPTY_RESULT
    assert empty.circuit == CircuitAction.NONE

    spawn = decide_completion(
        _base_facts(fanout=FanoutFact(state=FanoutState.PRESENT)),
        max_retry=2,
        task_timeout=60.0,
    )
    assert spawn.done is None
    assert spawn.fanout_spawn is True

    parse_fail = decide_completion(
        _base_facts(
            fanout=FanoutFact(
                state=FanoutState.PARSE_FAILED,
                error=ContractErrorFact("FANOUT_INVALID", "bad", "hint"),
            )
        ),
        max_retry=2,
        task_timeout=60.0,
    )
    assert parse_fail.repair is not None
    assert parse_fail.fallback is not None
    assert parse_fail.fallback.done == DONE_FANOUT_PARSE_FAILED
    assert parse_fail.fallback.circuit == CircuitAction.NONE


def _module_import_allowed(module: str) -> bool:
    return any(
        module == allowed or module.startswith(f"{allowed}.")
        for allowed in _ALLOWED_IMPORT_ROOTS
    )


def test_completion_module_imports() -> None:
    module_path = Path(__file__).resolve().parents[2] / "src/ghdag/dag/completion.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert _module_import_allowed(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module is None:
                continue
            assert _module_import_allowed(node.module)


def test_check_completions_is_short() -> None:
    source = inspect.getsource(TaskLauncher.check_completions)
    assert len(source.splitlines()) <= 100
