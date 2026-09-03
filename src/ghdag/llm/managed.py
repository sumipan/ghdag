"""Policy-managed LLM calls: classify, quota report, and one-shot fallback."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

from ghdag.core.models.metrics import FailureClass, TokenUsage
from ghdag.llm.adapters import get_output_adapter
from ghdag.llm.capabilities import TEXT_ONLY, LLMCapabilities
from ghdag.llm.engines import LLMResult, call, validate_engine_model
from ghdag.quota import QuotaGate

_FALLBACK_CLASSES = frozenset(
    {
        FailureClass.QUOTA_EXHAUSTED.value,
        FailureClass.AUTH.value,
    }
)


@dataclass(frozen=True)
class ManagedResult:
    body: str
    usage: TokenUsage | None
    returncode: int
    failure_class: str | None
    engine_used: str
    model_used: str
    attempts: int
    quota_reported: bool
    additional_tags: dict[str, str]


def call_managed(
    prompt: str,
    *,
    engine: str = "claude",
    model: str | None = None,
    timeout: int | None = None,
    stdin_text: str | None = None,
    cwd: Path | str | None = None,
    capabilities: LLMCapabilities = TEXT_ONLY,
    fallback_candidates: Sequence[tuple[str, str | None]] = (),
    additional_tags: Mapping[str, str] | None = None,
    quota_gate: QuotaGate | None = None,
) -> ManagedResult:
    """Run ``call()`` with failure classification, quota reporting, and one fallback.

    Fallback runs at most once, only for ``QUOTA_EXHAUSTED`` / ``AUTH``, skipping
    engines that ``QuotaGate.snapshot()`` reports as paused. Alternate attempts
    do not fall back again.
    """
    tags = dict(additional_tags or {})
    resolved_model = validate_engine_model(engine, model)
    result, body, usage, failure_class = _run_once(
        prompt,
        engine=engine,
        model=model,
        timeout=timeout,
        stdin_text=stdin_text,
        cwd=cwd,
        capabilities=capabilities,
    )
    attempts = 1
    quota_reported = False
    engine_used = engine
    model_used = resolved_model

    if result.returncode == 0:
        return ManagedResult(
            body=body,
            usage=usage,
            returncode=0,
            failure_class=None,
            engine_used=engine_used,
            model_used=model_used,
            attempts=attempts,
            quota_reported=False,
            additional_tags=tags,
        )

    if failure_class in _FALLBACK_CLASSES:
        if quota_gate is not None:
            quota_gate.report(
                engine=engine_used,
                status="paused",
                observed_at=datetime.now(timezone.utc),
                reason=f"call_managed classified as {failure_class}",
            )
            quota_reported = True

        alt = _select_fallback(fallback_candidates, quota_gate)
        if alt is not None:
            alt_engine, alt_model = alt
            result, body, usage, failure_class = _run_once(
                prompt,
                engine=alt_engine,
                model=alt_model,
                timeout=timeout,
                stdin_text=stdin_text,
                cwd=cwd,
                capabilities=capabilities,
            )
            attempts = 2
            engine_used = alt_engine
            model_used = validate_engine_model(alt_engine, alt_model)
            if result.returncode == 0:
                failure_class = None

    return ManagedResult(
        body=body,
        usage=usage,
        returncode=result.returncode,
        failure_class=failure_class,
        engine_used=engine_used,
        model_used=model_used,
        attempts=attempts,
        quota_reported=quota_reported,
        additional_tags=tags,
    )


def _run_once(
    prompt: str,
    *,
    engine: str,
    model: str | None,
    timeout: int | None,
    stdin_text: str | None,
    cwd: Path | str | None,
    capabilities: LLMCapabilities,
) -> tuple[LLMResult, str, TokenUsage | None, str | None]:
    result = call(
        prompt,
        engine=engine,
        model=model,
        timeout=timeout,
        stdin_text=stdin_text,
        cwd=cwd,
        capabilities=capabilities,
    )
    adapter = get_output_adapter(engine)
    stdout_bytes = result.stdout.encode("utf-8")
    stderr_bytes = result.stderr.encode("utf-8")
    extracted = adapter.extract_result_text(stdout_bytes, stderr_bytes).decode("utf-8")
    body = extracted if extracted else result.stdout
    usage = adapter.extract_token_usage(stdout_bytes, stderr_bytes)
    failure_class: str | None = None
    if result.returncode != 0:
        classified = adapter.classify_failure(
            result.returncode, stdout_bytes, stderr_bytes
        )
        failure_class = classified.value if classified is not None else None
    return result, body, usage, failure_class


def _select_fallback(
    candidates: Sequence[tuple[str, str | None]],
    quota_gate: QuotaGate | None,
) -> tuple[str, str | None] | None:
    if not candidates:
        return None
    paused: set[str] = set()
    if quota_gate is not None:
        snapshot = quota_gate.snapshot()
        paused = {
            name
            for name, state in snapshot.engines.items()
            if state.status == "paused"
        }
    for candidate_engine, candidate_model in candidates:
        if candidate_engine in paused:
            continue
        return candidate_engine, candidate_model
    return None


__all__ = ["ManagedResult", "call_managed"]
