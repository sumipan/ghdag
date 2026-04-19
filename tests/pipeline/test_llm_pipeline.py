"""Tests for ghdag.pipeline.llm_pipeline — LLMPipelineAPI (Issue #203)."""

from __future__ import annotations

from unittest.mock import MagicMock

from ghdag.pipeline.llm_pipeline import LLMPipelineAPI
from ghdag.workflow.schema import StepConfig


def _make_api(queue_dir: str = "queue") -> tuple[LLMPipelineAPI, MagicMock, MagicMock]:
    """LLMPipelineAPI with mocked PipelineState and OrderBuilder."""
    pipeline_state = MagicMock()
    pipeline_state.check_idempotency.return_value = True
    pipeline_state.write_order_file.return_value = "ts-claude-order-uuid.md"
    order_builder = MagicMock()
    order_builder.build_order.return_value = "order content"
    api = LLMPipelineAPI(
        pipeline_state=pipeline_state,
        order_builder=order_builder,
        queue_dir=queue_dir,
    )
    return api, pipeline_state, order_builder


# ---------------------------------------------------------------------------
# AC1-1: 1 step（engine=claude, depends=[]）
# ---------------------------------------------------------------------------


class TestAC1SingleStep:
    def test_submit_single_step_returns_exec_lines(self):
        """1 step で exec_lines 1 行（idempotency なし）。"""
        api, pipeline_state, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        exec_lines = api.submit(steps, {"issue_number": "10"})

        assert len(exec_lines) == 1
        pipeline_state.append_exec.assert_called_once_with(exec_lines)

    def test_submit_writes_order_file(self):
        """write_order_file が 1 回呼ばれる。"""
        api, pipeline_state, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        api.submit(steps, {})

        pipeline_state.write_order_file.assert_called_once()

    def test_submit_calls_build_order(self):
        """build_order が 1 回呼ばれ、template 名が渡される。"""
        api, _, order_builder = _make_api()
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        api.submit(steps, {})

        order_builder.build_order.assert_called_once()
        call_args = order_builder.build_order.call_args[0]
        assert call_args[0] == "brushup"

    def test_exec_line_format(self):
        """exec 行が {uuid}: cat queue/{order} | {cmd} | tee -a queue/{result} 形式。"""
        api, pipeline_state, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        exec_lines = api.submit(steps, {})

        line = exec_lines[0]
        assert ": cat queue/" in line
        assert " | " in line
        assert " | tee -a queue/" in line
        assert "[depends:" not in line

    def test_exec_line_contains_model(self):
        """exec 行にモデル名が含まれる。"""
        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        exec_lines = api.submit(steps, {})

        assert "--model" in exec_lines[0] and "claude-opus-4-6" in exec_lines[0]

    def test_exec_line_contains_dangerously_skip_permissions(self):
        """exec 行に --dangerously-skip-permissions が含まれる（engine=claude）。"""
        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        exec_lines = api.submit(steps, {})

        assert "--dangerously-skip-permissions" in exec_lines[0]


# ---------------------------------------------------------------------------
# AC1-2 / AC1-3: idempotency_key
# ---------------------------------------------------------------------------


class TestAC1IdempotencyKey:
    def test_idempotency_key_prepended(self):
        """idempotency_key 指定時、exec_lines[0] が '# idempotency: ...' になる。"""
        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        exec_lines = api.submit(steps, {}, idempotency_key="workflow:handler:42")

        assert exec_lines[0] == "# idempotency: workflow:handler:42"
        assert len(exec_lines) == 2  # idempotency + 1 step

    def test_no_idempotency_key_no_comment(self):
        """idempotency_key なしのとき先頭コメント行なし。"""
        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        exec_lines = api.submit(steps, {})

        assert not exec_lines[0].startswith("# idempotency:")


# ---------------------------------------------------------------------------
# AC1-2 / AC4: 2 steps with depends
# ---------------------------------------------------------------------------


class TestAC1TwoStepsWithDepends:
    def test_p2_has_depends_p1_uuid(self):
        """p2 の exec 行に [depends:{p1_uuid}] が含まれる。"""
        api, _, _ = _make_api()
        steps = [
            StepConfig(id="p1", template="p1", model="claude-sonnet-4-6"),
            StepConfig(id="p2", template="p2", model="claude-sonnet-4-6", depends=["p1"]),
        ]
        exec_lines = api.submit(steps, {})

        p1_uuid = exec_lines[0].split(":")[0]
        assert f"[depends:{p1_uuid}]" in exec_lines[1]

    def test_p2_context_has_p1_result_filename(self):
        """P2 の build_order に渡される context に p1_result_filename が含まれる。"""
        api, _, order_builder = _make_api()
        steps = [
            StepConfig(id="p1", template="p1", model="claude-sonnet-4-6"),
            StepConfig(id="p2", template="p2", model="claude-sonnet-4-6", depends=["p1"]),
        ]
        api.submit(steps, {})

        calls = order_builder.build_order.call_args_list
        p1_ctx = calls[0][0][1]
        p2_ctx = calls[1][0][1]

        assert "p1_result_filename" in p2_ctx
        expected = f"{p1_ctx['ts']}-claude-result-{p1_ctx['result_uuid']}.md"
        assert p2_ctx["p1_result_filename"] == expected

    def test_three_steps_chain(self):
        """p1→p2→p3 チェーンで 3 exec 行生成、依存が正しく解決される。"""
        api, pipeline_state, _ = _make_api()
        steps = [
            StepConfig(id="p1", template="p1", model="claude-sonnet-4-6"),
            StepConfig(id="p2", template="p2", model="claude-sonnet-4-6", depends=["p1"]),
            StepConfig(id="p3", template="p3", model="claude-sonnet-4-6", depends=["p2"]),
        ]
        exec_lines = api.submit(steps, {})

        assert len(exec_lines) == 3
        p1_uuid = exec_lines[0].split(":")[0]
        p2_uuid = exec_lines[1].split("[")[0]
        assert f"[depends:{p1_uuid}]" in exec_lines[1]
        assert f"[depends:{p2_uuid}]" in exec_lines[2]


