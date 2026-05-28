"""Tests for ghdag.pipeline.llm_pipeline — LLMPipelineAPI (Issue #203)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from ghdag.pipeline.audit import AuditContext
from ghdag.pipeline.llm_pipeline import DependencyError, LLMPipelineAPI
from ghdag.workflow.schema import StepConfig

_TEST_AUDIT_CTX = AuditContext(source="test")


def _make_api(
    queue_dir: str = "queue",
) -> tuple[LLMPipelineAPI, MagicMock, MagicMock]:
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
    def test_submit_single_step_returns_exec_records(self):
        """1 step で exec_records 1 レコード（JSONL 形式）。"""
        import json as _json
        api, pipeline_state, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        exec_lines = api.submit(steps, {"issue_number": "10"}, audit_context=_TEST_AUDIT_CTX)

        assert len(exec_lines) == 1
        # JSON parse succeeds
        record = _json.loads(exec_lines[0])
        assert "uuid" in record
        assert "command" in record
        pipeline_state.append_exec_records.assert_called_once()

    def test_submit_writes_order_file(self):
        """write_order_file が 1 回呼ばれる。"""
        api, pipeline_state, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        pipeline_state.write_order_file.assert_called_once()

    def test_submit_calls_build_order(self):
        """build_order が 1 回呼ばれ、template 名が渡される。"""
        api, _, order_builder = _make_api()
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        order_builder.build_order.assert_called_once()
        call_args = order_builder.build_order.call_args[0]
        assert call_args[0] == "brushup"

    def test_exec_record_contains_model(self):
        """exec レコードの command にモデル名が含まれる。"""
        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        assert "claude-opus-4-6" in exec_lines[0]

    def test_exec_record_contains_dangerously_skip_permissions(self):
        """exec レコードの command に --dangerously-skip-permissions が含まれる（engine=claude）。"""
        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        assert "--dangerously-skip-permissions" in exec_lines[0]


# ---------------------------------------------------------------------------
# AC1-2 / AC1-3: idempotency_key
# ---------------------------------------------------------------------------


class TestAC1IdempotencyKey:
    def test_no_idempotency_key_no_comment(self):
        """idempotency_key なしのとき先頭コメント行なし。"""
        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        assert not exec_lines[0].startswith("# idempotency:")


# ---------------------------------------------------------------------------
# AC1-2 / AC4: 2 steps with depends
# ---------------------------------------------------------------------------


class TestAC1TwoStepsWithDepends:
    def test_p2_has_depends_p1_uuid(self):
        """p2 の exec レコードの depends に p1_uuid が含まれる。"""
        import json as _json
        api, _, _ = _make_api()
        steps = [
            StepConfig(id="p1", template="p1", model="claude-sonnet-4-6"),
            StepConfig(id="p2", template="p2", model="claude-sonnet-4-6", depends=["p1"]),
        ]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        p1_record = _json.loads(exec_lines[0])
        p2_record = _json.loads(exec_lines[1])
        assert p1_record["uuid"] in p2_record["depends"]

    def test_p2_context_has_p1_result_filename(self):
        """P2 の build_order に渡される context に p1_result_filename が含まれる。"""
        api, _, order_builder = _make_api()
        steps = [
            StepConfig(id="p1", template="p1", model="claude-sonnet-4-6"),
            StepConfig(id="p2", template="p2", model="claude-sonnet-4-6", depends=["p1"]),
        ]
        api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        calls = order_builder.build_order.call_args_list
        p1_ctx = calls[0][0][1]
        p2_ctx = calls[1][0][1]

        assert "p1_result_filename" in p2_ctx
        expected = f"{p1_ctx['ts']}-claude-result-{p1_ctx['result_uuid']}.md"
        assert p2_ctx["p1_result_filename"] == expected

    def test_three_steps_chain(self):
        """p1→p2→p3 チェーンで 3 exec レコード生成、依存が正しく解決される。"""
        import json as _json
        api, pipeline_state, _ = _make_api()
        steps = [
            StepConfig(id="p1", template="p1", model="claude-sonnet-4-6"),
            StepConfig(id="p2", template="p2", model="claude-sonnet-4-6", depends=["p1"]),
            StepConfig(id="p3", template="p3", model="claude-sonnet-4-6", depends=["p2"]),
        ]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        assert len(exec_lines) == 3
        p1 = _json.loads(exec_lines[0])
        p2 = _json.loads(exec_lines[1])
        p3 = _json.loads(exec_lines[2])
        assert p1["uuid"] in p2["depends"]
        assert p2["uuid"] in p3["depends"]


# ---------------------------------------------------------------------------
# AC4: エンジン名が result_filename / exec 行に反映される
# ---------------------------------------------------------------------------


class TestAC4EngineResultFilename:
    def test_claude_engine_result_filename(self):
        """engine=claude のとき result_filename が {ts}-claude-result-{uuid}.md。"""
        api, _, order_builder = _make_api()
        steps = [StepConfig(id="p1", template="p1", model="claude-sonnet-4-6", engine="claude")]
        api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        ctx = order_builder.build_order.call_args[0][1]
        assert ctx["result_filename"].startswith(ctx["ts"] + "-claude-result-")

    def test_gemini_engine_result_filename(self):
        """engine=gemini のとき result_filename が {ts}-gemini-result-{uuid}.md。"""
        api, _, order_builder = _make_api()
        steps = [StepConfig(id="p1", template="p1", model="gemini-2.5-flash", engine="gemini")]
        api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        ctx = order_builder.build_order.call_args[0][1]
        assert ctx["result_filename"].startswith(ctx["ts"] + "-gemini-result-")

    def test_dep_result_filename_reflects_dep_engine(self):
        """p1(gemini)→p2: p1_result_filename が {ts}-gemini-result-{uuid}.md。"""
        api, _, order_builder = _make_api()
        steps = [
            StepConfig(id="p1", template="p1", model="gemini-2.5-flash", engine="gemini"),
            StepConfig(id="p2", template="p2", model="claude-sonnet-4-6", engine="claude", depends=["p1"]),
        ]
        api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        calls = order_builder.build_order.call_args_list
        p1_ctx = calls[0][0][1]
        p2_ctx = calls[1][0][1]

        assert "p1_result_filename" in p2_ctx
        expected = f"{p1_ctx['ts']}-gemini-result-{p1_ctx['result_uuid']}.md"
        assert p2_ctx["p1_result_filename"] == expected

    def test_gemini_engine_exec_record_no_skip_permissions(self):
        """engine=gemini のとき exec レコードの command に --dangerously-skip-permissions が含まれない。"""
        import json as _json
        api, _, _ = _make_api()
        steps = [StepConfig(template="p1", model="gemini-2.5-flash", engine="gemini")]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        record = _json.loads(exec_lines[0])
        assert "--dangerously-skip-permissions" not in record["command"]
        assert "gemini" in record["command"]


# ---------------------------------------------------------------------------
# AC3: DagEngine 互換フォーマット
# ---------------------------------------------------------------------------


class TestAC3ExecFormat:
    def test_uuid_field_in_exec_record(self):
        """exec レコードの uuid フィールドが UUID 形式（36文字のハイフン区切り）。"""
        import json as _json
        import re
        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        record = _json.loads(exec_lines[0])
        uuid_pattern = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
        assert re.match(uuid_pattern, record["uuid"])

    def test_depends_field_in_exec_record(self):
        """exec レコードの depends フィールドに p1 の uuid が含まれる。"""
        import json as _json
        api, _, _ = _make_api()
        steps = [
            StepConfig(id="p1", template="p1", model="claude-sonnet-4-6"),
            StepConfig(id="p2", template="p2", model="claude-sonnet-4-6", depends=["p1"]),
        ]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        p1_record = _json.loads(exec_lines[0])
        p2_record = _json.loads(exec_lines[1])
        assert p1_record["uuid"] in p2_record["depends"]

    def test_result_path_in_exec_record(self):
        """exec レコードに result_path フィールドが含まれる。"""
        import json as _json
        api, pipeline_state, _ = _make_api()
        pipeline_state.write_order_file.return_value = "20260419-claude-order-abc.md"
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        record = _json.loads(exec_lines[0])
        assert "result_path" in record
        assert "-result-" in record["result_path"]


# ---------------------------------------------------------------------------
# base_context が各ステップ context に引き継がれる
# ---------------------------------------------------------------------------


class TestBaseContextPropagation:
    def test_base_context_keys_in_step_context(self):
        """base_context の値が build_order に渡される context に含まれる。"""
        api, _, order_builder = _make_api()
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        api.submit(steps, {"issue_number": "42", "workflow_name": "test"}, audit_context=_TEST_AUDIT_CTX)

        ctx = order_builder.build_order.call_args[0][1]
        assert ctx["issue_number"] == "42"
        assert ctx["workflow_name"] == "test"

    def test_step_specific_keys_added(self):
        """ts, order_uuid, result_uuid, result_filename が context に含まれる。"""
        api, _, order_builder = _make_api()
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

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
        api.submit(steps, base, audit_context=_TEST_AUDIT_CTX)

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
        api.submit(steps, {"workflow_name": "issuesmith", "issue_number": "610"}, audit_context=_TEST_AUDIT_CTX)
        issuesmith_builder.build_order.assert_called_once()
        inkwell_builder.build_order.assert_not_called()
        default_builder.build_order.assert_not_called()

        # 別ワークフロー名で再 submit → 該当 builder が呼ばれる
        api.submit(steps, {"workflow_name": "inkwell"}, audit_context=_TEST_AUDIT_CTX)
        inkwell_builder.build_order.assert_called_once()

    def test_falls_back_to_default_builder_when_workflow_unknown(self):
        """order_builders に該当エントリがない場合はデフォルトに落ちる。"""
        from unittest.mock import MagicMock

        from ghdag.pipeline.llm_pipeline import LLMPipelineAPI

        pipeline_state = MagicMock()
        pipeline_state.check_idempotency.return_value = True
        pipeline_state.write_order_file.return_value = "ts-claude-order-uuid.md"

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
        api.submit(steps, {"workflow_name": "research"}, audit_context=_TEST_AUDIT_CTX)  # 未登録
        default_builder.build_order.assert_called_once()
        inkwell_builder.build_order.assert_not_called()

    def test_falls_back_to_default_when_workflow_name_missing(self):
        """base_context に workflow_name が無くてもデフォルトで動く（後方互換）。"""
        from unittest.mock import MagicMock

        from ghdag.pipeline.llm_pipeline import LLMPipelineAPI

        pipeline_state = MagicMock()
        pipeline_state.check_idempotency.return_value = True
        pipeline_state.write_order_file.return_value = "ts-claude-order-uuid.md"

        default_builder = MagicMock()
        default_builder.build_order.return_value = "default order"

        api = LLMPipelineAPI(
            pipeline_state=pipeline_state,
            order_builder=default_builder,
            queue_dir="queue",
            order_builders={"inkwell": MagicMock()},
        )

        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)  # workflow_name キーなし
        default_builder.build_order.assert_called_once()

    def test_backward_compat_no_order_builders_kwarg(self):
        """order_builders を渡さない既存の呼び出し方式は引き続き動作する。"""
        from unittest.mock import MagicMock

        from ghdag.pipeline.llm_pipeline import LLMPipelineAPI

        pipeline_state = MagicMock()
        pipeline_state.check_idempotency.return_value = True
        pipeline_state.write_order_file.return_value = "ts-claude-order-uuid.md"

        order_builder = MagicMock()
        order_builder.build_order.return_value = "order content"

        api = LLMPipelineAPI(
            pipeline_state=pipeline_state,
            order_builder=order_builder,
            queue_dir="queue",
        )

        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        api.submit(steps, {"workflow_name": "issuesmith"}, audit_context=_TEST_AUDIT_CTX)  # 何が来ても default に落ちる
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
        with pytest.raises(DependencyError, match="nonexistent_step"):
            api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

    def test_circular_dependency_a_b_a_raises_value_error(self):
        """A→B→A の循環参照 → ValueError('circular dependency' を含む)"""
        api, _, _ = _make_api()
        steps = [
            StepConfig(id="a", template="t", model="m", depends=["b"]),
            StepConfig(id="b", template="t", model="m", depends=["a"]),
        ]
        with pytest.raises(DependencyError, match="circular dependency"):
            api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

    def test_valid_linear_dependency_passes(self):
        """A→B→C の正常な直列依存 → 検証通過、3 exec 行生成"""
        api, pipeline_state, _ = _make_api()
        steps = [
            StepConfig(id="a", template="t", model="m"),
            StepConfig(id="b", template="t", model="m", depends=["a"]),
            StepConfig(id="c", template="t", model="m", depends=["b"]),
        ]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)
        assert len(exec_lines) == 3
        pipeline_state.append_exec_records.assert_called_once()

    def test_validation_error_no_order_file_written(self):
        """検証エラー時、order ファイルも exec.jsonl への追記も行われない"""
        api, pipeline_state, _ = _make_api()
        steps = [
            StepConfig(id="a", template="t", model="m", depends=["nonexistent"]),
        ]
        with pytest.raises(DependencyError):
            api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)
        pipeline_state.write_order_file.assert_not_called()
        pipeline_state.append_exec_records.assert_not_called()

    def test_validation_error_circular_no_files_written(self):
        """循環参照エラー時も order ファイルと exec.jsonl への追記が行われない"""
        api, pipeline_state, _ = _make_api()
        steps = [
            StepConfig(id="x", template="t", model="m", depends=["y"]),
            StepConfig(id="y", template="t", model="m", depends=["x"]),
        ]
        with pytest.raises(DependencyError):
            api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)
        pipeline_state.write_order_file.assert_not_called()
        pipeline_state.append_exec_records.assert_not_called()


# ---------------------------------------------------------------------------
# JSONL mode: exec.jsonl への JSON レコード書き込み
# ---------------------------------------------------------------------------


class TestJsonlMode:
    def test_calls_append_exec_records(self):
        """append_exec_records が呼ばれる。"""
        api, pipeline_state, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        pipeline_state.append_exec_records.assert_called_once()
        assert len(exec_lines) == 1

    def test_returns_json_strings(self):
        """exec_lines は JSON 文字列（uuid フィールドを含む）。"""
        import json as _json

        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        record = _json.loads(exec_lines[0])
        assert "uuid" in record
        assert "command" in record
        assert "result_path" in record

    def test_idempotency_in_record(self):
        """idempotency_key がレコードに埋め込まれ、コメント行は生成されない。"""
        import json as _json

        api, pipeline_state, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        exec_lines = api.submit(steps, {}, idempotency_key="workflow:handler:42", audit_context=_TEST_AUDIT_CTX)

        assert len(exec_lines) == 1
        record = _json.loads(exec_lines[0])
        assert record.get("idempotency_key") == "workflow:handler:42"

    def test_result_path_in_record(self):
        """result_path フィールドが queue_dir/filename 形式。"""
        import json as _json

        api, _, _ = _make_api(queue_dir="jobs")
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        record = _json.loads(exec_lines[0])
        assert record["result_path"].startswith("jobs/")

    def test_no_comment_idempotency_line(self):
        """exec_lines に # idempotency: コメント行が含まれない。"""
        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        exec_lines = api.submit(steps, {}, idempotency_key="scheduler:diary_review:ts", audit_context=_TEST_AUDIT_CTX)

        for line in exec_lines:
            assert not line.startswith("#"), f"comment line found: {line!r}"

    def test_cursor_engine_valid_json(self):
        """cursor engine の exec レコードが valid JSON で command に agent が含まれる。"""
        import json as _json

        api, _, _ = _make_api()
        steps = [StepConfig(template="skill", model="auto", engine="cursor")]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        record = _json.loads(exec_lines[0])
        assert "uuid" in record
        assert "agent" in record["command"]

    def test_all_exec_lines_are_parseable_json(self):
        """複数ステップを submit したとき、全 exec_lines が JSON パース可能。"""
        import json as _json

        api, _, _ = _make_api()
        steps = [
            StepConfig(id="s1", template="t1", model="m1"),
            StepConfig(id="s2", template="t2", model="m2", depends=["s1"]),
        ]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        assert len(exec_lines) == 2
        for line in exec_lines:
            record = _json.loads(line)  # must not raise
            assert "uuid" in record
            assert "command" in record

    def test_scheduler_idempotency_key_format(self):
        """スケジューラー形式 (scheduler:job_id:ISO8601) の idempotency_key が正常に埋め込まれる。"""
        import json as _json

        api, _, _ = _make_api()
        key = "scheduler:diary_review:2026-05-08T23:00:00.123456+09:00"
        steps = [StepConfig(template="diary-review", model="auto", engine="cursor")]
        exec_lines = api.submit(steps, {"workflow_name": "scheduler"}, idempotency_key=key, audit_context=_TEST_AUDIT_CTX)

        record = _json.loads(exec_lines[0])
        assert record["idempotency_key"] == key
        assert "# idempotency:" not in exec_lines[0]


