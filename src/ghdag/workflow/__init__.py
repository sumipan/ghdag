"""ghdag.workflow — Layer 2 workflow engine public API"""

from ghdag.core.ports.forge import ForgePort
from ghdag.forge import get_forge
from ghdag.github_client import GitHubClient as GitHubIssueClient
from ghdag.github_client import create_github_client
from ghdag.workflow import engine as engine  # noqa: F401 — triggers adapter registration
from ghdag.workflow.dispatcher import WorkflowDispatcher
from ghdag.workflow.loader import load_workflows
from ghdag.workflow.schema import (
    DispatchResult,
    HandlerConfig,
    OnTriggerConfig,
    StepConfig,
    TriggerConfig,
    WorkflowConfig,
)
from ghdag.workflow.state_machine import transition
from ghdag.workflow.typecheck import ConsumeEntry, SkillIOSpec, typecheck_dag

__all__ = [
    "ConsumeEntry",
    "DispatchResult",
    "ForgePort",
    "GitHubIssueClient",
    "HandlerConfig",
    "OnTriggerConfig",
    "SkillIOSpec",
    "StepConfig",
    "TriggerConfig",
    "WorkflowConfig",
    "load_workflows",
    "WorkflowDispatcher",
    "create_github_client",
    "get_forge",
    "transition",
    "typecheck_dag",
]
