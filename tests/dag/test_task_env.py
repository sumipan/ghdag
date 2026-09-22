"""Launched tasks receive their identity via GHDAG_TASK_UUID / GHDAG_RESULT_PATH (sumipan/nexus#3515)."""
from __future__ import annotations

import os

from ghdag.dag.models import Task
from ghdag.dag.task_launcher import _task_env


def test_task_env_adds_identity_and_keeps_parent_env(monkeypatch):
    monkeypatch.setenv("PARENT_MARKER", "kept")
    task = Task(uuid="u-1", command="true", engine="shell", model="bash", result_path="/tmp/r.md")
    env = _task_env("u-1", task)
    assert env["GHDAG_TASK_UUID"] == "u-1"
    assert env["GHDAG_RESULT_PATH"] == "/tmp/r.md"
    assert env["PARENT_MARKER"] == "kept"
    assert "GHDAG_TASK_UUID" not in os.environ


def test_task_env_without_result_path_is_empty_string():
    task = Task(uuid="u-2", command="true", engine="shell", model="bash", result_path=None)
    assert _task_env("u-2", task)["GHDAG_RESULT_PATH"] == ""


def test_every_popen_in_launcher_passes_task_env():
    """Structural: each subprocess.Popen call in task_launcher.py carries env=_task_env(...)."""
    import ast
    from pathlib import Path

    import ghdag.dag.task_launcher as mod

    tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
    popen_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "Popen"
    ]
    assert popen_calls, "no Popen calls found"
    for call in popen_calls:
        env_kw = [kw for kw in call.keywords if kw.arg == "env"]
        assert env_kw, f"Popen call at line {call.lineno} has no env="
        assert isinstance(env_kw[0].value, ast.Call) and env_kw[0].value.func.id == "_task_env"
