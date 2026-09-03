"""tests/llm/test_managed.py — call_managed() の回帰テスト (Issue #2793)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ghdag.core.models.metrics import FailureClass, TokenUsage
from ghdag.llm.engines import LLMResult
from ghdag.llm.managed import ManagedResult, call_managed
from ghdag.quota import QuotaGate


def _llm(stdout: str = "ok", stderr: str = "", returncode: int = 0) -> LLMResult:
    return LLMResult(stdout=stdout, stderr=stderr, returncode=returncode)


def _adapter(
    *,
    body: bytes = b"extracted",
    usage: TokenUsage | None = None,
    failure: FailureClass | None = None,
) -> MagicMock:
    adapter = MagicMock()
    adapter.extract_result_text.return_value = body
    adapter.extract_token_usage.return_value = usage
    adapter.classify_failure.return_value = failure
    return adapter


class TestManagedResult:
    def test_frozen(self):
        result = ManagedResult(
            body="x",
            usage=None,
            returncode=0,
            failure_class=None,
            engine_used="claude",
            model_used="claude-sonnet-4-6",
            attempts=1,
            quota_reported=False,
            additional_tags={},
        )
        with pytest.raises(Exception):
            result.body = "y"  # type: ignore[misc]


class TestCallManagedSuccess:
    def test_success_returns_extracted_body_and_usage(self):
        usage = TokenUsage(token_count=42, cost_usd=0.01)
        mock_adapter = _adapter(body=b"hello", usage=usage)
        with (
            patch("ghdag.llm.managed.call", return_value=_llm("raw")) as mock_call,
            patch("ghdag.llm.managed.get_output_adapter", return_value=mock_adapter),
            patch("ghdag.llm.managed.validate_engine_model", return_value="claude-sonnet-4-6"),
        ):
            result = call_managed("prompt", engine="claude", model=None)

        assert isinstance(result, ManagedResult)
        assert result.body == "hello"
        assert result.usage is usage
        assert result.returncode == 0
        assert result.failure_class is None
        assert result.engine_used == "claude"
        assert result.model_used == "claude-sonnet-4-6"
        assert result.attempts == 1
        assert result.quota_reported is False
        assert result.additional_tags == {}
        mock_adapter.classify_failure.assert_not_called()
        mock_call.assert_called_once()

    def test_passes_kwargs_including_cwd(self, tmp_path: Path):
        mock_adapter = _adapter(body=b"ok")
        with (
            patch("ghdag.llm.managed.call", return_value=_llm()) as mock_call,
            patch("ghdag.llm.managed.get_output_adapter", return_value=mock_adapter),
            patch("ghdag.llm.managed.validate_engine_model", return_value="m"),
        ):
            call_managed(
                "p",
                engine="claude",
                model="m",
                timeout=10,
                stdin_text="in",
                cwd=tmp_path,
                additional_tags={"template": "b1"},
            )

        kwargs = mock_call.call_args.kwargs
        assert kwargs["engine"] == "claude"
        assert kwargs["model"] == "m"
        assert kwargs["timeout"] == 10
        assert kwargs["stdin_text"] == "in"
        assert kwargs["cwd"] == tmp_path

    def test_additional_tags_preserved(self):
        mock_adapter = _adapter()
        with (
            patch("ghdag.llm.managed.call", return_value=_llm()),
            patch("ghdag.llm.managed.get_output_adapter", return_value=mock_adapter),
            patch("ghdag.llm.managed.validate_engine_model", return_value="m"),
        ):
            result = call_managed("p", additional_tags={"tier": "heavy"})
        assert result.additional_tags == {"tier": "heavy"}


class TestCallManagedClassification:
    def test_classify_failure_receives_stdout_and_stderr(self):
        mock_adapter = _adapter(failure=FailureClass.UNKNOWN_FAILURE)
        with (
            patch(
                "ghdag.llm.managed.call",
                return_value=_llm(stdout="OUT", stderr="ERR", returncode=1),
            ),
            patch("ghdag.llm.managed.get_output_adapter", return_value=mock_adapter),
            patch("ghdag.llm.managed.validate_engine_model", return_value="m"),
        ):
            result = call_managed("p", engine="claude")

        mock_adapter.classify_failure.assert_called_once_with(1, b"OUT", b"ERR")
        assert result.failure_class == FailureClass.UNKNOWN_FAILURE.value
        assert result.attempts == 1

    def test_unknown_failure_does_not_fallback(self):
        calls: list[str] = []

        def fake_call(prompt, *, engine, **kwargs):
            del prompt, kwargs
            calls.append(engine)
            return _llm(stdout="boom", returncode=1)

        mock_adapter = _adapter(failure=FailureClass.UNKNOWN_FAILURE)
        with (
            patch("ghdag.llm.managed.call", side_effect=fake_call),
            patch("ghdag.llm.managed.get_output_adapter", return_value=mock_adapter),
            patch("ghdag.llm.managed.validate_engine_model", side_effect=lambda e, m: m or "default"),
        ):
            result = call_managed(
                "p",
                engine="claude",
                fallback_candidates=[("codex", "gpt-5.6-sol")],
            )

        assert calls == ["claude"]
        assert result.engine_used == "claude"
        assert result.attempts == 1
        assert result.quota_reported is False


class TestCallManagedFallback:
    def test_quota_exhausted_reports_and_fallbacks_once(self, tmp_path: Path):
        gate = QuotaGate(tmp_path / "quota-gate.json")
        calls: list[str] = []

        def fake_call(prompt, *, engine, **kwargs):
            del prompt, kwargs
            calls.append(engine)
            if engine == "claude":
                return _llm(stdout="session limit", returncode=1)
            return _llm(stdout='{"result":"ok"}', returncode=0)

        def fake_adapter(engine: str):
            adapter = MagicMock()
            if engine == "claude":
                adapter.extract_result_text.return_value = b"session limit"
                adapter.extract_token_usage.return_value = None
                adapter.classify_failure.return_value = FailureClass.QUOTA_EXHAUSTED
            else:
                adapter.extract_result_text.return_value = b"ok"
                adapter.extract_token_usage.return_value = TokenUsage(token_count=1)
                adapter.classify_failure.return_value = None
            return adapter

        with (
            patch("ghdag.llm.managed.call", side_effect=fake_call),
            patch("ghdag.llm.managed.get_output_adapter", side_effect=fake_adapter),
            patch(
                "ghdag.llm.managed.validate_engine_model",
                side_effect=lambda e, m: m or f"{e}-default",
            ),
        ):
            result = call_managed(
                "p",
                engine="claude",
                model="claude-opus-4-6",
                fallback_candidates=[("codex", "gpt-5.6-sol")],
                quota_gate=gate,
            )

        assert calls == ["claude", "codex"]
        assert result.returncode == 0
        assert result.engine_used == "codex"
        assert result.model_used == "gpt-5.6-sol"
        assert result.attempts == 2
        assert result.quota_reported is True
        assert result.failure_class is None
        assert result.body == "ok"
        assert gate.snapshot().engines["claude"].status == "paused"

    def test_auth_also_triggers_fallback(self, tmp_path: Path):
        gate = QuotaGate(tmp_path / "quota-gate.json")
        calls: list[str] = []

        def fake_call(prompt, *, engine, **kwargs):
            del prompt, kwargs
            calls.append(engine)
            if engine == "claude":
                return _llm(stdout="OAuth session expired", returncode=1)
            return _llm(returncode=0)

        def fake_adapter(engine: str):
            adapter = MagicMock()
            adapter.extract_result_text.return_value = b"x"
            adapter.extract_token_usage.return_value = None
            adapter.classify_failure.return_value = (
                FailureClass.AUTH if engine == "claude" else None
            )
            return adapter

        with (
            patch("ghdag.llm.managed.call", side_effect=fake_call),
            patch("ghdag.llm.managed.get_output_adapter", side_effect=fake_adapter),
            patch(
                "ghdag.llm.managed.validate_engine_model",
                side_effect=lambda e, m: m or "default",
            ),
        ):
            result = call_managed(
                "p",
                engine="claude",
                fallback_candidates=[("codex", "gpt")],
                quota_gate=gate,
            )

        assert calls == ["claude", "codex"]
        assert result.engine_used == "codex"
        assert result.quota_reported is True

    def test_no_double_fallback_on_alternate_failure(self, tmp_path: Path):
        gate = QuotaGate(tmp_path / "quota-gate.json")
        calls: list[str] = []

        def fake_call(prompt, *, engine, **kwargs):
            del prompt, kwargs
            calls.append(engine)
            return _llm(stdout="session limit", returncode=1)

        mock_adapter = _adapter(failure=FailureClass.QUOTA_EXHAUSTED)
        with (
            patch("ghdag.llm.managed.call", side_effect=fake_call),
            patch("ghdag.llm.managed.get_output_adapter", return_value=mock_adapter),
            patch(
                "ghdag.llm.managed.validate_engine_model",
                side_effect=lambda e, m: m or "default",
            ),
        ):
            result = call_managed(
                "p",
                engine="claude",
                fallback_candidates=[("codex", "gpt"), ("cursor", "auto")],
                quota_gate=gate,
            )

        assert calls == ["claude", "codex"]
        assert result.attempts == 2
        assert result.returncode == 1
        assert result.engine_used == "codex"
        assert result.failure_class == FailureClass.QUOTA_EXHAUSTED.value

    def test_skips_paused_fallback_candidates(self, tmp_path: Path):
        gate = QuotaGate(tmp_path / "quota-gate.json")
        now = datetime.now(timezone.utc)
        gate.report(engine="codex", status="paused", observed_at=now, reason="pre-paused")
        calls: list[str] = []

        def fake_call(prompt, *, engine, **kwargs):
            del prompt, kwargs
            calls.append(engine)
            if engine == "claude":
                return _llm(stdout="session limit", returncode=1)
            return _llm(returncode=0)

        def fake_adapter(engine: str):
            adapter = MagicMock()
            adapter.extract_result_text.return_value = b"x"
            adapter.extract_token_usage.return_value = None
            adapter.classify_failure.return_value = (
                FailureClass.QUOTA_EXHAUSTED if engine == "claude" else None
            )
            return adapter

        with (
            patch("ghdag.llm.managed.call", side_effect=fake_call),
            patch("ghdag.llm.managed.get_output_adapter", side_effect=fake_adapter),
            patch(
                "ghdag.llm.managed.validate_engine_model",
                side_effect=lambda e, m: m or "default",
            ),
        ):
            result = call_managed(
                "p",
                engine="claude",
                fallback_candidates=[("codex", "gpt"), ("cursor", "auto")],
                quota_gate=gate,
            )

        assert calls == ["claude", "cursor"]
        assert result.engine_used == "cursor"

    def test_all_candidates_paused_returns_primary_failure(self, tmp_path: Path):
        gate = QuotaGate(tmp_path / "quota-gate.json")
        now = datetime.now(timezone.utc)
        gate.report(engine="codex", status="paused", observed_at=now)
        calls: list[str] = []

        def fake_call(prompt, *, engine, **kwargs):
            del prompt, kwargs
            calls.append(engine)
            return _llm(stdout="session limit", returncode=1)

        mock_adapter = _adapter(failure=FailureClass.QUOTA_EXHAUSTED)
        with (
            patch("ghdag.llm.managed.call", side_effect=fake_call),
            patch("ghdag.llm.managed.get_output_adapter", return_value=mock_adapter),
            patch(
                "ghdag.llm.managed.validate_engine_model",
                side_effect=lambda e, m: m or "default",
            ),
        ):
            result = call_managed(
                "p",
                engine="claude",
                fallback_candidates=[("codex", "gpt")],
                quota_gate=gate,
            )

        assert calls == ["claude"]
        assert result.engine_used == "claude"
        assert result.attempts == 1
        assert result.quota_reported is True
        assert result.failure_class == FailureClass.QUOTA_EXHAUSTED.value


class TestCallManagedExport:
    def test_import_from_ghdag_llm(self):
        from ghdag.llm import ManagedResult as MR
        from ghdag.llm import call_managed as cm

        assert MR is ManagedResult
        assert cm is call_managed
