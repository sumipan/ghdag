"""Tests for LLMResult.latency_ms and call(route=[("claude", None)]) latency measurement — Issue #1275."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from ghdag.core.capabilities import Permission
from ghdag.core.command import render_exec_command
from ghdag.core.engine_spec import ENGINE_SPECS
from ghdag.llm.engines import LLMResult, build_llm_cmd, call


@pytest.fixture(autouse=True)
def _stall_guard_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep cursor calls on the plain ``subprocess.run`` path these tests mock."""
    monkeypatch.setenv("GHDAG_ENGINE_STALL_GUARD", "0")


class TestLLMResultLatencyMs:
    def test_llm_result_latency_ms_default(self):
        """Default latency_ms is 0.0 when unspecified."""
        r = LLMResult(body="out", stdout="out", stderr="", returncode=0)
        assert r.latency_ms == 0.0


class TestBuildLlmCmdCodex:
    def test_build_llm_cmd_codex(self):
        """build_llm_cmd("codex") assembles subcommand + flags correctly."""
        cmd = build_llm_cmd("codex", "gpt-5.6-terra", "test")
        assert cmd == ["codex", "exec", "-", "--model", "gpt-5.6-terra", "--json", "--skip-git-repo-check"]

    def test_build_llm_cmd_codex_danger_flag(self):
        """dangerously_skip_permissions=True adds the bypass flag."""
        cmd = build_llm_cmd("codex", "gpt-5.6-terra", "test", dangerously_skip_permissions=True)
        assert "--dangerously-bypass-approvals-and-sandbox" in cmd

    def test_build_llm_cmd_existing_engines_unaffected(self):
        """Existing engines (claude/gemini/cursor/shell) keep argv unchanged because subcommand=()."""
        for engine, cli in [("claude", "claude"), ("gemini", "gemini"), ("shell", "bash")]:
            cmd = build_llm_cmd(engine, ENGINE_SPECS[engine].default_model or "auto", "test")
            assert cmd[0] == cli
            # empty subcommand: options/args follow cli directly (no extra elements like exec)
            assert "exec" not in cmd


_STDIN_ENGINE_MODELS = {
    "claude": "claude-opus-4-6",
    "cursor": "composer-2",
    "codex": "gpt-5.6-terra",
}


class TestCallPromptViaStdin:
    """nexus #3805: every STDIN engine gets prompt (+ stdin_text) via subprocess stdin."""

    @staticmethod
    def _run(engine: str, prompt: str, stdin_text: str | None) -> tuple[list[str], dict]:
        captured: dict = {}

        def _side_effect(*args, **kwargs):
            captured["cmd"] = args[0]
            captured.update(kwargs)
            return MagicMock(stdout="ok", stderr="", returncode=0)

        with patch("ghdag.llm.engines.subprocess.run", side_effect=_side_effect):
            call(
                prompt,
                route=[(engine, _STDIN_ENGINE_MODELS[engine])],
                stdin_text=stdin_text,
                permission=Permission(),
            )
        return captured["cmd"], captured

    def test_claude_prompt_in_stdin_not_argv(self):
        cmd, kwargs = self._run("claude", "hello", None)
        assert kwargs["input"] == "hello"
        assert "hello" not in cmd

    def test_cursor_prompt_in_stdin_not_argv(self):
        cmd, kwargs = self._run("cursor", "hello", None)
        assert kwargs["input"] == "hello"
        assert "hello" not in cmd

    def test_codex_prompt_in_stdin_not_argv(self):
        cmd, kwargs = self._run("codex", "hello", None)
        assert kwargs["input"] == "hello"
        assert "hello" not in cmd

    @pytest.mark.parametrize("engine", sorted(_STDIN_ENGINE_MODELS))
    @pytest.mark.parametrize(
        ("prompt", "stdin_text", "expected"),
        [
            ("hello", None, "hello"),
            ("", "data", "data"),
            ("hello", "data", "hello\n\ndata"),
        ],
    )
    def test_stdin_join_rule(self, engine, prompt, stdin_text, expected):
        _, kwargs = self._run(engine, prompt, stdin_text)
        assert kwargs["input"] == expected


