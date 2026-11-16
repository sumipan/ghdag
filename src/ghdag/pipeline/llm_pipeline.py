"""
pipeline/llm_pipeline.py — step dependency validation and order builder helpers

Task enqueue is implemented by ``TaskQueue`` in ``task_queue.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ghdag.core.exceptions import ConfigError
from ghdag.pipeline.order import OrderBuilder


class DependencyError(ConfigError):
    """Raised when step dependencies are invalid or circular."""

    code = "STEP_DEPENDENCY_INVALID"

if TYPE_CHECKING:
    from ghdag.core.models.workflow import StepConfig, WorkflowConfig


def build_order_builders(workflows: list[WorkflowConfig]) -> dict[str, OrderBuilder]:
    """Build per-workflow TemplateOrderBuilders from loaded workflow configs."""
    from ghdag.pipeline.order import TemplateOrderBuilder

    builders: dict[str, OrderBuilder] = {}
    for wf in workflows:
        if wf.template_dir is None:
            raise ConfigError(
                f"workflow {wf.name!r} has no template_dir; load via load_workflows",
                code="WORKFLOW_TEMPLATE_DIR_MISSING",
            )
        builders[wf.name] = TemplateOrderBuilder(wf.template_dir)
    return builders


def validate_depends(steps: "list[StepConfig]") -> None:
    """Pre-validate depends: detect undefined references and cycles.

    Raises:
        ValueError: an undefined dep_id exists, or there is a cycle
    """
    step_ids = {s.id for s in steps if s.id is not None}

    for step in steps:
        for dep_id in step.depends:
            if dep_id not in step_ids:
                raise DependencyError(
                    f"Unknown dependency: {dep_id!r}",
                    code="STEP_DEPENDENCY_UNKNOWN",
                    hint=f"defined step ids: {sorted(step_ids)}",
                )

    # Detect cycles via topological sort
    in_degree: dict[str, int] = {s.id: 0 for s in steps if s.id is not None}
    adjacency: dict[str, list[str]] = {s.id: [] for s in steps if s.id is not None}

    for step in steps:
        if step.id is None:
            continue
        for dep_id in step.depends:
            adjacency[dep_id].append(step.id)
            in_degree[step.id] += 1

    queue = [sid for sid, deg in in_degree.items() if deg == 0]
    visited = 0
    while queue:
        node = queue.pop(0)
        visited += 1
        for neighbor in adjacency[node]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if visited < len(in_degree):
        raise DependencyError(
            "circular dependency detected among steps",
            code="STEP_DEPENDENCY_CYCLE",
        )

    dep_map: dict[str, set[str]] = {
        s.id: set(s.depends) for s in steps if s.id is not None
    }

    def _is_transitive_ancestor(child_id: str, ancestor_id: str) -> bool:
        seen: set[str] = set()
        queue = list(dep_map.get(child_id, set()))
        while queue:
            dep = queue.pop(0)
            if dep == ancestor_id:
                return True
            if dep in seen:
                continue
            seen.add(dep)
            queue.extend(dep_map.get(dep, set()))
        return False

    for step in steps:
        resume_from = step.resume_from
        if resume_from is None:
            continue
        if not isinstance(resume_from, str):
            raise DependencyError(
                f"resume_from must be a single step id string (fan-in is unsupported): {resume_from!r}",
                code="STEP_RESUME_FROM_INVALID",
            )
        if step.id is None:
            raise DependencyError(
                "resume_from requires step.id to be set",
                code="STEP_RESUME_FROM_INVALID",
            )
        if resume_from not in step_ids:
            raise DependencyError(
                f"Unknown resume_from dependency: {resume_from!r}",
                code="STEP_RESUME_FROM_INVALID",
            )
        if step.id == resume_from:
            raise DependencyError(
                "resume_from cannot reference itself",
                code="STEP_RESUME_FROM_INVALID",
            )
        if not _is_transitive_ancestor(step.id, resume_from):
            raise DependencyError(
                f"resume_from {resume_from!r} must be a transitive ancestor of step {step.id!r}",
                code="STEP_RESUME_FROM_INVALID",
            )


@dataclass
class SubmittedStep:
    """Information about an enqueued step, included in enqueue return values."""
    step_id: str
    uuid: str
    order_filename: str
    result_filename: str
    exec_line: str
