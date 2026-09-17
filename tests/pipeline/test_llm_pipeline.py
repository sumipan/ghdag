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
        """One step yields one exec_records entry (JSONL format)."""
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
        """write_order_file is called once."""
        api, pipeline_state, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        pipeline_state.write_order_file.assert_called_once()

    def test_submit_calls_build_order(self):
        """build_order is called once with the template name."""
        api, _, order_builder = _make_api()
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        order_builder.build_order.assert_called_once()
        call_args = order_builder.build_order.call_args[0]
        assert call_args[0] == "brushup"

    def test_exec_record_contains_model(self):
        """The exec record command includes the model name."""
        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        assert "claude-opus-4-6" in exec_lines[0]

    def test_exec_record_contains_dangerously_skip_permissions(self):
        """The exec record command omits --dangerously-skip-permissions (TEXT_ONLY default)."""
        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        assert "--dangerously-skip-permissions" not in exec_lines[0]
        assert "--permission-mode default" in exec_lines[0]
        assert "--disallowed-tools" in exec_lines[0]


# ---------------------------------------------------------------------------
# AC1-2 / AC1-3: idempotency_key
# ---------------------------------------------------------------------------


class TestAC1IdempotencyKey:
    def test_no_idempotency_key_no_comment(self):
        """Without idempotency_key, no leading comment line."""
        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        assert not exec_lines[0].startswith("# idempotency:")


# ---------------------------------------------------------------------------
# AC1-2 / AC4: 2 steps with depends
# ---------------------------------------------------------------------------


class TestAC1TwoStepsWithDepends:
    def test_p2_has_depends_p1_uuid(self):
        """p2 exec record depends includes p1_uuid."""
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
        """Context passed to P2 build_order includes p1_result_filename."""
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
        """p1→p2→p3 chain yields 3 exec records with dependencies resolved."""
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
# AC4: engine name is reflected in result_filename / exec line
# ---------------------------------------------------------------------------


class TestAC4EngineResultFilename:
    def test_claude_engine_result_filename(self):
        """When engine=claude, result_filename is {ts}-claude-result-{uuid}.md."""
        api, _, order_builder = _make_api()
        steps = [StepConfig(id="p1", template="p1", model="claude-sonnet-4-6", engine="claude")]
        api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        ctx = order_builder.build_order.call_args[0][1]
        assert ctx["result_filename"].startswith(ctx["ts"] + "-claude-result-")

    def test_gemini_engine_result_filename(self):
        """When engine=gemini, result_filename is {ts}-gemini-result-{uuid}.md."""
        api, _, order_builder = _make_api()
        steps = [StepConfig(id="p1", template="p1", model="gemini-2.5-flash", engine="gemini")]
        api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        ctx = order_builder.build_order.call_args[0][1]
        assert ctx["result_filename"].startswith(ctx["ts"] + "-gemini-result-")

    def test_dep_result_filename_reflects_dep_engine(self):
        """p1(gemini)→p2: p1_result_filename is {ts}-gemini-result-{uuid}.md."""
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
        """When engine=gemini, exec command omits --dangerously-skip-permissions."""
        import json as _json
        api, _, _ = _make_api()
        steps = [StepConfig(template="p1", model="gemini-2.5-flash", engine="gemini")]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        record = _json.loads(exec_lines[0])
        assert "--dangerously-skip-permissions" not in record["command"]
        assert "gemini" in record["command"]


# ---------------------------------------------------------------------------
# AC3: DagEngine-compatible format
# ---------------------------------------------------------------------------


class TestAC3ExecFormat:
    def test_uuid_field_in_exec_record(self):
        """exec record uuid field is UUID format (36-char hyphenated)."""
        import json as _json
        import re
        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        record = _json.loads(exec_lines[0])
        uuid_pattern = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
        assert re.match(uuid_pattern, record["uuid"])

    def test_depends_field_in_exec_record(self):
        """exec record depends field includes p1 uuid."""
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
        """exec record includes a result_path field."""
        import json as _json
        api, pipeline_state, _ = _make_api()
        pipeline_state.write_order_file.return_value = "20260419-claude-order-abc.md"
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        record = _json.loads(exec_lines[0])
        assert "result_path" in record
        assert "-result-" in record["result_path"]


# ---------------------------------------------------------------------------
# base_context is carried into each step context
# ---------------------------------------------------------------------------


