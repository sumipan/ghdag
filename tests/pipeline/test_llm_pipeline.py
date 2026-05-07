"""Tests for ghdag.pipeline.llm_pipeline — LLMPipelineAPI (Issue #203)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ghdag.pipeline.llm_pipeline import LLMPipelineAPI
from ghdag.workflow.schema import StepConfig


def _make_api(
    queue_dir: str = "queue",
    *,
    jsonl_mode: bool = False,
) -> tuple[LLMPipelineAPI, MagicMock, MagicMock]:
    """LLMPipelineAPI with mocked PipelineState and OrderBuilder."""
    pipeline_state = MagicMock()
    pipeline_state.check_idempotency.return_value = True
    pipeline_state.write_order_file.return_value = "ts-claude-order-uuid.md"
    pipeline_state._is_jsonl_mode = jsonl_mode
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
        steps = [StepConfig(id="p1", template="p1", model="claude-sonnet-4-6", engine="claude")]
        api.submit(steps, {})

        ctx = order_builder.build_order.call_args[0][1]
        assert ctx["result_filename"].startswith(ctx["ts"] + "-claude-result-")

    def test_gemini_engine_result_filename(self):
        """engine=gemini のとき result_filename が {ts}-gemini-result-{uuid}.md。"""
        api, _, order_builder = _make_api()
        steps = [StepConfig(id="p1", template="p1", model="gemini-2.5-flash", engine="gemini")]
        api.submit(steps, {})

        ctx = order_builder.build_order.call_args[0][1]
        assert ctx["result_filename"].startswith(ctx["ts"] + "-gemini-result-")

    def test_dep_result_filename_reflects_dep_engine(self):
        """p1(gemini)→p2: p1_result_filename が {ts}-gemini-result-{uuid}.md。"""
        api, _, order_builder = _make_api()
        steps = [
            StepConfig(id="p1", template="p1", model="gemini-2.5-flash", engine="gemini"),
            StepConfig(id="p2", template="p2", model="claude-sonnet-4-6", engine="claude", depends=["p1"]),
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
        steps = [StepConfig(template="p1", model="gemini-2.5-flash", engine="gemini")]
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


# ---------------------------------------------------------------------------
# Regression: per-workflow OrderBuilder 切り替え
# テンプレート解決がワークフロー横断で混線する不具合（issue #610 で観測された
# `FileNotFoundError: workflows/inkwell/brushup.md` の経路）の再発防止。
# ---------------------------------------------------------------------------


class TestPerWorkflowOrderBuilder:
    def test_order_builder_resolved_by_workflow_name(self):
        """base_context['workflow_name'] に対応する OrderBuilder が使われる。"""
        from unittest.mock import MagicMock

        from ghdag.pipeline.llm_pipeline import LLMPipelineAPI

        pipeline_state = MagicMock()
        pipeline_state.check_idempotency.return_value = True
        pipeline_state.write_order_file.return_value = "ts-claude-order-uuid.md"
        pipeline_state._is_jsonl_mode = False

        default_builder = MagicMock()
        default_builder.build_order.return_value = "default order content"
        inkwell_builder = MagicMock()
        inkwell_builder.build_order.return_value = "inkwell order content"
        issuesmith_builder = MagicMock()
        issuesmith_builder.build_order.return_value = "issuesmith order content"

        api = LLMPipelineAPI(
            pipeline_state=pipeline_state,
            order_builder=default_builder,
            queue_dir="queue",
            order_builders={
                "inkwell": inkwell_builder,
                "issuesmith": issuesmith_builder,
            },
        )

        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]

        # issuesmith の workflow_name で submit → issuesmith_builder が呼ばれる
        api.submit(steps, {"workflow_name": "issuesmith", "issue_number": "610"})
        issuesmith_builder.build_order.assert_called_once()
        inkwell_builder.build_order.assert_not_called()
        default_builder.build_order.assert_not_called()

        # 別ワークフロー名で再 submit → 該当 builder が呼ばれる
        api.submit(steps, {"workflow_name": "inkwell"})
        inkwell_builder.build_order.assert_called_once()

    def test_falls_back_to_default_builder_when_workflow_unknown(self):
        """order_builders に該当エントリがない場合はデフォルトに落ちる。"""
        from unittest.mock import MagicMock

        from ghdag.pipeline.llm_pipeline import LLMPipelineAPI

        pipeline_state = MagicMock()
        pipeline_state.check_idempotency.return_value = True
        pipeline_state.write_order_file.return_value = "ts-claude-order-uuid.md"
        pipeline_state._is_jsonl_mode = False

        default_builder = MagicMock()
        default_builder.build_order.return_value = "default order"
        inkwell_builder = MagicMock()
        inkwell_builder.build_order.return_value = "inkwell order"

        api = LLMPipelineAPI(
            pipeline_state=pipeline_state,
            order_builder=default_builder,
            queue_dir="queue",
            order_builders={"inkwell": inkwell_builder},
        )

        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        api.submit(steps, {"workflow_name": "research"})  # 未登録
        default_builder.build_order.assert_called_once()
        inkwell_builder.build_order.assert_not_called()

    def test_falls_back_to_default_when_workflow_name_missing(self):
        """base_context に workflow_name が無くてもデフォルトで動く（後方互換）。"""
        from unittest.mock import MagicMock

        from ghdag.pipeline.llm_pipeline import LLMPipelineAPI

        pipeline_state = MagicMock()
        pipeline_state.check_idempotency.return_value = True
        pipeline_state.write_order_file.return_value = "ts-claude-order-uuid.md"
        pipeline_state._is_jsonl_mode = False

        default_builder = MagicMock()
        default_builder.build_order.return_value = "default order"

        api = LLMPipelineAPI(
            pipeline_state=pipeline_state,
            order_builder=default_builder,
            queue_dir="queue",
            order_builders={"inkwell": MagicMock()},
        )

        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        api.submit(steps, {})  # workflow_name キーなし
        default_builder.build_order.assert_called_once()

    def test_backward_compat_no_order_builders_kwarg(self):
        """order_builders を渡さない既存の呼び出し方式は引き続き動作する。"""
        from unittest.mock import MagicMock

        from ghdag.pipeline.llm_pipeline import LLMPipelineAPI

        pipeline_state = MagicMock()
        pipeline_state.check_idempotency.return_value = True
        pipeline_state.write_order_file.return_value = "ts-claude-order-uuid.md"
        pipeline_state._is_jsonl_mode = False

        order_builder = MagicMock()
        order_builder.build_order.return_value = "order content"

        api = LLMPipelineAPI(
            pipeline_state=pipeline_state,
            order_builder=order_builder,
            queue_dir="queue",
        )

        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        api.submit(steps, {"workflow_name": "issuesmith"})  # 何が来ても default に落ちる
        order_builder.build_order.assert_called_once()


# ---------------------------------------------------------------------------
# AC3: depends 事前検証 (Issue #766)
# ---------------------------------------------------------------------------


class TestAC3DependsValidation:
    def test_unknown_dependency_raises_value_error(self):
        """未定義の depends id → ValueError('Unknown dependency: ...')"""
        api, pipeline_state, _ = _make_api()
        steps = [
            StepConfig(id="step_a", template="t", model="m", depends=["nonexistent_step"]),
        ]
        with pytest.raises(ValueError, match="nonexistent_step"):
            api.submit(steps, {})

    def test_circular_dependency_a_b_a_raises_value_error(self):
        """A→B→A の循環参照 → ValueError('circular dependency' を含む)"""
        api, _, _ = _make_api()
        steps = [
            StepConfig(id="a", template="t", model="m", depends=["b"]),
            StepConfig(id="b", template="t", model="m", depends=["a"]),
        ]
        with pytest.raises(ValueError, match="circular dependency"):
            api.submit(steps, {})

    def test_valid_linear_dependency_passes(self):
        """A→B→C の正常な直列依存 → 検証通過、3 exec 行生成"""
        api, pipeline_state, _ = _make_api()
        steps = [
            StepConfig(id="a", template="t", model="m"),
            StepConfig(id="b", template="t", model="m", depends=["a"]),
            StepConfig(id="c", template="t", model="m", depends=["b"]),
        ]
        exec_lines = api.submit(steps, {})
        assert len(exec_lines) == 3
        pipeline_state.append_exec.assert_called_once()

    def test_validation_error_no_order_file_written(self):
        """検証エラー時、order ファイルも exec.md への追記も行われない"""
        api, pipeline_state, _ = _make_api()
        steps = [
            StepConfig(id="a", template="t", model="m", depends=["nonexistent"]),
        ]
        with pytest.raises(ValueError):
            api.submit(steps, {})
        pipeline_state.write_order_file.assert_not_called()
        pipeline_state.append_exec.assert_not_called()

    def test_validation_error_circular_no_files_written(self):
        """循環参照エラー時も order ファイルと exec.md への追記が行われない"""
        api, pipeline_state, _ = _make_api()
        steps = [
            StepConfig(id="x", template="t", model="m", depends=["y"]),
            StepConfig(id="y", template="t", model="m", depends=["x"]),
        ]
        with pytest.raises(ValueError):
            api.submit(steps, {})
        pipeline_state.write_order_file.assert_not_called()
        pipeline_state.append_exec.assert_not_called()


# ---------------------------------------------------------------------------
# JSONL mode: exec.jsonl への JSON レコード書き込み
# ---------------------------------------------------------------------------


class TestJsonlMode:
    def test_jsonl_mode_calls_append_exec_records(self):
        """JSONL モードでは append_exec_records が呼ばれ append_exec は呼ばれない。"""
        api, pipeline_state, _ = _make_api(jsonl_mode=True)
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        exec_lines = api.submit(steps, {})

        pipeline_state.append_exec_records.assert_called_once()
        pipeline_state.append_exec.assert_not_called()
        assert len(exec_lines) == 1

    def test_jsonl_mode_returns_json_strings(self):
        """JSONL モードの exec_lines は JSON 文字列（uuid フィールドを含む）。"""
        import json as _json

        api, _, _ = _make_api(jsonl_mode=True)
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        exec_lines = api.submit(steps, {})

        record = _json.loads(exec_lines[0])
        assert "uuid" in record
        assert "command" in record
        assert "result_path" in record

    def test_jsonl_mode_idempotency_in_record(self):
        """JSONL モードでは idempotency_key がレコードに埋め込まれ、コメント行は生成されない。"""
        import json as _json

        api, pipeline_state, _ = _make_api(jsonl_mode=True)
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        exec_lines = api.submit(steps, {}, idempotency_key="workflow:handler:42")

        assert len(exec_lines) == 1
        record = _json.loads(exec_lines[0])
        assert record.get("idempotency_key") == "workflow:handler:42"

    def test_jsonl_mode_result_path_in_record(self):
        """JSONL モードの result_path フィールドが queue_dir/filename 形式。"""
        import json as _json

        api, _, _ = _make_api(queue_dir="jobs", jsonl_mode=True)
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        exec_lines = api.submit(steps, {})

        record = _json.loads(exec_lines[0])
        assert record["result_path"].startswith("jobs/")
