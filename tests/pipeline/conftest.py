"""Shared helpers for ghdag.pipeline.llm_pipeline tests."""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

from ghdag.io.audit import AuditContext
from ghdag.pipeline.llm_pipeline import LLMPipelineAPI

_TEST_AUDIT_CTX = AuditContext(source="test")


def _track_build_order(order_builder: MagicMock) -> list[tuple[str, dict]]:
    """Record build_order(template, ctx) calls via side_effect."""
    captured: list[tuple[str, dict]] = []

    def _side(template: str, ctx: dict) -> str:
        captured.append((template, ctx))
        return order_builder.build_order.return_value or "order content"

    order_builder.build_order.side_effect = _side
    return captured


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


_helpers = types.ModuleType("ghdag_pipeline_conftest_helpers")
sys.modules["ghdag_pipeline_conftest_helpers"] = _helpers
for _name in ("_TEST_AUDIT_CTX", "_track_build_order", "_make_api"):
    setattr(_helpers, _name, globals()[_name])