class TestBaseContextPropagation:
    def test_base_context_keys_in_step_context(self):
        """base_context values appear in the context passed to build_order."""
        api, _, order_builder = _make_api()
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        api.submit(steps, {"issue_number": "42", "workflow_name": "test"}, audit_context=_TEST_AUDIT_CTX)

        ctx = order_builder.build_order.call_args[0][1]
        assert ctx["issue_number"] == "42"
        assert ctx["workflow_name"] == "test"

    def test_step_specific_keys_added(self):
        """ts, order_uuid, result_uuid, result_filename are included in context."""
        api, _, order_builder = _make_api()
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        ctx = order_builder.build_order.call_args[0][1]
        assert "ts" in ctx
        assert "order_uuid" in ctx
        assert "result_uuid" in ctx
        assert "result_filename" in ctx

    def test_base_context_not_mutated(self):
        """submit() does not mutate the base_context dict in place."""
        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        base = {"issue_number": "10"}
        original_keys = set(base.keys())
        api.submit(steps, base, audit_context=_TEST_AUDIT_CTX)

        assert set(base.keys()) == original_keys


# ---------------------------------------------------------------------------
# Regression: per-workflow OrderBuilder switching
# Prevents cross-workflow template resolution mix-ups (observed in issue #610 as
# `FileNotFoundError: workflows/inkwell/brushup.md`).
# ---------------------------------------------------------------------------


class TestPerWorkflowOrderBuilder:
    def test_order_builder_resolved_by_workflow_name(self):
        """The OrderBuilder matching base_context['workflow_name'] is used."""
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

        # submit with issuesmith workflow_name → issuesmith_builder is called
        api.submit(steps, {"workflow_name": "issuesmith", "issue_number": "610"}, audit_context=_TEST_AUDIT_CTX)
        issuesmith_builder.build_order.assert_called_once()
        inkwell_builder.build_order.assert_not_called()
        default_builder.build_order.assert_not_called()

        # re-submit with another workflow name → matching builder is called
        api.submit(steps, {"workflow_name": "inkwell"}, audit_context=_TEST_AUDIT_CTX)
        inkwell_builder.build_order.assert_called_once()

    def test_falls_back_to_default_builder_when_workflow_unknown(self):
        """Falls back to default when order_builders has no matching entry."""
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
        api.submit(steps, {"workflow_name": "research"}, audit_context=_TEST_AUDIT_CTX)  # unregistered
        default_builder.build_order.assert_called_once()
        inkwell_builder.build_order.assert_not_called()

    def test_falls_back_to_default_when_workflow_name_missing(self):
        """Works with default when base_context has no workflow_name (backward compatible)."""
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
        api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)  # no workflow_name key
        default_builder.build_order.assert_called_once()

    def test_backward_compat_no_order_builders_kwarg(self):
        """Legacy call style without order_builders still works."""
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
        api.submit(steps, {"workflow_name": "issuesmith"}, audit_context=_TEST_AUDIT_CTX)  # always falls back to default
        order_builder.build_order.assert_called_once()


# ---------------------------------------------------------------------------
# Issue #3029: submit(order_builder=..., workflow_roles=...) public kwargs
# ---------------------------------------------------------------------------