# ---------------------------------------------------------------------------
# Issue #984: audit_context 必須化テスト (AC-1, AC-5 置換)
# ---------------------------------------------------------------------------


class TestAuditContextPropagation:
    def test_ac5_submit_without_audit_context_raises_type_error(self):
        """AC-5 (必須化): audit_context を省略した submit() が TypeError を送出する。"""
        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        with pytest.raises(TypeError):
            api.submit(steps, {"issue_number": "10"}, idempotency_key="wf:h:10")

    def test_ac1_audit_context_passed_to_append_exec_records(self):
        """AC-1: audit_context が append_exec_records に中継される。"""
        from ghdag.pipeline.audit import AuditContext

        api, pipeline_state, _ = _make_api()
        ctx = AuditContext(source="issuesmith", correlation_id="test:key")
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        api.submit(steps, {}, audit_context=ctx)

        call_kwargs = pipeline_state.append_exec_records.call_args
        passed_ctx = call_kwargs[1].get("audit_context") or (
            call_kwargs[0][1] if len(call_kwargs[0]) > 1 else None
        )
        assert passed_ctx is ctx


# ---------------------------------------------------------------------------
# Issue #1014: result content injection into context (AC1-AC4)
# ---------------------------------------------------------------------------

_P1_UUID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_P2_UUID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
_P3_UUID = "cccccccc-cccc-cccc-cccc-cccccccccccc"
_TS = "20260523120000"


