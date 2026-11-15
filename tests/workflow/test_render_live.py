"""Tests for StepConfig.render live / frozen and ghdag.workflow.render (Issue #2822)."""

from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ghdag.core.exceptions import ConfigError
from ghdag.core.models.workflow import (
    HandlerConfig,
    StepConfig,
    TriggerConfig,
    WorkflowConfig,
)
from ghdag.github_client import GitHubIssuePort
from ghdag.pipeline.llm_pipeline import LLMPipelineAPI, build_order_builders
from ghdag.pipeline.order import TemplateOrderBuilder
from ghdag.workflow.dispatcher import WorkflowDispatcher
from ghdag.workflow.loader import load_workflows
from ghdag.workflow.render import main as render_main


def _write_workflow(tmp_path: Path, *, render: str | None, engine: str = "shell") -> Path:
    render_line = f"\n        render: {render}" if render is not None else ""
    yaml_content = f"""\
name: live-wf
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
        engine: {engine}{render_line}
"""
    (tmp_path / "wf.yml").write_text(yaml_content, encoding="utf-8")
    tdir = tmp_path / "templates"
    tdir.mkdir()
    (tdir / "step.md").write_text('echo "hello-${issue_number}"\n', encoding="utf-8")
    return tmp_path


def test_invalid_render_raises_value_error(tmp_path: Path) -> None:
    _write_workflow(tmp_path, render="invalid")
    with pytest.raises(ValueError, match="render"):
        load_workflows(tmp_path)


def test_default_render_is_frozen(tmp_path: Path) -> None:
    _write_workflow(tmp_path, render=None)
    configs = load_workflows(tmp_path)
    assert configs[0].handlers["run"].steps[0].render == "frozen"


def test_frozen_order_matches_prechange_template_builder(tmp_path: Path) -> None:
    """Order body with render unspecified (frozen) matches TemplateOrderBuilder."""
    queue = tmp_path / "queue"
    queue.mkdir()
    _write_workflow(tmp_path, render=None, engine="claude")
    configs = load_workflows(tmp_path)
    workflow = configs[0]
    step = workflow.handlers["run"].steps[0]

    order_builder = TemplateOrderBuilder(tmp_path / "templates")
    expected = order_builder.build_order(
        "step",
        {
            "issue_number": "42",
            "workflow_name": "live-wf",
            "handler_name": "run",
            "ts": "20260101000000",
            "order_uuid": "u",
            "result_uuid": "u",
            "result_filename": "r.md",
        },
    )

    captured: list[str] = []

    def _capture_write(ts, step_uuid, order_content, queue_dir, engine="claude"):
        captured.append(order_content)
        return f"{ts}-{engine}-order-{step_uuid}.md"

    pipeline_state = MagicMock()
    pipeline_state.check_idempotency.return_value = True
    pipeline_state.write_order_file.side_effect = _capture_write
    pipeline = LLMPipelineAPI(
        pipeline_state=pipeline_state,
        order_builder=order_builder,
        queue_dir=str(queue),
    )
    github = MagicMock(spec=GitHubIssuePort)
    dispatcher = WorkflowDispatcher(
        workflows=[workflow],
        github_client=github,
        pipeline=pipeline,
        queue_dir=str(queue),
    )
    issue = {
        "number": 42,
        "title": "t",
        "body": "",
        "labels": [{"name": "pipe:ready"}],
        "url": "https://example/issues/42",
    }
    result = dispatcher.dispatch(
        issue,
        workflow,
        workflow.handlers["run"],
        trigger=workflow.triggers[0],
        trigger_rank=0,
    )
    assert result.status == "dispatched"
    assert len(captured) == 1
    # ts/uuid are generated inside submit; compare only the fixed parts of the body
    assert "hello-42" in captured[0]
    assert "python -m ghdag.workflow.render" not in captured[0]
    # Default StepConfig.render should expand equivalently to a direct builder call
    rebuilt = order_builder.build_order(
        step.template,
        {
            "issue_number": "42",
            "workflow_name": "live-wf",
            "handler_name": "run",
            "ts": "fixed",
            "order_uuid": "fixed",
            "result_uuid": "fixed",
            "result_filename": "fixed.md",
        },
    )
    assert rebuilt == 'echo "hello-42"\n'
    assert expected == 'echo "hello-42"\n'