class TestSubmitOrderBuilderAndWorkflowRoles:
    def test_submit_order_builder_kwarg_skips_resolve(self):
        """With submit(order_builder=wrapped), _resolve_order_builder is not called."""
        api, pipeline_state, default_builder = _make_api()
        wrapped = MagicMock()
        wrapped.build_order.return_value = "wrapped order"
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]

        with patch.object(
            api, "_resolve_order_builder", wraps=api._resolve_order_builder
        ) as resolve:
            api.submit(
                steps,
                {"workflow_name": "issuesmith", "issue_number": "1"},
                audit_context=_TEST_AUDIT_CTX,
                order_builder=wrapped,
            )

        resolve.assert_not_called()
        wrapped.build_order.assert_called_once()
        default_builder.build_order.assert_not_called()
        pipeline_state.append_exec_records.assert_called_once()

    def test_workflow_roles_set_role_annotations(self):
        """With workflow_roles, step.role is set in annotations."""
        import json as _json

        api, pipeline_state, _ = _make_api()
        steps = [
            StepConfig(
                id="cp2",
                template="brushup",
                model="claude-opus-4-6",
                role="design",
            )
        ]
        lines = api.submit(
            steps,
            {"workflow_name": "issuesmith", "issue_number": "1"},
            audit_context=_TEST_AUDIT_CTX,
            workflow_roles={"design": ["claude", "codex"]},
        )
        records = pipeline_state.append_exec_records.call_args[0][0]
        assert records[0]["annotations"]["role"] == "design"
        assert records[0]["annotations"]["role_engines"] == ["claude", "codex"]
        parsed = _json.loads(lines[0])
        assert parsed["annotations"]["role"] == "design"
        assert parsed["annotations"]["role_engines"] == ["claude", "codex"]

    def test_role_annotations_drive_quota_gate_admit(self, tmp_path):
        """AC-2: annotations from loaded roles affect QuotaGate.admit(role, role_engines)."""
        from datetime import datetime, timedelta, timezone

        from ghdag.io import exec_jsonl
        from ghdag.quota import QuotaGate

        api, pipeline_state, _ = _make_api(queue_dir=str(tmp_path / "queue"))
        captured: list[dict] = []

        def _capture_append(records, **kwargs):
            captured.extend(records)

        pipeline_state.append_exec_records.side_effect = _capture_append

        steps = [
            StepConfig(
                id="cp2",
                template="brushup",
                model="claude-opus-4-6",
                engine="claude",
                role="design",
            )
        ]
        api.submit(
            steps,
            {"workflow_name": "issuesmith"},
            audit_context=_TEST_AUDIT_CTX,
            workflow_roles={"design": ["claude", "codex"]},
        )
        assert captured[0]["annotations"]["role"] == "design"

        jst = timezone(timedelta(hours=9))
        gate = QuotaGate(tmp_path / "quota-gate.json")
        gate.report(
            engine="claude",
            status="paused",
            observed_at=datetime(2026, 9, 5, 12, tzinfo=jst),
        )
        gate.report(
            engine="codex",
            status="paused",
            observed_at=datetime(2026, 9, 5, 12, tzinfo=jst),
        )
        exec_path = tmp_path / "jobs" / "exec.jsonl"
        exec_jsonl.append(
            exec_path,
            [
                {
                    "uuid": "task-1",
                    "command": "claude -p hi",
                    "engine": "claude",
                    "depends": [],
                    "annotations": captured[0]["annotations"],
                }
            ],
            _TEST_AUDIT_CTX,
            audit_path=tmp_path / "audit.jsonl",
            quota_gate=gate,
        )
        deferred = gate.snapshot().deferred_tasks["task-1"]
        assert deferred.role == "design"


# ---------------------------------------------------------------------------
# AC3: depends pre-validation (Issue #766)
# ---------------------------------------------------------------------------


class TestAC3DependsValidation:
    def test_unknown_dependency_raises_value_error(self):
        """Undefined depends id → ValueError('Unknown dependency: ...')"""
        api, pipeline_state, _ = _make_api()
        steps = [
            StepConfig(id="step_a", template="t", model="m", depends=["nonexistent_step"]),
        ]
        with pytest.raises(DependencyError, match="nonexistent_step"):
            api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

    def test_circular_dependency_a_b_a_raises_value_error(self):
        """A→B→A circular dependency → ValueError containing 'circular dependency'"""
        api, _, _ = _make_api()
        steps = [
            StepConfig(id="a", template="t", model="m", depends=["b"]),
            StepConfig(id="b", template="t", model="m", depends=["a"]),
        ]
        with pytest.raises(DependencyError, match="circular dependency"):
            api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

    def test_valid_linear_dependency_passes(self):
        """A→B→C valid linear depends → validation passes, 3 exec lines"""
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
        """On validation error, neither order file nor exec.jsonl is written"""
        api, pipeline_state, _ = _make_api()
        steps = [
            StepConfig(id="a", template="t", model="m", depends=["nonexistent"]),
        ]
        with pytest.raises(DependencyError):
            api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)
        pipeline_state.write_order_file.assert_not_called()
        pipeline_state.append_exec_records.assert_not_called()

    def test_validation_error_circular_no_files_written(self):
        """On circular dependency error, neither order file nor exec.jsonl is written"""
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
# JSONL mode: write JSON records to exec.jsonl
# ---------------------------------------------------------------------------


