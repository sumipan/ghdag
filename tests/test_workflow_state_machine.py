from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

from ghdag.core.models.workflow import WorkflowConfig
from ghdag.workflow import state_machine
from ghdag.workflow.state_machine import get_current_phase, validate_transition

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


def test_transition_main_calls_load_workflow_file_directly(tmp_path: Path) -> None:
    """Refs sumipan/ghdag#7545 — state_machine.main uses canonical loader, no deprecated wrapper."""
    assert not hasattr(state_machine, "_load_workflow_config")

    workflow = tmp_path / "workflow.yml"
    workflow.write_text(
        """\
name: sm
triggers: []
handlers: {}
transitions:
  phase:a:
    - phase:b
""",
        encoding="utf-8",
    )
    cfg = WorkflowConfig(
        name="sm",
        triggers=[],
        handlers={},
        transitions={"phase:a": ["phase:b"]},
    )
    with (
        patch.object(
            sys,
            "argv",
            [
                "state_machine",
                "transition",
                "--workflow",
                str(workflow),
                "1",
                "phase:b",
            ],
        ),
        patch(
            "ghdag.workflow.state_machine.load_workflow_file",
            return_value=cfg,
        ) as load_mock,
        patch("ghdag.workflow.state_machine.transition") as transition_mock,
    ):
        assert state_machine.main() == 0
        load_mock.assert_called_once_with(workflow)
        transition_mock.assert_called_once_with(1, "phase:b", cfg.transitions, None)


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




