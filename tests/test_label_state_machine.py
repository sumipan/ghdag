from __future__ import annotations

import importlib
import subprocess
import sys
import warnings

import pytest

from ghdag.workflow import label_state_machine
from ghdag.workflow.label_state_machine import TRANSITIONS


def _ignore_deprecation():
    warnings.simplefilter("ignore", category=RuntimeWarning)


def test_from_import_emits_runtime_warning_on_call():
    importlib.reload(label_state_machine)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        from ghdag.workflow.label_state_machine import validate_transition

        validate_transition(["issuesmith:draft-running"], "issuesmith:draft-done")

    deprecation = [
        w for w in caught
        if issubclass(w.category, RuntimeWarning)
        and "非推奨" in str(w.message)
    ]
    assert len(deprecation) >= 1


def test_validate_transition_emits_runtime_warning():
    importlib.reload(label_state_machine)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        from ghdag.workflow.label_state_machine import validate_transition

        ok, _ = validate_transition(
            ["issuesmith:draft-running"], "issuesmith:draft-done"
        )

    assert ok is True
    deprecation = [
        w for w in caught
        if issubclass(w.category, RuntimeWarning)
        and "非推奨" in str(w.message)
    ]
    assert len(deprecation) == 1


def test_wrapper_validate_transition_uses_hardcoded_transitions():
    from ghdag.workflow.label_state_machine import validate_transition

    with warnings.catch_warnings():
        _ignore_deprecation()
        ok, msg = validate_transition(
            ["issuesmith:draft-running"], "issuesmith:draft-done"
        )
    assert ok is True
    assert "issuesmith:draft-running" in msg
    assert "issuesmith:draft-done" in msg


def test_wrapper_rejects_undefined_transition():
    from ghdag.workflow.label_state_machine import validate_transition

    with warnings.catch_warnings():
        _ignore_deprecation()
        ok, _ = validate_transition(
            ["issuesmith:draft-running"], "issuesmith:merge-done"
        )
    assert ok is False


def test_wrapper_reset_from_any_phase():
    from ghdag.workflow.label_state_machine import validate_transition

    with warnings.catch_warnings():
        _ignore_deprecation()
        ok, _ = validate_transition([], "issuesmith:reset")
    assert ok is True


@pytest.mark.parametrize("source,target", [
    (src, dst)
    for src, dsts in TRANSITIONS.items()
    for dst in dsts
    if dst != "issuesmith:reset"
])
def test_all_defined_transitions_valid(source: str, target: str):
    from ghdag.workflow.label_state_machine import validate_transition

    with warnings.catch_warnings():
        _ignore_deprecation()
        ok, _ = validate_transition([source], target)
    assert ok is True, f"{source} -> {target} should be valid"


def test_cli_emits_runtime_warning_to_stderr():
    worktree_root = __import__("os").path.dirname(
        __import__("os").path.dirname(__import__("os").path.abspath(__file__))
    )
    result = subprocess.run(
        [
            sys.executable, "-W", "always::RuntimeWarning",
            "-m", "ghdag.workflow.label_state_machine",
            "transition", "999999", "issuesmith:draft-done",
        ],
        capture_output=True,
        text=True,
        env={
            **__import__("os").environ,
            "PYTHONPATH": f"{worktree_root}/src",
        },
        cwd=worktree_root,
    )
    assert "非推奨" in result.stderr