class TestJsonlMode:
    def test_calls_append_exec_records(self):
        """append_exec_records is called."""
        api, pipeline_state, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        pipeline_state.append_exec_records.assert_called_once()
        assert len(exec_lines) == 1

    def test_returns_json_strings(self):
        """exec_lines are JSON strings (including a uuid field)."""
        import json as _json

        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        record = _json.loads(exec_lines[0])
        assert "uuid" in record
        assert "command" in record
        assert "result_path" in record

    def test_idempotency_in_record(self):
        """idempotency_key is embedded in the record; no comment line is generated."""
        import json as _json

        api, pipeline_state, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        exec_lines = api.submit(steps, {}, idempotency_key="workflow:handler:42", audit_context=_TEST_AUDIT_CTX)

        assert len(exec_lines) == 1
        record = _json.loads(exec_lines[0])
        assert record.get("idempotency_key") == "workflow:handler:42"

    def test_result_path_in_record(self):
        """result_path field is queue_dir/filename format."""
        import json as _json

        api, _, _ = _make_api(queue_dir="jobs")
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        record = _json.loads(exec_lines[0])
        assert record["result_path"].startswith("jobs/")

    def test_no_comment_idempotency_line(self):
        """exec_lines do not include # idempotency: comment lines."""
        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        exec_lines = api.submit(steps, {}, idempotency_key="scheduler:diary_review:ts", audit_context=_TEST_AUDIT_CTX)

        for line in exec_lines:
            assert not line.startswith("#"), f"comment line found: {line!r}"

    def test_cursor_engine_valid_json(self):
        """cursor engine exec record is valid JSON and command includes agent."""
        import json as _json

        api, _, _ = _make_api()
        steps = [StepConfig(template="skill", model="auto", engine="cursor")]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        record = _json.loads(exec_lines[0])
        assert "uuid" in record
        assert "agent" in record["command"]

    def test_all_exec_lines_are_parseable_json(self):
        """When submitting multiple steps, all exec_lines are JSON-parseable."""
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
        """Scheduler-format idempotency_key (scheduler:job_id:ISO8601) embeds correctly."""
        import json as _json

        api, _, _ = _make_api()
        key = "scheduler:diary_review:2026-05-08T23:00:00.123456+09:00"
        steps = [StepConfig(template="diary-review", model="auto", engine="cursor")]
        exec_lines = api.submit(steps, {"workflow_name": "scheduler"}, idempotency_key=key, audit_context=_TEST_AUDIT_CTX)

        record = _json.loads(exec_lines[0])
        assert record["idempotency_key"] == key
        assert "# idempotency:" not in exec_lines[0]


# ---------------------------------------------------------------------------
# Issue #984: audit_context required tests (replaces AC-1, AC-5)
# ---------------------------------------------------------------------------


