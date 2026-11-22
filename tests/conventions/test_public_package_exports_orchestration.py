"""Orchestration-layer checks for pipeline / workflow ``__all__``."""

from __future__ import annotations

import importlib

# Issue sumipan/nexus#6587 — orchestration packages.
CURATED_ORCHESTRATION_EXPORTS: dict[str, frozenset[str]] = {
    "ghdag.pipeline": frozenset(
        {
            "AuditContext",
            "PipelineConfig",
            "TaskQueue",
            "OrderBuilder",
            "TemplateOrderBuilder",
            "InlineOrderBuilder",
            "TemplateVariableError",
            "build_idempotency_key",
            "get_command_adapter",
            "resolve_models",
            "wait_for_result",
            "write_task_exit_audit",
        }
    ),
    "ghdag.workflow": frozenset(
        {
            "ConsumeEntry",
            "HandlerConfig",
            "SkillIOSpec",
            "StepConfig",
            "TriggerConfig",
            "WorkflowConfig",
            "load_workflows",
            "WorkflowDispatcher",
            "transition",
            "typecheck_dag",
        }
    ),
}


def test_orchestration_public_exports_match_curated_contract() -> None:
    """Issue sumipan/nexus#6587 — pipeline and workflow ``__all__`` match the curated set."""
    for module_name, expected in CURATED_ORCHESTRATION_EXPORTS.items():
        mod = importlib.import_module(module_name)
        assert frozenset(mod.__all__) == expected, module_name
