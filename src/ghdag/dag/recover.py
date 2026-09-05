"""dag/recover.py — recover failed or pending steps from an existing handler run."""

from __future__ import annotations

import logging
import re
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from ghdag.io.done import interpret_done, read_done_content
from ghdag.io import exec_jsonl

logger = logging.getLogger(__name__)

_ORDER_PATH_RE = re.compile(r"[\w./-]+-order-([0-9a-f-]{36})\.md")


@dataclass
class RecoverStepInfo:
    uuid: str
    step_name: str
    depends: list[str]
    command: str
    status: str  # success | failed | pending | running | skipped


@dataclass
class RecoverPlan:
    idempotency_key: str
    generation: int
    steps: list[RecoverStepInfo]
    rerun_uuids: list[str]


@dataclass
class RecoverResult:
    plan: RecoverPlan
    dry_run: bool
    recovered: int
    warnings: list[str]
    errors: list[str]


class RecoverError(Exception):
    """Recover cannot proceed (e.g. missing order files)."""


def plan_recover(
    *,
    state_dir: str | Path,
    exec_jsonl_path: str | Path,
    workflow_name: str,
    handler_name: str,
    issue_number: int,
    queue_dir: str | Path,
    done_dir: str | Path,
    from_step: str | None = None,
    running_uuids: set[str] | None = None,
) -> RecoverPlan:
    """Build a recover plan for the current handler generation."""
    generation = exec_jsonl.get_generation(
        state_dir, workflow_name, handler_name, issue_number,
    )
    idempotency_key = exec_jsonl.build_idempotency_key(
        workflow_name, handler_name, issue_number, generation,
    )
    records = exec_jsonl.find_records_by_idempotency_key(exec_jsonl_path, idempotency_key)
    if not records:
        return RecoverPlan(
            idempotency_key=idempotency_key,
            generation=generation,
            steps=[],
            rerun_uuids=[],
        )

    done_path = Path(done_dir)
    running = running_uuids or set()
    step_infos: list[RecoverStepInfo] = []
    for rec in records:
        uuid = str(rec["uuid"])
        annotations = rec.get("annotations") or {}
        step_name = str(annotations.get("step_name") or uuid)
        raw_done = read_done_content(done_path, uuid)
        if uuid in running:
            status = "running"
        elif raw_done is None:
            status = "pending"
        elif interpret_done(raw_done) == "success":
            status = "success"
        else:
            status = "failed"

        step_infos.append(
            RecoverStepInfo(
                uuid=uuid,
                step_name=step_name,
                depends=[str(d) for d in rec.get("depends", [])],
                command=str(rec.get("command", "")),
                status=status,
            )
        )

    downstream_uuids: set[str] | None = None
    if from_step is not None:
        start_uuid = _find_step_uuid(step_infos, from_step)
        if start_uuid is None:
            raise RecoverError(
                f"step '{from_step}' not found in run (idempotency_key={idempotency_key})"
            )
        downstream_uuids = _collect_downstream(start_uuid, step_infos)

    rerun_uuids: list[str] = []
    for info in _topological_sort(step_infos):
        if info.status == "success":
            continue
        if downstream_uuids is not None and info.uuid not in downstream_uuids:
            continue
        if info.status == "running":
            continue
        rerun_uuids.append(info.uuid)

    return RecoverPlan(
        idempotency_key=idempotency_key,
        generation=generation,
        steps=step_infos,
        rerun_uuids=rerun_uuids,
    )