class TestAuditContextPropagation:
    def test_ac5_submit_without_audit_context_raises_type_error(self):
        """AC-5 (required): submit() without audit_context raises TypeError."""
        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        with pytest.raises(TypeError):
            api.submit(steps, {"issue_number": "10"}, idempotency_key="wf:h:10")

    def test_ac1_audit_context_passed_to_append_exec_records(self):
        """AC-1: audit_context is forwarded to append_exec_records."""
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
    """Issue #1014: ${dep_id_result_content} context injection."""

    def test_ac1_result_content_injected_when_file_exists(self, tmp_path):
        """AC1: when result file exists, p1_result_content is injected into p2 context."""
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
        """AC2: when result file is missing, p1_result_content is empty string (no error)."""
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
        """AC3: both p1_result_filename and p1_result_content expand correctly."""
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
        """AC4: p3 depends on both p1 and p2 → both result_content values are injected."""
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
        """AC5: permission='text_only' → exec record command has --permission-mode default --disallowed-tools"""
        import json as _json
        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-sonnet-4-6", permission="text_only")]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        record = _json.loads(exec_lines[0])
        assert "--permission-mode" in record["command"]
        assert "default" in record["command"]
        assert "--disallowed-tools" in record["command"]

    def test_ac5_permission_text_only_no_dangerously(self):
        """AC5: permission='text_only' → no --dangerously-skip-permissions"""
        import json as _json
        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-sonnet-4-6", permission="text_only")]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        record = _json.loads(exec_lines[0])
        assert "--dangerously-skip-permissions" not in record["command"]

    def test_ac10_permission_none_default_behavior(self):
        """AC10: permission=None (default) → TEXT_ONLY (--permission-mode default --disallowed-tools)"""
        import json as _json
        api, _, _ = _make_api()
        steps_default = [StepConfig(template="brushup", model="claude-opus-4-6")]
        steps_none = [StepConfig(template="brushup", model="claude-opus-4-6", permission=None)]

        lines_default = api.submit(steps_default, {}, audit_context=_TEST_AUDIT_CTX)
        lines_none = api.submit(steps_none, {}, audit_context=_TEST_AUDIT_CTX)

        rec_default = _json.loads(lines_default[0])
        rec_none = _json.loads(lines_none[0])
        for record in (rec_default, rec_none):
            assert "--permission-mode default" in record["command"]
            assert "--disallowed-tools" in record["command"]
            assert "Bash,Edit,Write,NotebookEdit,WebFetch,WebSearch" in record["command"]
            assert "--dangerously-skip-permissions" not in record["command"]
            assert record["annotations"].get("safe_default_applied") is True
            assert record["annotations"].get("safe_default_preset") == "text_only"

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

    def test_permission_dangerous_full_access_codex(self):
        """codex + permission='dangerous_full_access' → sandbox bypass flags are attached.

        nexus#2558 regression: without the flags, codex starts as workspace-write and
        cannot write outside cwd (diary repo), so skills fail silently.
        """
        import json as _json
        api, _, _ = _make_api()
        steps = [
            StepConfig(
                template="brushup",
                engine="codex",
                model="gpt-5.6-terra",
                permission="dangerous_full_access",
            )
        ]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        record = _json.loads(exec_lines[0])
        assert "--dangerously-bypass-approvals-and-sandbox" in record["command"]
        assert record["command"].split().count("--json") == 1

    def test_safe_default_permission_applied_when_env_set_and_permission_none(self, monkeypatch):
        """AC2: with env set and permission=None, the safe default is applied."""
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

    def test_safe_default_rollback_dangerous_full_access(self, monkeypatch):
        """GHDAG_SAFE_DEFAULT_PERMISSION=dangerous_full_access can roll back to the dangerous default."""
        import json as _json

        monkeypatch.setenv("GHDAG_SAFE_DEFAULT_PERMISSION", "dangerous_full_access")
        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-opus-4-6", permission=None)]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        record = _json.loads(exec_lines[0])
        assert "bypassPermissions" in record["command"]
        assert "--disallowed-tools" not in record["command"]
        assert record["annotations"].get("safe_default_applied") is True
        assert record["annotations"].get("safe_default_preset") == "dangerous_full_access"

    def test_explicit_permission_wins_over_safe_default_env(self, monkeypatch):
        """AC3: an explicit permission overrides the env default."""
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
        """AC4: cursor + dangerous_full_access attaches --force."""
        import json as _json

        api, _, _ = _make_api()
        steps = [StepConfig(template="skill", model="auto", engine="cursor", permission="dangerous_full_access")]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        record = _json.loads(exec_lines[0])
        assert "--force" in record["command"]

    def test_cursor_text_only_does_not_include_force(self):
        """AC5: cursor + text_only does not attach --force."""
        import json as _json

        api, _, _ = _make_api()
        steps = [StepConfig(template="skill", model="auto", engine="cursor", permission="text_only")]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        record = _json.loads(exec_lines[0])
        assert "--force" not in record["command"]

    def test_invalid_safe_default_permission_raises_value_error(self, monkeypatch):
        """AC6: invalid GHDAG_SAFE_DEFAULT_PERMISSION raises ValueError."""
        monkeypatch.setenv("GHDAG_SAFE_DEFAULT_PERMISSION", "invalid_preset")
        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-opus-4-6", permission=None)]
        with pytest.raises(ValueError, match="GHDAG_SAFE_DEFAULT_PERMISSION"):
            api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

    def test_shell_engine_command_unchanged_when_safe_default_env_set(self, monkeypatch):
        """AC7: shell engine is unaffected by the safe-default env."""
        import json as _json

        monkeypatch.setenv("GHDAG_SAFE_DEFAULT_PERMISSION", "text_only")
        api, _, _ = _make_api()
        steps = [StepConfig(template="cp1-gate", model="bash", engine="shell", permission=None)]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        record = _json.loads(exec_lines[0])
        assert record["command"] == "bash -o pipefail queue/ts-claude-order-uuid.md"


# ---------------------------------------------------------------------------
# S0-2 (#1295): default permission audit annotations (AC1–AC3, AC7)
# ---------------------------------------------------------------------------


