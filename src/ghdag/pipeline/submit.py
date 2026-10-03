"""ghdag/pipeline/submit.py — order submission helpers"""

from __future__ import annotations

import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from ghdag.core.command import get_adapter
from ghdag.pipeline.audit import AuditContext
from ghdag.pipeline.state import PipelineState

_TZ = ZoneInfo("Asia/Tokyo")


def make_order_record(
    state: PipelineState,
    *,
    engine: str,
    content: str,
    model: str | None = None,
    queue_dir: str = "jobs",
    depends: list[str] | None = None,
    annotations: dict[str, str] | None = None,
    idempotency_key: str | None = None,
) -> tuple[dict, str]:
    """Write the order file, build an exec record and return it. Does not append.

    Returns:
        (record, uuid) — record is the dict returned by build_exec_record.
    """
    ts = datetime.now(_TZ).strftime("%Y%m%d%H%M%S")
    uid = str(uuid.uuid4())

    order_filename = state.write_order_file(ts, uid, content, queue_dir, engine)
    order_path = f"{queue_dir}/{order_filename}"
    result_path = f"{queue_dir}/{ts}-{engine}-result-{uid}.md"

    record = get_adapter(engine).build_exec_record(
        uuid=uid,
        order_path=order_path,
        result_path=result_path,
        model=model,
        depends=depends or [],
    )

    if annotations:
        record.setdefault("annotations", {}).update(annotations)

    if idempotency_key is not None:
        record["idempotency_key"] = idempotency_key

    return record, uid


def submit_order(
    state: PipelineState,
    *,
    engine: str,
    content: str,
    audit_source: str,
    model: str | None = None,
    queue_dir: str = "jobs",
    depends: list[str] | None = None,
    annotations: dict[str, str] | None = None,
    idempotency_key: str | None = None,
    correlation_id: str | None = None,
) -> dict:
    """Create order → build exec record → append to exec.jsonl in one call.

    Returns:
        record dict (build_exec_record return value with annotations etc. added).
    """
    record, uid = make_order_record(
        state,
        engine=engine,
        content=content,
        model=model,
        queue_dir=queue_dir,
        depends=depends,
        annotations=annotations,
        idempotency_key=idempotency_key,
    )

    audit_ctx = AuditContext(
        source=audit_source,
        correlation_id=correlation_id if correlation_id is not None else uid,
    )
    state.append_exec_records([record], audit_context=audit_ctx)

    return record
