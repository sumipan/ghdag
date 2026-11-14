"""tests/llm/test_call_text.py — unified ghdag.llm.call / LLMResult (Issue sumipan/nexus#6529)."""

from __future__ import annotations

import inspect
import json
from unittest.mock import MagicMock, patch

import pytest

from ghdag.core.capabilities import JSON_ONLY
from ghdag.core.command import render_exec_command
from ghdag.core.container import container_prefix
from ghdag.core.exceptions import LLMParseError
from ghdag.core.ports.output import EngineErrorKind
from ghdag.llm import call
from ghdag.llm import engines as engines_mod
from ghdag.llm.engines import build_llm_cmd


def test_public_signatures_use_permission() -> None:
    """Issue sumipan/nexus#6501 — public LLM entrypoints take permission, not capabilities."""
    targets = (
        call,
        build_llm_cmd,
        render_exec_command,
        container_prefix,
    )
    for fn in targets:
        params = inspect.signature(fn).parameters
        assert "permission" in params, f"{fn.__name__} missing permission parameter"
        assert "capabilities" not in params, f"{fn.__name__} still exposes capabilities"


def test_public_api_exports_only_call_and_llm_result() -> None:
    """Issue sumipan/nexus#6529 — one-shot API surface is call + LLMResult only."""
    import ghdag.llm as llm_pkg

    assert "call" in llm_pkg.__all__
    assert "LLMResult" in llm_pkg.__all__
    for removed in ("call_text", "call_managed", "TextResult", "ManagedResult"):
        assert removed not in llm_pkg.__all__
    with pytest.raises(ImportError):
        from ghdag.llm import call_text  # noqa: F401
    with pytest.raises(ImportError):
        from ghdag.llm import call_managed  # noqa: F401
    with pytest.raises(ImportError):
        from ghdag.llm import TextResult  # noqa: F401
    with pytest.raises(ImportError):
        from ghdag.llm import ManagedResult  # noqa: F401




def test_call_success_returns_body_raw_output_and_metadata() -> None:
    """Issue sumipan/nexus#6529 — success path fills body and metadata from adapter."""
    envelope = json.dumps({"type": "result", "result": "extracted", "session_id": "s1"})
    mock_adapter = MagicMock()
    mock_adapter.extract_error.return_value = None
    mock_adapter.extract_result_text.return_value = b"extracted"
    mock_adapter.extract_token_usage.return_value = None
    mock_adapter.extract_session_id.return_value = "s1"
    mock_adapter.classify_failure.return_value = None

    with (
        patch.object(engines_mod, "_call_once") as mock_once,
        patch.object(engines_mod, "get_output_adapter", return_value=mock_adapter),
        patch.object(engines_mod, "validate_engine_model", return_value="claude-sonnet-4-6"),
    ):
        mock_once.return_value = engines_mod._SubprocessRun(
            stdout=envelope,
            stderr="",
            returncode=0,
            latency_ms=5.0,
            session_id="s1",
        )
        result = call("hello", route=[("claude", "claude-sonnet-4-6")])

    assert result.ok is True
    assert result.body == "extracted"
    assert result.stdout == envelope
    assert result.raw_stdout is None
    assert result.attempts == 1
    assert result.engine_used == "claude"
    assert result.model_used == "claude-sonnet-4-6"
    assert result.failure_class is None


def test_call_adapter_error_sets_error_and_empty_body() -> None:
    """Issue sumipan/nexus#6529 — structured adapter error forces ok=False and body=''."""
    error_jsonl = '{"type":"error","message":"Selected model is at capacity."}'
    mock_adapter = MagicMock()
    mock_adapter.extract_error.return_value = MagicMock(kind=EngineErrorKind.CAPACITY)
    mock_adapter.extract_result_text.return_value = b""
    mock_adapter.extract_token_usage.return_value = None

    with (
        patch.object(engines_mod, "_call_once") as mock_once,
        patch.object(engines_mod, "get_output_adapter", return_value=mock_adapter),
        patch.object(engines_mod, "validate_engine_model", return_value="gpt-default"),
    ):
        mock_once.return_value = engines_mod._SubprocessRun(
            stdout=error_jsonl,
            stderr="",
            returncode=0,
            latency_ms=1.0,
        )
        result = call("test", route=[("codex", None)])

    assert result.ok is False
    assert result.error is not None
    assert result.body == ""


def test_call_json_permission_extracts_json_or_raises_parse_error() -> None:
    """Issue sumipan/nexus#6529 — JSON_ONLY puts parsed JSON in body or raises LLMParseError."""
    fenced = 'Here is the answer:\n```json\n{"k": "v"}\n```'
    mock_adapter = MagicMock()
    mock_adapter.extract_error.return_value = None
    mock_adapter.extract_result_text.return_value = fenced.encode()
    mock_adapter.extract_token_usage.return_value = None
    mock_adapter.classify_failure.return_value = None

    with (
        patch.object(engines_mod, "_call_once") as mock_once,
        patch.object(engines_mod, "get_output_adapter", return_value=mock_adapter),
        patch.object(engines_mod, "validate_engine_model", return_value="claude-sonnet-4-6"),
    ):
        mock_once.return_value = engines_mod._SubprocessRun(
            stdout=fenced,
            stderr="",
            returncode=0,
            latency_ms=1.0,
        )
        result = call("p", permission=JSON_ONLY, route=[("claude", None)])

    assert result.body == '{"k": "v"}'

    bad_adapter = MagicMock()
    bad_adapter.extract_error.return_value = None
    bad_adapter.extract_result_text.return_value = b"not json at all"
    bad_adapter.extract_token_usage.return_value = None
    bad_adapter.classify_failure.return_value = None

    with (
        patch.object(engines_mod, "_call_once") as mock_once,
        patch.object(engines_mod, "get_output_adapter", return_value=bad_adapter),
        patch.object(engines_mod, "validate_engine_model", return_value="claude-sonnet-4-6"),
    ):
        mock_once.return_value = engines_mod._SubprocessRun(
            stdout="not json at all",
            stderr="",
            returncode=0,
            latency_ms=1.0,
        )
        with pytest.raises(LLMParseError):
            call("p", permission=JSON_ONLY, route=[("claude", None)])
