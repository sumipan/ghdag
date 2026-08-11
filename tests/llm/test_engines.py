"""Tests for LLMResult.latency_ms and call() latency measurement — Issue #1275."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from ghdag.llm.capabilities import LLMCapabilities
from ghdag.llm.engines import LLMResult, build_llm_cmd, call
from ghdag.llm.spec import ENGINE_SPECS, render_exec_command


class TestLLMResultLatencyMs:
    def test_llm_result_latency_ms_default(self):
        """latency_ms 未指定時のデフォルトは 0.0。"""
        r = LLMResult(stdout="out", stderr="", returncode=0)
        assert r.latency_ms == 0.0

    @patch("ghdag.llm.engines.subprocess.run")
    def test_call_measures_latency(self, mock_run: MagicMock):
        """call() は latency_ms > 0 を返す。"""
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
        result = call("hello", engine="claude")
        assert result.latency_ms > 0


class TestBuildLlmCmdCodex:
    def test_build_llm_cmd_codex(self):
        """build_llm_cmd("codex") は subcommand + flags を正しく組み立てる。"""
        cmd = build_llm_cmd("codex", "gpt-5.6-terra", "test")
        assert cmd == ["codex", "exec", "-", "--model", "gpt-5.6-terra", "--json", "--skip-git-repo-check"]

    def test_build_llm_cmd_codex_danger_flag(self):
        """dangerously_skip_permissions=True で bypass フラグが追加される。"""
        cmd = build_llm_cmd("codex", "gpt-5.6-terra", "test", dangerously_skip_permissions=True)
        assert "--dangerously-bypass-approvals-and-sandbox" in cmd

    def test_build_llm_cmd_existing_engines_unaffected(self):
        """既存エンジン（claude/gemini/cursor/shell）は subcommand=() のため argv が変わらない。"""
        for engine, cli in [("claude", "claude"), ("gemini", "gemini"), ("shell", "bash")]:
            cmd = build_llm_cmd(engine, ENGINE_SPECS[engine].default_model or "auto", "test")
            assert cmd[0] == cli
            # subcommand が空なので cli の直後がオプションや引数（exec など余分な要素が入らない）
            assert "exec" not in cmd

    def test_render_exec_command_codex(self):
        """render_exec_command が codex 向けの正しいシェルコマンドを生成する。"""
        spec = ENGINE_SPECS["codex"]
        cmd = render_exec_command(
            spec,
            order_path="jobs/order.md",
            prompt="hello",
            model="gpt-5.6-terra",
        )
        assert cmd == "cat jobs/order.md | codex exec - --model 'gpt-5.6-terra' --json --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox"

    def test_render_exec_command_codex_with_capabilities(self):
        """capabilities 指定時は _build_codex_flags 経由でフラグ生成される。"""
        from ghdag.llm.capabilities import LLMCapabilities
        spec = ENGINE_SPECS["codex"]
        cmd = render_exec_command(
            spec,
            order_path="jobs/order.md",
            prompt="hello",
            model="gpt-5.6-terra",
            capabilities=LLMCapabilities(),
        )
        assert "codex exec -" in cmd
        assert "--json" in cmd
        assert "--skip-git-repo-check" in cmd
        # extra_args と _build_codex_flags の両方が出すため重複しやすい。
        # codex CLI は `--json` の重複を argument error（exit 2）で弾く。
        assert cmd.split().count("--json") == 1
        assert cmd.split().count("--skip-git-repo-check") == 1


class TestCallCodexPromptRouting:
    @patch("ghdag.llm.engines.subprocess.run")
    def test_call_codex_prompt_to_stdin(self, mock_run: MagicMock):
        """call(engine="codex", prompt="hello") で subprocess.run の input に prompt が渡る。"""
        mock_run.return_value = MagicMock(stdout="jsonl", stderr="", returncode=0)
        call("hello", engine="codex", capabilities=LLMCapabilities())
        _, kwargs = mock_run.call_args
        assert kwargs["input"] == "hello"

    @patch("ghdag.llm.engines.subprocess.run")
    def test_call_codex_stdin_text_priority(self, mock_run: MagicMock):
        """stdin_text が明示指定されている場合は prompt より優先される。"""
        mock_run.return_value = MagicMock(stdout="jsonl", stderr="", returncode=0)
        call("hello", engine="codex", stdin_text="override", capabilities=LLMCapabilities())
        _, kwargs = mock_run.call_args
        assert kwargs["input"] == "override"

    @patch("ghdag.llm.engines.subprocess.run")
    def test_call_claude_stdin_text_unaffected(self, mock_run: MagicMock):
        """claude エンジンは prompt_flag があるため stdin ルーティングが発動しない。"""
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
        call("hello", engine="claude", stdin_text=None)
        _, kwargs = mock_run.call_args
        assert kwargs["input"] is None


class TestCodexUnsupportedCapabilities:
    def test_codex_unsupported_stream(self):
        """stream=True で NotImplementedError が送出される。"""
        with pytest.raises(NotImplementedError, match="stream"):
            call("test", engine="codex", capabilities=LLMCapabilities(stream=True))

    def test_codex_unsupported_output_format_json(self):
        """output_format="json" で NotImplementedError が送出される。"""
        with pytest.raises(NotImplementedError, match="output_format"):
            call("test", engine="codex", capabilities=LLMCapabilities(output_format="json"))

    def test_codex_unsupported_permission_mode(self):
        """permission_mode 非デフォルト値で NotImplementedError が送出される。"""
        with pytest.raises(NotImplementedError, match="permission_mode"):
            call(
                "test",
                engine="codex",
                capabilities=LLMCapabilities(permission_mode="bypassPermissions"),
            )


class TestCodexIgnoredCapabilities:
    """codex は allowed_tools / disallowed_tools を noop で受理する（Issue: TEXT_ONLY で codex を呼びたい）。"""

    @patch("ghdag.llm.engines.subprocess.run")
    def test_codex_with_text_only_does_not_raise(self, mock_run: MagicMock):
        """TEXT_ONLY (disallowed_tools あり) を codex に渡しても NotImplementedError が出ない。"""
        from ghdag.llm.capabilities import TEXT_ONLY
        mock_run.return_value = MagicMock(stdout="jsonl", stderr="", returncode=0)
        result = call("test", engine="codex", capabilities=TEXT_ONLY)
        assert result.returncode == 0

    @patch("ghdag.llm.engines.subprocess.run")
    def test_codex_with_disallowed_tools_does_not_raise(self, mock_run: MagicMock):
        """disallowed_tools を明示指定しても codex では noop で通る。"""
        mock_run.return_value = MagicMock(stdout="jsonl", stderr="", returncode=0)
        result = call(
            "test",
            engine="codex",
            capabilities=LLMCapabilities(disallowed_tools=("Bash", "Edit")),
        )
        assert result.returncode == 0

    @patch("ghdag.llm.engines.subprocess.run")
    def test_codex_with_allowed_tools_does_not_raise(self, mock_run: MagicMock):
        """allowed_tools を明示指定しても codex では noop で通る。"""
        mock_run.return_value = MagicMock(stdout="jsonl", stderr="", returncode=0)
        result = call(
            "test",
            engine="codex",
            capabilities=LLMCapabilities(allowed_tools=("Read", "Grep")),
        )
        assert result.returncode == 0

    def test_codex_tool_flags_not_emitted_in_argv(self):
        """codex の argv には --allowed-tools / --disallowed-tools が現れない（CLI に該当フラグがない）。"""
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
        # 既存の codex 固有フラグは維持されている
        assert "--json" in cmd
        assert "--skip-git-repo-check" in cmd


class TestExtraArgsDedupe:
    """EngineSpec.extra_args と _CAPABILITY_FLAG_BUILDERS の重複フラグを除去する。

    codex CLI は `--json` の重複を `error: the argument '--json' cannot be used
    multiple times`（exit 2）で拒否するため、重複はモデル呼び出し前の即死になる。
    """

    def test_dedupe_removes_valueless_flag_emitted_by_builder(self):
        """値を取らないフラグが builder 側にあれば extra_args から落ちる。"""
        from ghdag.llm.spec import _dedupe_extra_args
        assert _dedupe_extra_args(
            ("--json", "--skip-git-repo-check"), ["--json", "--skip-git-repo-check"]
        ) == []

    def test_dedupe_removes_flag_with_value_as_a_pair(self):
        """値付きフラグはフラグと値のペアごと落とす。"""
        from ghdag.llm.spec import _dedupe_extra_args
        assert _dedupe_extra_args(
            ("--output-format", "json"), ["--permission-mode", "default", "--output-format", "json"]
        ) == []

    def test_dedupe_keeps_flags_builder_did_not_emit(self):
        """builder が出していないフラグは値ごと残る。"""
        from ghdag.llm.spec import _dedupe_extra_args
        assert _dedupe_extra_args(
            ("--output-format", "json"), ["--permission-mode", "default"]
        ) == ["--output-format", "json"]

    def test_dedupe_noop_when_builder_emits_nothing(self):
        """perm_flags が空なら extra_args はそのまま。"""
        from ghdag.llm.spec import _dedupe_extra_args
        assert _dedupe_extra_args(("-o", "pipefail"), []) == ["-o", "pipefail"]

    def test_codex_render_has_no_duplicate_flags_for_every_preset(self):
        """どの permission preset でも codex の argv にフラグ重複が出ない。"""
        from ghdag.llm.capabilities import PRESETS
        spec = ENGINE_SPECS["codex"]
        for name, caps in PRESETS.items():
            cmd = render_exec_command(
                spec, order_path="jobs/order.md", prompt="hello",
                model="gpt-5.6-terra", capabilities=caps,
            )
            tokens = cmd.split()
            for flag in ("--json", "--skip-git-repo-check"):
                assert tokens.count(flag) == 1, f"preset={name} cmd={cmd}"

    def test_claude_json_only_no_duplicate_output_format(self):
        """claude + json_only でも --output-format が重複しない（値付きフラグの回帰）。"""
        from ghdag.llm.capabilities import PRESETS
        cmd = render_exec_command(
            ENGINE_SPECS["claude"], order_path="jobs/order.md", prompt="hello",
            model="claude-sonnet-4-6", capabilities=PRESETS["json_only"],
        )
        assert cmd.split().count("--output-format") == 1
        assert "--output-format json" in cmd

    def test_claude_text_only_keeps_extra_args_output_format(self):
        """claude + text_only は builder が --output-format を出さないので extra_args 側が残る。

        ここを落とすと ClaudeJsonAdapter が usage を取れなくなる。
        """
        from ghdag.llm.capabilities import PRESETS
        cmd = render_exec_command(
            ENGINE_SPECS["claude"], order_path="jobs/order.md", prompt="hello",
            model="claude-sonnet-4-6", capabilities=PRESETS["text_only"],
        )
        assert "--output-format json" in cmd

    def test_gemini_without_builder_keeps_extra_args(self):
        """builder を持たないエンジンの extra_args は変化しない。"""
        from ghdag.llm.capabilities import PRESETS
        cmd = render_exec_command(
            ENGINE_SPECS["gemini"], order_path="jobs/order.md", prompt="hello",
            model="gemini-3-flash", capabilities=PRESETS["text_only"],
        )
        assert "--approval-mode yolo" in cmd
