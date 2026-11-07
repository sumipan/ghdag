"""Tests for ghdag.pipeline.llm_pipeline — LLMPipelineAPI (Issue #203)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from ghdag_pipeline_conftest_helpers import _TEST_AUDIT_CTX, _make_api, _track_build_order

from ghdag.pipeline.audit import AuditContext
from ghdag.pipeline.llm_pipeline import DependencyError, LLMPipelineAPI, validate_depends
from ghdag.workflow.schema import StepConfig


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

    def test_exec_record_command_uses_template_name(self):
        """build_order receives the step template name."""
        api, _, order_builder = _make_api()
        captured = _track_build_order(order_builder)
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        assert captured[0][0] == "brushup"

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


class TestAC1IdempotencyKey:
    def test_no_idempotency_key_no_comment(self):
        """Without idempotency_key, no leading comment line."""
        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        assert not exec_lines[0].startswith("# idempotency:")


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
        captured = _track_build_order(order_builder)
        steps = [
            StepConfig(id="p1", template="p1", model="claude-sonnet-4-6"),
            StepConfig(id="p2", template="p2", model="claude-sonnet-4-6", depends=["p1"]),
        ]
        api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        p1_ctx = captured[0][1]
        p2_ctx = captured[1][1]

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


class TestAC4EngineResultFilename:
    def test_claude_engine_result_filename(self):
        """When engine=claude, result_filename is {ts}-claude-result-{uuid}.md."""
        api, _, order_builder = _make_api()
        captured = _track_build_order(order_builder)
        steps = [StepConfig(id="p1", template="p1", model="claude-sonnet-4-6", engine="claude")]
        api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        ctx = captured[0][1]
        assert ctx["result_filename"].startswith(ctx["ts"] + "-claude-result-")

    def test_gemini_engine_result_filename(self):
        """When engine=gemini, result_filename is {ts}-gemini-result-{uuid}.md."""
        api, _, order_builder = _make_api()
        captured = _track_build_order(order_builder)
        steps = [StepConfig(id="p1", template="p1", model="gemini-2.5-flash", engine="gemini")]
        api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        ctx = captured[0][1]
        assert ctx["result_filename"].startswith(ctx["ts"] + "-gemini-result-")

    def test_dep_result_filename_reflects_dep_engine(self):
        """p1(gemini)→p2: p1_result_filename is {ts}-gemini-result-{uuid}.md."""
        api, _, order_builder = _make_api()
        captured = _track_build_order(order_builder)
        steps = [
            StepConfig(id="p1", template="p1", model="gemini-2.5-flash", engine="gemini"),
            StepConfig(id="p2", template="p2", model="claude-sonnet-4-6", engine="claude", depends=["p1"]),
        ]
        api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        p1_ctx = captured[0][1]
        p2_ctx = captured[1][1]

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


class TestBaseContextPropagation:
    def test_base_context_keys_in_step_context(self):
        """base_context values appear in the context passed to build_order."""
        api, _, order_builder = _make_api()
        captured = _track_build_order(order_builder)
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        api.submit(steps, {"issue_number": "42", "workflow_name": "test"}, audit_context=_TEST_AUDIT_CTX)

        ctx = captured[0][1]
        assert ctx["issue_number"] == "42"
        assert ctx["workflow_name"] == "test"

    def test_step_specific_keys_added(self):
        """ts, order_uuid, result_uuid, result_filename are included in context."""
        api, _, order_builder = _make_api()
        captured = _track_build_order(order_builder)
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        ctx = captured[0][1]
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


class TestAC3DependsValidation:
    def test_unknown_dependency_raises_value_error(self):
        """Undefined depends id → ValueError('Unknown dependency: ...')"""
        api, pipeline_state, _ = _make_api()
        steps = [
            StepConfig(id="step_a", template="t", model="m", depends=["nonexistent_step"]),
        ]
        with pytest.raises(DependencyError, match="nonexistent_step") as exc_info:
            api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)
        assert exc_info.value.code == "STEP_DEPENDENCY_UNKNOWN"

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


class TestJsonlMode:
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


class TestAuditContextPropagation:
    def test_submit_without_audit_context_raises_type_error(self):
        """AC-5 (required): submit() without audit_context raises TypeError."""
        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        with pytest.raises(TypeError):
            api.submit(steps, {"issue_number": "10"}, idempotency_key="wf:h:10")

    def test_submit_forwards_audit_context_to_append_exec_records(self):
        """AC-1: audit_context is forwarded to append_exec_records."""
        from ghdag.pipeline.audit import AuditContext

        api, pipeline_state, _ = _make_api()
        forwarded: list[AuditContext | None] = []

        def _append(records, audit_context=None, **kwargs):
            forwarded.append(audit_context)

        pipeline_state.append_exec_records.side_effect = _append
        ctx = AuditContext(source="issuesmith", correlation_id="test:key")
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        api.submit(steps, {}, audit_context=ctx)

        assert forwarded[0] is ctx
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

    def test_submit_injects_dep_result_content_when_file_exists(self, tmp_path):
        """AC1: when result file exists, p1_result_content is injected into p2 context."""
        result_content = "# Analysis Result\nScore: 85"
        result_file = tmp_path / f"{_TS}-claude-result-{_P1_UUID}.md"
        result_file.write_text(result_content, encoding="utf-8")

        api, _, order_builder = _make_api_with_tmpdir(tmp_path)
        captured = _track_build_order(order_builder)
        steps = [
            StepConfig(id="p1", template="p1", model="claude-sonnet-4-6"),
            StepConfig(id="p2", template="p2", model="claude-sonnet-4-6", depends=["p1"]),
        ]
        with patch("ghdag.pipeline.llm_pipeline.uuid.uuid4", side_effect=[_P1_UUID, _P2_UUID]), \
             patch("ghdag.pipeline.llm_pipeline.datetime") as mock_dt:
            mock_dt.now.return_value.strftime.return_value = _TS
            api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        p2_ctx = captured[1][1]
        assert p2_ctx["p1_result_content"] == result_content

    def test_submit_dep_result_content_empty_when_file_missing(self, tmp_path):
        """AC2: when result file is missing, p1_result_content is empty string (no error)."""
        api, _, order_builder = _make_api_with_tmpdir(tmp_path)
        captured = _track_build_order(order_builder)
        steps = [
            StepConfig(id="p1", template="p1", model="claude-sonnet-4-6"),
            StepConfig(id="p2", template="p2", model="claude-sonnet-4-6", depends=["p1"]),
        ]
        with patch("ghdag.pipeline.llm_pipeline.uuid.uuid4", side_effect=[_P1_UUID, _P2_UUID]), \
             patch("ghdag.pipeline.llm_pipeline.datetime") as mock_dt:
            mock_dt.now.return_value.strftime.return_value = _TS
            api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        p2_ctx = captured[1][1]
        assert p2_ctx["p1_result_content"] == ""

    def test_submit_injects_both_dep_filename_and_content(self, tmp_path):
        """AC3: both p1_result_filename and p1_result_content expand correctly."""
        result_content = "summary output"
        result_file = tmp_path / f"{_TS}-claude-result-{_P1_UUID}.md"
        result_file.write_text(result_content, encoding="utf-8")

        api, _, order_builder = _make_api_with_tmpdir(tmp_path)
        captured = _track_build_order(order_builder)
        steps = [
            StepConfig(id="p1", template="p1", model="claude-sonnet-4-6"),
            StepConfig(id="p2", template="p2", model="claude-sonnet-4-6", depends=["p1"]),
        ]
        with patch("ghdag.pipeline.llm_pipeline.uuid.uuid4", side_effect=[_P1_UUID, _P2_UUID]), \
             patch("ghdag.pipeline.llm_pipeline.datetime") as mock_dt:
            mock_dt.now.return_value.strftime.return_value = _TS
            api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        p2_ctx = captured[1][1]
        assert p2_ctx["p1_result_filename"] == f"{_TS}-claude-result-{_P1_UUID}.md"
        assert p2_ctx["p1_result_content"] == result_content

    def test_submit_injects_multiple_dep_result_contents(self, tmp_path):
        """AC4: p3 depends on both p1 and p2 → both result_content values are injected."""
        content_p1 = "p1 result"
        content_p2 = "p2 result"
        (tmp_path / f"{_TS}-claude-result-{_P1_UUID}.md").write_text(content_p1, encoding="utf-8")
        (tmp_path / f"{_TS}-claude-result-{_P2_UUID}.md").write_text(content_p2, encoding="utf-8")

        api, _, order_builder = _make_api_with_tmpdir(tmp_path)
        captured = _track_build_order(order_builder)
        steps = [
            StepConfig(id="p1", template="p1", model="claude-sonnet-4-6"),
            StepConfig(id="p2", template="p2", model="claude-sonnet-4-6"),
            StepConfig(id="p3", template="p3", model="claude-sonnet-4-6", depends=["p1", "p2"]),
        ]
        with patch("ghdag.pipeline.llm_pipeline.uuid.uuid4", side_effect=[_P1_UUID, _P2_UUID, _P3_UUID]), \
             patch("ghdag.pipeline.llm_pipeline.datetime") as mock_dt:
            mock_dt.now.return_value.strftime.return_value = _TS
            api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        p3_ctx = captured[2][1]
        assert p3_ctx["p1_result_content"] == content_p1
        assert p3_ctx["p2_result_content"] == content_p2


_A_UUID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_B_UUID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
_FORWARD_TS = "20260523120000"


class TestForwardDepends:
    """sumipan/nexus#6083: forward references in step order."""

    def test_forward_depends_resolved_to_uuid_and_filename(self):
        """Depends on a later step still resolves uuid and dep result filename in context."""
        import json as _json

        api, _, order_builder = _make_api()
        captured = _track_build_order(order_builder)
        steps = [
            StepConfig(id="b", template="p2", model="claude-sonnet-4-6", depends=["a"]),
            StepConfig(id="a", template="p1", model="claude-sonnet-4-6"),
        ]
        with patch("ghdag.pipeline.llm_pipeline.uuid.uuid4", side_effect=[_B_UUID, _A_UUID]), patch(
            "ghdag.pipeline.llm_pipeline.datetime"
        ) as mock_dt:
            mock_dt.now.return_value.strftime.return_value = _FORWARD_TS
            exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        b_record = _json.loads(exec_lines[0])
        a_record = _json.loads(exec_lines[1])
        assert a_record["uuid"] in b_record["depends"]

        b_ctx = captured[0][1]
        expected = f"{_FORWARD_TS}-claude-result-{_A_UUID}.md"
        assert b_ctx["a_result_filename"] == expected

    def test_records_keep_input_order(self):
        """Return value and append_exec_records preserve steps input order (B before A)."""
        import json as _json

        api, pipeline_state, _ = _make_api()
        appended: list[list[dict]] = []

        def _append(records, audit_context=None, **kwargs):
            appended.append(list(records))

        pipeline_state.append_exec_records.side_effect = _append
        steps = [
            StepConfig(id="b", template="p2", model="claude-sonnet-4-6", depends=["a"]),
            StepConfig(id="a", template="p1", model="claude-sonnet-4-6"),
        ]
        with patch("ghdag.pipeline.llm_pipeline.uuid.uuid4", side_effect=[_B_UUID, _A_UUID]), patch(
            "ghdag.pipeline.llm_pipeline.datetime"
        ) as mock_dt:
            mock_dt.now.return_value.strftime.return_value = _FORWARD_TS
            exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        assert len(exec_lines) == 2
        assert _json.loads(exec_lines[0])["annotations"]["step_name"] == "b"
        assert _json.loads(exec_lines[1])["annotations"]["step_name"] == "a"
        assert appended[0][0]["annotations"]["step_name"] == "b"
        assert appended[0][1]["annotations"]["step_name"] == "a"


