"""ghdag.pipeline — Layer 1 パイプライン公開 API"""

from ghdag.io.audit_query import get_latest_status, read_task_exit_events
from ghdag.pipeline.config import (
    ModelValidationError,
    PipelineConfig,
    build_agent_cmd,
    resolve_models,
)
from ghdag.pipeline.hooks import AuditHooks
from ghdag.pipeline.llm_pipeline import LLMPipelineAPI, SubmittedStep
from ghdag.pipeline.order import InlineOrderBuilder, OrderBuilder, TemplateOrderBuilder
from ghdag.pipeline.state import PipelineState, parse_frontmatter, status_rank
from ghdag.pipeline.status import (
    STATE_EMPTY,
    STATE_FAIL,
    STATE_OK,
    STATE_PENDING_DEPS,
    STATE_PENDING_RUN,
    STATE_REJECTED,
    STATE_RUNNING,
    STATE_UNKNOWN_DONE,
    task_status,
)
from ghdag.pipeline.submit import make_order_record, submit_order
from ghdag.pipeline.wait import wait_for_result

__all__ = [
    "AuditHooks",
    "ModelValidationError",
    "PipelineConfig",
    "PipelineState",
    "OrderBuilder",
    "TemplateOrderBuilder",
    "InlineOrderBuilder",
    "resolve_models",
    "build_agent_cmd",
    "status_rank",
    "parse_frontmatter",
    "LLMPipelineAPI",
    "SubmittedStep",
    "task_status",
    "wait_for_result",
    "read_task_exit_events",
    "get_latest_status",
    "STATE_EMPTY",
    "STATE_FAIL",
    "STATE_OK",
    "STATE_PENDING_DEPS",
    "STATE_PENDING_RUN",
    "STATE_REJECTED",
    "STATE_RUNNING",
    "STATE_UNKNOWN_DONE",
    "make_order_record",
    "submit_order",
]
