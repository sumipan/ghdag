"""Tests for ghdag.pipeline.llm_pipeline — TaskQueue (Issue #203)."""

from __future__ import annotations

import pytest

from ghdag.core.exceptions import ConfigError
from ghdag.core.models.workflow import (
    HandlerConfig,
    StepConfig,
    TriggerConfig,
    WorkflowConfig,
)
from ghdag.pipeline.llm_pipeline import build_order_builders


def test_build_order_builders_rejects_missing_template_dir() -> None:
    """Refs sumipan/nexus#6803 — unloaded WorkflowConfig without template_dir is rejected."""
    wf = WorkflowConfig(
        name="orphan",
        triggers=[TriggerConfig(label="l", handler="h")],
        handlers={
            "h": HandlerConfig(steps=[StepConfig(template="t", model="m")]),
        },
        template_dir=None,
    )
    with pytest.raises(ConfigError, match="orphan"):
        build_order_builders([wf])


# ---------------------------------------------------------------------------
# Issue #3029: submit(order_builder=...) public kwargs
# ---------------------------------------------------------------------------
