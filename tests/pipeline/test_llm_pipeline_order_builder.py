"""Tests for ghdag.pipeline.llm_pipeline — LLMPipelineAPI (Issue #203)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from ghdag_pipeline_conftest_helpers import _TEST_AUDIT_CTX, _make_api

from ghdag.pipeline.llm_pipeline import LLMPipelineAPI
from ghdag.workflow.schema import StepConfig


class TestPerWorkflowOrderBuilder:
    def test_order_builder_resolved_by_workflow_name(self):
        """The OrderBuilder matching base_context['workflow_name'] is used."""
        from unittest.mock import MagicMock


        pipeline_state = MagicMock()
        pipeline_state.check_idempotency.return_value = True
        written_orders: list[str] = []

        def _write_order(_ts: str, _uid: str, content: str, _queue_dir: str, **_kw) -> str:
            written_orders.append(content)
            return "ts-claude-order-uuid.md"

        pipeline_state.write_order_file.side_effect = _write_order

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
        assert written_orders[-1] == "issuesmith order content"

        # re-submit with another workflow name → matching builder is called
        api.submit(steps, {"workflow_name": "inkwell"}, audit_context=_TEST_AUDIT_CTX)
        assert written_orders[-1] == "inkwell order content"

    def test_falls_back_to_default_builder_when_workflow_unknown(self):
        """Falls back to default when order_builders has no matching entry."""
        from unittest.mock import MagicMock


        pipeline_state = MagicMock()
        pipeline_state.check_idempotency.return_value = True
        written_orders: list[str] = []

        def _write_order(_ts: str, _uid: str, content: str, _queue_dir: str, **_kw) -> str:
            written_orders.append(content)
            return "ts-claude-order-uuid.md"

        pipeline_state.write_order_file.side_effect = _write_order

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
        assert written_orders[-1] == "default order"

    def test_falls_back_to_default_when_workflow_name_missing(self):
        """Works with default when base_context has no workflow_name (backward compatible)."""
        from unittest.mock import MagicMock


        pipeline_state = MagicMock()
        pipeline_state.check_idempotency.return_value = True
        written_orders: list[str] = []

        def _write_order(_ts: str, _uid: str, content: str, _queue_dir: str, **_kw) -> str:
            written_orders.append(content)
            return "ts-claude-order-uuid.md"

        pipeline_state.write_order_file.side_effect = _write_order

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
        assert written_orders[-1] == "default order"

    def test_backward_compat_no_order_builders_kwarg(self):
        """Legacy call style without order_builders still works."""
        from unittest.mock import MagicMock


        pipeline_state = MagicMock()
        pipeline_state.check_idempotency.return_value = True
        written_orders: list[str] = []

        def _write_order(_ts: str, _uid: str, content: str, _queue_dir: str, **_kw) -> str:
            written_orders.append(content)
            return "ts-claude-order-uuid.md"

        pipeline_state.write_order_file.side_effect = _write_order

        order_builder = MagicMock()
        order_builder.build_order.return_value = "order content"

        api = LLMPipelineAPI(
            pipeline_state=pipeline_state,
            order_builder=order_builder,
            queue_dir="queue",
        )

        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        api.submit(steps, {"workflow_name": "issuesmith"}, audit_context=_TEST_AUDIT_CTX)  # always falls back to default
        assert written_orders[-1] == "order content"


# ---------------------------------------------------------------------------
# Issue #3029: submit(order_builder=..., workflow_roles=...) public kwargs
# ---------------------------------------------------------------------------


class TestSubmitOrderBuilderAndWorkflowRoles:
    def test_submit_order_builder_kwarg_skips_resolve(self):
        """With submit(order_builder=wrapped), _resolve_order_builder is not called."""
        api, pipeline_state, _ = _make_api()
        wrapped = MagicMock()
        wrapped.build_order.return_value = "wrapped order"
        written_orders: list[str] = []

        def _write_order(_ts: str, _uid: str, content: str, _queue_dir: str, **_kw) -> str:
            written_orders.append(content)
            return "ts-claude-order-uuid.md"

        pipeline_state.write_order_file.side_effect = _write_order
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]

        resolve_called = False

        def _resolve(*args, **kwargs):
            nonlocal resolve_called
            resolve_called = True
            return api._resolve_order_builder(*args, **kwargs)

        with patch.object(api, "_resolve_order_builder", side_effect=_resolve):
            exec_lines = api.submit(
                steps,
                {"workflow_name": "issuesmith", "issue_number": "1"},
                audit_context=_TEST_AUDIT_CTX,
                order_builder=wrapped,
            )

        assert resolve_called is False
        assert written_orders[-1] == "wrapped order"
        assert len(exec_lines) == 1

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
        gate = QuotaGate(tmp_path / "quota-gate.json", pause_ttl_seconds=None)
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
        decision = gate.begin_run(
            task_uuid="task-1",
            engine="claude",
            role="design",
            role_engines=["claude", "codex"],
            now=datetime(2026, 9, 5, 12, 1, tzinfo=jst),
        )
        assert decision.allowed is False
