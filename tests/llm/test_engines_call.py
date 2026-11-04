"""Tests for ghdag.llm — one-shot LLM call interface"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from ghdag.llm import (
    DANGEROUS_FULL_ACCESS,
    ENGINE_DEFAULTS,
    ENGINE_SPECS,
    JSON_ONLY,
    TEXT_ONLY,
    WEB_RESEARCH,
    EngineModelError,
    LLMCapabilities,
    build_llm_cmd,
    call,
)


@pytest.fixture(autouse=True)
def _stall_guard_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep cursor calls on the plain ``subprocess.run`` path these tests mock."""
    monkeypatch.setenv("GHDAG_ENGINE_STALL_GUARD", "0")


def _last_subprocess_cmd(mock_run: MagicMock) -> list:
    assert mock_run.mock_calls, "subprocess.run was not invoked"
    return list(mock_run.mock_calls[-1].args[0])


def _last_subprocess_kwargs(mock_run: MagicMock) -> dict:
    assert mock_run.mock_calls, "subprocess.run was not invoked"
    return dict(mock_run.mock_calls[-1].kwargs)
class TestValidateCapabilitiesForEngine:
    @patch("ghdag.llm.engines.subprocess.run")
    def test_gemini_with_disallowed_tools_raises(self, mock_run):
        with pytest.raises(NotImplementedError, match="disallowed_tools"):
            call("hello", engine="gemini", capabilities=TEXT_ONLY)

    @patch("ghdag.llm.engines.subprocess.run")
    def test_gemini_with_permission_mode_raises(self, mock_run):
        with pytest.raises(NotImplementedError, match="permission_mode"):
            call("hello", engine="gemini", capabilities=DANGEROUS_FULL_ACCESS)

    @patch("ghdag.llm.engines.subprocess.run")
    def test_cursor_with_web_research_ok(self, mock_run):
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
        result = call("hello", engine="cursor", capabilities=WEB_RESEARCH)
        assert result.ok
        cmd = _last_subprocess_cmd(mock_run)
        assert "--search" not in cmd
        assert "--allowed-tools" not in cmd

    @patch("ghdag.llm.engines.subprocess.run")
    def test_cursor_with_allowed_tools_raises(self, mock_run):
        with pytest.raises(NotImplementedError):
            call(
                "hello",
                engine="cursor",
                capabilities=LLMCapabilities(allowed_tools=("Read",)),
            )

    @patch("ghdag.llm.engines.subprocess.run")
    def test_cursor_with_text_only_ok(self, mock_run):
        """cursor engine accepts TEXT_ONLY (with disallowed_tools)"""
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
        result = call("hello", engine="cursor", capabilities=TEXT_ONLY)
        assert result.ok

    @patch("ghdag.llm.engines.subprocess.run")
    def test_claude_with_text_only_ok(self, mock_run):
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
        result = call("hello", engine="claude", capabilities=TEXT_ONLY)
        assert result.ok

    @patch("ghdag.llm.engines.subprocess.run")
    def test_gemini_with_stream_raises(self, mock_run):
        with pytest.raises(NotImplementedError, match="stream"):
            call("hello", engine="gemini", capabilities=LLMCapabilities(stream=True))

    @patch("ghdag.llm.engines.subprocess.run")
    def test_cursor_with_stream_builds_flags(self, mock_run):
        """cursor + stream=True does not raise NotImplementedError; attaches stream-json."""
        mock_run.return_value = MagicMock(
            stdout=(
                '{"type":"result","subtype":"success","is_error":false,'
                '"result":"pong","session_id":"sess-1"}\n'
            ),
            stderr="",
            returncode=0,
        )
        result = call("hello", engine="cursor", capabilities=LLMCapabilities(stream=True))
        assert result.ok
        assert result.stdout == "pong"
        cmd = _last_subprocess_cmd(mock_run)
        assert cmd[cmd.index("--output-format") + 1] == "stream-json"
        assert "--stream-partial-output" in cmd


# ---------------------------------------------------------------------------
# call() — mock subprocess
# ---------------------------------------------------------------------------

