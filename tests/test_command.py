"""Tests for build_llm_cmd / render_exec_command isolation opt-in (nexus #3174)."""

from __future__ import annotations

from ghdag.core.command import build_llm_cmd, render_exec_command
from ghdag.core.engine_spec import ENGINE_SPECS
from ghdag.llm.capabilities import TEXT_ONLY


class TestIsolationOptIn:
    """AC-1 / AC-2: --disable-slash-commands is attached only when isolation=True."""

    def test_build_llm_cmd_default_omits_disable_slash_commands(self) -> None:
        cmd = build_llm_cmd("claude", "claude-sonnet-4-6", "hello", isolation=False)
        assert "--disable-slash-commands" not in cmd
        assert "--disable-slash-commands" not in ENGINE_SPECS["claude"].extra_args

    def test_render_exec_command_default_omits_disable_slash_commands(self) -> None:
        cmd = render_exec_command(
            ENGINE_SPECS["claude"],
            order_path="queue/order.md",
            model="claude-opus-4-6",
            isolation=False,
        )
        assert "--disable-slash-commands" not in cmd

    def test_build_llm_cmd_isolation_true_includes_disable_slash_commands(self) -> None:
        cmd = build_llm_cmd("claude", "claude-sonnet-4-6", "hello", isolation=True)
        assert "--disable-slash-commands" in cmd

    def test_render_exec_command_isolation_true_includes_disable_slash_commands(
        self,
    ) -> None:
        cmd = render_exec_command(
            ENGINE_SPECS["claude"],
            order_path="queue/order.md",
            model="claude-opus-4-6",
            isolation=True,
        )
        assert "--disable-slash-commands" in cmd

    def test_isolation_true_does_not_affect_cursor(self) -> None:
        cmd = build_llm_cmd("cursor", "auto", "hello", isolation=True)
        assert "--disable-slash-commands" not in cmd


_CLAUDE_TEXT_ONLY_FLAGS = [
    "--permission-mode",
    "default",
    "--disallowed-tools",
    "Bash,Edit,Write,NotebookEdit,WebFetch,WebSearch",
]


class TestBuildLlmCmdPromptViaStdin:
    """nexus #3805: STDIN engines keep the prompt body out of argv."""

    def test_claude_prompt_not_in_argv(self) -> None:
        cmd = build_llm_cmd("claude", "claude-opus-4-6", "hello")
        assert "-p" in cmd
        assert "hello" not in cmd
        assert cmd == [
            "claude", "--model", "claude-opus-4-6", "-p", *_CLAUDE_TEXT_ONLY_FLAGS,
        ]

    def test_claude_resume_and_isolation_positions(self) -> None:
        cmd = build_llm_cmd(
            "claude",
            "claude-opus-4-6",
            "hello",
            resume_session_id="sid",
            isolation=True,
        )
        assert cmd == [
            "claude", "--model", "claude-opus-4-6", "-p",
            "--resume", "sid",
            *_CLAUDE_TEXT_ONLY_FLAGS,
            "--disable-slash-commands",
        ]

    def test_cursor_argv(self) -> None:
        cmd = build_llm_cmd("cursor", "composer-2", "hello")
        assert cmd == ["agent", "--model", "composer-2", "-p"]

    def test_gemini_argv(self) -> None:
        cmd = build_llm_cmd("gemini", "gemini-2.5-flash", "hello")
        assert cmd == ["gemini", "--model", "gemini-2.5-flash", "-p"]

    def test_codex_argv_unchanged(self) -> None:
        base = ["codex", "exec", "-", "--model", "gpt-5.6-terra", "--json", "--skip-git-repo-check"]
        assert build_llm_cmd("codex", "gpt-5.6-terra", "hello") == base
        assert build_llm_cmd(
            "codex", "gpt-5.6-terra", "hello", dangerously_skip_permissions=True
        ) == [*base, "--dangerously-bypass-approvals-and-sandbox"]
        assert build_llm_cmd(
            "codex", "gpt-5.6-terra", "hello", resume_session_id="sid"
        ) == [
            "codex", "exec", "resume", "sid",
            "--model", "gpt-5.6-terra", "--json", "--skip-git-repo-check",
        ]

    def test_large_prompt_keeps_argv_small(self) -> None:
        big = "x" * 200_000
        for engine, model in (
            ("claude", "claude-opus-4-6"),
            ("cursor", "composer-2"),
            ("codex", "gpt-5.6-terra"),
        ):
            cmd = build_llm_cmd(engine, model, big)
            assert all(len(arg) < 1024 for arg in cmd), engine

    def test_unknown_engine_keeps_prompt_in_argv(self) -> None:
        cmd = build_llm_cmd("some-cli", "m1", "hello")
        assert cmd == ["some-cli", "--model", "m1", "-p", "hello"]


class TestRenderExecCommandUnchanged:
    """nexus #3805: exec.jsonl path output is pinned to the pre-change strings."""

    def test_claude(self) -> None:
        cmd = render_exec_command(
            ENGINE_SPECS["claude"], order_path="jobs/order.md", model="claude-opus-4-6",
            capabilities=TEXT_ONLY,
        )
        assert cmd == (
            "claude -p --model 'claude-opus-4-6' --output-format stream-json --verbose "
            "--permission-mode default "
            "--disallowed-tools Bash,Edit,Write,NotebookEdit,WebFetch,WebSearch "
            "< jobs/order.md"
        )

    def test_cursor(self) -> None:
        cmd = render_exec_command(
            ENGINE_SPECS["cursor"], order_path="jobs/order.md", model="composer-2",
            capabilities=TEXT_ONLY,
        )
        assert cmd == (
            "agent --model 'composer-2' -p --output-format stream-json "
            "--stream-partial-output < jobs/order.md"
        )

    def test_codex(self) -> None:
        cmd = render_exec_command(
            ENGINE_SPECS["codex"], order_path="jobs/order.md", model="gpt-5.6-terra",
            capabilities=TEXT_ONLY,
        )
        assert cmd == (
            "codex exec - --model 'gpt-5.6-terra' --json --skip-git-repo-check "
            "< jobs/order.md"
        )
