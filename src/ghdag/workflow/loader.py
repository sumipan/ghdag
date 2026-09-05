"""workflow/loader.py — directory YAML loading and validation"""

from __future__ import annotations

import logging
import shlex
import shutil
from pathlib import Path

import yaml

from ghdag.core.models.workflow import NonterminalClosedConfig
from ghdag.exceptions import GhdagError
from ghdag.workflow.schema import (
    HandlerConfig,
    OnTriggerConfig,
    StepConfig,
    TriggerConfig,
    WorkflowConfig,
)

logger = logging.getLogger(__name__)


class ValidationError(GhdagError, ValueError):
    """Raised when workflow YAML fails validation."""


def load_workflows(directory: str | Path) -> list[WorkflowConfig]:
    """Load *.yml / *.yaml files under the given directory and return a list of WorkflowConfig.

    Args:
        directory: directory where workflow YAML files are located
    Returns:
        list of WorkflowConfig
    Raises:
        FileNotFoundError: directory does not exist
        ValidationError: YAML parse error or missing required fields
    """
    directory = Path(directory)
    if not directory.exists():
        raise FileNotFoundError(f"Directory not found: {directory}")

    paths = sorted(directory.glob("*.yml")) + sorted(directory.glob("*.yaml"))
    configs: list[WorkflowConfig] = []

    for path in paths:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            raise ValidationError(f"YAML parse error ({path.name}): {e}") from e

        if not isinstance(data, dict):
            raise ValidationError(f"YAML root must be a mapping: {path.name}")

        _validate(data, path.name)
        config = _parse(data, workflow_dir=directory.resolve())
        _validate_references(config, workflow_dir=directory.resolve())
        configs.append(config)

    return configs


def _validate(data: dict, filename: str) -> None:
    """Check for required fields."""
    if "name" not in data:
        raise ValidationError(f"'name' field is required: {filename}")
    if "triggers" not in data or not data["triggers"]:
        raise ValidationError(f"'triggers' field is required and must not be empty: {filename}")
    if "handlers" not in data or data["handlers"] is None:
        raise ValidationError(f"'handlers' field is required: {filename}")

    # validate each trigger entry
    for i, trigger in enumerate(data["triggers"]):
        if not isinstance(trigger, dict):
            raise ValidationError(f"triggers[{i}] must be a mapping: {filename}")
        if "label" not in trigger:
            raise ValidationError(f"triggers[{i}] requires 'label': {filename}")
        if "handler" not in trigger:
            raise ValidationError(f"triggers[{i}] requires 'handler': {filename}")

    # validate each handler entry
    handlers = data["handlers"]
    if not isinstance(handlers, dict):
        raise ValidationError(f"'handlers' must be a mapping: {filename}")

    for handler_name, handler_data in handlers.items():
        if handler_data is None:
            continue
        if not isinstance(handler_data, dict):
            raise ValidationError(f"handler '{handler_name}' must be a mapping: {filename}")

        # reset handlers do not require steps
        handler_type = handler_data.get("type")
        if handler_type == "reset":
            continue

        steps = handler_data.get("steps")
        if steps is None:
            raise ValidationError(f"handler '{handler_name}' requires 'steps': {filename}")

        step_engine_by_id: dict[str, str] = {}
        for i, step in enumerate(steps):
            if not isinstance(step, dict):
                raise ValidationError(f"handler '{handler_name}' step[{i}] must be a mapping: {filename}")
            if "template" not in step:
                raise ValidationError(f"handler '{handler_name}' step[{i}] requires 'template': {filename}")
            if "model" not in step:
                raise ValidationError(f"handler '{handler_name}' step[{i}] requires 'model': {filename}")
            render = step.get("render", "frozen")
            if render not in ("frozen", "live"):
                raise ValidationError(
                    f"handler '{handler_name}' step[{i}].render must be 'frozen' or 'live' "
                    f"(got {render!r}): {filename}"
                )
            step_id = step.get("id")
            if isinstance(step_id, str):
                step_engine_by_id[step_id] = str(step.get("engine", "claude"))

        for i, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            resume_from = step.get("resume_from")
            if resume_from is None:
                continue
            if not isinstance(resume_from, str):
                raise ValidationError(
                    f"handler '{handler_name}' step[{i}].resume_from must be a string: {filename}"
                )
            if resume_from not in step_engine_by_id:
                raise ValidationError(
                    f"handler '{handler_name}' step[{i}].resume_from references unknown step id "
                    f"{resume_from!r}: {filename}"
                )
            step_engine = str(step.get("engine", "claude"))
            parent_engine = step_engine_by_id[resume_from]
            if step_engine != parent_engine:
                raise ValidationError(
                    f"handler '{handler_name}' step[{i}].resume_from requires same engine "
                    f"(self={step_engine!r}, parent={parent_engine!r}): {filename}"
                )

    handler_names = set(data["handlers"].keys())
    for i, t in enumerate(data["triggers"]):
        if t["handler"] not in handler_names:
            raise ValidationError(
                f"{filename}: triggers[{i}].handler '{t['handler']}' is not defined in handlers "
                f"(available: {sorted(handler_names)})"
            )

    nonterminal_closed = data.get("nonterminal_closed")
    if nonterminal_closed is not None:
        if not isinstance(nonterminal_closed, dict):
            raise ValidationError(
                f"'nonterminal_closed' must be a mapping: {filename}"
            )
        action = nonterminal_closed.get("action")
        if action not in ("reopen", "trigger"):
            raise ValidationError(
                f"'nonterminal_closed.action' must be 'reopen' or 'trigger': {filename}"
            )
        terminal_labels = nonterminal_closed.get("terminal_labels")
        if not terminal_labels or not isinstance(terminal_labels, list):
            raise ValidationError(
                f"'nonterminal_closed.terminal_labels' is required and must be a list: {filename}"
            )
        if action == "trigger" and not nonterminal_closed.get("trigger"):
            raise ValidationError(
                f"'nonterminal_closed.trigger' is required when action is 'trigger': {filename}"
            )


