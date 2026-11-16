"""ghdag.pipeline — Layer 1 pipeline public API."""

from ghdag.core.command import get_command_adapter
from ghdag.io.audit import AuditContext, write_task_exit_audit
from ghdag.io.exec_jsonl import build_idempotency_key
from ghdag.pipeline.config import PipelineConfig, resolve_models
from ghdag.pipeline.order import (
    InlineOrderBuilder,
    OrderBuilder,
    TemplateOrderBuilder,
    TemplateVariableError,
)
from ghdag.pipeline.task_queue import TaskQueue
from ghdag.pipeline.wait import wait_for_result

__all__ = [
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
]
