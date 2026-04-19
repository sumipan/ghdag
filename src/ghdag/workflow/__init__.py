"""ghdag.workflow — Layer 2 ワークフローエンジン公開 API"""

from ghdag.workflow.dispatcher import WorkflowDispatcher
from ghdag.workflow import engine as engine  # noqa: F401 — triggers adapter registration
from ghdag.workflow.github import GitHubIssueClient
from ghdag.workflow.loader import load_workflows
from ghdag.workflow.schema import (
    DispatchResult,
    HandlerConfig,
    OnTriggerConfig,
    StepConfig,
    TriggerConfig,
    WorkflowConfig,
)

__all__ = [
    "WorkflowConfig",
    "TriggerConfig",
    "HandlerConfig",
    "StepConfig",
    "OnTriggerConfig",
    "DispatchResult",
    "load_workflows",
    "WorkflowDispatcher",
    "GitHubIssueClient",
]
