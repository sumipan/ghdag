"""Task subprocess environment deny / allow / passthrough policy."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock

from ghdag.dag.models import Task
from ghdag.dag.parser import parse_jsonl
from ghdag.dag.task_launcher import _task_env
from ghdag.io.audit import AuditContext
from ghdag.pipeline.llm_pipeline import LLMPipelineAPI
from ghdag.pipeline.order import TemplateOrderBuilder
from ghdag.workflow.loader import load_workflows


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


def test_loader_env_passthrough_survives_submit_under_deny_pattern(
    monkeypatch, tmp_path: Path,
) -> None:
    """Workflow step env_passthrough reaches task env via exec annotations (nexus #6080)."""
    monkeypatch.setenv("GHDAG_TASK_ENV_DENY", "MY_*")
    monkeypatch.setenv("MY_TOKEN", "keep-me")
    monkeypatch.delenv("GHDAG_TASK_ENV_ALLOW", raising=False)

    yaml_content = """\
name: env-wf
template_dir: templates
triggers:
  - label: "pipe:ready"
    handler: run
handlers:
  run:
    steps:
      - id: s1
        template: step
        model: bash
        engine: shell
        env_passthrough:
          - MY_TOKEN
"""
    (tmp_path / "wf.yml").write_text(yaml_content, encoding="utf-8")
    tdir = tmp_path / "templates"
    tdir.mkdir()
    (tdir / "step.md").write_text("echo ok\n", encoding="utf-8")

    workflow = load_workflows(tmp_path)[0]
    step = workflow.handlers["run"].steps[0]
    assert step.env_passthrough == ["MY_TOKEN"]

    queue = tmp_path / "queue"
    queue.mkdir()
    pipeline_state = MagicMock()
    pipeline_state.check_idempotency.return_value = True
    pipeline_state.write_order_file.return_value = "order.md"
    captured_records: list[dict] = []

    def _capture(records, **kwargs):
        captured_records.extend(records)

    pipeline_state.append_exec_records.side_effect = _capture
    api = LLMPipelineAPI(
        pipeline_state=pipeline_state,
        order_builder=TemplateOrderBuilder(tdir),
        queue_dir=str(queue),
    )
    api.submit([step], {"issue_number": "1"}, audit_context=AuditContext(source="test"))

    assert len(captured_records) == 1
    jsonl_text = json.dumps(captured_records[0], ensure_ascii=False)
    task = parse_jsonl(jsonl_text)[0]
    env = _task_env(task.uuid, task)
    assert env.get("MY_TOKEN") == "keep-me"
