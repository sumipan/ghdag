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
        return LLMResult(stdout="ok\n", stderr="", returncode=returncode)

    def test_llm_audit_path_and_correlation_id(self, tmp_path):
        """AC1: --audit-path + --correlation-id + success → append record."""
        audit_path = tmp_path / "audit.jsonl"
        mock_result = self._make_result(returncode=0)

        with patch("ghdag.llm.engines.call", return_value=mock_result), \
             patch("ghdag.llm.engines.validate_engine_model", return_value="claude-sonnet-4-6"):
            from ghdag.cli import main
            with pytest.raises(SystemExit) as exc:
                main([
                    "llm", "hello",
                    "--engine", "claude",
                    "--model", "claude-sonnet-4-6",
                    "--audit-path", str(audit_path),
                    "--correlation-id", "slack:1234",
                ])
        assert exc.value.code == 0
        assert audit_path.exists()
        r = json.loads(audit_path.read_text().strip())
        assert r["event"] == "llm_call"
        assert r["source"] == "llm_cli"
        assert r["engine"] == "claude"
        assert r["model"] == "claude-sonnet-4-6"
        assert r["exit_code"] == 0
        assert r["correlation_id"] == "slack:1234"
        assert "+00:00" in r["timestamp"]

    def test_llm_correlation_id_unspecified_is_null(self, tmp_path):
        """AC2: correlation_id omitted → null."""
        audit_path = tmp_path / "audit.jsonl"
        mock_result = self._make_result(returncode=0)

        with patch("ghdag.llm.engines.call", return_value=mock_result), \
             patch("ghdag.llm.engines.validate_engine_model", return_value="claude-sonnet-4-6"):
            from ghdag.cli import main
            with pytest.raises(SystemExit):
                main(["llm", "hello", "--audit-path", str(audit_path)])

        r = json.loads(audit_path.read_text().strip())
        assert r["correlation_id"] is None

    def test_llm_audit_path_from_env_var(self, tmp_path, monkeypatch):
        """AC3: path via env GHDAG_AUDIT_PATH."""
        audit_path = tmp_path / "audit.jsonl"
        monkeypatch.setenv("GHDAG_AUDIT_PATH", str(audit_path))
        mock_result = self._make_result(returncode=0)

        with patch("ghdag.llm.engines.call", return_value=mock_result), \
             patch("ghdag.llm.engines.validate_engine_model", return_value="claude-sonnet-4-6"):
            from ghdag.cli import main
            with pytest.raises(SystemExit):
                main(["llm", "hello"])

        assert audit_path.exists()
        r = json.loads(audit_path.read_text().strip())
        assert r["event"] == "llm_call"

    def test_llm_audit_path_flag_overrides_env(self, tmp_path, monkeypatch):
        """AC4: --audit-path takes precedence over env GHDAG_AUDIT_PATH."""
        env_path = tmp_path / "env.jsonl"
        flag_path = tmp_path / "flag.jsonl"
        monkeypatch.setenv("GHDAG_AUDIT_PATH", str(env_path))
        mock_result = self._make_result(returncode=0)

        with patch("ghdag.llm.engines.call", return_value=mock_result), \
             patch("ghdag.llm.engines.validate_engine_model", return_value="claude-sonnet-4-6"):
            from ghdag.cli import main
            with pytest.raises(SystemExit):
                main(["llm", "hello", "--audit-path", str(flag_path)])

        assert flag_path.exists()
        assert not env_path.exists()

    def test_llm_request_id_flag(self, tmp_path):
        """AC-A6: with --request-id, audit record request_id matches that value."""
        audit_path = tmp_path / "audit.jsonl"
        mock_result = self._make_result(returncode=0)

        with patch("ghdag.llm.engines.call", return_value=mock_result), \
             patch("ghdag.llm.engines.validate_engine_model", return_value="claude-sonnet-4-6"):
            from ghdag.cli import main
            with pytest.raises(SystemExit) as exc:
                main([
                    "llm", "hello",
                    "--audit-path", str(audit_path),
                    "--request-id", "ext-123",
                ])
        assert exc.value.code == 0
        r = json.loads(audit_path.read_text().strip())
        assert r["request_id"] == "ext-123"
        assert r["schema_version"] == 3

    def test_llm_timeout_recorded(self, tmp_path):
        """AC5: with --timeout, timeout_sec is recorded."""
        audit_path = tmp_path / "audit.jsonl"
        mock_result = self._make_result(returncode=0)

        with patch("ghdag.llm.engines.call", return_value=mock_result), \
             patch("ghdag.llm.engines.validate_engine_model", return_value="claude-sonnet-4-6"):
            from ghdag.cli import main
            with pytest.raises(SystemExit):
                main(["llm", "hello", "--audit-path", str(audit_path), "--timeout-sec", "120"])

        r = json.loads(audit_path.read_text().strip())
        assert r["timeout_sec"] == 120

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

    def test_llm_no_audit_on_failure(self, tmp_path):
        """AC7: LLM failure → no audit log recorded."""
        audit_path = tmp_path / "audit.jsonl"
        mock_result = self._make_result(returncode=1)

        with patch("ghdag.llm.engines.call", return_value=mock_result), \
             patch("ghdag.llm.engines.validate_engine_model", return_value="claude-sonnet-4-6"):
            from ghdag.cli import main
            with pytest.raises(SystemExit) as exc:
                main(["llm", "hello", "--audit-path", str(audit_path)])

        assert exc.value.code == 1
        assert not audit_path.exists()

    def test_llm_no_audit_when_path_unset(self, tmp_path, monkeypatch):
        """AC9: no audit-path and no env → no audit log (backward compatible)."""
        monkeypatch.delenv("GHDAG_AUDIT_PATH", raising=False)
        mock_result = self._make_result(returncode=0)

        with patch("ghdag.llm.engines.call", return_value=mock_result), \
             patch("ghdag.llm.engines.validate_engine_model", return_value="claude-sonnet-4-6"):
            from ghdag.cli import main
            with pytest.raises(SystemExit) as exc:
                main(["llm", "hello"])

        assert exc.value.code == 0


