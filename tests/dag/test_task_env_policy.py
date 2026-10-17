"""Task subprocess environment deny / allow / passthrough policy."""

from __future__ import annotations

import json
import os

from ghdag.dag.models import Task
from ghdag.dag.task_launcher import _task_env


def _base_task(**kwargs) -> Task:
    defaults = {
        "uuid": "u-env",
        "command": "true",
        "engine": "shell",
        "model": "bash",
        "result_path": "/tmp/out.md",
    }
    defaults.update(kwargs)
    return Task(**defaults)


def test_deny_list_removes_matching_vars_but_keeps_path_and_task_uuid(monkeypatch):
    monkeypatch.setenv("GHDAG_TASK_ENV_DENY", "SLACK_*,GITHUB_TOKEN")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "secret")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_should_go")
    monkeypatch.setenv("PATH", "/bin")
    monkeypatch.delenv("GHDAG_TASK_ENV_ALLOW", raising=False)
    task = _base_task()
    env = _task_env("u-env", task)
    assert "SLACK_BOT_TOKEN" not in env
    assert "GITHUB_TOKEN" not in env
    assert env["PATH"] == "/bin"
    assert env["GHDAG_TASK_UUID"] == "u-env"


def test_allow_list_keeps_only_allow_and_base_vars(monkeypatch):
    monkeypatch.setenv("GHDAG_TASK_ENV_ALLOW", "FOO")
    monkeypatch.setenv("FOO", "bar")
    monkeypatch.setenv("BAR", "baz")
    monkeypatch.setenv("HOME", "/home/test")
    monkeypatch.delenv("GHDAG_TASK_ENV_DENY", raising=False)
    task = _base_task()
    env = _task_env("u-env", task)
    assert env.get("FOO") == "bar"
    assert "BAR" not in env
    assert env["HOME"] == "/home/test"
    assert env["GHDAG_TASK_UUID"] == "u-env"


def test_passthrough_exempts_from_deny(monkeypatch):
    monkeypatch.setenv("GHDAG_TASK_ENV_DENY", "GITHUB_TOKEN")
    monkeypatch.setenv("GITHUB_TOKEN", "keep-me")
    task = _base_task(
        annotations={"env_passthrough": json.dumps(["GITHUB_TOKEN"])},
    )
    env = _task_env("u-env", task)
    assert env.get("GITHUB_TOKEN") == "keep-me"


def test_unset_policy_preserves_parent_env(monkeypatch):
    monkeypatch.delenv("GHDAG_TASK_ENV_DENY", raising=False)
    monkeypatch.delenv("GHDAG_TASK_ENV_ALLOW", raising=False)
    monkeypatch.setenv("CUSTOM_MARKER", "present")
    task = _base_task()
    expected = os.environ.copy()
    expected["GHDAG_TASK_UUID"] = "u-env"
    expected["GHDAG_RESULT_PATH"] = task.result_path or ""
    env = _task_env("u-env", task)
    assert env == expected


def test_passthrough_accepts_list_annotation(monkeypatch):
    monkeypatch.setenv("GHDAG_TASK_ENV_DENY", "GITHUB_TOKEN")
    monkeypatch.setenv("GITHUB_TOKEN", "keep-me")
    monkeypatch.delenv("GHDAG_TASK_ENV_ALLOW", raising=False)
    task = _base_task(annotations={"env_passthrough": ["GITHUB_TOKEN"]})
    env = _task_env("u-env", task)
    assert env.get("GITHUB_TOKEN") == "keep-me"
