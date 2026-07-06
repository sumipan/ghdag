"""tests/loop/test_budget.py — LoopBudget 単体テスト"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ghdag.loop import BudgetExceededError, LoopBudget, from_skill_meta


class TestLoopBudgetCheck:
    def test_wall_clock_exceeded(self):
        budget = LoopBudget(wall_clock=60.0)
        with pytest.raises(BudgetExceededError) as exc_info:
            budget.check(elapsed=61.0)
        assert "wall_clock" in exc_info.value.dimensions

    def test_steps_exceeded_at_limit(self):
        budget = LoopBudget(steps=3)
        with pytest.raises(BudgetExceededError) as exc_info:
            budget.check(step=3)
        assert "steps" in exc_info.value.dimensions

    def test_steps_ok_below_limit(self):
        budget = LoopBudget(steps=3)
        budget.check(step=2)  # 例外なし

    def test_all_none_never_raises(self):
        budget = LoopBudget()
        budget.check(elapsed=9999.0, tokens=9999999, cost=9999.0, step=9999)

    def test_multi_dimension_both_in_dimensions(self):
        budget = LoopBudget(wall_clock=60, token=1000)
        with pytest.raises(BudgetExceededError) as exc_info:
            budget.check(elapsed=61, tokens=1001)
        dims = exc_info.value.dimensions
        assert "wall_clock" in dims
        assert "token" in dims

    def test_token_exceeded(self):
        budget = LoopBudget(token=500)
        with pytest.raises(BudgetExceededError) as exc_info:
            budget.check(tokens=501)
        assert "token" in exc_info.value.dimensions

    def test_cost_exceeded(self):
        budget = LoopBudget(cost=1.0)
        with pytest.raises(BudgetExceededError) as exc_info:
            budget.check(cost=1.01)
        assert "cost" in exc_info.value.dimensions

    def test_error_str_contains_info(self):
        budget = LoopBudget(wall_clock=60.0)
        with pytest.raises(BudgetExceededError) as exc_info:
            budget.check(elapsed=61.0)
        msg = str(exc_info.value)
        assert "wall_clock" in msg

    def test_all_dimensions_evaluated_before_raise(self):
        budget = LoopBudget(wall_clock=10, token=100, cost=0.5, steps=5)
        with pytest.raises(BudgetExceededError) as exc_info:
            budget.check(elapsed=11, tokens=101, cost=0.6, step=5)
        dims = exc_info.value.dimensions
        assert set(dims) == {"wall_clock", "token", "cost", "steps"}


class TestLoopBudgetRemaining:
    def test_wall_clock_remaining(self):
        budget = LoopBudget(wall_clock=60)
        result = budget.remaining(elapsed=20)
        assert result == {"wall_clock": 40.0}

    def test_steps_remaining(self):
        budget = LoopBudget(steps=5)
        result = budget.remaining(step=2)
        assert result == {"steps": 3.0}

    def test_all_none_returns_empty(self):
        budget = LoopBudget()
        assert budget.remaining() == {}

    def test_multiple_dimensions(self):
        budget = LoopBudget(wall_clock=100, token=1000)
        result = budget.remaining(elapsed=30, tokens=400)
        assert result == {"wall_clock": 70.0, "token": 600.0}

    def test_none_dimension_excluded(self):
        budget = LoopBudget(wall_clock=60, token=None)
        result = budget.remaining(elapsed=10, tokens=100)
        assert "token" not in result
        assert result == {"wall_clock": 50.0}


class TestFromSkillMeta:
    def test_basic_mapping(self):
        result = from_skill_meta({"max_iterations": 5, "wall_clock_limit": 120.0})
        assert result == LoopBudget(steps=5, wall_clock=120.0)

    def test_empty_meta(self):
        result = from_skill_meta({})
        assert result == LoopBudget()

    def test_unknown_keys_ignored(self):
        result = from_skill_meta({"unknown_key": 42})
        assert result == LoopBudget()

    def test_token_limit(self):
        result = from_skill_meta({"token_limit": 5000})
        assert result == LoopBudget(token=5000)

    def test_cost_limit(self):
        result = from_skill_meta({"cost_limit": 2.5})
        assert result == LoopBudget(cost=2.5)

    def test_none_value_treated_as_unlimited(self):
        result = from_skill_meta({"max_iterations": None})
        assert result == LoopBudget()

    def test_all_fields(self):
        result = from_skill_meta({
            "max_iterations": 10,
            "wall_clock_limit": 300.0,
            "token_limit": 10000,
            "cost_limit": 5.0,
        })
        assert result == LoopBudget(steps=10, wall_clock=300.0, token=10000, cost=5.0)


class TestLLMPipelineLoopBudget:
    def _make_api(self, tmp_path: Path):
        from ghdag.pipeline.llm_pipeline import LLMPipelineAPI
        from ghdag.pipeline.state import PipelineState

        exec_jsonl = tmp_path / "exec.jsonl"
        exec_jsonl.write_text("", encoding="utf-8")
        state = PipelineState(state_dir=tmp_path / "state", exec_jsonl_path=exec_jsonl)

        order_builder = MagicMock()
        order_builder.build_order.return_value = "order content"

        api = LLMPipelineAPI(
            pipeline_state=state,
            order_builder=order_builder,
            queue_dir=str(tmp_path / "queue"),
        )
        (tmp_path / "queue").mkdir()
        return api

    def test_loop_budget_stored_in_metadata(self, tmp_path):
        from ghdag.pipeline.audit import AuditContext
        from ghdag.workflow.schema import StepConfig

        budget = LoopBudget(steps=3)
        audit = AuditContext(source="test")

        state_mock = MagicMock()
        state_mock.write_order_file.return_value = "20240101000000-claude-order-uuid.md"
        state_mock.check_idempotency.return_value = False

        from ghdag.pipeline.llm_pipeline import LLMPipelineAPI
        from ghdag.pipeline.state import PipelineState

        exec_jsonl = tmp_path / "exec2.jsonl"
        exec_jsonl.write_text("", encoding="utf-8")
        real_state = PipelineState(state_dir=tmp_path / "state2", exec_jsonl_path=exec_jsonl)

        order_builder = MagicMock()
        order_builder.build_order.return_value = "order content"

        (tmp_path / "queue").mkdir(exist_ok=True)
        api2 = LLMPipelineAPI(
            pipeline_state=real_state,
            order_builder=order_builder,
            queue_dir=str(tmp_path / "queue"),
        )
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        records = api2.submit(
            steps,
            {"workflow_name": "test"},
            audit_context=audit,
            loop_budget=budget,
        )
        assert len(records) == 1
        rec = json.loads(records[0])
        annotations = rec.get("annotations", {})
        assert "loop_budget_steps" in annotations
        assert annotations["loop_budget_steps"] == "3"

    def test_submit_without_loop_budget_unchanged(self, tmp_path):
        from ghdag.pipeline.audit import AuditContext
        from ghdag.pipeline.llm_pipeline import LLMPipelineAPI
        from ghdag.pipeline.state import PipelineState
        from ghdag.workflow.schema import StepConfig

        exec_jsonl = tmp_path / "exec3.jsonl"
        exec_jsonl.write_text("", encoding="utf-8")
        real_state = PipelineState(state_dir=tmp_path / "state3", exec_jsonl_path=exec_jsonl)

        order_builder = MagicMock()
        order_builder.build_order.return_value = "order content"

        queue_dir = tmp_path / "queue3"
        queue_dir.mkdir()
        api = LLMPipelineAPI(
            pipeline_state=real_state,
            order_builder=order_builder,
            queue_dir=str(queue_dir),
        )
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        audit = AuditContext(source="test")
        records = api.submit(steps, {"workflow_name": "test"}, audit_context=audit)
        assert len(records) == 1
        rec = json.loads(records[0])
        annotations = rec.get("annotations", {})
        for key in annotations:
            assert not key.startswith("loop_budget_"), f"Unexpected key: {key}"