def _make_api_with_tmpdir(tmp_path):
    """LLMPipelineAPI that uses a real tmp_path as queue_dir."""
    pipeline_state = MagicMock()
    pipeline_state.check_idempotency.return_value = True
    pipeline_state.write_order_file.return_value = "ts-claude-order-uuid.md"
    order_builder = MagicMock()
    order_builder.build_order.return_value = "order content"
    api = LLMPipelineAPI(
        pipeline_state=pipeline_state,
        order_builder=order_builder,
        queue_dir=str(tmp_path),
    )
    return api, pipeline_state, order_builder


class TestResultContentInjection:
    """Issue #1014: ${dep_id_result_content} コンテキスト注入。"""

    def test_ac1_result_content_injected_when_file_exists(self, tmp_path):
        """AC1: result ファイル存在時、p1_result_content が p2 コンテキストに注入される。"""
        result_content = "# Analysis Result\nScore: 85"
        result_file = tmp_path / f"{_TS}-claude-result-{_P1_UUID}.md"
        result_file.write_text(result_content, encoding="utf-8")

        api, _, order_builder = _make_api_with_tmpdir(tmp_path)
        steps = [
            StepConfig(id="p1", template="p1", model="claude-sonnet-4-6"),
            StepConfig(id="p2", template="p2", model="claude-sonnet-4-6", depends=["p1"]),
        ]
        with patch("ghdag.pipeline.llm_pipeline.uuid.uuid4", side_effect=[_P1_UUID, _P2_UUID]), \
             patch("ghdag.pipeline.llm_pipeline.datetime") as mock_dt:
            mock_dt.now.return_value.strftime.return_value = _TS
            api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        p2_ctx = order_builder.build_order.call_args_list[1][0][1]
        assert p2_ctx["p1_result_content"] == result_content

    def test_ac2_empty_string_when_result_file_missing(self, tmp_path):
        """AC2: result ファイル未存在時、p1_result_content が空文字列になる（エラーなし）。"""
        api, _, order_builder = _make_api_with_tmpdir(tmp_path)
        steps = [
            StepConfig(id="p1", template="p1", model="claude-sonnet-4-6"),
            StepConfig(id="p2", template="p2", model="claude-sonnet-4-6", depends=["p1"]),
        ]
        with patch("ghdag.pipeline.llm_pipeline.uuid.uuid4", side_effect=[_P1_UUID, _P2_UUID]), \
             patch("ghdag.pipeline.llm_pipeline.datetime") as mock_dt:
            mock_dt.now.return_value.strftime.return_value = _TS
            api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        p2_ctx = order_builder.build_order.call_args_list[1][0][1]
        assert p2_ctx["p1_result_content"] == ""

    def test_ac3_result_filename_and_content_coexist(self, tmp_path):
        """AC3: p1_result_filename と p1_result_content が両方正しく展開される。"""
        result_content = "summary output"
        result_file = tmp_path / f"{_TS}-claude-result-{_P1_UUID}.md"
        result_file.write_text(result_content, encoding="utf-8")

        api, _, order_builder = _make_api_with_tmpdir(tmp_path)
        steps = [
            StepConfig(id="p1", template="p1", model="claude-sonnet-4-6"),
            StepConfig(id="p2", template="p2", model="claude-sonnet-4-6", depends=["p1"]),
        ]
        with patch("ghdag.pipeline.llm_pipeline.uuid.uuid4", side_effect=[_P1_UUID, _P2_UUID]), \
             patch("ghdag.pipeline.llm_pipeline.datetime") as mock_dt:
            mock_dt.now.return_value.strftime.return_value = _TS
            api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        p2_ctx = order_builder.build_order.call_args_list[1][0][1]
        assert p2_ctx["p1_result_filename"] == f"{_TS}-claude-result-{_P1_UUID}.md"
        assert p2_ctx["p1_result_content"] == result_content

    def test_ac4_multiple_dep_contents_injected(self, tmp_path):
        """AC4: p3 が p1・p2 両方に依存 → 両方の result_content が注入される。"""
        content_p1 = "p1 result"
        content_p2 = "p2 result"
        (tmp_path / f"{_TS}-claude-result-{_P1_UUID}.md").write_text(content_p1, encoding="utf-8")
        (tmp_path / f"{_TS}-claude-result-{_P2_UUID}.md").write_text(content_p2, encoding="utf-8")

        api, _, order_builder = _make_api_with_tmpdir(tmp_path)
        steps = [
            StepConfig(id="p1", template="p1", model="claude-sonnet-4-6"),
            StepConfig(id="p2", template="p2", model="claude-sonnet-4-6"),
            StepConfig(id="p3", template="p3", model="claude-sonnet-4-6", depends=["p1", "p2"]),
        ]
        with patch("ghdag.pipeline.llm_pipeline.uuid.uuid4", side_effect=[_P1_UUID, _P2_UUID, _P3_UUID]), \
             patch("ghdag.pipeline.llm_pipeline.datetime") as mock_dt:
            mock_dt.now.return_value.strftime.return_value = _TS
            api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        p3_ctx = order_builder.build_order.call_args_list[2][0][1]
        assert p3_ctx["p1_result_content"] == content_p1
        assert p3_ctx["p2_result_content"] == content_p2