def test_live_order_is_trampoline_and_rereads_template(tmp_path: Path) -> None:
    """render: live order is a one-line trampoline; template edits take effect at runtime."""
    queue = tmp_path / "queue"
    queue.mkdir()
    _write_workflow(tmp_path, render="live", engine="shell")
    configs = load_workflows(tmp_path)
    workflow = configs[0]
    assert workflow.handlers["run"].steps[0].render == "live"

    captured: list[str] = []

    def _capture_write(ts, step_uuid, order_content, queue_dir, engine="claude"):
        captured.append(order_content)
        return f"{ts}-{engine}-order-{step_uuid}.md"

    pipeline_state = MagicMock()
    pipeline_state.check_idempotency.return_value = True
    pipeline_state.write_order_file.side_effect = _capture_write
    pipeline = LLMPipelineAPI(
        pipeline_state=pipeline_state,
        order_builder=TemplateOrderBuilder(tmp_path / "templates"),
        queue_dir=str(queue),
    )
    github = MagicMock(spec=GitHubIssuePort)
    dispatcher = WorkflowDispatcher(
        workflows=[workflow],
        github_client=github,
        pipeline=pipeline,
        queue_dir=str(queue),
    )
    issue = {
        "number": 7,
        "title": "t",
        "body": "",
        "labels": [{"name": "pipe:ready"}],
        "url": "https://example/issues/7",
    }
    result = dispatcher.dispatch(
        issue,
        workflow,
        workflow.handlers["run"],
        trigger=workflow.triggers[0],
        trigger_rank=0,
    )
    assert result.status == "dispatched"
    assert len(captured) == 1
    order = captured[0].strip()
    assert order.startswith("python -m ghdag.workflow.render ")
    assert "issue_number=7" in order or 'issue_number=7' in order
    template_path = str((tmp_path / "templates" / "step.md").resolve())
    assert template_path in order or shlex.quote(template_path) in order

    # rewrite template after enqueue → trampoline run uses the new body
    (tmp_path / "templates" / "step.md").write_text('echo "WORLD-${issue_number}"\n', encoding="utf-8")
    proc = subprocess.run(
        ["bash", "-o", "pipefail", "-c", order],
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
        env={**__import__("os").environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")},
    )
    assert proc.returncode == 0, proc.stderr
    assert "WORLD-7" in proc.stdout
    assert "hello-7" not in proc.stdout


def test_render_undefined_variable_exits_2(tmp_path: Path) -> None:
    tmpl = tmp_path / "t.md"
    tmpl.write_text("echo ${missing}\n", encoding="utf-8")
    code = render_main([str(tmpl), "other=1"])
    assert code == 2


def test_render_propagates_bash_exit_code(tmp_path: Path) -> None:
    tmpl = tmp_path / "t.md"
    tmpl.write_text("exit 17\n", encoding="utf-8")
    code = render_main([str(tmpl)])
    assert code == 17


def test_render_cli_module_undefined_variable_exits_2(tmp_path: Path) -> None:
    tmpl = tmp_path / "t.md"
    tmpl.write_text("echo ${missing}\n", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "ghdag.workflow.render", str(tmpl)],
        capture_output=True,
        text=True,
        env={**__import__("os").environ, "PYTHONPATH": "src"},
        cwd=str(Path(__file__).resolve().parents[2]),
    )
    assert proc.returncode == 2


def test_live_submit_does_not_mutate_pipeline_order_builders(tmp_path: Path) -> None:
    """AC-3: dispatcher does not mutate _pipeline._order_builders directly."""
    queue = tmp_path / "queue"
    queue.mkdir()
    _write_workflow(tmp_path, render="live", engine="shell")
    configs = load_workflows(tmp_path)
    workflow = configs[0]

    pipeline_state = MagicMock()
    pipeline_state.check_idempotency.return_value = True
    pipeline_state.write_order_file.return_value = "ts-shell-order-u.md"
    pipeline = LLMPipelineAPI(
        pipeline_state=pipeline_state,
        order_builder=TemplateOrderBuilder(tmp_path / "templates"),
        queue_dir=str(queue),
    )

    mutations: list[object] = []

    class _TrackingBuilders(dict):
        def __setitem__(self, key, value):  # type: ignore[no-untyped-def]
            mutations.append(("set", key))
            return super().__setitem__(key, value)

        def pop(self, key, *args):  # type: ignore[no-untyped-def]
            mutations.append(("pop", key))
            return super().pop(key, *args)

    pipeline._order_builders = _TrackingBuilders(pipeline._order_builders)

    github = MagicMock(spec=GitHubIssuePort)
    dispatcher = WorkflowDispatcher(
        workflows=[workflow],
        github_client=github,
        pipeline=pipeline,
        queue_dir=str(queue),
    )
    issue = {
        "number": 7,
        "title": "t",
        "body": "",
        "labels": [{"name": "pipe:ready"}],
        "url": "https://example/issues/7",
    }
    result = dispatcher.dispatch(
        issue,
        workflow,
        workflow.handlers["run"],
        trigger=workflow.triggers[0],
        trigger_rank=0,
    )
    assert result.status == "dispatched"
    assert mutations == [], f"dispatcher mutated _order_builders: {mutations}"


