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

    def test_render_exec_command_codex_dangerous_full_access(self):
        """DANGEROUS_FULL_ACCESS 指定時はサンドボックスバイパスフラグが付く（nexus#2558 回帰）。

        render_exec_command は builder を dangerously_skip_permissions=False で呼ぶため、
        capabilities.permission_mode を見ないとフラグが落ちる。落ちると codex は
        workspace-write のまま起動し、cwd 外（例: 日記リポジトリ）へ書けずに失敗する。
        """
        from ghdag.llm.capabilities import DANGEROUS_FULL_ACCESS
        spec = ENGINE_SPECS["codex"]
        cmd = render_exec_command(
            spec,
            order_path="jobs/order.md",
            prompt="hello",
            model="gpt-5.6-terra",
            capabilities=DANGEROUS_FULL_ACCESS,
        )
        assert "--dangerously-bypass-approvals-and-sandbox" in cmd
        assert cmd.split().count("--dangerously-bypass-approvals-and-sandbox") == 1
        assert cmd.split().count("--json") == 1

    def test_render_exec_command_codex_default_keeps_sandbox(self):
        """既定 capabilities ではバイパスフラグを付けない（サンドボックス維持）。"""
        from ghdag.llm.capabilities import LLMCapabilities
        spec = ENGINE_SPECS["codex"]
        cmd = render_exec_command(
            spec,
            order_path="jobs/order.md",
            prompt="hello",
            model="gpt-5.6-terra",
            capabilities=LLMCapabilities(),
        )
        assert "--dangerously-bypass-approvals-and-sandbox" not in cmd


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


class TestSandboxCapability:
    """sandbox capability と READONLY_OBSERVE プリセット（nexus Issue #2640）。"""

    def test_sandbox_off_matches_default_for_all_engines(self):
        """LLMCapabilities(sandbox='off') と LLMCapabilities() で全エンジンの argv が一致する。"""
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
        """PRESETS['readonly_observe'] が sandbox=readonly と編集系 deny を持つ。"""
        from ghdag.llm.capabilities import PRESETS

        caps = PRESETS["readonly_observe"]
        assert caps.sandbox == "readonly"
        assert caps.disallowed_tools == ("Edit", "Write", "NotebookEdit")

    def test_claude_sandbox_readonly_uses_permission_mode_plan(self):
        """claude + sandbox=readonly → --permission-mode plan。"""
        cmd = build_llm_cmd(
            "claude",
            "claude-sonnet-4-6",
            "p",
            capabilities=LLMCapabilities(sandbox="readonly"),
        )
        assert "--permission-mode" in cmd
        assert cmd[cmd.index("--permission-mode") + 1] == "plan"

    def test_codex_sandbox_readonly_uses_s_read_only(self):
        """codex + sandbox=readonly → -s read-only、bypass フラグなし。"""
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
        """cursor + sandbox=readonly → --sandbox enabled、--force なし。"""
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
        """claude + sandbox=readonly + 明示 permission_mode → ValueError。"""
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
        """codex + sandbox=readonly + dangerously_skip_permissions → ValueError。"""
        with pytest.raises(ValueError, match="sandbox='readonly' conflicts"):
            build_llm_cmd(
                "codex",
                "gpt-5.6-terra",
                "p",
                capabilities=LLMCapabilities(sandbox="readonly"),
                dangerously_skip_permissions=True,
            )

    def test_cursor_sandbox_readonly_conflicts_with_force(self):
        """cursor + sandbox=readonly + dangerously_skip_permissions → ValueError。"""
        with pytest.raises(ValueError, match="sandbox='readonly' conflicts"):
            build_llm_cmd(
                "cursor",
                "auto",
                "p",
                capabilities=LLMCapabilities(sandbox="readonly"),
                dangerously_skip_permissions=True,
            )

    def test_gemini_sandbox_readonly_raises(self):
        """gemini + sandbox=readonly → NotImplementedError。"""
        with pytest.raises(NotImplementedError, match="sandbox"):
            call(
                "test",
                engine="gemini",
                capabilities=LLMCapabilities(sandbox="readonly"),
            )

    def test_shell_sandbox_readonly_raises(self):
        """shell + sandbox=readonly → NotImplementedError。"""
        with pytest.raises(NotImplementedError, match="sandbox"):
            call(
                "test",
                engine="shell",
                capabilities=LLMCapabilities(sandbox="readonly"),
            )

    def test_cursor_disallowed_tools_in_ignored_capabilities(self):
        """cursor の disallowed_tools は _IGNORED_CAPABILITIES で noop 宣言される。"""
        from ghdag.llm.engines import _IGNORED_CAPABILITIES, _UNSUPPORTED_CAPABILITIES

        assert "disallowed_tools" in _IGNORED_CAPABILITIES["cursor"]
        # 差集合後は検証対象から外れる（unsupported に含まれていても ignored でスキップ）
        unsupported = _UNSUPPORTED_CAPABILITIES.get("cursor", set())
        ignored = _IGNORED_CAPABILITIES.get("cursor", set())
        assert "disallowed_tools" not in (unsupported - ignored)

    @patch("ghdag.llm.engines.subprocess.run")
    def test_cursor_disallowed_tools_noop(self, mock_run: MagicMock):
        """cursor に disallowed_tools を渡しても NotImplementedError にならず argv にも出ない。"""
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
        result = call(
            "test",
            engine="cursor",
            capabilities=LLMCapabilities(disallowed_tools=("Edit", "Write")),
        )
        assert result.returncode == 0
        cmd = mock_run.call_args[0][0]
        assert "--disallowed-tools" not in cmd