class TestCallResumeSessionId:
    def test_call_with_resume_unsupported_engine_raises(self):
        """gemini + resume_session_id raises NotImplementedError."""
        with pytest.raises(NotImplementedError, match="resume"):
            call("hello",
                permission=Permission(),
                resume_session_id="sess-1", route=[("gemini", None)])

    @patch("ghdag.llm.engines.get_output_adapter")
    @patch("ghdag.llm.engines.subprocess.run")
    def test_call_sets_session_id_from_adapter(self, mock_run: MagicMock, mock_get_adapter: MagicMock):
        """On success, adapter-extracted value is stored in LLMResult.session_id."""
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
        adapter = MagicMock()
        adapter.extract_session_id.return_value = "sess-abc"
        mock_get_adapter.return_value = adapter

        result = call("hello", route=[("claude", None)])

        assert result.session_id == "sess-abc"


class TestCodexUnsupportedCapabilities:

    def test_codex_unsupported_output_format_json(self):
        """output_format="json" raises NotImplementedError."""
        with pytest.raises(NotImplementedError, match="output_format"):
            call("test", permission=Permission(output_format="json"), route=[("codex", None)])


class TestExtraArgsDedupe:
    """Deduplicate flags between EngineSpec.extra_args and _PERMISSION_FLAG_BUILDERS.

    codex CLI rejects duplicate `--json` with `error: the argument '--json' cannot be used
    multiple times` (exit 2), so duplicates are an immediate failure before the model call.
    """

    def test_dedupe_removes_flag_with_value_as_a_pair(self):
        """Flags with values are dropped as flag+value pairs."""
        from ghdag.core.command import _dedupe_extra_args
        assert _dedupe_extra_args(
            ("--output-format", "json"), ["--permission-mode", "default", "--output-format", "json"]
        ) == []

    def test_dedupe_noop_when_builder_emits_nothing(self):
        """When perm_flags is empty, extra_args is unchanged."""
        from ghdag.core.command import _dedupe_extra_args
        assert _dedupe_extra_args(("-o", "pipefail"), []) == ["-o", "pipefail"]

    def test_cursor_json_only_overrides_stream_default(self):
        """cursor + json_only returns to single JSON and drops stream-only flags."""
        from ghdag.core.capabilities import PERMISSIONS
        cmd = render_exec_command(
            ENGINE_SPECS["cursor"], order_path="jobs/order.md",
            model="auto", permission=PERMISSIONS["json_only"],
        )
        assert cmd.split().count("--output-format") == 1
        assert "--output-format json" in cmd
        assert "--output-format stream-json" not in cmd
        assert "--stream-partial-output" not in cmd


class TestSandboxCapability:
    """sandbox capability and READONLY_OBSERVE preset (nexus Issue #2640)."""

    def test_readonly_observe_preset(self):
        """PERMISSIONS['readonly_observe'] has sandbox=readonly and edit-tool denies."""
        from ghdag.core.capabilities import PERMISSIONS

        caps = PERMISSIONS["readonly_observe"]
        assert caps.sandbox == "readonly"
        assert caps.disallowed_tools == ("Edit", "NotebookEdit")

    def test_non_plan_presets_keep_write_denied(self):
        """TEXT_ONLY / JSON_ONLY / WEB_RESEARCH still deny Write."""
        from ghdag.core.capabilities import JSON_ONLY, TEXT_ONLY, WEB_RESEARCH

        for caps in (TEXT_ONLY, JSON_ONLY, WEB_RESEARCH):
            assert "Write" in caps.disallowed_tools

    def test_claude_sandbox_readonly_uses_permission_mode_plan(self):
        """claude + sandbox=readonly → --permission-mode plan."""
        cmd = build_llm_cmd(
            "claude",
            "claude-sonnet-4-6",
            "p",
            permission=Permission(sandbox="readonly"),
        )
        assert "--permission-mode" in cmd
        assert cmd[cmd.index("--permission-mode") + 1] == "plan"

    def test_codex_sandbox_readonly_uses_s_read_only(self):
        """codex + sandbox=readonly → -s read-only, no bypass flag."""
        cmd = build_llm_cmd(
            "codex",
            "gpt-5.6-terra",
            "p",
            permission=Permission(sandbox="readonly"),
        )
        assert "-s" in cmd
        assert cmd[cmd.index("-s") + 1] == "read-only"
        assert "--dangerously-bypass-approvals-and-sandbox" not in cmd

    def test_cursor_sandbox_readonly_uses_sandbox_enabled(self):
        """cursor + sandbox=readonly → --sandbox enabled, no --force."""
        cmd = build_llm_cmd(
            "cursor",
            "auto",
            "p",
            permission=Permission(sandbox="readonly"),
        )
        assert "--sandbox" in cmd
        assert cmd[cmd.index("--sandbox") + 1] == "enabled"
        assert "--force" not in cmd

    def test_claude_sandbox_readonly_conflicts_with_permission_mode(self):
        """claude + sandbox=readonly + explicit permission_mode → ValueError."""
        with pytest.raises(ValueError, match="sandbox='readonly' conflicts"):
            build_llm_cmd(
                "claude",
                "claude-sonnet-4-6",
                "p",
                permission=Permission(
                    sandbox="readonly",
                    permission_mode="bypassPermissions",
                ),
            )

    def test_codex_sandbox_readonly_conflicts_with_bypass(self):
        """codex + sandbox=readonly + dangerously_skip_permissions → ValueError."""
        with pytest.raises(ValueError, match="sandbox='readonly' conflicts"):
            build_llm_cmd(
                "codex",
                "gpt-5.6-terra",
                "p",
                permission=Permission(sandbox="readonly"),
                dangerously_skip_permissions=True,
            )

    def test_cursor_sandbox_readonly_conflicts_with_force(self):
        """cursor + sandbox=readonly + dangerously_skip_permissions → ValueError."""
        with pytest.raises(ValueError, match="sandbox='readonly' conflicts"):
            build_llm_cmd(
                "cursor",
                "auto",
                "p",
                permission=Permission(sandbox="readonly"),
                dangerously_skip_permissions=True,
            )

    def test_cursor_disallowed_tools_in_ignored_capabilities(self):
        """cursor disallowed_tools is declared noop via _IGNORED_PERMISSION_ATTRS."""
        from ghdag.llm.engines import _IGNORED_PERMISSION_ATTRS, _UNSUPPORTED_PERMISSION_ATTRS

        assert "disallowed_tools" in _IGNORED_PERMISSION_ATTRS["cursor"]
        # After set difference, excluded from validation (skipped via ignored even if unsupported)
        unsupported = _UNSUPPORTED_PERMISSION_ATTRS.get("cursor", set())
        ignored = _IGNORED_PERMISSION_ATTRS.get("cursor", set())
        assert "disallowed_tools" not in (unsupported - ignored)


