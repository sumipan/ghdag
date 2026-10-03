"""workflow/schema.py — WorkflowConfig dataclass, YAML → dataclass conversion"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class StepConfig:
    template: str           # order template file name (without extension)
    model: str              # execution model (required)
    id: str | None = None   # step ID (referenced by depends)
    engine: str = "claude"  # LLM engine name ("claude", "gemini", "cursor", etc.)
    depends: list[str] = field(default_factory=list)  # list of dependency step IDs
    resume_from: str | None = None  # ID of the parent step whose session is continued
    permission: str | None = None  # capabilities preset name (None = engine default)
    skill_name: str | None = None  # skill name invoked by this step
    render: str = "frozen"  # "frozen" (expanded at enqueue) | "live" (re-expanded at run time via trampoline)
    role: str | None = None  # QuotaGate role name (per-engine check when omitted)


@dataclass
class OnTriggerConfig:
    issue_context: bool = False  # True: write Issue body + comments to design.md


@dataclass
class HandlerConfig:
    steps: list[StepConfig]
    on_trigger: OnTriggerConfig | None = None
    type: str | None = None  # special handler kind such as "reset"
    context_hook: str | None = None  # custom script for context generation


@dataclass
class TriggerConfig:
    label: str     # label to match (e.g. "pipeline:draft-ready")
    handler: str   # handler name (key in handlers)


@dataclass
class DispatchResult:
    status: str              # "dispatched" | "skipped" | "reset"
    reason: str = ""
    exec_lines: list[str] = field(default_factory=list)


@dataclass
class NonterminalClosedConfig:
    action: str                          # "reopen" | "trigger"
    terminal_labels: list[str]           # terminal labels (CLOSED issues with any of them are excluded)
    trigger: str | None = None           # label of the handler to start when action="trigger"


@dataclass
class WorkflowConfig:
    name: str                              # workflow name
    triggers: list[TriggerConfig]          # list of trigger conditions (definition order is priority)
    handlers: dict[str, HandlerConfig]     # handler name → HandlerConfig
    polling_interval: int = 30             # polling interval (seconds)
    template_dir: str | None = None        # template directory (relative paths resolve from the workflow file)
    label_namespace: str | None = None     # label prefix (e.g. "issuesmith")
    transitions: dict[str, list[str]] | None = None  # state transition map
    reset_label: str | None = None         # special label that can be transitioned to from any state
    roles: dict[str, list[str]] = field(default_factory=dict)  # role name → list of engine names
    nonterminal_closed: NonterminalClosedConfig | None = None  # detection settings for CLOSED non-terminal issues


def validate_workflow_roles(config: WorkflowConfig) -> None:
    """Raise ValueError when a step references an undeclared role."""
    for handler_name, handler in config.handlers.items():
        for step in handler.steps:
            if step.role is None:
                continue
            if step.role not in config.roles:
                raise ValueError(
                    f"handler '{handler_name}' step '{step.id or step.template}': "
                    f"role '{step.role}' is not declared in workflow roles"
                )
