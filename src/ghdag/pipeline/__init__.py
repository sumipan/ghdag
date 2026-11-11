"""ghdag.pipeline — Layer 1 pipeline public API."""

from ghdag.core.command import get_adapter
from ghdag.io.audit import AuditContext, write_task_exit_audit
from ghdag.io.audit_query import get_latest_status, read_task_exit_events
from ghdag.io.exec_jsonl import build_idempotency_key
from ghdag.pipeline.config import (
    ModelValidationError,
    PipelineConfig,
    resolve_models,
)
from ghdag.pipeline.llm_pipeline import LLMPipelineAPI, SubmittedStep
from ghdag.pipeline.order import (
    InlineOrderBuilder,
    OrderBuilder,
    TemplateOrderBuilder,
    TemplateVariableError,
)
from ghdag.pipeline.state import PipelineState, parse_frontmatter, status_rank
from ghdag.pipeline.status import (
    STATE_DEFERRED,
    STATE_EMPTY,
    STATE_ENGINE_ERROR,
    STATE_FAIL,
    STATE_OK,
    STATE_PENDING_DEPS,
    STATE_PENDING_RUN,
    STATE_REJECTED,
    STATE_RUNNING,
    STATE_UNKNOWN_DONE,
    state_label,
    task_status,
)
from ghdag.pipeline.wait import wait_for_result

__all__ = [
    "AuditContext",
    "ModelValidationError",
    "PipelineConfig",
    "PipelineState",
    "OrderBuilder",
    "TemplateOrderBuilder",
    "InlineOrderBuilder",
    "TemplateVariableError",
    "build_idempotency_key",
    "get_adapter",
    "resolve_models",
    "status_rank",
    "parse_frontmatter",
    "LLMPipelineAPI",
    "SubmittedStep",
    "task_status",
    "state_label",
    "wait_for_result",
    "read_task_exit_events",
    "get_latest_status",
    "STATE_EMPTY",
    "STATE_DEFERRED",
    "STATE_ENGINE_ERROR",
    "STATE_FAIL",
    "STATE_OK",
    "STATE_PENDING_DEPS",
    "STATE_PENDING_RUN",
    "STATE_REJECTED",
    "STATE_RUNNING",
    "STATE_UNKNOWN_DONE",
    "write_task_exit_audit",
]
