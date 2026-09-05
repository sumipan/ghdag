"""Tests for StepConfig.render live / frozen and ghdag.workflow.render (Issue #2822)."""

from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

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


def test_frozen_order_matches_prechange_template_builder(tmp_path: Path) -> None:
    """render 未指定（frozen）の order 本文は TemplateOrderBuilder と同一。"""
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
    # ts/uuid は submit 内で生成されるため、本文の固定部分だけ比較
    assert "hello-42" in captured[0]
    assert "python -m ghdag.workflow.render" not in captured[0]
    # StepConfig.render デフォルトでも builder 直呼びと同等の展開結果になること
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
    """render: live の order は trampoline 1 行で、テンプレ書き換えが実行時に効く。"""
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

    # enqueue 後にテンプレを書き換え → trampoline 実行で新本文が使われる
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