def execute_recover(
    plan: RecoverPlan,
    *,
    queue_dir: str | Path,
    done_dir: str | Path,
    dry_run: bool = False,
    running_uuids: set[str] | None = None,
) -> RecoverResult:
    """Execute (or dry-run) a recover plan."""
    queue_path = Path(queue_dir)
    done_path = Path(done_dir)
    running = running_uuids or set()
    warnings: list[str] = []
    errors: list[str] = []
    recovered = 0

    rerun_set = set(plan.rerun_uuids)
    for info in plan.steps:
        if info.uuid in running and info.uuid in rerun_set:
            msg = (
                f"step '{info.step_name}' (uuid={info.uuid}) is running — skipped"
            )
            warnings.append(msg)
            logger.warning(msg)

    for uuid in plan.rerun_uuids:
        info = next(s for s in plan.steps if s.uuid == uuid)
        order_path = _resolve_order_path(info, queue_path)
        if order_path is None or not order_path.is_file():
            expected = _expected_order_path(info.uuid, queue_path)
            msg = (
                f"order file missing for step '{info.step_name}' "
                f"(uuid={uuid}, expected={expected})"
            )
            errors.append(msg)
            errors.append(
                "  → Recover is not possible. Start a new run with: "
                "ghdag trigger --issue <N> --handler <handler> --redispatch --reason \"...\""
            )
            raise RecoverError("\n".join(errors))

        if dry_run:
            continue

        done_file = done_path / uuid
        if done_file.is_file():
            done_file.unlink()
        recovered += 1

    return RecoverResult(
        plan=plan,
        dry_run=dry_run,
        recovered=recovered,
        warnings=warnings,
        errors=errors,
    )


def format_recover_plan(plan: RecoverPlan) -> str:
    """Human-readable summary for --dry-run output."""
    lines = [
        f"idempotency_key: {plan.idempotency_key}",
        f"generation: {plan.generation}",
        f"steps: {len(plan.steps)}",
        f"rerun targets: {len(plan.rerun_uuids)}",
        "",
    ]
    uuid_set = set(plan.rerun_uuids)
    for info in plan.steps:
        marker = " [rerun]" if info.uuid in uuid_set else ""
        deps = ", ".join(info.depends) if info.depends else "-"
        lines.append(
            f"  {info.step_name} uuid={info.uuid} status={info.status} depends=[{deps}]{marker}"
        )
    return "\n".join(lines)


def collect_running_uuids(
    done_dir: str | Path,
    *,
    candidate_uuids: set[str],
    running_uuids: set[str],
) -> set[str]:
    """Return candidate UUIDs that are currently marked as running."""
    return {uuid for uuid in candidate_uuids if uuid in running_uuids}


def _find_step_uuid(step_infos: list[RecoverStepInfo], step_name: str) -> str | None:
    for info in step_infos:
        if info.step_name == step_name:
            return info.uuid
    return None


def _collect_downstream(start_uuid: str, step_infos: list[RecoverStepInfo]) -> set[str]:
    """Return start_uuid and all transitive dependents."""
    dependents: dict[str, list[str]] = {info.uuid: [] for info in step_infos}
    for info in step_infos:
        for dep in info.depends:
            if dep in dependents:
                dependents[dep].append(info.uuid)

    result = {start_uuid}
    queue: deque[str] = deque([start_uuid])
    while queue:
        current = queue.popleft()
        for child in dependents.get(current, []):
            if child not in result:
                result.add(child)
                queue.append(child)
    return result


def _topological_sort(step_infos: list[RecoverStepInfo]) -> list[RecoverStepInfo]:
    by_uuid = {info.uuid: info for info in step_infos}
    in_degree = {info.uuid: 0 for info in step_infos}
    adjacency: dict[str, list[str]] = {info.uuid: [] for info in step_infos}

    for info in step_infos:
        for dep in info.depends:
            if dep in by_uuid:
                adjacency[dep].append(info.uuid)
                in_degree[info.uuid] += 1

    queue = deque([uid for uid, deg in in_degree.items() if deg == 0])
    ordered: list[RecoverStepInfo] = []
    while queue:
        uid = queue.popleft()
        ordered.append(by_uuid[uid])
        for child in adjacency[uid]:
            in_degree[child] -= 1
            if in_degree[child] == 0:
                queue.append(child)
    return ordered


def _resolve_order_path(info: RecoverStepInfo, queue_dir: Path) -> Path | None:
    match = _ORDER_PATH_RE.search(info.command)
    if match:
        candidate = queue_dir / Path(match.group(0)).name
        if candidate.is_file():
            return candidate
        alt = Path(match.group(0))
        if alt.is_file():
            return alt

    expected = _expected_order_path(info.uuid, queue_dir)
    if expected.is_file():
        return expected
    return None


def _expected_order_path(uuid: str, queue_dir: Path) -> Path:
    if not queue_dir.is_dir():
        return queue_dir / f"*-order-{uuid}.md"
    matches = list(queue_dir.glob(f"*-order-{uuid}.md"))
    if matches:
        return matches[0]
    return queue_dir / f"*-order-{uuid}.md"
