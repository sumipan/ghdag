"""Tests for ghdag.llm — one-shot LLM call interface"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from ghdag.core.capabilities import TEXT_ONLY
from ghdag.llm import DANGEROUS_FULL_ACCESS, Permission, call
from ghdag.llm.engines import EngineModelError, build_llm_cmd


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
            call("hello", permission=TEXT_ONLY, route=[("gemini", None)])

    @patch("ghdag.llm.engines.subprocess.run")
    def test_gemini_with_permission_mode_raises(self, mock_run):
        with pytest.raises(NotImplementedError, match="permission_mode"):
            call("hello", permission=DANGEROUS_FULL_ACCESS, route=[("gemini", None)])

    @pytest.mark.parametrize(
        ("engine", "flag"),
        [("codex", "--dangerously-bypass-approvals-and-sandbox"), ("cursor", "--force")],
    )
    @patch("ghdag.llm.engines.subprocess.run")
    def test_full_access_runs_codex_and_cursor_with_their_bypass_flag(
        self, mock_run, engine, flag
    ):
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
        call("hello", permission=DANGEROUS_FULL_ACCESS, route=[(engine, None)])
        assert flag in _last_subprocess_cmd(mock_run)

    @patch("ghdag.llm.engines.subprocess.run")
    def test_gemini_with_stream_raises(self, mock_run):
        with pytest.raises(NotImplementedError, match="stream"):
            call("hello", permission=Permission(stream=True), route=[("gemini", None)])

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
        result = call("hello", permission=Permission(stream=True), route=[("cursor", None)])
        assert result.ok
        assert result.stdout == "pong"
        cmd = _last_subprocess_cmd(mock_run)
        assert cmd[cmd.index("--output-format") + 1] == "stream-json"
        assert "--stream-partial-output" in cmd


# ---------------------------------------------------------------------------
# call(route=[("claude", None)]) — mock subprocess
# ---------------------------------------------------------------------------


class TestCall:

    def test_call_invalid_model(self):
        """Disallowed model → EngineModelError (before subprocess call)"""
        with pytest.raises(EngineModelError):
            call("hello", route=[("claude", "bad-model")])


class TestCallCapabilities:

    def test_call_action_raises_type_error(self):
        """Legacy action arg → TypeError"""
        with pytest.raises(TypeError):
            call("hello", action="skill", route=[("claude", None)])  # type: ignore

    def test_build_llm_cmd_cursor_dangerous_full_access_force(self):
        """DANGEROUS_FULL_ACCESS on cursor argv adds --force (build_llm_cmd path)."""
        cmd = build_llm_cmd("cursor", "auto", "test", permission=DANGEROUS_FULL_ACCESS)
        assert "--force" in cmd


class TestEngineIsolation:
    """Engine isolation opt-in — nexus #3174 (conditionalizes always-on isolation from #3044)."""

    @patch("ghdag.llm.engines.subprocess.run")
    def test_codex_call_sets_codex_home_when_isolation_and_auth(
        self, mock_run: MagicMock, tmp_path, monkeypatch
    ) -> None:
        """Inject CODEX_HOME only when isolation=True and auth.json exists."""
        import ghdag.llm.engines as engines_mod

        monkeypatch.setattr(engines_mod, "_CODEX_DAG_HOME", str(tmp_path) + "/")
        (tmp_path / "auth.json").write_text("{}", encoding="utf-8")
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
        call("hello", permission=TEXT_ONLY, isolation=True, route=[("codex", None)])
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
        call("hello", permission=TEXT_ONLY, isolation=True, route=[("codex", None)])
        kwargs = _last_subprocess_kwargs(mock_run)
        assert "env" not in kwargs or "CODEX_HOME" not in kwargs.get("env", {})
        err = capsys.readouterr().err
        assert "codex isolation skipped" in err
