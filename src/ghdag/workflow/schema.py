"""workflow/schema.py — WorkflowConfig dataclass (re-export shim)."""

from ghdag.core.models.workflow import (
    DispatchResult,
    HandlerConfig,
    NonterminalClosedConfig,
    OnTriggerConfig,
    StepConfig,
    TriggerConfig,
    WorkflowConfig,
)

__all__ = [
    "StepConfig",
    "OnTriggerConfig",
    "HandlerConfig",
    "TriggerConfig",
    "DispatchResult",
    "NonterminalClosedConfig",
    "WorkflowConfig",
]
