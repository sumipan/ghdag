"""ghdag — Generic DAG execution engine."""

from __future__ import annotations

import importlib
import warnings
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("ghdag")
except PackageNotFoundError:
    __version__ = "unknown"

from ghdag.core.exceptions import GhdagError

__all__ = [
    "GhdagError",
]

# Transition-only lazy aliases for host consumers (sumipan/nexus#6486). Not in __all__.
_DEPRECATED_EXPORTS: dict[str, tuple[str, str]] = {
    "DagEngine": ("ghdag.dag", "DagEngine"),
    "IssueStatus": ("ghdag.status", "IssueStatus"),
    "LLMPipelineAPI": ("ghdag.pipeline.llm_pipeline", "LLMPipelineAPI"),
    "PipelineState": ("ghdag.pipeline.state", "PipelineState"),
    "QueueTask": ("ghdag.io.queue", "QueueTask"),
    "QueueTaskStore": ("ghdag.io.queue", "QueueTaskStore"),
    "QuotaGate": ("ghdag.quota", "QuotaGate"),
    "RunningTask": ("ghdag.status", "RunningTask"),
    "StepStatus": ("ghdag.status", "StepStatus"),
    "WorkflowDispatcher": ("ghdag.workflow", "WorkflowDispatcher"),
    "issue_status": ("ghdag.status", "issue_status"),
    "running_tasks": ("ghdag.status", "running_tasks"),
}


def __getattr__(name: str):
    target = _DEPRECATED_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = target
    warnings.warn(
        f"ghdag.{name} is deprecated; use {module_name}.{attr_name}",
        DeprecationWarning,
        stacklevel=2,
    )
    module = importlib.import_module(module_name)
    return getattr(module, attr_name)


def __dir__() -> list[str]:
    return sorted({*globals(), *_DEPRECATED_EXPORTS})
