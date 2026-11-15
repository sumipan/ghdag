"""Tests for StepConfig.render live / frozen and ghdag.workflow.render (Issue #2822)."""

from __future__ import annotations

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
from ghdag.pipeline.llm_pipeline import LLMPipelineAPI
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
