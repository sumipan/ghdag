"""Tests for ghdag.workflow.typecheck — typecheck_dag()."""

from __future__ import annotations

from ghdag.workflow.schema import (
    HandlerConfig,
    StepConfig,
    TriggerConfig,
    WorkflowConfig,
)
from ghdag.workflow.typecheck import (
    ConsumeEntry,
    SkillIOSpec,
    TypeCheckError,
    typecheck_dag,
)


def _config(
    handlers: dict[str, list[StepConfig]],
) -> WorkflowConfig:
    first = next(iter(handlers))
    return WorkflowConfig(
        name="test",
        triggers=[TriggerConfig(label="t", handler=first)],
        handlers={name: HandlerConfig(steps=steps) for name, steps in handlers.items()},
    )


SKILL_IO_OK = {
    "system-plan-draft": SkillIOSpec(produces_content_type="text/markdown"),
    "system-operate-issuesmith": SkillIOSpec(
        produces_content_type="text/markdown",
        consumes=[
            ConsumeEntry(producer="system-plan-draft", content_type="text/markdown"),
        ],
    ),
}


class TestTypecheckDag:
    def test_tc1_happy_path_two_step_dag(self):
        """TC-1: system-plan-draft → system-operate-issuesmith passes."""
        config = _config({
            "canary": [
                StepConfig(
                    id="plan",
                    template="plan",
                    model="m",
                    skill_name="system-plan-draft",
                ),
                StepConfig(
                    id="operate",
                    template="operate",
                    model="m",
                    skill_name="system-operate-issuesmith",
                    depends=["plan"],
                ),
            ],
        })
        assert typecheck_dag(config, SKILL_IO_OK) == []

    def test_tc2_content_type_mismatch(self):
        """TC-2: produces/consumes content_type mismatch returns type error."""
        skill_io = {
            "system-plan-draft": SkillIOSpec(produces_content_type="text/plain"),
            "system-operate-issuesmith": SkillIOSpec(
                consumes=[
                    ConsumeEntry(producer="system-plan-draft", content_type="text/markdown"),
                ],
            ),
        }
        config = _config({
            "canary": [
                StepConfig(
                    id="plan",
                    template="plan",
                    model="m",
                    skill_name="system-plan-draft",
                ),
                StepConfig(
                    id="operate",
                    template="operate",
                    model="m",
                    skill_name="system-operate-issuesmith",
                    depends=["plan"],
                ),
            ],
        })
        errors = typecheck_dag(config, skill_io)
        assert len(errors) == 1
        assert isinstance(errors[0], TypeCheckError)
        assert errors[0].handler == "canary"
        assert errors[0].step_id == "operate"
        assert "text/markdown" in errors[0].message
        assert "text/plain" in errors[0].message

    def test_tc3_unregistered_skill(self):
        """TC-3: skill_name not in skill_io dict returns error."""
        config = _config({
            "canary": [
                StepConfig(
                    id="plan",
                    template="plan",
                    model="m",
                    skill_name="unknown-skill",
                ),
            ],
        })
        errors = typecheck_dag(config, SKILL_IO_OK)
        assert len(errors) == 1
        assert errors[0].step_id == "plan"
        assert "unknown-skill" in errors[0].message

    def test_tc4_none_skill_name_skipped(self):
        """TC-4: steps without skill_name are not checked."""
        config = _config({
            "canary": [
                StepConfig(id="plan", template="plan", model="m"),
                StepConfig(
                    id="operate",
                    template="operate",
                    model="m",
                    depends=["plan"],
                ),
            ],
        })
        assert typecheck_dag(config, {}) == []

    def test_tc5_multiple_handlers_independent(self):
        """TC-5: each handler is typechecked independently."""
        config = WorkflowConfig(
            name="test",
            triggers=[
                TriggerConfig(label="h1", handler="good"),
                TriggerConfig(label="h2", handler="bad"),
            ],
            handlers={
                "good": HandlerConfig(steps=[
                    StepConfig(
                        id="plan",
                        template="plan",
                        model="m",
                        skill_name="system-plan-draft",
                    ),
                    StepConfig(
                        id="operate",
                        template="operate",
                        model="m",
                        skill_name="system-operate-issuesmith",
                        depends=["plan"],
                    ),
                ]),
                "bad": HandlerConfig(steps=[
                    StepConfig(
                        id="plan",
                        template="plan",
                        model="m",
                        skill_name="missing-skill",
                    ),
                ]),
            },
        )
        errors = typecheck_dag(config, SKILL_IO_OK)
        assert len(errors) == 1
        assert errors[0].handler == "bad"
        assert errors[0].step_id == "plan"

    def test_tc6_unsatisfied_consumes_is_warning_not_error(self):
        """TC-6: consumes producer absent from depends is warning only (no error)."""
        skill_io = {
            "system-operate-issuesmith": SkillIOSpec(
                consumes=[
                    ConsumeEntry(producer="system-plan-draft", content_type="text/markdown"),
                ],
            ),
        }
        config = _config({
            "canary": [
                StepConfig(
                    id="operate",
                    template="operate",
                    model="m",
                    skill_name="system-operate-issuesmith",
                ),
            ],
        })
        assert typecheck_dag(config, skill_io) == []