class TestCall:
    @patch("ghdag.llm.engines.subprocess.run")
    def test_call_default_model(self, mock_run: MagicMock):
        """model=None → call with the default model"""
        mock_run.return_value = MagicMock(
            stdout="response", stderr="", returncode=0,
        )
        result = call("hello", engine="claude")
        assert result.ok
        assert result.stdout == "response"
        cmd = _last_subprocess_cmd(mock_run)
        assert "--model" in cmd
        assert ENGINE_DEFAULTS["claude"] in cmd

    @patch("ghdag.llm.engines.subprocess.run")
    def test_call_explicit_model(self, mock_run: MagicMock):
        """Explicit model selection"""
        mock_run.return_value = MagicMock(
            stdout="ok", stderr="", returncode=0,
        )
        result = call("hello", engine="claude", model="claude-opus-4-6")
        assert result.ok
        cmd = _last_subprocess_cmd(mock_run)
        assert "claude-opus-4-6" in cmd

    def test_call_invalid_model(self):
        """Disallowed model → EngineModelError (before subprocess call)"""
        with pytest.raises(EngineModelError):
            call("hello", engine="claude", model="bad-model")

    @patch("ghdag.llm.engines.subprocess.run")
    def test_call_with_stdin(self, mock_run: MagicMock):
        """stdin_text is joined after the prompt on stdin (nexus #3805)"""
        mock_run.return_value = MagicMock(
            stdout="ok", stderr="", returncode=0,
        )
        call("hello", engine="claude", stdin_text="input data")
        kwargs = _last_subprocess_kwargs(mock_run)
        assert kwargs["input"] == "hello\n\ninput data"

    @patch("ghdag.llm.engines.subprocess.run")
    def test_call_with_timeout(self, mock_run: MagicMock):
        """timeout is passed through"""
        mock_run.return_value = MagicMock(
            stdout="ok", stderr="", returncode=0,
        )
        call("hello", engine="claude", timeout=30)
        kwargs = _last_subprocess_kwargs(mock_run)
        assert kwargs["timeout"] == 30

    @patch("ghdag.llm.engines.subprocess.run")
    def test_call_action_none_default_no_dangerously(self, mock_run: MagicMock):
        """Default capabilities do not attach --dangerously-skip-permissions"""
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
        call("test prompt", engine="claude")
        cmd = _last_subprocess_cmd(mock_run)
        assert "--dangerously-skip-permissions" not in cmd

    @patch("ghdag.llm.engines.subprocess.run")
    def test_call_dangerous_full_access_bypasspermissions(self, mock_run: MagicMock):
        """DANGEROUS_FULL_ACCESS attaches --permission-mode bypassPermissions"""
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
        call("test prompt", engine="claude", capabilities=DANGEROUS_FULL_ACCESS)
        cmd = _last_subprocess_cmd(mock_run)
        assert "--permission-mode" in cmd
        perm_idx = cmd.index("--permission-mode")
        assert cmd[perm_idx + 1] == "bypassPermissions"


class TestCallCapabilities:
    @patch("ghdag.llm.engines.subprocess.run")
    def test_call_default_uses_text_only(self, mock_run):
        """call() defaults to TEXT_ONLY"""
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
        call("hello")
        cmd = _last_subprocess_cmd(mock_run)
        assert "--permission-mode" in cmd
        assert "--disallowed-tools" in cmd

    @patch("ghdag.llm.engines.subprocess.run")
    def test_call_dangerously_skip_permissions_true_claude(self, mock_run: MagicMock):
        """dangerously_skip_permissions=True + engine=claude → flag on command"""
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
        call("hello", engine="claude", dangerously_skip_permissions=True)
        cmd = _last_subprocess_cmd(mock_run)
        assert "--dangerously-skip-permissions" in cmd

    @patch("ghdag.llm.engines.subprocess.run")
    def test_call_dangerously_skip_permissions_false_no_flag(self, mock_run: MagicMock):
        """dangerously_skip_permissions=False (default) → no flag"""
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
        call("hello", engine="claude", dangerously_skip_permissions=False)
        cmd = _last_subprocess_cmd(mock_run)
        assert "--dangerously-skip-permissions" not in cmd

    @patch("ghdag.llm.engines.subprocess.run")
    def test_call_dangerously_skip_permissions_true_gemini_ignored(self, mock_run: MagicMock):
        """dangerously_skip_permissions=True + engine=gemini → Claude-specific flag ignored"""
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
        call("hello", engine="gemini", capabilities=LLMCapabilities(), dangerously_skip_permissions=True)
        cmd = _last_subprocess_cmd(mock_run)
        assert "--dangerously-skip-permissions" not in cmd

    def test_call_action_raises_type_error(self):
        """Legacy action arg → TypeError"""
        with pytest.raises(TypeError):
            call("hello", action="skill")  # type: ignore

    @patch("ghdag.llm.engines.subprocess.run")
    def test_call_action_skill_cursor_force(self, mock_run: MagicMock):
        """cursor with dangerously_skip_permissions=True attaches --force"""
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
        call("test prompt", engine="cursor", dangerously_skip_permissions=True)
        cmd = _last_subprocess_cmd(mock_run)
        assert "--force" in cmd

    @patch("ghdag.llm.engines.subprocess.run")
    def test_call_with_json_only(self, mock_run):
        """JSON_ONLY capabilities are passed correctly"""
        mock_run.return_value = MagicMock(stdout='{"k":"v"}', stderr="", returncode=0)
        result = call("hello", capabilities=JSON_ONLY)
        assert result.ok
        cmd = _last_subprocess_cmd(mock_run)
        assert "--output-format" in cmd

    @patch("ghdag.llm.engines.subprocess.run")
    def test_call_with_custom_capabilities(self, mock_run):
        """Custom capabilities"""
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
        caps = LLMCapabilities(permission_mode="plan")
        call("hello", capabilities=caps)
        cmd = _last_subprocess_cmd(mock_run)
        assert "--permission-mode" in cmd
        perm_idx = cmd.index("--permission-mode")
        assert cmd[perm_idx + 1] == "plan"
