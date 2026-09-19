from __future__ import annotations

from ghdag.workflow.schema import WorkflowConfig
from ghdag.workflow.state_machine import (
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


# --- validate_transition happy path ---

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


# --- validate_transition error cases ---

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
    # Japanese text intentionally kept for CJK processing test
    assert "遷移元を特定できない" in msg


# --- validate_transition transitions=None ---

def test_transitions_none_skips_validation():
    ok, msg = validate_transition(["test:a"], "test:b", transitions=None)
    assert ok is True
    # Japanese text intentionally kept for CJK processing test
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


# --- AC-1/AC-2/AC-3: multi-phase label deterministic resolution ---

_TRANSITIONS = {
    "issuesmith:draft-done": ["issuesmith:develop-ready"],
    "issuesmith:develop-running": [
        "issuesmith:develop-done",
        "issuesmith:scope-too-large",
    ],
}


def test_get_current_phase_most_advanced():
    # AC-1: returns develop-running even when draft-done comes first
    assert (
        get_current_phase(
            ["issuesmith:draft-done", "issuesmith:develop-running"],
            transitions=_TRANSITIONS,
        )
        == "issuesmith:develop-running"
    )


def test_get_current_phase_order_invariant():
    # AC-1: same result even when label array is reversed
    assert (
        get_current_phase(
            ["issuesmith:develop-running", "issuesmith:draft-done"],
            transitions=_TRANSITIONS,
        )
        == "issuesmith:develop-running"
    )


def test_transition_scope_too_large_from_develop_running():
    # AC-4: transition to scope-too-large succeeds for issue with develop-running + draft-done
    # Forge fake: verify labels_remove is ["issuesmith:develop-running"]
    from unittest.mock import MagicMock, patch

    fake_forge = MagicMock()
    fake_forge.issue_get.side_effect = [
        # 1st call: get current_labels
        {"labels": [
            {"name": "issuesmith:draft-done"},
            {"name": "issuesmith:develop-running"},
        ]},
        # 2nd call: post-transition verification
        {"labels": [{"name": "issuesmith:scope-too-large"}]},
    ]

    with patch("ghdag.workflow.state_machine.get_forge", return_value=fake_forge):
        from ghdag.workflow.state_machine import transition

        transition(
            issue_number=3364,
            target="issuesmith:scope-too-large",
            transitions=_TRANSITIONS,
        )

    fake_forge.issue_update.assert_called_once_with(
        3364,
        labels_remove=["issuesmith:develop-running"],
        labels_add=["issuesmith:scope-too-large"],
    )
