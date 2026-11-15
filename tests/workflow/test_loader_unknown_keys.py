from __future__ import annotations

from pathlib import Path

import pytest

from ghdag.workflow.loader import ValidationError, load_workflows


def _workflow_yaml(extra: str = "") -> str:
    return f"name: example\ntriggers:\n  - label: ready\n    handler: impl\nhandlers:\n  impl:\n    steps:\n      - id: build\n        template: build\n        model: test-model\n{extra}"


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
        (_workflow_yaml().replace("    handler: impl", "    handler: impl\n    event: opened"), "triggers[0].event"),
        (_workflow_yaml().replace("  impl:", "  impl:\n    on_complete: archive"), "handlers.impl.on_complete"),
        (
            _workflow_yaml().replace("  impl:", "  impl:\n    on_trigger:\n      add_label: active"),
            "handlers.impl.on_trigger.add_label",
        ),
        (
            _workflow_yaml().replace(
                "        model: test-model", "        model: test-model\n        requires: [deps]"
            ),
            "handlers.impl.steps[0].requires",
        ),
        (
            _workflow_yaml("nonterminal_closed:\n  action: reopen\n  terminal_labels: [done]\n  stale: true\n"),
            "nonterminal_closed.stale",
        ),
        (_workflow_yaml("roles:\n  impl: [claude]\n"), "workflow.roles"),
        (
            _workflow_yaml().replace("        model: test-model", "        model: test-model\n        role: impl"),
            "handlers.impl.steps[0].role",
        ),
    ],
)
def test_load_workflows_rejects_unknown_keys(tmp_path: Path, text: str, unknown_path: str) -> None:
    _write_workflow(tmp_path, text)
    with pytest.raises(ValidationError) as exc_info:
        load_workflows(tmp_path)
    err = exc_info.value
    assert unknown_path in str(err)
    assert err.code == "WORKFLOW_UNKNOWN_KEY"
    assert err.hint is not None and "Allowed:" in err.hint


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


def test_top_level_roles_map_key_is_rejected(tmp_path: Path) -> None:
    """Refs sumipan/nexus#6680."""
    _write_workflow(tmp_path, _workflow_yaml("roles:\n  impl: [claude]\n"))
    with pytest.raises(ValidationError, match="workflow\\.roles"):
        load_workflows(tmp_path)
