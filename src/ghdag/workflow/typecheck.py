"""workflow/typecheck.py — static type checking for skill I/O across DAG steps."""

from __future__ import annotations

from dataclasses import dataclass, field

from ghdag.workflow.schema import StepConfig, WorkflowConfig


@dataclass
class ConsumeEntry:
    """One entry in a skill's consumes list."""

    producer: str
    content_type: str


@dataclass
class SkillIOSpec:
    """Lightweight skill I/O contract for typecheck."""

    produces_content_type: str = "text/markdown"
    consumes: list[ConsumeEntry] = field(default_factory=list)


@dataclass
class TypeCheckError:
    """Typecheck failure for a workflow step."""

    handler: str
    step_id: str
    message: str


def typecheck_dag(
    config: WorkflowConfig,
    skill_io: dict[str, SkillIOSpec],
) -> list[TypeCheckError]:
    """Validate skill_name references and content_type consistency across steps."""
    errors: list[TypeCheckError] = []

    for handler_name, handler in config.handlers.items():
        step_by_id: dict[str, StepConfig] = {
            step.id: step for step in handler.steps if step.id
        }

        for step in handler.steps:
            if step.skill_name is None:
                continue

            if step.skill_name not in skill_io:
                errors.append(
                    TypeCheckError(
                        handler=handler_name,
                        step_id=step.id or "",
                        message=(
                            f"skill_name '{step.skill_name}' not found in skill_io"
                        ),
                    )
                )
                continue

            spec = skill_io[step.skill_name]

            for dep_id in step.depends:
                dep_step = step_by_id.get(dep_id)
                if dep_step is None or dep_step.skill_name is None:
                    continue
                if dep_step.skill_name not in skill_io:
                    continue

                producer_spec = skill_io[dep_step.skill_name]

                for consume in spec.consumes:
                    if consume.producer != dep_step.skill_name:
                        continue
                    if consume.content_type != producer_spec.produces_content_type:
                        errors.append(
                            TypeCheckError(
                                handler=handler_name,
                                step_id=step.id or "",
                                message=(
                                    f"content_type mismatch: step '{step.id}' consumes "
                                    f"{consume.content_type} from '{consume.producer}' "
                                    f"but producer produces "
                                    f"{producer_spec.produces_content_type}"
                                ),
                            )
                        )

    return errors