def _validate_references(config: WorkflowConfig, *, workflow_dir: Path | None = None) -> None:
    """Check template file existence and context_hook executability."""
    template_dir = Path(config.template_dir) if config.template_dir else (
        workflow_dir / "templates" if workflow_dir else Path("templates")
    )
    for handler_name, handler in config.handlers.items():
        if handler.context_hook:
            tokens = shlex.split(handler.context_hook)
            cmd = tokens[0] if tokens else handler.context_hook
            if shutil.which(cmd) is None:
                logger.warning(
                    "handler '%s' context_hook command '%s' not found "
                    "(not in PATH or not executable)",
                    handler_name, cmd,
                )
        for i, step in enumerate(handler.steps):
            template_path = template_dir / f"{step.template}.md"
            if not template_path.exists():
                raise ValidationError(
                    f"handler '{handler_name}' steps[{i}].template: "
                    f"file not found: {template_path}"
                )


def _parse(data: dict, *, workflow_dir: Path | None = None) -> WorkflowConfig:
    """Convert validated dict to WorkflowConfig."""
    triggers = [
        TriggerConfig(label=t["label"], handler=t["handler"])
        for t in data["triggers"]
    ]

    handlers: dict[str, HandlerConfig] = {}
    for name, h in data["handlers"].items():
        if h is None:
            h = {}

        handler_type = h.get("type")

        # parse on_trigger
        on_trigger_data = h.get("on_trigger")
        on_trigger = None
        if on_trigger_data and isinstance(on_trigger_data, dict):
            on_trigger = OnTriggerConfig(
                issue_context=on_trigger_data.get("issue_context", False)
            )

        context_hook = h.get("context_hook")

        if handler_type == "reset":
            handlers[name] = HandlerConfig(
                steps=[], on_trigger=on_trigger, type="reset",
                context_hook=context_hook,
            )
            continue

        steps = []
        for s in h.get("steps", []):
            steps.append(
                StepConfig(
                    id=s.get("id"),
                    template=s["template"],
                    model=s["model"],
                    engine=s.get("engine", "claude"),
                    depends=s.get("depends", []),
                    resume_from=s.get("resume_from"),
                    permission=s.get("permission"),
                    skill_name=s.get("skill_name"),
                    render=s.get("render", "frozen"),
                )
            )

        handlers[name] = HandlerConfig(
            steps=steps, on_trigger=on_trigger, type=handler_type,
            context_hook=context_hook,
        )

    # template_dir resolution: relative paths are resolved against the workflow file directory
    raw_template_dir = data.get("template_dir")
    resolved_template_dir: str | None = None
    if raw_template_dir is not None:
        td = Path(raw_template_dir)
        if not td.is_absolute() and workflow_dir is not None:
            td = workflow_dir / td
        resolved_template_dir = str(td)

    nonterminal_closed_data = data.get("nonterminal_closed")
    nonterminal_closed: NonterminalClosedConfig | None = None
    if nonterminal_closed_data is not None:
        nonterminal_closed = NonterminalClosedConfig(
            action=nonterminal_closed_data["action"],
            terminal_labels=list(nonterminal_closed_data["terminal_labels"]),
            trigger=nonterminal_closed_data.get("trigger"),
        )

    return WorkflowConfig(
        name=data["name"],
        triggers=triggers,
        handlers=handlers,
        polling_interval=data.get("polling_interval", 30),
        template_dir=resolved_template_dir,
        nonterminal_closed=nonterminal_closed,
    )