class TestEngineIsolation:
    """Engine isolation opt-in — nexus #3174 (conditionalizes always-on isolation from #3044)."""

    def test_claude_cmd_omits_disable_slash_commands_by_default(self) -> None:
        """By default do not attach --disable-slash-commands (skills remain usable)."""
        cmd = build_llm_cmd("claude", "claude-sonnet-4-6", "hello")
        assert "--disable-slash-commands" not in cmd
        assert "--disable-slash-commands" not in ENGINE_SPECS["claude"].extra_args

    def test_claude_cmd_includes_disable_slash_commands_when_isolation_true(
        self,
    ) -> None:
        cmd = build_llm_cmd("claude", "claude-sonnet-4-6", "hello", isolation=True)
        assert "--disable-slash-commands" in cmd

    @patch("ghdag.llm.engines.subprocess.run")
    def test_codex_call_sets_codex_home_when_isolation_and_auth(
        self, mock_run: MagicMock, tmp_path, monkeypatch
    ) -> None:
        """Inject CODEX_HOME only when isolation=True and auth.json exists."""
        import ghdag.llm.engines as engines_mod

        monkeypatch.setattr(engines_mod, "_CODEX_DAG_HOME", str(tmp_path) + "/")
        (tmp_path / "auth.json").write_text("{}", encoding="utf-8")
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
        call("hello", engine="codex", capabilities=TEXT_ONLY, isolation=True)
        assert mock_run.called
        kwargs = _last_subprocess_kwargs(mock_run)
        assert "env" in kwargs
        assert kwargs["env"]["CODEX_HOME"] == str(tmp_path) + "/"

    @patch("ghdag.llm.engines.subprocess.run")
    def test_codex_isolation_skipped_without_auth(
        self, mock_run: MagicMock, tmp_path, monkeypatch, capsys
    ) -> None:
        """With isolation=True but no auth.json, do not inject CODEX_HOME; warn."""
        import ghdag.llm.engines as engines_mod

        monkeypatch.setattr(engines_mod, "_CODEX_DAG_HOME", str(tmp_path) + "/")
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
        call("hello", engine="codex", capabilities=TEXT_ONLY, isolation=True)
        kwargs = _last_subprocess_kwargs(mock_run)
        assert "env" not in kwargs or "CODEX_HOME" not in kwargs.get("env", {})
        err = capsys.readouterr().err
        assert "codex isolation skipped" in err

    @patch("ghdag.llm.engines.subprocess.run")
    def test_codex_call_no_codex_home_by_default(
        self, mock_run: MagicMock, monkeypatch
    ) -> None:
        """With isolation=None and GHDAG_ENGINE_ISOLATION unset, do not inject CODEX_HOME."""
        monkeypatch.delenv("GHDAG_ENGINE_ISOLATION", raising=False)
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
        call("hello", engine="codex", capabilities=TEXT_ONLY, isolation=None)
        kwargs = _last_subprocess_kwargs(mock_run)
        assert "env" not in kwargs or "CODEX_HOME" not in kwargs.get("env", {})

    @patch("ghdag.llm.engines.subprocess.run")
    def test_cursor_call_unaffected_by_isolation_meta_capability(
        self, mock_run: MagicMock
    ) -> None:
        """isolation is not an LLMCapabilities attr, so cursor call stays intact."""
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
        result = call("hello", engine="cursor", capabilities=TEXT_ONLY)
        assert result.ok
