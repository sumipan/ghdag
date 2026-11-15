from __future__ import annotations

from pathlib import Path

import pytest

from ghdag.core.models.workflow import WorkflowConfig
from ghdag.workflow.loader import ValidationError
from ghdag.workflow.state_machine import (
    _load_workflow_config,
    get_current_phase,
    validate_transition,
)

# --- WorkflowConfig parsing ---

def test_workflow_config_with_label_fields():
    cfg = WorkflowConfig(
        name="test",
        triggers=[],
        handlers={},
        label_namespace="test",
        transitions={"test:a": ["test:b"]},
        reset_label="test:reset",
    )
    assert cfg.label_namespace == "test"
    assert cfg.transitions == {"test:a": ["test:b"]}
    assert cfg.reset_label == "test:reset"


def test_workflow_config_transitions_default_none():
    cfg = WorkflowConfig(name="test", triggers=[], handlers={})
    assert cfg.transitions is None
    assert cfg.label_namespace is None
    assert cfg.reset_label is None


def test_load_workflow_config_rejects_unknown_key(tmp_path: Path) -> None:
    path = tmp_path / "workflow.yml"
    path.write_text(
        """\
name: example
triggers:
  - label: ready
    handler: reset
handlers:
  reset:
    type: reset
    on_complete: archive
""",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError, match=r"handlers\.reset\.on_complete"):
        _load_workflow_config(path)


# --- validate_transition happy path ---


# --- validate_transition error cases ---


def test_no_phase_label_rejected():
    ok, msg = validate_transition(
        [], "test:b", transitions={"test:a": ["test:b"]}
    )
    assert ok is False
    assert msg == "no phase label: cannot determine the source state"


# --- validate_transition transitions=None ---

def test_transitions_none_skips_validation():
    ok, msg = validate_transition(["test:a"], "test:b", transitions=None)
    assert ok is True
    assert msg == "validation skipped"


# --- reset_label ---


def test_reset_from_no_phase():
    ok, _ = validate_transition(
        [],
        "test:reset",
        transitions={"test:a": ["test:b"]},
        reset_label="test:reset",
    )
    assert ok is True


# --- get_current_phase ---

def test_get_current_phase_match():
    assert get_current_phase(
        ["test:a", "other"], transitions={"test:a": ["test:b"]}
    ) == "test:a"


def test_get_current_phase_none():
    assert get_current_phase(
        ["other"], transitions={"test:a": ["test:b"]}
    ) is None




