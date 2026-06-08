from __future__ import annotations

from ghdag.workflow.schema import WorkflowConfig
from ghdag.workflow.state_machine import (
    get_current_phase,
    validate_transition,
)

# --- WorkflowConfig パース ---

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


# --- validate_transition 正常系 ---

def test_valid_transition():
    ok, msg = validate_transition(
        ["test:a"], "test:b", transitions={"test:a": ["test:b"]}
    )
    assert ok is True
    assert msg == "test:a -> test:b"


def test_valid_transition_with_unrelated_labels():
    ok, _ = validate_transition(
        ["test:a", "unrelated"], "test:b", transitions={"test:a": ["test:b"]}
    )
    assert ok is True


# --- validate_transition 異常系 ---

def test_undefined_transition_rejected():
    ok, msg = validate_transition(
        ["test:a"], "test:c", transitions={"test:a": ["test:b"]}
    )
    assert ok is False
    assert msg


def test_no_phase_label_rejected():
    ok, msg = validate_transition(
        [], "test:b", transitions={"test:a": ["test:b"]}
    )
    assert ok is False
    assert "遷移元を特定できない" in msg


# --- validate_transition transitions=None ---

def test_transitions_none_skips_validation():
    ok, msg = validate_transition(["test:a"], "test:b", transitions=None)
    assert ok is True
    assert msg == "バリデーションスキップ"


# --- reset_label ---

def test_reset_from_any_phase():
    ok, _ = validate_transition(
        ["test:a"],
        "test:reset",
        transitions={"test:a": ["test:b"]},
        reset_label="test:reset",
    )
    assert ok is True


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