# ---------------------------------------------------------------------------
# StepConfig.permission → exec record capabilities (AC5, AC10, AC12)
# ---------------------------------------------------------------------------


class TestStepConfigPermission:
    def test_ac5_permission_text_only_exec_record_has_permission_mode(self):
        """AC5: permission='text_only' → exec record command に --permission-mode default --disallowed-tools"""
        import json as _json
        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-sonnet-4-6", permission="text_only")]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        record = _json.loads(exec_lines[0])
        assert "--permission-mode" in record["command"]
        assert "default" in record["command"]
        assert "--disallowed-tools" in record["command"]

    def test_ac5_permission_text_only_no_dangerously(self):
        """AC5: permission='text_only' → --dangerously-skip-permissions なし"""
        import json as _json
        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-sonnet-4-6", permission="text_only")]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        record = _json.loads(exec_lines[0])
        assert "--dangerously-skip-permissions" not in record["command"]

    def test_ac10_permission_none_default_behavior(self):
        """AC10: permission=None（デフォルト）→ exec record が従来と同一（--dangerously-skip-permissions）"""
        import json as _json
        api, _, _ = _make_api()
        steps_default = [StepConfig(template="brushup", model="claude-opus-4-6")]
        steps_none = [StepConfig(template="brushup", model="claude-opus-4-6", permission=None)]

        lines_default = api.submit(steps_default, {}, audit_context=_TEST_AUDIT_CTX)
        lines_none = api.submit(steps_none, {}, audit_context=_TEST_AUDIT_CTX)

        rec_default = _json.loads(lines_default[0])
        rec_none = _json.loads(lines_none[0])
        assert "--dangerously-skip-permissions" in rec_default["command"]
        # uuidが異なるので命令部だけ比較
        assert "--dangerously-skip-permissions" in rec_none["command"]
        assert "--permission-mode" not in rec_none["command"]

    def test_ac12_unknown_preset_raises_value_error(self):
        """AC12: permission='unknown_preset' → ValueError"""
        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-sonnet-4-6", permission="unknown_preset")]
        with pytest.raises(ValueError, match="unknown_preset"):
            api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

    def test_permission_dangerous_full_access(self):
        """permission='dangerous_full_access' → --permission-mode bypassPermissions"""
        import json as _json
        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-sonnet-4-6", permission="dangerous_full_access")]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        record = _json.loads(exec_lines[0])
        assert "--permission-mode" in record["command"]
        assert "bypassPermissions" in record["command"]

    def test_safe_default_permission_applied_when_env_set_and_permission_none(self, monkeypatch):
        """AC2: env 指定 + permission=None で safe default が適用される。"""
        import json as _json

        monkeypatch.setenv("GHDAG_SAFE_DEFAULT_PERMISSION", "text_only")
        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-opus-4-6", permission=None)]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        record = _json.loads(exec_lines[0])
        assert "--permission-mode default" in record["command"]
        assert "--disallowed-tools" in record["command"]
        assert "--dangerously-skip-permissions" not in record["command"]
        assert record["annotations"].get("safe_default_applied") is True
        assert record["annotations"].get("safe_default_preset") == "text_only"
        assert "default_permission_applied" not in record["annotations"]

    def test_explicit_permission_wins_over_safe_default_env(self, monkeypatch):
        """AC3: permission 明示時は env より permission が優先される。"""
        import json as _json

        monkeypatch.setenv("GHDAG_SAFE_DEFAULT_PERMISSION", "text_only")
        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-opus-4-6", permission="dangerous_full_access")]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        record = _json.loads(exec_lines[0])
        assert "--permission-mode bypassPermissions" in record["command"]
        assert "safe_default_applied" not in record["annotations"]
        assert "safe_default_preset" not in record["annotations"]

    def test_cursor_dangerous_full_access_includes_force(self):
        """AC4: cursor + dangerous_full_access で --force が付与される。"""
        import json as _json

        api, _, _ = _make_api()
        steps = [StepConfig(template="skill", model="auto", engine="cursor", permission="dangerous_full_access")]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        record = _json.loads(exec_lines[0])
        assert "--force" in record["command"]

    def test_cursor_text_only_does_not_include_force(self):
        """AC5: cursor + text_only では --force が付与されない。"""
        import json as _json

        api, _, _ = _make_api()
        steps = [StepConfig(template="skill", model="auto", engine="cursor", permission="text_only")]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        record = _json.loads(exec_lines[0])
        assert "--force" not in record["command"]

    def test_invalid_safe_default_permission_raises_value_error(self, monkeypatch):
        """AC6: 不正な GHDAG_SAFE_DEFAULT_PERMISSION は ValueError。"""
        monkeypatch.setenv("GHDAG_SAFE_DEFAULT_PERMISSION", "invalid_preset")
        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-opus-4-6", permission=None)]
        with pytest.raises(ValueError, match="GHDAG_SAFE_DEFAULT_PERMISSION"):
            api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

    def test_shell_engine_command_unchanged_when_safe_default_env_set(self, monkeypatch):
        """AC7: shell エンジンは safe default env の影響を受けない。"""
        import json as _json

        monkeypatch.setenv("GHDAG_SAFE_DEFAULT_PERMISSION", "text_only")
        api, _, _ = _make_api()
        steps = [StepConfig(template="cp1-gate", model="bash", engine="shell", permission=None)]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        record = _json.loads(exec_lines[0])
        assert record["command"] == "bash -o pipefail queue/ts-claude-order-uuid.md"