def test_permission_preset_is_forwarded():
    from ghdag.core.capabilities import TEXT_ONLY
    from ghdag.llm.engines import LLMResult

    captured: dict = {}

    def capture_call(*_args, **kwargs):
        captured.update(kwargs)
        return LLMResult(stdout="ok", stderr="", returncode=0)

    from ghdag.cli import main

    main(["llm", "--permission", "text_only", "--list-engines"])
    with patch("ghdag.llm.engines.call", side_effect=capture_call):
        with patch("ghdag.llm.engines.validate_engine_model", return_value="claude-sonnet-4-6"):
            with pytest.raises(SystemExit) as exc:
                main(["llm", "hello", "--permission", "text_only"])
            assert exc.value.code == 0
    assert captured["capabilities"] == TEXT_ONLY


def test_removed_permission_flags_are_rejected():
    from ghdag.cli import main

    for flag in (
        ["--capabilities-preset", "text_only"],
        ["--permission-mode", "plan"],
        ["--dangerously-skip-permissions"],
    ):
        with pytest.raises(SystemExit) as exc:
            main(["llm", "hello", *flag])
        assert exc.value.code == 2


class TestCLI:
    def test_llm_list_engines(self):
        """ghdag llm --list-engines"""
        import contextlib
        from io import StringIO

        from ghdag.cli import main

        buf = StringIO()
        with contextlib.redirect_stdout(buf):
            main(["llm", "--list-engines"])
        output = buf.getvalue()
        assert "claude" in output
        assert "gemini" in output

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
    def test_llm_json_envelope_on_success(self, capsys, monkeypatch):
        monkeypatch.delenv("GHDAG_AUDIT_PATH", raising=False)
        monkeypatch.setattr("ghdag.config.env.ghdag_audit_path", lambda: None)
        from ghdag.llm.engines import LLMResult

        mock_result = LLMResult(stdout="ok\n", stderr="", returncode=0, latency_ms=12.0)
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
        assert payload["stdout"] == "ok\n"
        assert payload["failure_class"] is None

    def test_llm_prompt_required_on_tty_exits_2(self, capsys, monkeypatch):
        monkeypatch.setattr("sys.stdin", type("Tty", (), {"isatty": lambda self: True})())
        from ghdag.cli import main
        with pytest.raises(SystemExit) as exc:
            main(["llm"])
        assert exc.value.code == 2
        assert capsys.readouterr().err.splitlines()[0].startswith("error[LLM_PROMPT_REQUIRED]:")