def _write_omitted_template_dir_live_workflow(wf_dir: Path) -> None:
    tdir = wf_dir / "templates"
    tdir.mkdir(parents=True)
    (tdir / "step.md").write_text('echo "live-${issue_number}"\n', encoding="utf-8")
    yaml_content = """\
name: live-wf
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
        render: live
"""
    (wf_dir / "wf.yml").write_text(yaml_content, encoding="utf-8")


def test_live_render_uses_workflow_dir_templates_when_template_dir_omitted(
    tmp_path: Path, monkeypatch,
) -> None:
    """Refs sumipan/nexus#6803 — live render trampoline points at workflow-dir templates."""
    wf_dir = tmp_path / "wf"
    wf_dir.mkdir()
    other_cwd = tmp_path / "other"
    other_cwd.mkdir()
    _write_omitted_template_dir_live_workflow(wf_dir)

    queue = tmp_path / "queue"
    queue.mkdir()
    monkeypatch.chdir(other_cwd)

    configs = load_workflows(wf_dir)
    workflow = configs[0]
    order_builders = build_order_builders(configs)

    captured: list[str] = []

    def _capture_write(ts, step_uuid, order_content, queue_dir, engine="claude"):
        captured.append(order_content)
        return f"{ts}-{engine}-order-{step_uuid}.md"

    pipeline_state = MagicMock()
    pipeline_state.check_idempotency.return_value = True
    pipeline_state.write_order_file.side_effect = _capture_write
    pipeline = LLMPipelineAPI(
        pipeline_state=pipeline_state,
        order_builder=order_builders[workflow.name],
        queue_dir=str(queue),
        order_builders=order_builders,
    )
    github = MagicMock(spec=GitHubIssuePort)
    dispatcher = WorkflowDispatcher(
        workflows=[workflow],
        github_client=github,
        pipeline=pipeline,
        queue_dir=str(queue),
    )
    issue = {
        "number": 3,
        "title": "t",
        "body": "",
        "labels": [{"name": "pipe:ready"}],
        "url": "https://example/issues/3",
    }
    result = dispatcher.dispatch(
        issue,
        workflow,
        workflow.handlers["run"],
        trigger=workflow.triggers[0],
        trigger_rank=0,
    )
    assert result.status == "dispatched"
    template_path = str((wf_dir / "templates" / "step.md").resolve())
    order = captured[0].strip()
    assert template_path in order or shlex.quote(template_path) in order


def test_live_render_raises_when_template_dir_none_on_config(tmp_path: Path) -> None:
    """Refs sumipan/nexus#6803 — live render requires template_dir on WorkflowConfig."""
    workflow = WorkflowConfig(
        name="live-wf",
        triggers=[TriggerConfig(label="pipe:ready", handler="run")],
        handlers={
            "run": HandlerConfig(
                steps=[
                    StepConfig(
                        id="s1",
                        template="step",
                        model="bash",
                        engine="shell",
                        render="live",
                    ),
                ],
            ),
        },
        template_dir=None,
    )
    queue = tmp_path / "queue"
    queue.mkdir()
    pipeline_state = MagicMock()
    pipeline_state.check_idempotency.return_value = True
    pipeline = LLMPipelineAPI(
        pipeline_state=pipeline_state,
        order_builder=MagicMock(),
        queue_dir=str(queue),
    )
    github = MagicMock(spec=GitHubIssuePort)
    dispatcher = WorkflowDispatcher(
        workflows=[workflow],
        github_client=github,
        pipeline=pipeline,
        queue_dir=str(queue),
    )
    issue = {
        "number": 1,
        "title": "t",
        "body": "",
        "labels": [{"name": "pipe:ready"}],
        "url": "https://example/issues/1",
    }
    with pytest.raises(ConfigError, match="live-wf"):
        dispatcher.dispatch(
            issue,
            workflow,
            workflow.handlers["run"],
            trigger=workflow.triggers[0],
            trigger_rank=0,
        )
