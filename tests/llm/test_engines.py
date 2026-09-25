"""Tests for LLMResult.latency_ms and call() latency measurement — Issue #1275."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from ghdag.llm.capabilities import LLMCapabilities
from ghdag.llm.engines import LLMResult, build_llm_cmd, call
from ghdag.llm.spec import ENGINE_SPECS, render_exec_command


class TestLLMResultLatencyMs:
    def test_llm_result_latency_ms_default(self):
        """Default latency_ms is 0.0 when unspecified."""
        r = LLMResult(stdout="out", stderr="", returncode=0)
        assert r.latency_ms == 0.0

    @patch("ghdag.llm.engines.subprocess.run")
    def test_call_measures_latency(self, mock_run: MagicMock):
        """call() returns latency_ms > 0."""
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
        result = call("hello", engine="claude")
        assert result.latency_ms > 0


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

    def test_build_llm_cmd_claude_includes_resume(self):
        """With resume_session_id, claude gets --resume."""
        cmd = build_llm_cmd(
            "claude",
            "claude-sonnet-4-6",
            "test",
            resume_session_id="sess-1",
        )
        assert "--resume" in cmd
        assert "sess-1" in cmd

    def test_build_llm_cmd_codex_switches_to_resume_subcommand(self):
        """With resume_session_id, codex switches to exec resume."""
        cmd = build_llm_cmd(
            "codex",
            "gpt-5.6-terra",
            "test",
            resume_session_id="sess-1",
        )
        assert cmd[:4] == ["codex", "exec", "resume", "sess-1"]
        assert "-" not in cmd[:5]

    def test_render_exec_command_codex(self):
        """render_exec_command builds the correct shell command for codex."""
        spec = ENGINE_SPECS["codex"]
        cmd = render_exec_command(
            spec,
            order_path="jobs/order.md",
            model="gpt-5.6-terra",
        )
        assert cmd == (
            "codex exec - --model 'gpt-5.6-terra' --json --skip-git-repo-check"
            " --dangerously-bypass-approvals-and-sandbox < jobs/order.md"
        )

    def test_render_exec_command_codex_with_capabilities(self):
        """With capabilities set, flags are built via _build_codex_flags."""
        from ghdag.llm.capabilities import LLMCapabilities
        spec = ENGINE_SPECS["codex"]
        cmd = render_exec_command(
            spec,
            order_path="jobs/order.md",
            model="gpt-5.6-terra",
            capabilities=LLMCapabilities(),
        )
        assert "codex exec -" in cmd
        assert "--json" in cmd
        assert "--skip-git-repo-check" in cmd
        # Both extra_args and _build_codex_flags emit these, so duplicates are likely.
        # codex CLI rejects duplicate `--json` with an argument error (exit 2).
        assert cmd.split().count("--json") == 1
        assert cmd.split().count("--skip-git-repo-check") == 1

    def test_render_exec_command_codex_dangerous_full_access(self):
        """DANGEROUS_FULL_ACCESS adds the sandbox bypass flag (nexus#2558 regression).

        render_exec_command calls the builder with dangerously_skip_permissions=False,
        so without reading capabilities.permission_mode the flag is dropped. Then codex
        starts in workspace-write and fails to write outside cwd (e.g. a diary repo).
        """
        from ghdag.llm.capabilities import DANGEROUS_FULL_ACCESS
        spec = ENGINE_SPECS["codex"]
        cmd = render_exec_command(
            spec,
            order_path="jobs/order.md",
            model="gpt-5.6-terra",
            capabilities=DANGEROUS_FULL_ACCESS,
        )
        assert "--dangerously-bypass-approvals-and-sandbox" in cmd
        assert cmd.split().count("--dangerously-bypass-approvals-and-sandbox") == 1
        assert cmd.split().count("--json") == 1

    def test_render_exec_command_codex_default_keeps_sandbox(self):
        """Default capabilities do not add the bypass flag (sandbox stays on)."""
        from ghdag.llm.capabilities import LLMCapabilities
        spec = ENGINE_SPECS["codex"]
        cmd = render_exec_command(
            spec,
            order_path="jobs/order.md",
            model="gpt-5.6-terra",
            capabilities=LLMCapabilities(),
        )
        assert "--dangerously-bypass-approvals-and-sandbox" not in cmd


class TestCallCodexPromptRouting:
    @patch("ghdag.llm.engines.subprocess.run")
    def test_call_codex_prompt_to_stdin(self, mock_run: MagicMock):
        """call(engine="codex", prompt="hello") passes prompt as subprocess.run input."""
        mock_run.return_value = MagicMock(stdout="jsonl", stderr="", returncode=0)
        call("hello", engine="codex", capabilities=LLMCapabilities())
        _, kwargs = mock_run.call_args
        assert kwargs["input"] == "hello"

    @patch("ghdag.llm.engines.subprocess.run")
    def test_call_codex_stdin_text_priority(self, mock_run: MagicMock):
        """stdin_text is joined after the prompt (nexus #3805)."""
        mock_run.return_value = MagicMock(stdout="jsonl", stderr="", returncode=0)
        call("hello", engine="codex", stdin_text="override", capabilities=LLMCapabilities())
        _, kwargs = mock_run.call_args
        assert kwargs["input"] == "hello\n\noverride"

    @patch("ghdag.llm.engines.subprocess.run")
    def test_call_claude_stdin_text_unaffected(self, mock_run: MagicMock):
        """claude is a STDIN engine, so the prompt goes to stdin (nexus #3805)."""
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
        call("hello", engine="claude", stdin_text=None)
        _, kwargs = mock_run.call_args
        assert kwargs["input"] == "hello"


_STDIN_ENGINE_MODELS = {
    "claude": "claude-opus-4-6",
    "cursor": "composer-2",
    "codex": "gpt-5.6-terra",
}


class TestCallPromptViaStdin:
    """nexus #3805: every STDIN engine gets prompt (+ stdin_text) via subprocess stdin."""

    @staticmethod
    def _run(engine: str, prompt: str, stdin_text: str | None) -> tuple[list[str], dict]:
        with patch("ghdag.llm.engines.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
            call(
                prompt,
                engine=engine,
                model=_STDIN_ENGINE_MODELS[engine],
                stdin_text=stdin_text,
                capabilities=LLMCapabilities(),
            )
            args, kwargs = mock_run.call_args
        return args[0], kwargs

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
            call(
                "hello",
                engine="gemini",
                capabilities=LLMCapabilities(),
                resume_session_id="sess-1",
            )

    @patch("ghdag.llm.engines.get_output_adapter")
    @patch("ghdag.llm.engines.subprocess.run")
    def test_call_sets_session_id_from_adapter(self, mock_run: MagicMock, mock_get_adapter: MagicMock):
        """On success, adapter-extracted value is stored in LLMResult.session_id."""
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
        adapter = MagicMock()
        adapter.extract_session_id.return_value = "sess-abc"
        mock_get_adapter.return_value = adapter

        result = call("hello", engine="claude")

        assert result.session_id == "sess-abc"
        adapter.extract_session_id.assert_called_once_with(b"ok", b"")

    @patch("ghdag.llm.engines.subprocess.run")
    def test_call_cursor_extracts_session_id_from_real_json(self, mock_run: MagicMock):
        """Extract session_id from measured cursor JSON stdout."""
        stdout = (
            '{"type":"result","subtype":"success","is_error":false,'
            '"result":"pong",'
            '"session_id":"85105031-11df-48a2-a791-812a0128b4cf",'
            '"usage":{"inputTokens":7144,"outputTokens":73}}'
        )
        mock_run.return_value = MagicMock(stdout=stdout, stderr="", returncode=0)
        result = call("hello", engine="cursor", capabilities=LLMCapabilities())
        assert result.session_id == "85105031-11df-48a2-a791-812a0128b4cf"

    @patch("ghdag.llm.engines.subprocess.run")
    def test_call_text_cursor_extracts_result_from_json(self, mock_run: MagicMock):
        """call_text(cursor) puts JSON result in body and keeps session_id."""
        from ghdag.llm.adapters.cursor import CursorAdapter
        from ghdag.llm.engines import call_text

        stdout = (
            '{"type":"result","subtype":"success","is_error":false,'
            '"result":"pong",'
            '"session_id":"85105031-11df-48a2-a791-812a0128b4cf",'
            '"usage":{"inputTokens":7144,"outputTokens":73}}'
        )
        mock_run.return_value = MagicMock(stdout=stdout, stderr="", returncode=0)
        result = call_text("hello", engine="cursor", capabilities=LLMCapabilities())
        assert result.body == "pong"
        assert result.session_id == "85105031-11df-48a2-a791-812a0128b4cf"
        usage = CursorAdapter().extract_token_usage(stdout.encode("utf-8"), b"")
        assert usage is not None
        assert usage.token_count == 7217


class TestCodexUnsupportedCapabilities:
    @patch("ghdag.llm.engines.subprocess.run")
    def test_codex_stream_accepted(self, mock_run: MagicMock):
        """stream=True is accepted and uses the --json path (#2967)."""
        mock_run.return_value = MagicMock(stdout="{}", stderr="", returncode=0)
        result = call("test", engine="codex", capabilities=LLMCapabilities(stream=True))
        assert result.returncode == 0
        assert "--json" in mock_run.call_args[0][0]

    def test_codex_unsupported_output_format_json(self):
        """output_format="json" raises NotImplementedError."""
        with pytest.raises(NotImplementedError, match="output_format"):
            call("test", engine="codex", capabilities=LLMCapabilities(output_format="json"))

    def test_codex_unsupported_permission_mode(self):
        """Non-default permission_mode raises NotImplementedError."""
        with pytest.raises(NotImplementedError, match="permission_mode"):
            call(
                "test",
                engine="codex",
                capabilities=LLMCapabilities(permission_mode="bypassPermissions"),
            )


class TestCodexIgnoredCapabilities:
    """codex accepts allowed_tools / disallowed_tools as noop (want TEXT_ONLY via codex)."""

    @patch("ghdag.llm.engines.subprocess.run")
    def test_codex_with_text_only_does_not_raise(self, mock_run: MagicMock):
        """Passing TEXT_ONLY (with disallowed_tools) to codex does not raise NotImplementedError."""
        from ghdag.llm.capabilities import TEXT_ONLY
        mock_run.return_value = MagicMock(stdout="jsonl", stderr="", returncode=0)
        result = call("test", engine="codex", capabilities=TEXT_ONLY)
        assert result.returncode == 0

    @patch("ghdag.llm.engines.subprocess.run")
    def test_codex_with_disallowed_tools_does_not_raise(self, mock_run: MagicMock):
        """Explicit disallowed_tools is a noop for codex."""
        mock_run.return_value = MagicMock(stdout="jsonl", stderr="", returncode=0)
        result = call(
            "test",
            engine="codex",
            capabilities=LLMCapabilities(disallowed_tools=("Bash", "Edit")),
        )
        assert result.returncode == 0

    @patch("ghdag.llm.engines.subprocess.run")
    def test_codex_with_allowed_tools_does_not_raise(self, mock_run: MagicMock):
        """Explicit allowed_tools is a noop for codex."""
        mock_run.return_value = MagicMock(stdout="jsonl", stderr="", returncode=0)
        result = call(
            "test",
            engine="codex",
            capabilities=LLMCapabilities(allowed_tools=("Read", "Grep")),
        )
        assert result.returncode == 0

    def test_codex_tool_flags_not_emitted_in_argv(self):
        """codex argv has no --allowed-tools / --disallowed-tools (CLI has no such flags)."""
        cmd = build_llm_cmd(
            "codex",
            "gpt-5.6-terra",
            "test",
            capabilities=LLMCapabilities(
                allowed_tools=("Read",),
                disallowed_tools=("Bash", "Edit"),
            ),
        )
        assert "--allowed-tools" not in cmd
        assert "--disallowed-tools" not in cmd
        # Existing codex-specific flags are preserved
        assert "--json" in cmd
        assert "--skip-git-repo-check" in cmd


class TestExtraArgsDedupe:
    """Deduplicate flags between EngineSpec.extra_args and _CAPABILITY_FLAG_BUILDERS.

    codex CLI rejects duplicate `--json` with `error: the argument '--json' cannot be used
    multiple times` (exit 2), so duplicates are an immediate failure before the model call.
    """

    def test_dedupe_removes_valueless_flag_emitted_by_builder(self):
        """Valueless flags present on the builder side are dropped from extra_args."""
        from ghdag.llm.spec import _dedupe_extra_args
        assert _dedupe_extra_args(
            ("--json", "--skip-git-repo-check"), ["--json", "--skip-git-repo-check"]
        ) == []

    def test_dedupe_removes_flag_with_value_as_a_pair(self):
        """Flags with values are dropped as flag+value pairs."""
        from ghdag.llm.spec import _dedupe_extra_args
        assert _dedupe_extra_args(
            ("--output-format", "json"), ["--permission-mode", "default", "--output-format", "json"]
        ) == []

    def test_dedupe_keeps_flags_builder_did_not_emit(self):
        """Flags the builder did not emit remain, including their values."""
        from ghdag.llm.spec import _dedupe_extra_args
        assert _dedupe_extra_args(
            ("--output-format", "json"), ["--permission-mode", "default"]
        ) == ["--output-format", "json"]

    def test_dedupe_noop_when_builder_emits_nothing(self):
        """When perm_flags is empty, extra_args is unchanged."""
        from ghdag.llm.spec import _dedupe_extra_args
        assert _dedupe_extra_args(("-o", "pipefail"), []) == ["-o", "pipefail"]

    def test_codex_render_has_no_duplicate_flags_for_every_preset(self):
        """No flag duplicates in codex argv for any permission preset."""
        from ghdag.llm.capabilities import PRESETS
        spec = ENGINE_SPECS["codex"]
        for name, caps in PRESETS.items():
            cmd = render_exec_command(
                spec, order_path="jobs/order.md",
                model="gpt-5.6-terra", capabilities=caps,
            )
            tokens = cmd.split()
            for flag in ("--json", "--skip-git-repo-check"):
                assert tokens.count(flag) == 1, f"preset={name} cmd={cmd}"

    def test_claude_json_only_no_duplicate_output_format(self):
        """claude + json_only does not duplicate --output-format (valued-flag regression)."""
        from ghdag.llm.capabilities import PRESETS
        cmd = render_exec_command(
            ENGINE_SPECS["claude"], order_path="jobs/order.md",
            model="claude-sonnet-4-6", capabilities=PRESETS["json_only"],
        )
        assert cmd.split().count("--output-format") == 1
        assert "--output-format json" in cmd

    def test_claude_text_only_keeps_extra_args_output_format(self):
        """claude + text_only keeps extra_args --output-format because the builder omits it.

        DAG default is stream-json --verbose (#2966). ClaudeJsonAdapter reads usage /
        session_id from the JSONL result line.
        """
        from ghdag.llm.capabilities import PRESETS
        cmd = render_exec_command(
            ENGINE_SPECS["claude"], order_path="jobs/order.md",
            model="claude-sonnet-4-6", capabilities=PRESETS["text_only"],
        )
        assert "--output-format stream-json" in cmd
        assert "--verbose" in cmd
        assert cmd.split().count("--output-format") == 1

    def test_cursor_text_only_keeps_extra_args_output_format(self):
        """cursor + text_only keeps extra_args --output-format stream-json."""
        from ghdag.llm.capabilities import PRESETS
        cmd = render_exec_command(
            ENGINE_SPECS["cursor"], order_path="jobs/order.md",
            model="auto", capabilities=PRESETS["text_only"],
        )
        assert cmd.split().count("--output-format") == 1
        assert "--output-format stream-json" in cmd
        assert "--stream-partial-output" in cmd

    def test_cursor_json_only_overrides_stream_default(self):
        """cursor + json_only returns to single JSON and drops stream-only flags."""
        from ghdag.llm.capabilities import PRESETS
        cmd = render_exec_command(
            ENGINE_SPECS["cursor"], order_path="jobs/order.md",
            model="auto", capabilities=PRESETS["json_only"],
        )
        assert cmd.split().count("--output-format") == 1
        assert "--output-format json" in cmd
        assert "--output-format stream-json" not in cmd
        assert "--stream-partial-output" not in cmd

    def test_cursor_stream_dedupes_output_format(self):
        """cursor + stream prefers stream-json and emits --output-format only once."""
        cmd = render_exec_command(
            ENGINE_SPECS["cursor"], order_path="jobs/order.md",
            model="auto", capabilities=LLMCapabilities(stream=True),
        )
        assert cmd.split().count("--output-format") == 1
        assert "--output-format stream-json" in cmd
        assert cmd.split().count("--stream-partial-output") == 1

    def test_gemini_without_builder_keeps_extra_args(self):
        """extra_args for engines without a builder are unchanged."""
        from ghdag.llm.capabilities import PRESETS
        cmd = render_exec_command(
            ENGINE_SPECS["gemini"], order_path="jobs/order.md",
            model="gemini-3-flash", capabilities=PRESETS["text_only"],
        )
        assert "--approval-mode yolo" in cmd


class TestSandboxCapability:
    """sandbox capability and READONLY_OBSERVE preset (nexus Issue #2640)."""

    def test_sandbox_off_matches_default_for_all_engines(self):
        """LLMCapabilities(sandbox='off') and LLMCapabilities() yield the same argv for all engines."""
        default = LLMCapabilities()
        explicit_off = LLMCapabilities(sandbox="off")
        for engine, model in [
            ("claude", "claude-sonnet-4-6"),
            ("codex", "gpt-5.6-terra"),
            ("cursor", "auto"),
            ("gemini", "gemini-3-flash"),
            ("shell", "bash"),
        ]:
            assert build_llm_cmd(engine, model, "p", capabilities=default) == build_llm_cmd(
                engine, model, "p", capabilities=explicit_off
            )

    def test_readonly_observe_preset(self):
        """PRESETS['readonly_observe'] has sandbox=readonly and edit-tool denies."""
        from ghdag.llm.capabilities import PRESETS

        caps = PRESETS["readonly_observe"]
        assert caps.sandbox == "readonly"
        assert caps.disallowed_tools == ("Edit", "NotebookEdit")

    def test_readonly_observe_claude_flags(self):
        """READONLY_OBSERVE → plan mode without Write deny (sumipan/nexus#3188)."""
        from ghdag.llm.capabilities import READONLY_OBSERVE
        from ghdag.llm.engines import _build_claude_flags

        assert _build_claude_flags(READONLY_OBSERVE, False) == [
            "--permission-mode",
            "plan",
            "--disallowed-tools",
            "Edit,NotebookEdit",
        ]

    def test_non_plan_presets_keep_write_denied(self):
        """TEXT_ONLY / JSON_ONLY / WEB_RESEARCH still deny Write."""
        from ghdag.llm.capabilities import JSON_ONLY, TEXT_ONLY, WEB_RESEARCH

        for caps in (TEXT_ONLY, JSON_ONLY, WEB_RESEARCH):
            assert "Write" in caps.disallowed_tools

    def test_readonly_observe_cursor_codex_argv_unchanged(self):
        """cursor / codex argv is identical to the pre-#3188 preset value."""
        from ghdag.llm.capabilities import READONLY_OBSERVE

        previous = LLMCapabilities(
            sandbox="readonly", disallowed_tools=("Edit", "Write", "NotebookEdit")
        )
        for engine, model in [("cursor", "auto"), ("codex", "gpt-5.6-terra")]:
            current_cmd = build_llm_cmd(engine, model, "p", capabilities=READONLY_OBSERVE)
            previous_cmd = build_llm_cmd(engine, model, "p", capabilities=previous)
            assert current_cmd == previous_cmd
            assert "--disallowed-tools" not in current_cmd

    def test_claude_sandbox_readonly_uses_permission_mode_plan(self):
        """claude + sandbox=readonly → --permission-mode plan."""
        cmd = build_llm_cmd(
            "claude",
            "claude-sonnet-4-6",
            "p",
            capabilities=LLMCapabilities(sandbox="readonly"),
        )
        assert "--permission-mode" in cmd
        assert cmd[cmd.index("--permission-mode") + 1] == "plan"

    def test_codex_sandbox_readonly_uses_s_read_only(self):
        """codex + sandbox=readonly → -s read-only, no bypass flag."""
        cmd = build_llm_cmd(
            "codex",
            "gpt-5.6-terra",
            "p",
            capabilities=LLMCapabilities(sandbox="readonly"),
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
            capabilities=LLMCapabilities(sandbox="readonly"),
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
                capabilities=LLMCapabilities(
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
                capabilities=LLMCapabilities(sandbox="readonly"),
                dangerously_skip_permissions=True,
            )

    def test_cursor_sandbox_readonly_conflicts_with_force(self):
        """cursor + sandbox=readonly + dangerously_skip_permissions → ValueError."""
        with pytest.raises(ValueError, match="sandbox='readonly' conflicts"):
            build_llm_cmd(
                "cursor",
                "auto",
                "p",
                capabilities=LLMCapabilities(sandbox="readonly"),
                dangerously_skip_permissions=True,
            )

    def test_gemini_sandbox_readonly_raises(self):
        """gemini + sandbox=readonly → NotImplementedError."""
        with pytest.raises(NotImplementedError, match="sandbox"):
            call(
                "test",
                engine="gemini",
                capabilities=LLMCapabilities(sandbox="readonly"),
            )

    def test_shell_sandbox_readonly_raises(self):
        """shell + sandbox=readonly → NotImplementedError."""
        with pytest.raises(NotImplementedError, match="sandbox"):
            call(
                "test",
                engine="shell",
                capabilities=LLMCapabilities(sandbox="readonly"),
            )

    def test_cursor_disallowed_tools_in_ignored_capabilities(self):
        """cursor disallowed_tools is declared noop via _IGNORED_CAPABILITIES."""
        from ghdag.llm.engines import _IGNORED_CAPABILITIES, _UNSUPPORTED_CAPABILITIES

        assert "disallowed_tools" in _IGNORED_CAPABILITIES["cursor"]
        # After set difference, excluded from validation (skipped via ignored even if unsupported)
        unsupported = _UNSUPPORTED_CAPABILITIES.get("cursor", set())
        ignored = _IGNORED_CAPABILITIES.get("cursor", set())
        assert "disallowed_tools" not in (unsupported - ignored)

    @patch("ghdag.llm.engines.subprocess.run")
    def test_cursor_disallowed_tools_noop(self, mock_run: MagicMock):
        """Passing disallowed_tools to cursor does not raise and does not appear in argv."""
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
        result = call(
            "test",
            engine="cursor",
            capabilities=LLMCapabilities(disallowed_tools=("Edit", "Write")),
        )
        assert result.returncode == 0
        cmd = mock_run.call_args[0][0]
        assert "--disallowed-tools" not in cmd


class TestCallCwd:
    @patch("ghdag.llm.engines.subprocess.run")
    def test_call_passes_cwd_to_subprocess(self, mock_run: MagicMock, tmp_path):
        """call(..., cwd=...) passes cwd to subprocess.run."""
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
        call("hello", engine="claude", cwd=tmp_path)
        _, kwargs = mock_run.call_args
        assert kwargs["cwd"] == tmp_path

    @patch("ghdag.llm.engines.subprocess.run")
    def test_call_default_cwd_is_none(self, mock_run: MagicMock):
        """When cwd is omitted, subprocess.run gets cwd=None (current compat)."""
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
        call("hello", engine="claude")
        _, kwargs = mock_run.call_args
        assert kwargs.get("cwd") is None
