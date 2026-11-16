"""Tests for WorkflowDispatcher hot-reload and dispatch-failed audit (AC-1, AC-2, AC-4, AC-5, AC-8) — Issue #3431."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

from ghdag.core.models.workflow import WorkflowConfig
from ghdag.github_client import GitHubIssuePort
from ghdag.pipeline.llm_pipeline import build_order_builders
from ghdag.pipeline.task_queue import TaskQueue
from ghdag.pipeline.order import TemplateOrderBuilder
from ghdag.workflow.dispatcher import WorkflowDispatcher
from ghdag.workflow.loader import load_workflows


def _bump_mtime_ahead(path: Path) -> None:
    stat = path.stat()
    bumped = stat.st_mtime_ns + 1_000_000_000
    os.utime(path, ns=(bumped, bumped))


def _make_github_mock():
    github_client = MagicMock(spec=GitHubIssuePort)
    github_client.list_all_issues.return_value = []
    github_client.get_last_rate_limit.return_value = None
    return github_client


def _make_pipeline_mock():
    return MagicMock(spec=TaskQueue)


_INITIAL_YAML = """\
name: test-workflow
triggers:
  - label: "test:ready"
    handler: brushup
handlers:
  brushup:
    steps:
      - template: brushup
        model: claude-opus-4-6
polling_interval: 0
"""

_UPDATED_YAML = """\
name: test-workflow
triggers:
  - label: "test:ready"
    handler: brushup
  - label: "test:develop-ready"
    handler: impl
handlers:
  brushup:
    steps:
      - template: brushup
        model: claude-opus-4-6
  impl:
    steps:
      - template: impl
        model: claude-sonnet-4-6
polling_interval: 0
"""


def _setup_workflow_dir(tmp_path: Path, yaml_content: str, templates: list[str]) -> Path:
    workflows_dir = tmp_path / "workflows"
    workflows_dir.mkdir(exist_ok=True)
    (workflows_dir / "test.yml").write_text(yaml_content)
    tdir = workflows_dir / "templates"
    tdir.mkdir(exist_ok=True)
    for name in templates:
        (tdir / f"{name}.md").write_text(f"# {name}\n")
    return workflows_dir


class TestAC2InvalidYamlFallback:
    def test_invalid_yaml_run_does_not_crash(self, tmp_path):
        """AC-2: dispatcher.run() does not crash when yml becomes invalid mid-run."""
        workflows_dir = _setup_workflow_dir(tmp_path, _INITIAL_YAML, ["brushup"])
        initial_workflows = load_workflows(workflows_dir)

        dispatcher = WorkflowDispatcher(
            workflows=initial_workflows,
            workflows_dir=workflows_dir,
            github_client=_make_github_mock(),
            task_queue=_make_pipeline_mock(),
            queue_dir=str(tmp_path),
        )

        # Corrupt the yml before run
        (workflows_dir / "test.yml").write_text("invalid: yaml: {broken")

        dispatcher.run(max_iterations=1)  # must not raise


def _make_real_pipeline(tmp_path: Path, workflows: list[WorkflowConfig]) -> TaskQueue:
    default_template_dir = next(
        (wf.template_dir for wf in workflows if wf.template_dir), "templates"
    )
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    return TaskQueue(
        state_dir=tmp_path / ".pipeline-state",
        exec_jsonl_path=queue_dir / "exec.jsonl",
        order_builder=TemplateOrderBuilder(default_template_dir),
        queue_dir=str(queue_dir),
        order_builders=build_order_builders(workflows),
    )


def _write_workflow_bundle(
    workflows_dir: Path,
    *,
    wf_yaml: str,
    wf2_yaml: str | None = None,
    tpl_a_step: str = "from tpl_a",
    tpl_b_step: str = "from tpl_b",
) -> None:
    workflows_dir.mkdir(parents=True, exist_ok=True)
    (workflows_dir / "wf.yml").write_text(wf_yaml)
    tpl_a = workflows_dir / "tpl_a"
    tpl_a.mkdir(exist_ok=True)
    (tpl_a / "step.md").write_text(tpl_a_step)
    tpl_b = workflows_dir / "tpl_b"
    tpl_b.mkdir(exist_ok=True)
    (tpl_b / "step.md").write_text(tpl_b_step)
    if wf2_yaml is not None:
        (workflows_dir / "wf2.yml").write_text(wf2_yaml)


_WF_TPL_A = """\
name: wf
template_dir: tpl_a
triggers:
  - label: "wf:ready"
    handler: h
handlers:
  h:
    steps:
      - template: step
        model: claude-opus-4-6
polling_interval: 0
"""

_WF2_TPL_B = """\
name: wf2
template_dir: tpl_b
triggers:
  - label: "wf2:ready"
    handler: h
handlers:
  h:
    steps:
      - template: step
        model: claude-opus-4-6
polling_interval: 0
"""

_WF_TPL_B = """\
name: wf
template_dir: tpl_b
triggers:
  - label: "wf:ready"
    handler: h
handlers:
  h:
    steps:
      - template: step
        model: claude-opus-4-6
polling_interval: 0
"""


class TestHotReloadOrderBuilders:
    """Issue sumipan/nexus#6092 — yaml mtime and order builder refresh on reload."""

    def test_reload_switches_template_dir(self, tmp_path):
        """template_dir change on reload switches builder templates (AC 6092-3)."""
        workflows_dir = tmp_path / "workflows"
        _write_workflow_bundle(workflows_dir, wf_yaml=_WF_TPL_A)
        initial = load_workflows(workflows_dir)
        task_queue = _make_real_pipeline(tmp_path, initial)
        dispatcher = WorkflowDispatcher(
            workflows=initial,
            workflows_dir=workflows_dir,
            github_client=_make_github_mock(),
            task_queue=task_queue,
            queue_dir=str(tmp_path),
        )
        assert task_queue.order_builder_for("wf").build_order("step", {}) == "from tpl_a"

        (workflows_dir / "wf.yml").write_text(_WF_TPL_B)
        _bump_mtime_ahead(workflows_dir / "wf.yml")
        dispatcher._maybe_reload_workflows()

        assert task_queue.order_builder_for("wf").build_order("step", {}) == "from tpl_b"
