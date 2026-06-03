from __future__ import annotations

from ghdag.workflow.label_state_machine import (
    get_current_phase,
    validate_transition,
)

# --- get_current_phase ---

def test_get_current_phase_running():
    assert get_current_phase(["issuesmith:draft-running", "bug"]) == "issuesmith:draft-running"


def test_get_current_phase_none_when_no_phase():
    assert get_current_phase(["bug", "enhancement"]) is None


def test_get_current_phase_draft_done_is_phase():
    assert get_current_phase(["issuesmith:draft-done"]) == "issuesmith:draft-done"


def test_get_current_phase_returns_first_match():
    labels = ["issuesmith:develop-running", "issuesmith:merge-running"]
    phase = get_current_phase(labels)
    assert phase in ("issuesmith:develop-running", "issuesmith:merge-running")


# --- validate_transition 正常系 ---

def test_draft_running_to_draft_done():
    ok, msg = validate_transition(["issuesmith:draft-running"], "issuesmith:draft-done")
    assert ok is True
    assert "issuesmith:draft-running" in msg
    assert "issuesmith:draft-done" in msg


def test_draft_running_to_develop_ready():
    ok, msg = validate_transition(["issuesmith:draft-running"], "issuesmith:develop-ready")
    assert ok is True


def test_develop_running_to_merge_ready():
    ok, msg = validate_transition(["issuesmith:develop-running"], "issuesmith:merge-ready")
    assert ok is True


def test_merge_running_to_migrate_ready():
    ok, msg = validate_transition(["issuesmith:merge-running"], "issuesmith:migrate-ready")
    assert ok is True


def test_migrate_running_to_merge_ready():
    ok, msg = validate_transition(["issuesmith:migrate-running"], "issuesmith:merge-ready")
    assert ok is True


def test_develop_running_to_develop_done():
    ok, _ = validate_transition(["issuesmith:develop-running"], "issuesmith:develop-done")
    assert ok is True


def test_merge_running_to_merge_done():
    ok, _ = validate_transition(["issuesmith:merge-running"], "issuesmith:merge-done")
    assert ok is True


def test_draft_done_to_develop_ready():
    ok, _ = validate_transition(["issuesmith:draft-done"], "issuesmith:develop-ready")
    assert ok is True


# --- validate_transition 異常系 ---

def test_draft_running_to_merge_done_rejected():
    ok, msg = validate_transition(["issuesmith:draft-running"], "issuesmith:merge-done")
    assert ok is False
    assert msg


def test_no_phase_label_rejected():
    ok, msg = validate_transition([], "issuesmith:draft-done")
    assert ok is False


def test_no_phase_label_with_other_labels_rejected():
    ok, msg = validate_transition(["bug", "enhancement"], "issuesmith:draft-done")
    assert ok is False


def test_merge_done_as_source_rejected():
    ok, msg = validate_transition(["issuesmith:merge-done"], "issuesmith:develop-ready")
    assert ok is False


def test_develop_running_to_draft_done_rejected():
    ok, _ = validate_transition(["issuesmith:develop-running"], "issuesmith:draft-done")
    assert ok is False


# --- validate_transition reset ---

def test_reset_from_merge_running():
    ok, _ = validate_transition(["issuesmith:merge-running"], "issuesmith:reset")
    assert ok is True


def test_reset_from_no_phase():
    ok, _ = validate_transition([], "issuesmith:reset")
    assert ok is True


def test_reset_from_any_phase():
    for phase in [
        "issuesmith:draft-running",
        "issuesmith:develop-running",
        "issuesmith:migrate-running",
    ]:
        ok, _ = validate_transition([phase], "issuesmith:reset")
        assert ok is True, f"reset from {phase} should be valid"