class TestDefaultPermissionAuditAnnotations:
    def test_ac1_permission_none_claude_annotations(self):
        """AC1: permission=None → safe_default_applied + safe_default_preset=text_only."""
        import json as _json
        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        record = _json.loads(exec_lines[0])
        assert record["annotations"]["safe_default_applied"] is True
        assert record["annotations"]["safe_default_preset"] == "text_only"
        assert "default_permission_applied" not in record["annotations"]
        assert "injected_danger_flag" not in record["annotations"]

    def test_ac2_permission_text_only_no_default_annotation(self):
        """AC2: permission='text_only' → no default_permission_applied key."""
        import json as _json
        api, _, _ = _make_api()
        steps = [
            StepConfig(
                template="brushup",
                model="claude-sonnet-4-6",
                permission="text_only",
            )
        ]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        record = _json.loads(exec_lines[0])
        assert "default_permission_applied" not in record["annotations"]

    def test_ac3_gemini_engine_no_default_annotation(self):
        """AC3: gemini (danger_flag=None) → no default_permission_applied key."""
        import json as _json
        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="gemini-2.5-pro", engine="gemini")]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        record = _json.loads(exec_lines[0])
        assert "default_permission_applied" not in record["annotations"]

    def test_ac3_shell_engine_no_default_annotation(self):
        """AC3: shell (danger_flag=None) → no default_permission_applied key."""
        import json as _json
        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="echo", engine="shell")]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        record = _json.loads(exec_lines[0])
        assert "default_permission_applied" not in record["annotations"]

    def test_ac7_cursor_permission_none_injected_force(self):
        """AC7: cursor + permission=None → TEXT_ONLY default (no --force)."""
        import json as _json
        api, _, _ = _make_api()
        steps = [StepConfig(template="impl", model="cursor", engine="cursor")]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        record = _json.loads(exec_lines[0])
        assert record["annotations"]["safe_default_applied"] is True
        assert record["annotations"]["safe_default_preset"] == "text_only"
        assert "--force" not in record["command"]
        assert "default_permission_applied" not in record["annotations"]


# ---------------------------------------------------------------------------
# metadata kwarg — annotations reflected from submit() (Issue #1295)
# ---------------------------------------------------------------------------


class TestMetadataInAnnotations:
    def test_metadata_reflected_in_record_annotations(self):
        """submit(metadata={"k": "v"}) includes {"k": "v"} in annotations."""
        import json as _json
        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX, metadata={"k": "v"})

        record = _json.loads(exec_lines[0])
        assert record["annotations"].get("k") == "v"

    def test_metadata_channel_and_thread_ts(self):
        """Passing channel_id and thread_ts via metadata stores them in annotations."""
        import json as _json
        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        metadata = {"channel_id": "C123", "thread_ts": "1234567890.000"}
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX, metadata=metadata)

        record = _json.loads(exec_lines[0])
        assert record["annotations"]["channel_id"] == "C123"
        assert record["annotations"]["thread_ts"] == "1234567890.000"

    def test_submit_with_metadata_completes_without_error(self):
        """submit(metadata=...) completes successfully (no exception)."""
        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        result = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX, metadata={"foo": "bar"})
        assert len(result) == 1


class TestMetadataMultipleSteps:
    def test_all_steps_get_same_annotations(self):
        """When submitting multiple steps, every record gets the same annotations."""
        import json as _json
        api, _, _ = _make_api()
        steps = [
            StepConfig(id="s1", template="t1", model="m1"),
            StepConfig(id="s2", template="t2", model="m2", depends=["s1"]),
            StepConfig(id="s3", template="t3", model="m3", depends=["s2"]),
        ]
        metadata = {"channel_id": "C999", "thread_ts": "9999.000"}
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX, metadata=metadata)

        assert len(exec_lines) == 3
        for line in exec_lines:
            record = _json.loads(line)
            assert record["annotations"]["channel_id"] == "C999"
            assert record["annotations"]["thread_ts"] == "9999.000"

    def test_two_steps_both_have_annotations(self):
        """Both steps (independent and dependent) receive annotations."""
        import json as _json
        api, _, _ = _make_api()
        steps = [
            StepConfig(id="p1", template="p1", model="claude-sonnet-4-6"),
            StepConfig(id="p2", template="p2", model="claude-sonnet-4-6", depends=["p1"]),
        ]
        metadata = {"k": "v"}
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX, metadata=metadata)

        for line in exec_lines:
            record = _json.loads(line)
            assert record["annotations"].get("k") == "v"


