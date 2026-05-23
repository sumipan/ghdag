"""workflow/loader.py — directory YAML loading and validation"""

from __future__ import annotations

import logging
import shlex
import shutil
from pathlib import Path

import yaml

from ghdag.workflow.schema import (
    HandlerConfig,
    OnTriggerConfig,
    StepConfig,
    TriggerConfig,
    WorkflowConfig,
)

logger = logging.getLogger(__name__)


class ValidationError(ValueError):
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

        for i, step in enumerate(steps):
            if not isinstance(step, dict):
                raise ValidationError(f"handler '{handler_name}' step[{i}] must be a mapping: {filename}")
            if "template" not in step:
                raise ValidationError(f"handler '{handler_name}' step[{i}] requires 'template': {filename}")
            if "model" not in step:
                raise ValidationError(f"handler '{handler_name}' step[{i}] requires 'model': {filename}")

    handler_names = set(data["handlers"].keys())
    for i, t in enumerate(data["triggers"]):
        if t["handler"] not in handler_names:
            raise ValidationError(
                f"{filename}: triggers[{i}].handler '{t['handler']}' is not defined in handlers "
                f"(available: {sorted(handler_names)})"
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

    return WorkflowConfig(
        name=data["name"],
        triggers=triggers,
        handlers=handlers,
        polling_interval=data.get("polling_interval", 30),
        template_dir=resolved_template_dir,
    )