class TestWebSearchCapability:

    def test_gemini_web_search_raises_before_subprocess(self):
        with pytest.raises(NotImplementedError, match="web_search"):
            call("hello", permission=Permission(web_search=True), route=[("gemini", None)])

    def test_shell_web_search_raises_before_subprocess(self):
        with pytest.raises(NotImplementedError, match="web_search"):
            call("hello", permission=Permission(web_search=True), route=[("shell", "bash")])


class TestCallLaunchFailure:
    """Launch failures of the engine binary become an LLMResult, not an exception."""

    @patch("ghdag.llm.engines.subprocess.run")
    def test_missing_cwd_still_raises(self, mock_run: MagicMock):
        mock_run.side_effect = FileNotFoundError(2, "No such file or directory", "/no/such/dir")
        with pytest.raises(FileNotFoundError):
            call("hello", cwd="/no/such/dir", route=[("cursor", None)])


class TestCallTimeout:
    """subprocess.TimeoutExpired becomes an LLMResult tagged TIMEOUT (sumipan/nexus#4304)."""

    @patch("ghdag.llm.engines.subprocess.run")
    def test_timeout_keeps_partial_bytes_output(self, mock_run: MagicMock):
        """subprocess.run joins the captured chunks as bytes even in text mode."""
        import subprocess

        mock_run.side_effect = subprocess.TimeoutExpired(
            ["claude"], 10, output=b'{"type":"assistant"}\n', stderr=b"warn"
        )
        result = call("hello", timeout=10, route=[("claude", None)])
        assert result.stdout == '{"type":"assistant"}\n'
        assert result.stderr == "warn\nTIMEOUT: claude timed out after 10s\n"

    def test_llm_result_failure_class_defaults_to_none(self):
        r = LLMResult(body="", stdout="", stderr="", returncode=1)
        assert r.failure_class is None
        assert r.timed_out is False

    @patch("ghdag.llm.engines.subprocess.run")
    def test_call_reports_timeout(self, mock_run: MagicMock):
        import subprocess

        from ghdag.core.models.metrics import FailureClass

        mock_run.side_effect = subprocess.TimeoutExpired(["cursor-agent"], 5)
        result = call("hello", timeout=5, route=[("cursor", None)])
        assert result.returncode == 124
        assert result.failure_class == FailureClass.TIMEOUT
        assert result.attempts == 1