# ---------------------------------------------------------------------------
# AC4: エンジン名が result_filename / exec 行に反映される
# ---------------------------------------------------------------------------


class TestAC4EngineResultFilename:
    def test_claude_engine_result_filename(self):
        """engine=claude のとき result_filename が {ts}-claude-result-{uuid}.md。"""
        api, _, order_builder = _make_api()
        steps = [StepConfig(id="p1", template="p1", model="claude-sonnet-4-6", agent="claude")]
        api.submit(steps, {})

        ctx = order_builder.build_order.call_args[0][1]
        assert ctx["result_filename"].startswith(ctx["ts"] + "-claude-result-")

    def test_gemini_engine_result_filename(self):
        """engine=gemini のとき result_filename が {ts}-gemini-result-{uuid}.md。"""
        api, _, order_builder = _make_api()
        steps = [StepConfig(id="p1", template="p1", model="gemini-2.5-flash", agent="gemini")]
        api.submit(steps, {})

        ctx = order_builder.build_order.call_args[0][1]
        assert ctx["result_filename"].startswith(ctx["ts"] + "-gemini-result-")

    def test_dep_result_filename_reflects_dep_engine(self):
        """p1(gemini)→p2: p1_result_filename が {ts}-gemini-result-{uuid}.md。"""
        api, _, order_builder = _make_api()
        steps = [
            StepConfig(id="p1", template="p1", model="gemini-2.5-flash", agent="gemini"),
            StepConfig(id="p2", template="p2", model="claude-sonnet-4-6", agent="claude", depends=["p1"]),
        ]
        api.submit(steps, {})

        calls = order_builder.build_order.call_args_list
        p1_ctx = calls[0][0][1]
        p2_ctx = calls[1][0][1]

        assert "p1_result_filename" in p2_ctx
        expected = f"{p1_ctx['ts']}-gemini-result-{p1_ctx['result_uuid']}.md"
        assert p2_ctx["p1_result_filename"] == expected

    def test_gemini_engine_exec_line_no_skip_permissions(self):
        """engine=gemini のとき exec 行に --dangerously-skip-permissions が含まれない。"""
        api, _, _ = _make_api()
        steps = [StepConfig(template="p1", model="gemini-2.5-flash", agent="gemini")]
        exec_lines = api.submit(steps, {})

        assert "--dangerously-skip-permissions" not in exec_lines[0]
        assert "gemini" in exec_lines[0]


# ---------------------------------------------------------------------------
# AC3: DagEngine 互換フォーマット
# ---------------------------------------------------------------------------


class TestAC3ExecFormat:
    def test_uuid_at_start_of_exec_line(self):
        """exec 行が UUID で始まる（36文字のハイフン区切り形式）。"""
        import re
        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        exec_lines = api.submit(steps, {})

        uuid_pattern = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
        assert re.match(uuid_pattern, exec_lines[0])

    def test_depends_format(self):
        """depends の書式が [depends:{uuid1},{uuid2}] 形式。"""
        import re
        api, _, _ = _make_api()
        steps = [
            StepConfig(id="p1", template="p1", model="claude-sonnet-4-6"),
            StepConfig(id="p2", template="p2", model="claude-sonnet-4-6", depends=["p1"]),
        ]
        exec_lines = api.submit(steps, {})

        dep_pattern = r"\[depends:[0-9a-f\-]+\]"
        assert re.search(dep_pattern, exec_lines[1])

    def test_tee_appends_to_result_file(self):
        """exec 行に tee -a queue/{result} が含まれる。"""
        api, pipeline_state, _ = _make_api()
        pipeline_state.write_order_file.return_value = "20260419-claude-order-abc.md"
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        exec_lines = api.submit(steps, {})

        assert "tee -a queue/" in exec_lines[0]
        assert "-result-" in exec_lines[0]


# ---------------------------------------------------------------------------
# base_context が各ステップ context に引き継がれる
# ---------------------------------------------------------------------------


class TestBaseContextPropagation:
    def test_base_context_keys_in_step_context(self):
        """base_context の値が build_order に渡される context に含まれる。"""
        api, _, order_builder = _make_api()
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        api.submit(steps, {"issue_number": "42", "workflow_name": "test"})

        ctx = order_builder.build_order.call_args[0][1]
        assert ctx["issue_number"] == "42"
        assert ctx["workflow_name"] == "test"

    def test_step_specific_keys_added(self):
        """ts, order_uuid, result_uuid, result_filename が context に含まれる。"""
        api, _, order_builder = _make_api()
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        api.submit(steps, {})

        ctx = order_builder.build_order.call_args[0][1]
        assert "ts" in ctx
        assert "order_uuid" in ctx
        assert "result_uuid" in ctx
        assert "result_filename" in ctx

    def test_base_context_not_mutated(self):
        """submit() が base_context 辞書を直接変更しない。"""
        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        base = {"issue_number": "10"}
        original_keys = set(base.keys())
        api.submit(steps, base)

        assert set(base.keys()) == original_keys
