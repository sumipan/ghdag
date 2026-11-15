"""Tests for build_llm_cmd / render_exec_command isolation opt-in (nexus #3174)."""

from __future__ import annotations

import dataclasses
import shlex

import pytest

from ghdag.core.capabilities import TEXT_ONLY, WEB_RESEARCH, Permission
from ghdag.core.command import build_llm_cmd, inject_resume, render_exec_command
from ghdag.core.engine_spec import ENGINE_SPECS, EngineSpec, InputMode, PromptFlag
from ghdag.llm.engines import _validate_permission_for_engine


class TestIsolationOptIn:
    """AC-1 / AC-2: --disable-slash-commands is attached only when isolation=True."""

    def test_build_llm_cmd_default_omits_disable_slash_commands(self) -> None:
        cmd = build_llm_cmd("claude", "claude-sonnet-4-6", "hello", isolation=False)
        assert "--disable-slash-commands" not in cmd
        assert "--disable-slash-commands" not in ENGINE_SPECS["claude"].extra_args

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


_CODEX_SKILLS_OFF = ["-c", "skills.include_instructions=false"]


class TestCodexIsolationOptIn:
    """Codex isolation adds -c skills.include_instructions=false (#5541)."""

    def test_build_llm_cmd_codex_isolation_false_omits_skills_config(self) -> None:
        cmd = build_llm_cmd("codex", "gpt-5.6-terra", "hello", isolation=False)
        assert _CODEX_SKILLS_OFF[0] not in cmd

    def test_render_exec_command_codex_resume_isolation(self) -> None:
        cmd = render_exec_command(
            ENGINE_SPECS["codex"],
            order_path="jobs/order.md",
            model="gpt-5.6-terra",
            permission=TEXT_ONLY,
            resume_session_id="sess-1",
            isolation=True,
        )
        inner = cmd.split(" < ", 1)[0]
        assert "exec resume 'sess-1'" in inner
        tokens = shlex.split(inner)
        assert tokens.count("-c") == 1


_CONTAINER = Permission(
    sandbox="container",
    container_image="img:1",
    container_mounts=("/h/.codex:/root/.codex:ro",),
    container_env=("CURSOR_API_KEY",),
    docker_bin="/opt/d/bin/docker",
)


class TestPreSubcommandArgs:
    def test_engine_spec_pre_subcommand_args_default_empty(self) -> None:
        assert ENGINE_SPECS["codex"].pre_subcommand_args == ()

    def test_engine_spec_preserves_positional_subcommand_argument(self) -> None:
        spec = EngineSpec(
            "demo",
            "tool",
            InputMode.STDIN,
            PromptFlag.NONE,
            None,
            None,
            None,
            None,
            "trailing",
            (),
            ("run",),
        )
        assert spec.subcommand == ("run",)
        assert spec.pre_subcommand_args == ()

    def test_codex_web_research_resume(self) -> None:
        cmd = build_llm_cmd(
            "codex",
            "gpt-5.5",
            "hello",
            permission=WEB_RESEARCH,
            resume_session_id="sid",
        )
        assert cmd[:5] == ["codex", "--search", "exec", "resume", "sid"]

class TestBuildLlmCmdPromptViaStdin:
    """nexus #3805: STDIN engines keep the prompt body out of argv."""

    def test_cursor_argv(self) -> None:
        cmd = build_llm_cmd("cursor", "composer-2", "hello")
        assert cmd == ["cursor-agent", "--model", "composer-2", "-p"]

    def test_gemini_argv(self) -> None:
        cmd = build_llm_cmd("gemini", "gemini-2.5-flash", "hello")
        assert cmd == ["gemini", "--model", "gemini-2.5-flash", "-p"]

    def test_unknown_engine_keeps_prompt_in_argv(self) -> None:
        cmd = build_llm_cmd("some-cli", "m1", "hello")
        assert cmd == ["some-cli", "--model", "m1", "-p", "hello"]


class TestRenderExecCommandContainer:
    """sandbox='container' wraps the engine CLI with docker run for all 3 engines."""

    def test_no_os_sandbox_flags(self) -> None:
        caps = dataclasses.replace(_CONTAINER, permission_mode="default")
        for engine in ("claude", "cursor", "codex"):
            cmd = render_exec_command(
                ENGINE_SPECS[engine], order_path="o.md", model="m", permission=caps,
            )
            assert "--permission-mode plan" not in cmd
            assert "--sandbox" not in cmd
            assert "read-only" not in cmd.split("img:1", 1)[1]

    def test_shell_argv_engine_rejected(self) -> None:
        with pytest.raises(ValueError, match="container"):
            render_exec_command(
                ENGINE_SPECS["shell"], order_path="o.sh", model=None,
                permission=_CONTAINER,
            )

    @pytest.mark.parametrize("engine", ["claude", "cursor", "codex"])
    def test_build_llm_cmd_rejects_container(self, engine: str) -> None:
        with pytest.raises(ValueError, match="container"):
            build_llm_cmd(engine, "m", "prompt", permission=_CONTAINER)

    @pytest.mark.parametrize("engine", ["gemini", "shell"])
    def test_gemini_shell_reject_container(self, engine: str) -> None:
        with pytest.raises(NotImplementedError, match="sandbox"):
            _validate_permission_for_engine(engine, _CONTAINER)


class TestInjectResume:
    """Resume injection into render_exec_command strings (sumipan/nexus#6084)."""

    @pytest.mark.parametrize("engine", ["claude", "cursor", "codex"])
    def test_inject_resume_already_resumed_is_unchanged(self, engine: str) -> None:
        spec = ENGINE_SPECS[engine]
        cmd = render_exec_command(
            spec,
            order_path="jobs/x.md",
            model="M",
            resume_session_id="OLD",
        )
        assert inject_resume(spec, cmd, "S") == cmd

    @pytest.mark.parametrize("engine", ["gemini", "shell"])
    def test_inject_resume_unsupported_engine_is_unchanged(self, engine: str) -> None:
        spec = ENGINE_SPECS[engine]
        cmd = render_exec_command(spec, order_path="jobs/x.md", model="M")
        assert inject_resume(spec, cmd, "S") == cmd
