from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ghdag.core.models.workflow import (
    HANDLER_KEYS,
    NONTERMINAL_CLOSED_KEYS,
    ON_TRIGGER_KEYS,
    STEP_KEYS,
    TRIGGER_KEYS,
    WORKFLOW_KEYS,
)
from ghdag.workflow.loader import ValidationError, _parse, load_workflow_file, load_workflows


def _workflow_yaml(extra: str = "") -> str:
    return f"""\
name: example
triggers:
  - label: ready
    handler: impl
handlers:
  impl:
    steps:
      - id: build
        template: build
        model: test-model
{extra}"""


def _write_workflow(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "wf.yml"
    path.write_text(text, encoding="utf-8")
    templates = tmp_path / "templates"
    templates.mkdir(exist_ok=True)
    (templates / "build.md").write_text("# build\n", encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("text", "unknown_path"),
    [
        (_workflow_yaml("mystery: true\n"), "workflow.mystery"),
        (
            _workflow_yaml().replace("    handler: impl", "    handler: impl\n    event: opened"),
            "triggers[0].event",
        ),
        (
            _workflow_yaml().replace("  impl:", "  impl:\n    on_complete: archive"),
            "handlers.impl.on_complete",
        ),
        (
            _workflow_yaml().replace(
                "  impl:", "  impl:\n    on_trigger:\n      add_label: active"
            ),
            "handlers.impl.on_trigger.add_label",
        ),
        (
            _workflow_yaml().replace("        model: test-model", "        model: test-model\n        requires: [deps]"),
            "handlers.impl.steps[0].requires",
        ),
        (
            _workflow_yaml("nonterminal_closed:\n  action: reopen\n  terminal_labels: [done]\n  stale: true\n"),
            "nonterminal_closed.stale",
        ),
    ],
)
def test_load_workflows_rejects_unknown_keys(
    tmp_path: Path, text: str, unknown_path: str
) -> None:
    _write_workflow(tmp_path, text)

    with pytest.raises(ValidationError) as exc_info:
        load_workflows(tmp_path)

    assert unknown_path in str(exc_info.value)
    assert "Allowed:" in str(exc_info.value)


def test_step_unknown_keys_are_sorted_and_list_all_allowed_keys(tmp_path: Path) -> None:
    text = _workflow_yaml().replace(
        "        model: test-model",
        "        model: test-model\n        z_unknown: true\n        a_unknown: true",
    )
    _write_workflow(tmp_path, text)

    with pytest.raises(ValidationError) as exc_info:
        load_workflows(tmp_path)

    assert str(exc_info.value) == (
        "wf.yml: unknown key(s) in handlers.impl.steps[0]: "
        "handlers.impl.steps[0].a_unknown, handlers.impl.steps[0].z_unknown. "
        f"Allowed: {', '.join(sorted(STEP_KEYS))}"
    )


def test_reset_handler_rejects_unknown_key(tmp_path: Path) -> None:
    text = """\
name: example
triggers:
  - label: reset
    handler: reset
handlers:
  reset:
    type: reset
    on_complete: archive
"""
    _write_workflow(tmp_path, text)

    with pytest.raises(ValidationError, match=r"handlers\.reset\.on_complete"):
        load_workflows(tmp_path)


def test_load_workflow_file_loads_one_valid_file(tmp_path: Path) -> None:
    path = _write_workflow(
        tmp_path,
        _workflow_yaml("label_namespace: example\ntransitions:\n  ready: [done]\nreset_label: reset\n"),
    )

    config = load_workflow_file(path)

    assert config.label_namespace == "example"
    assert config.transitions == {"ready": ["done"]}
    assert config.reset_label == "reset"


class _TrackingDict(dict):
    def __init__(self, *args, accessed: set[str], **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.accessed = accessed

    def __getitem__(self, key):
        self.accessed.add(key)
        return super().__getitem__(key)

    def get(self, key, default=None):
        self.accessed.add(key)
        return super().get(key, default)


def test_parse_reads_exactly_the_declared_allowed_keys() -> None:
    accessed = {
        "workflow": set(),
        "trigger": set(),
        "handler": set(),
        "on_trigger": set(),
        "step": set(),
        "nonterminal_closed": set(),
    }
    step = _TrackingDict(
        {
            "id": "build",
            "template": "build",
            "model": "model",
            "engine": "claude",
            "depends": [],
            "resume_from": None,
            "permission": None,
            "skill_name": None,
            "render": "frozen",
            "role": None,
        },
        accessed=accessed["step"],
    )
    handler = _TrackingDict(
        {
            "steps": [step],
            "on_trigger": _TrackingDict(
                {"issue_context": True}, accessed=accessed["on_trigger"]
            ),
            "type": None,
            "context_hook": None,
        },
        accessed=accessed["handler"],
    )
    data = _TrackingDict(
        {
            "name": "example",
            "triggers": [
                _TrackingDict(
                    {"label": "ready", "handler": "impl"},
                    accessed=accessed["trigger"],
                )
            ],
            "handlers": {"impl": handler},
            "polling_interval": 10,
            "template_dir": "templates",
            "label_namespace": "example",
            "transitions": {"ready": ["done"]},
            "reset_label": "reset",
            "roles": {},
            "nonterminal_closed": _TrackingDict(
                {"action": "reopen", "terminal_labels": ["done"], "trigger": None},
                accessed=accessed["nonterminal_closed"],
            ),
        },
        accessed=accessed["workflow"],
    )

    _parse(data, workflow_dir=Path("/tmp"))

    assert accessed["workflow"] == WORKFLOW_KEYS
    assert accessed["trigger"] == TRIGGER_KEYS
    assert accessed["handler"] == HANDLER_KEYS
    assert accessed["on_trigger"] == ON_TRIGGER_KEYS
    assert accessed["step"] == STEP_KEYS
    assert accessed["nonterminal_closed"] == NONTERMINAL_CLOSED_KEYS


def test_existing_workflow_fixtures_still_load(tmp_path: Path) -> None:
    fixture_dir = Path(__file__).parents[1] / "fixtures"
    loaded = []
    for fixture_name in (
        "sample-workflow.yml",
        "issuesmith_workflow_roles.yml",
    ):
        workflow_dir = tmp_path / fixture_name
        workflow_dir.mkdir()
        source = fixture_dir / fixture_name
        path = workflow_dir / source.name
        path.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        template_dir = workflow_dir / data.get("template_dir", "templates")
        template_dir.mkdir(parents=True)
        for handler in data["handlers"].values():
            for step in handler.get("steps", []):
                (template_dir / f"{step['template']}.md").write_text(
                    "# fixture\n", encoding="utf-8"
                )
        loaded.append(load_workflow_file(path))

    sample, roles = loaded

    assert sample.name
    assert roles.name == "issuesmith"
