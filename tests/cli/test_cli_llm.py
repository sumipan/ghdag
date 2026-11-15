"""Tests for ghdag.cli — AC1 - AC11.

Test approach:
- Parse validation via main(argv=[...])
- Mock DagEngine / WorkflowDispatcher; assert correct args
- Error cases assert SystemExit code
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest


class TestLlmAuditPath:
    """Tests for --audit-path and --correlation-id in ghdag llm."""

    def _make_result(self, returncode: int = 0):
        from ghdag.llm.engines import LLMResult
        return LLMResult(body="ok\n", stdout="ok\n", stderr="", returncode=returncode)

    def test_llm_timeout_unspecified_is_null(self, tmp_path):
        """AC6: without --timeout, timeout_sec = null."""
        audit_path = tmp_path / "audit.jsonl"
        mock_result = self._make_result(returncode=0)

        with patch("ghdag.llm.engines.call", return_value=mock_result), \
             patch("ghdag.llm.engines.validate_engine_model", return_value="claude-sonnet-4-6"):
            from ghdag.cli import main
            with pytest.raises(SystemExit):
                main(["llm", "hello", "--audit-path", str(audit_path)])

        r = json.loads(audit_path.read_text().strip())
        assert r["timeout_sec"] is None


def test_permission_preset_is_forwarded():
    from ghdag.core.capabilities import TEXT_ONLY
    from ghdag.llm.engines import LLMResult

    captured: dict = {}

    def capture_call(*_args, **kwargs):
        captured.update(kwargs)
        return LLMResult(body="ok", stdout="ok", stderr="", returncode=0)

    from ghdag.cli import main

    main(["llm", "--permission", "text_only", "--list-engines"])
    with patch("ghdag.llm.engines.call", side_effect=capture_call):
        with patch("ghdag.llm.engines.validate_engine_model", return_value="claude-sonnet-4-6"):
            with pytest.raises(SystemExit) as exc:
                main(["llm", "hello", "--permission", "text_only"])
            assert exc.value.code == 0
    assert captured["permission"] == TEXT_ONLY


class TestCLI:
    def test_llm_list_models(self):
        """ghdag llm --list-models --engine claude"""
        import contextlib
        from io import StringIO

        from ghdag.cli import main

        buf = StringIO()
        with contextlib.redirect_stdout(buf):
            main(["llm", "--list-models", "--engine", "claude"])
        output = buf.getvalue()
        assert "claude-opus-4-6" in output
        assert "claude-sonnet-4-6" in output

    def test_llm_list_models_unknown_engine(self, capsys):
        """ghdag llm --list-models --engine unknown → exit 3"""
        from ghdag.cli import main

        with pytest.raises(SystemExit) as exc_info:
            main(["llm", "--list-models", "--engine", "unknown"])
        assert exc_info.value.code == 3
        assert capsys.readouterr().err.splitlines()[0].startswith("error[ENGINE_UNKNOWN]:")


class TestLlmStructuredOutput:
    def test_llm_json_prints_unified_result_fields(self, capsys, monkeypatch):
        monkeypatch.delenv("GHDAG_AUDIT_PATH", raising=False)
        monkeypatch.setattr("ghdag.config.env.ghdag_audit_path", lambda: None)
        from ghdag.llm.engines import LLMResult

        mock_result = LLMResult(
            body="ok\n",
            stdout="ok\n",
            stderr="",
            returncode=0,
            latency_ms=12.0,
            engine_used="claude",
            model_used="claude-sonnet-4-6",
            session_id="sess-1",
        )
        with patch("ghdag.llm.engines.call", return_value=mock_result), \
             patch("ghdag.llm.engines.validate_engine_model", return_value="claude-sonnet-4-6"):
            from ghdag.cli import main
            with pytest.raises(SystemExit) as exc:
                main(["llm", "hello", "--json"])
        assert exc.value.code == 0
        captured = capsys.readouterr()
        assert captured.err == ""
        payload = json.loads(captured.out.strip())
        assert payload["ok"] is True
        assert payload["returncode"] == 0
        assert payload["body"] == "ok\n"
        assert payload["failure_class"] is None
        assert payload["engine_used"] == "claude"
        assert payload["model_used"] == "claude-sonnet-4-6"
        assert payload["session_id"] == "sess-1"
        assert payload["attempts"] == 1

    def test_llm_prompt_required_on_tty_exits_2(self, capsys, monkeypatch):
        monkeypatch.setattr("sys.stdin", type("Tty", (), {"isatty": lambda self: True})())
        from ghdag.cli import main
        with pytest.raises(SystemExit) as exc:
            main(["llm"])
        assert exc.value.code == 2
        assert capsys.readouterr().err.splitlines()[0].startswith("error[LLM_PROMPT_REQUIRED]:")
