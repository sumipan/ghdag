"""ghdag.dag — Generic DAG execution engine."""

from ghdag.core.vocabulary import (
    DONE_SUCCESS,
    LEGACY_DONE_DEFERRED,
    PIPELINE_WAIT_QUOTA,
    WAIT_QUOTA_COMPANION_RE,
)

from ._util import WaitQuotaSignal, check_pipeline_status, default_check_rejected, parse_wait_quota_signal
from ._util import _extract_tee_target as extract_tee_target
from .audit_hooks import AuditHooks
from .engine import DagEngine
from .fanout import FanOutSpec, build_child_jsonl_record, parse_fanout_spec
from .hooks import DagHooks, DefaultHooks
from .models import DagConfig, RunningTask, Task
from .parser import parse_jsonl
from .recover import execute_recover, plan_recover, running_uuids_from_queue_dir

__all__ = [
    "AuditHooks",
    "DagConfig",
    "DagEngine",
    "DagHooks",
    "DefaultHooks",
    "DONE_SUCCESS",
    "FanOutSpec",
    "LEGACY_DONE_DEFERRED",
    "PIPELINE_WAIT_QUOTA",
    "RunningTask",
    "Task",
    "WAIT_QUOTA_COMPANION_RE",
    "WaitQuotaSignal",
    "build_child_jsonl_record",
    "check_pipeline_status",
    "default_check_rejected",
    "execute_recover",
    "extract_tee_target",
    "parse_fanout_spec",
    "parse_jsonl",
    "parse_wait_quota_signal",
    "plan_recover",
    "running_uuids_from_queue_dir",
]