class TestMetadataRoundTrip:
    def test_roundtrip_via_parse_jsonl(self):
        """submit() with metadata: exec.jsonl → parse_jsonl() → Task.annotations match."""
        import json as _json

        from ghdag.dag.parser import parse_jsonl

        api, pipeline_state, _ = _make_api()
        captured_records: list[dict] = []

        def capture_records(records, **kwargs):
            captured_records.extend(records)

        pipeline_state.append_exec_records.side_effect = capture_records

        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        metadata = {"channel_id": "C123", "thread_ts": "1234567890.000"}
        api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX, metadata=metadata)

        assert len(captured_records) == 1
        jsonl_content = _json.dumps(captured_records[0], ensure_ascii=False)
        tasks = parse_jsonl(jsonl_content)

        assert len(tasks) == 1
        assert tasks[0].annotations["channel_id"] == "C123"
        assert tasks[0].annotations["thread_ts"] == "1234567890.000"

    def test_roundtrip_multiple_steps(self):
        """Multi-step round-trip: every Task.annotations matches metadata."""
        import json as _json

        from ghdag.dag.parser import parse_jsonl

        api, pipeline_state, _ = _make_api()
        captured_records: list[dict] = []

        def capture_records(records, **kwargs):
            captured_records.extend(records)

        pipeline_state.append_exec_records.side_effect = capture_records

        steps = [
            StepConfig(id="p1", template="p1", model="m"),
            StepConfig(id="p2", template="p2", model="m", depends=["p1"]),
        ]
        metadata = {"slack_channel_id": "C001", "slack_thread_ts": "111.222"}
        api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX, metadata=metadata)

        assert len(captured_records) == 2
        jsonl_text = "\n".join(
            _json.dumps(r, ensure_ascii=False) for r in captured_records
        )
        tasks = parse_jsonl(jsonl_text)

        assert len(tasks) == 2
        for task in tasks:
            assert task.annotations["slack_channel_id"] == "C001"
            assert task.annotations["slack_thread_ts"] == "111.222"


class TestMetadataBackwardCompat:
    def test_no_metadata_annotations_empty(self):
        """When metadata is omitted, gemini has no metadata-derived annotation keys (safe default only)."""
        import json as _json
        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="gemini-2.5-pro", engine="gemini")]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        record = _json.loads(exec_lines[0])
        annotations = record.get("annotations", {})
        assert annotations.get("safe_default_applied") is True
        assert annotations.get("safe_default_preset") == "text_only"
        assert "step_name" in annotations
        assert len(annotations) == 3

    def test_no_metadata_does_not_break_existing_behavior(self):
        """Without metadata, existing fields (uuid, command, result_path) remain valid."""
        import json as _json
        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        record = _json.loads(exec_lines[0])
        assert "uuid" in record
        assert "command" in record
        assert "result_path" in record


class TestMetadataWithIdempotencyKey:
    def test_idempotency_key_and_metadata_together(self):
        """idempotency_key and metadata can be specified together."""
        import json as _json
        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        exec_lines = api.submit(
            steps,
            {},
            idempotency_key="workflow:handler:42",
            audit_context=_TEST_AUDIT_CTX,
            metadata={"channel_id": "C123"},
        )

        record = _json.loads(exec_lines[0])
        assert record["idempotency_key"] == "workflow:handler:42"
        assert record["annotations"]["channel_id"] == "C123"


class TestMetadataEmptyDict:
    def test_empty_metadata_dict_annotations_stays_empty(self):
        """With metadata={}, gemini has no metadata-derived annotation keys (safe default only)."""
        import json as _json
        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="gemini-2.5-pro", engine="gemini")]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX, metadata={})

        record = _json.loads(exec_lines[0])
        annotations = record.get("annotations", {})
        assert annotations.get("safe_default_applied") is True
        assert annotations.get("safe_default_preset") == "text_only"
        assert "step_name" in annotations
        assert len(annotations) == 3


class TestMetadataExplicitNone:
    def test_explicit_none_metadata_same_as_omitted(self):
        """With metadata=None explicitly, gemini matches the omitted case (safe default only)."""
        import json as _json
        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="gemini-2.5-pro", engine="gemini")]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX, metadata=None)

        record = _json.loads(exec_lines[0])
        annotations = record.get("annotations", {})
        assert annotations.get("safe_default_applied") is True
        assert annotations.get("safe_default_preset") == "text_only"
        assert "step_name" in annotations
        assert len(annotations) == 3