class TestEnvPassthroughInAnnotations:
    def test_submit_writes_env_passthrough_to_annotations(self):
        """Non-empty StepConfig.env_passthrough is copied to record annotations."""
        import json as _json

        api, _, _ = _make_api()
        steps = [
            StepConfig(
                template="brushup",
                model="claude-opus-4-6",
                env_passthrough=["MY_TOKEN"],
            ),
        ]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        record = _json.loads(exec_lines[0])
        assert record["annotations"]["env_passthrough"] == ["MY_TOKEN"]

    def test_submit_omits_env_passthrough_key_when_empty(self):
        """Empty env_passthrough does not add annotations.env_passthrough."""
        import json as _json

        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-opus-4-6", env_passthrough=[])]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        record = _json.loads(exec_lines[0])
        assert "env_passthrough" not in record.get("annotations", {})


class TestValidateDependsPublic:
    """Issue #6167 — validate_depends is importable and rejects unknown deps."""

    def test_import_validate_depends(self):
        assert callable(validate_depends)

    def test_unknown_dependency_raises_dependency_error(self):
        steps = [
            StepConfig(id="a", template="t", model="m"),
            StepConfig(id="b", template="t", model="m", depends=["missing"]),
        ]
        with pytest.raises(DependencyError) as exc_info:
            validate_depends(steps)
        assert exc_info.value.code == "STEP_DEPENDENCY_UNKNOWN"


class TestUnknownEngine:
    def test_unknown_engine_raises_engine_model_error(self):
        """#6142: unknown engine → EngineModelError(ENGINE_UNKNOWN), not KeyError."""
        from ghdag.llm.engines import EngineModelError

        api, _, _ = _make_api()
        steps = [
            StepConfig(template="t", model="claude-opus-4-6", engine="nonexistent"),
        ]
        with pytest.raises(EngineModelError) as exc_info:
            api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)
        assert exc_info.value.code == "ENGINE_UNKNOWN"
        assert "claude" in (exc_info.value.hint or "")
