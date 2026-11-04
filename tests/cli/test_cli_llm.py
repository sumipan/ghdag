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
                main(["llm", "hello", "--audit-path", str(audit_path), "--timeout", "120"])

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
class TestLLMCapabilitiesArgs:
    @staticmethod
    def _make_result(returncode=0):
        from ghdag.llm.engines import LLMResult
        return LLMResult(stdout="ok", stderr="", returncode=returncode)

    def test_llm_capabilities_preset_text_only(self):
        """--capabilities-preset text_only → TEXT_ONLY capabilities passed to call()"""
        from ghdag.llm.capabilities import TEXT_ONLY
        mock_result = self._make_result()
        captured: dict = {}

        def capture_call(*_args, **kwargs):
            captured.update(kwargs)
            return mock_result

        with patch("ghdag.llm.engines.call", side_effect=capture_call):
            from ghdag.cli import main
            with pytest.raises(SystemExit):
                main(["llm", "hello", "--capabilities-preset", "text_only"])
        assert captured["capabilities"] == TEXT_ONLY

    def test_llm_permission_mode_plan(self):
        """--permission-mode plan → LLMCapabilities(permission_mode='plan') passed to call()"""
        mock_result = self._make_result()
        captured: dict = {}

        def capture_call(*_args, **kwargs):
            captured.update(kwargs)
            return mock_result

        with patch("ghdag.llm.engines.call", side_effect=capture_call):
            from ghdag.cli import main
            with pytest.raises(SystemExit):
                main(["llm", "hello", "--permission-mode", "plan"])
        assert captured["capabilities"].permission_mode == "plan"

    def test_llm_dangerously_skip_permissions_flag(self):
        """--dangerously-skip-permissions → dangerously_skip_permissions=True as before"""
        mock_result = self._make_result()
        captured: dict = {}

        def capture_call(*_args, **kwargs):
            captured.update(kwargs)
            return mock_result

        with patch("ghdag.llm.engines.call", side_effect=capture_call):
            from ghdag.cli import main
            with pytest.raises(SystemExit):
                main(["llm", "hello", "--dangerously-skip-permissions"])
        assert captured["dangerously_skip_permissions"] is True

    def test_llm_invalid_capabilities_preset_exits_2(self, capsys):
        """--capabilities-preset invalid → exit via argparse error"""
        from ghdag.cli import main
        with pytest.raises(SystemExit) as exc:
            main(["llm", "hello", "--capabilities-preset", "invalid_preset"])
        assert exc.value.code == 2

    def test_permission_mode_invalid_exits_2(self, capsys):
        """--permission-mode invalid → exit via argparse error"""
        from ghdag.cli import main
        with pytest.raises(SystemExit) as exc:
            main(["llm", "hello", "--permission-mode", "invalid_mode"])
        assert exc.value.code == 2

    def test_llm_preset_and_permission_mode_override(self):
        """--capabilities-preset text_only + --permission-mode plan → override permission_mode on preset"""
        from ghdag.llm.capabilities import TEXT_ONLY
        mock_result = self._make_result()
        captured: dict = {}

        def capture_call(*_args, **kwargs):
            captured.update(kwargs)
            return mock_result

        with patch("ghdag.llm.engines.call", side_effect=capture_call):
            from ghdag.cli import main
            with pytest.raises(SystemExit):
                main(["llm", "hello", "--capabilities-preset", "text_only", "--permission-mode", "plan"])
        caps = captured["capabilities"]
        assert caps.permission_mode == "plan"
        assert caps.disallowed_tools == TEXT_ONLY.disallowed_tools
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

    def test_llm_list_models_unknown_engine(self):
        """ghdag llm --list-models --engine unknown → exit 1"""
        from ghdag.cli import main

        with pytest.raises(SystemExit) as exc_info:
            main(["llm", "--list-models", "--engine", "unknown"])
        assert exc_info.value.code == 1
