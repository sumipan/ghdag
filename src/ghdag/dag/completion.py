"""Pure completion decisions from gathered task exit facts (#6876)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

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


class CircuitAction(str, Enum):
    NONE = "none"
    RECORD_FAILURE = "record_failure"
    RESET = "reset"


class HookKind(str, Enum):
    NONE = "none"
    SUCCESS = "success"
    FAILURE = "failure"
    REJECTED = "rejected"
    EMPTY_RESULT = "empty_result"
    CANCELLED = "cancelled"


class ResultActionKind(str, Enum):
    KEEP = "keep"
    UNLINK = "unlink"
    WRITE_ENGINE_ERROR = "write_engine_error"


class FanoutState(str, Enum):
    NONE = "none"
    PRESENT = "present"
    PARSE_FAILED = "parse_failed"
    SPAWN_FAILED = "spawn_failed"


@dataclass(frozen=True)
class ContractErrorFact:
    code: str
    message: str
    hint: str | None = None


@dataclass(frozen=True)
class EngineErrorFact:
    kind: str
    message: str
    retryable: bool
    resume_at: datetime | None = None


@dataclass(frozen=True)
class WaitQuotaFact:
    engines: tuple[str, ...] | None = None
    after: datetime | None = None
    invalid_reason: str | None = None


@dataclass(frozen=True)
class FanoutFact:
    state: FanoutState
    error: ContractErrorFact | None = None


@dataclass(frozen=True)
class QuotaPauseFact:
    engines: tuple[str, ...]
    resume_at: datetime | None
    reason: str
    set_by: str | None = None


@dataclass(frozen=True)
class HookDecision:
    kind: HookKind
    returncode: int | None = None
    message: str | None = None
    is_final: bool | None = None


@dataclass(frozen=True)
class CompletionFacts:
    returncode: int
    cancelled: bool
    timed_out: bool
    retry: int
    engine: str | None
    stderr_text: str
    engine_error: EngineErrorFact | None = None
    failure_class: FailureClass | None = None
    failure_output: str = ""
    failure_class_reason: str | None = None
    rejected: bool = False
    pipeline_status: str | None = None
    wait_quota: WaitQuotaFact | None = None
    reply_block_error: ContractErrorFact | None = None
    result_empty: bool = False
    fanout: FanoutFact | None = None


@dataclass(frozen=True)
class CompletionDecision:
    done: str | int | None = None
    metrics_status: str | None = None
    failure_class: FailureClass | None = None
    failure_class_reason: str | None = None
    hook: HookDecision | None = None
    circuit: CircuitAction = CircuitAction.NONE
    quota_pause: QuotaPauseFact | None = None
    wait_quota: WaitQuotaFact | None = None
    result_action: ResultActionKind = ResultActionKind.KEEP
    engine_error_summary: str | None = None
    quarantine: bool = False
    retry_audit: bool = False
    fanout_spawn: bool = False
    promote: bool = False
    repair: ContractErrorFact | None = None
    fallback: CompletionDecision | None = None


def _engine_error_decision(
    error: EngineErrorFact,
    *,
    retry: int,
    max_retry: int,
    returncode: int,
) -> CompletionDecision:
    if error.kind == "QUOTA_EXHAUSTED":
        return CompletionDecision(
            quota_pause=QuotaPauseFact(
                engines=(),
                resume_at=error.resume_at,
                reason=error.message,
            ),
            result_action=ResultActionKind.UNLINK,
        )
    summary = f"ENGINE_ERROR ({error.kind}): {error.message}"
    is_final = (not error.retryable) or (retry >= max_retry)
    done = DONE_ENGINE_ERROR_FINAL if is_final else DONE_ENGINE_ERROR
    return CompletionDecision(
        done=done,
        metrics_status="failure",
        failure_class=FailureClass.ENGINE_ERROR,
        hook=HookDecision(
            kind=HookKind.FAILURE,
            returncode=returncode,
            message=summary,
        ),
        circuit=CircuitAction.RECORD_FAILURE,
        result_action=ResultActionKind.WRITE_ENGINE_ERROR,
        engine_error_summary=summary,
    )


def _timeout_decision(task_timeout: float | None, returncode: int) -> CompletionDecision:
    msg = f"TIMEOUT: task exceeded task_timeout={task_timeout}s"
    return CompletionDecision(
        done=DONE_TIMEOUT,
        metrics_status="failure",
        failure_class=FailureClass.TIMEOUT,
        hook=HookDecision(kind=HookKind.FAILURE, returncode=returncode, message=msg),
        circuit=CircuitAction.RECORD_FAILURE,
    )


def _rejected_decision(retry: int, max_retry: int) -> CompletionDecision:
    is_final = retry >= max_retry
    done = DONE_REJECTED_FINAL if is_final else DONE_REJECTED
    return CompletionDecision(
        done=done,
        metrics_status="rejected",
        failure_class=FailureClass.REJECTED,
        hook=HookDecision(
            kind=HookKind.REJECTED,
            is_final=is_final,
        ),
        circuit=CircuitAction.NONE,
        result_action=ResultActionKind.UNLINK if not is_final else ResultActionKind.KEEP,
    )


def _wait_quota_decision(fact: WaitQuotaFact) -> CompletionDecision:
    if fact.invalid_reason is not None:
        pipeline_failed = f"{DONE_PIPELINE_FAILED_PREFIX}{PIPELINE_WAIT_QUOTA_INVALID}"
        return CompletionDecision(
            done=pipeline_failed,
            metrics_status="failure",
            failure_class=FailureClass.PIPELINE_FAILED,
            hook=HookDecision(
                kind=HookKind.FAILURE,
                returncode=0,
                message=fact.invalid_reason,
            ),
            circuit=CircuitAction.RECORD_FAILURE,
        )
    return CompletionDecision(
        wait_quota=fact,
        result_action=ResultActionKind.UNLINK,
    )


def _reply_block_decision(block_err: ContractErrorFact | None) -> CompletionDecision:
    pipeline_failed = f"{DONE_PIPELINE_FAILED_PREFIX}{PIPELINE_REPLY_BLOCK_FAILED}"
    fallback = CompletionDecision(
        done=pipeline_failed,
        metrics_status="failure",
        failure_class=FailureClass.PIPELINE_FAILED,
        hook=HookDecision(
            kind=HookKind.FAILURE,
            returncode=0,
            message=pipeline_failed,
        ),
        circuit=CircuitAction.RECORD_FAILURE,
    )
    if block_err is None:
        return fallback
    return CompletionDecision(repair=block_err, fallback=fallback)


def _fanout_failure_decision(error: ContractErrorFact) -> CompletionDecision:
    fallback = CompletionDecision(
        done=DONE_FANOUT_PARSE_FAILED,
        metrics_status="failure",
        failure_class=FailureClass.FANOUT_PARSE_FAILED,
        hook=HookDecision(kind=HookKind.FAILURE, returncode=0, message=error.message),
        circuit=CircuitAction.NONE,
    )
    return CompletionDecision(repair=error, fallback=fallback)


def _success_decision(fanout: FanoutFact | None) -> CompletionDecision:
    if fanout is None or fanout.state == FanoutState.NONE:
        return CompletionDecision(
            done=0,
            metrics_status="success",
            hook=HookDecision(kind=HookKind.SUCCESS),
            circuit=CircuitAction.RESET,
            promote=True,
        )
    if fanout.state == FanoutState.PRESENT:
        return CompletionDecision(
            metrics_status="success",
            fanout_spawn=True,
        )
    if fanout.state in (FanoutState.PARSE_FAILED, FanoutState.SPAWN_FAILED):
        assert fanout.error is not None
        return _fanout_failure_decision(fanout.error)
    return CompletionDecision(
        done=0,
        metrics_status="success",
        hook=HookDecision(kind=HookKind.SUCCESS),
        circuit=CircuitAction.RESET,
        promote=True,
    )


def _nonzero_decision(
    facts: CompletionFacts, max_retry: int
) -> CompletionDecision:
    classified = facts.failure_class
    if classified == FailureClass.QUOTA_EXHAUSTED:
        engines: tuple[str, ...] = (facts.engine,) if facts.engine else ("",)
        return CompletionDecision(
            quota_pause=QuotaPauseFact(
                engines=engines,
                resume_at=None,
                reason=facts.stderr_text,
            ),
            result_action=ResultActionKind.UNLINK,
        )
    if classified == FailureClass.ENGINE_ENVIRONMENT_ERROR:
        return CompletionDecision(
            done=DONE_ENGINE_ENV_ERROR,
            metrics_status="failure",
            failure_class=FailureClass.ENGINE_ENVIRONMENT_ERROR,
            hook=HookDecision(
                kind=HookKind.FAILURE,
                returncode=facts.returncode,
                message=facts.stderr_text,
            ),
            circuit=CircuitAction.RECORD_FAILURE,
            quarantine=True,
        )
    if classified == FailureClass.INTERACTIVE_PROMPT:
        question = facts.failure_output[:200]
        return CompletionDecision(
            done=DONE_INTERACTIVE_PROMPT,
            metrics_status="failure",
            failure_class=FailureClass.INTERACTIVE_PROMPT,
            failure_class_reason=facts.failure_class_reason,
            hook=HookDecision(
                kind=HookKind.FAILURE,
                returncode=facts.returncode,
                message=question,
            ),
            circuit=CircuitAction.RECORD_FAILURE,
        )
    if classified == FailureClass.ENGINE_ERROR:
        is_final = facts.retry >= max_retry
        return CompletionDecision(
            done=DONE_ENGINE_ERROR_FINAL if is_final else DONE_ENGINE_ERROR,
            metrics_status="failure",
            failure_class=FailureClass.ENGINE_ERROR,
            hook=HookDecision(
                kind=HookKind.FAILURE,
                returncode=facts.returncode,
                message=facts.stderr_text,
            ),
            circuit=CircuitAction.RECORD_FAILURE,
            retry_audit=not is_final,
        )
    if classified == FailureClass.AUTH:
        return CompletionDecision(
            done=facts.returncode,
            metrics_status="failure",
            failure_class=FailureClass.AUTH,
            hook=HookDecision(
                kind=HookKind.FAILURE,
                returncode=facts.returncode,
                message=facts.stderr_text,
            ),
            circuit=CircuitAction.RECORD_FAILURE,
        )
    return CompletionDecision(
        done=facts.returncode,
        metrics_status="failure",
        failure_class=FailureClass.PROCESS_ERROR,
        hook=HookDecision(
            kind=HookKind.FAILURE,
            returncode=facts.returncode,
            message=facts.stderr_text,
        ),
        circuit=CircuitAction.RECORD_FAILURE,
    )


def decide_completion(
    facts: CompletionFacts,
    *,
    max_retry: int,
    task_timeout: float | None,
) -> CompletionDecision:
    """Return side-effect-free completion handling for finished task facts."""
    if facts.cancelled:
        return CompletionDecision(
            done=DONE_CANCELLED,
            hook=HookDecision(kind=HookKind.CANCELLED),
        )
    if facts.timed_out:
        return _timeout_decision(task_timeout, facts.returncode)

    if facts.engine_error is not None:
        return _engine_error_decision(
            facts.engine_error,
            retry=facts.retry,
            max_retry=max_retry,
            returncode=facts.returncode,
        )

    if facts.returncode == 0:
        if facts.rejected:
            return _rejected_decision(facts.retry, max_retry)
        status = facts.pipeline_status
        if status == PIPELINE_WAIT_QUOTA and facts.wait_quota is not None:
            return _wait_quota_decision(facts.wait_quota)
        if status == PIPELINE_REPLY_BLOCK_FAILED:
            return _reply_block_decision(facts.reply_block_error)
        if status and status.endswith("_FAILED"):
            pipeline_failed = f"{DONE_PIPELINE_FAILED_PREFIX}{status}"
            return CompletionDecision(
                done=pipeline_failed,
                metrics_status="failure",
                failure_class=FailureClass.PIPELINE_FAILED,
                hook=HookDecision(
                    kind=HookKind.FAILURE,
                    returncode=0,
                    message=pipeline_failed,
                ),
                circuit=CircuitAction.RECORD_FAILURE,
            )
        if facts.result_empty:
            return CompletionDecision(
                done=DONE_EMPTY_RESULT,
                metrics_status="empty_result",
                failure_class=FailureClass.EMPTY_RESULT,
                hook=HookDecision(kind=HookKind.EMPTY_RESULT),
                circuit=CircuitAction.NONE,
            )
        return _success_decision(facts.fanout)

    return _nonzero_decision(facts, max_retry)
