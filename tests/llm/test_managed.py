"""tests/llm/test_managed.py — fallback / quota on unified call() (Issue sumipan/nexus#6529)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from ghdag.core.capabilities import JSON_ONLY, TEXT_ONLY
from ghdag.core.models.metrics import FailureClass
from ghdag.core.ports.output import EngineError, EngineErrorKind
from ghdag.llm import engines as engines_mod
from ghdag.llm.engines import call
from ghdag.quota import QuotaGate

_QUOTA_OBSERVED_AT = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
_QUOTA_NOW = datetime(2024, 1, 15, 12, 1, 0, tzinfo=timezone.utc)


def _adapter(*, failure: FailureClass | None = None, body: bytes = b"extracted") -> MagicMock:
    adapter = MagicMock()
    adapter.extract_result_text.return_value = body
    adapter.extract_token_usage.return_value = None
    adapter.extract_error.return_value = None
    adapter.classify_failure.return_value = failure
    adapter.extract_session_id.return_value = None
    return adapter


def _subprocess(stdout: str = "ok", *, returncode: int = 1, failure: FailureClass | None = None):
    return engines_mod._SubprocessRun(
        stdout=stdout,
        stderr="",
        returncode=returncode,
        latency_ms=1.0,
        failure_class=failure,
    )


def test_call_without_fallback_returns_primary_quota_failure(tmp_path: Path) -> None:
    """Issue sumipan/nexus#6529 — quota exhaustion without candidate stays on primary."""
    mock_adapter = _adapter(failure=FailureClass.QUOTA_EXHAUSTED)

    with (
        patch.object(engines_mod, "_call_once", return_value=_subprocess("limit", returncode=1)),
        patch.object(engines_mod, "get_output_adapter", return_value=mock_adapter),
        patch.object(engines_mod, "validate_engine_model", return_value="claude-sonnet-4-6"),
    ):
        result = call("p", engine="claude")

    assert result.attempts == 1
    assert result.ok is False
    assert result.failure_class is FailureClass.QUOTA_EXHAUSTED


def test_call_quota_fallback_uses_override_and_reports_primary(tmp_path: Path) -> None:
    """Issue sumipan/nexus#6529 — fallback uses permission_overrides and reports pause."""
    gate = QuotaGate(tmp_path / "quota.json")
    calls: list[tuple[str, object]] = []

    def fake_once(prompt, *, engine, permission, **kwargs):
        del prompt, kwargs
        calls.append((engine, permission))
        if engine == "claude":
            return _subprocess("limit", returncode=1, failure=FailureClass.QUOTA_EXHAUSTED)
        stdout = '{"verdict": "ok"}' if permission.output_format == "json" else "ok"
        return _subprocess(stdout, returncode=0)

    codex_json = JSON_ONLY
    mock_adapter = _adapter()

    with (
        patch("ghdag.quota._aware_now", side_effect=lambda now=None: _QUOTA_NOW if now is None else now),
        patch.object(engines_mod, "_call_once", side_effect=fake_once),
        patch.object(engines_mod, "get_output_adapter", return_value=mock_adapter),
        patch.object(
            engines_mod,
            "validate_engine_model",
            side_effect=lambda e, m: m or ("claude-sonnet-4-6" if e == "claude" else "gpt"),
        ),
    ):
        result = call(
            "p",
            engine="claude",
            permission=TEXT_ONLY,
            fallback_candidates=[("codex", "gpt")],
            permission_overrides={"codex": codex_json},
            quota_gate=gate,
        )

    assert result.attempts == 2
    assert result.engine_used == "codex"
    assert result.quota_reported is True
    assert calls[0][1] is TEXT_ONLY
    assert calls[1][1] is codex_json
    snap = gate.snapshot()
    assert snap.engines["claude"].status == "paused"


def test_call_structured_quota_error_is_classified_before_fallback(tmp_path: Path) -> None:
    """A structured error must not bypass failure classification and fallback."""
    gate = QuotaGate(tmp_path / "quota.json")
    calls: list[str] = []

    def fake_once(prompt, *, engine, **kwargs):
        del prompt, kwargs
        calls.append(engine)
        if engine == "claude":
            return _subprocess("quota error", returncode=1)
        return _subprocess("ok", returncode=0)

    primary_adapter = _adapter(failure=FailureClass.QUOTA_EXHAUSTED)
    primary_adapter.extract_error.return_value = EngineError(
        kind=EngineErrorKind.QUOTA_EXHAUSTED,
        message="quota exhausted",
        retryable=False,
    )
    fallback_adapter = _adapter(body=b"ok")

    with (
        patch.object(engines_mod, "_call_once", side_effect=fake_once),
        patch.object(
            engines_mod,
            "get_output_adapter",
            side_effect=lambda engine: (
                primary_adapter if engine == "claude" else fallback_adapter
            ),
        ),
        patch.object(
            engines_mod,
            "validate_engine_model",
            side_effect=lambda engine, model: model or f"{engine}-default",
        ),
    ):
        result = call(
            "p",
            engine="claude",
            fallback_candidates=[("codex", None)],
            quota_gate=gate,
        )

    assert calls == ["claude", "codex"]
    assert result.ok is True
    assert result.engine_used == "codex"
    assert result.attempts == 2
    assert result.quota_reported is True


def test_call_timeout_does_not_fallback_or_pause_by_default(tmp_path: Path) -> None:
    """Issue sumipan/nexus#6529 — TIMEOUT does not fallback or pause by default."""
    gate = QuotaGate(tmp_path / "quota.json")

    with (
        patch.object(
            engines_mod,
            "_call_once",
            return_value=_subprocess(
                "partial",
                returncode=engines_mod.TIMEOUT_RETURNCODE,
                failure=FailureClass.TIMEOUT,
            ),
        ),
        patch.object(engines_mod, "get_output_adapter", return_value=_adapter()),
        patch.object(engines_mod, "validate_engine_model", return_value="claude-sonnet-4-6"),
    ):
        result = call(
            "p",
            engine="claude",
            fallback_candidates=[("codex", "gpt")],
            quota_gate=gate,
        )

    assert result.returncode == engines_mod.TIMEOUT_RETURNCODE
    assert result.failure_class is FailureClass.TIMEOUT
    assert result.attempts == 1
    assert gate.snapshot().engines == {}
