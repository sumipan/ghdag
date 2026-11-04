"""Shared helpers for ghdag.workflow dispatcher and loader tests."""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ghdag.pipeline.llm_pipeline import LLMPipelineAPI
from ghdag.workflow.dispatcher import WorkflowDispatcher
from ghdag.workflow.loader import ValidationError, load_workflows
from ghdag.workflow.schema import (
    HandlerConfig,
    OnTriggerConfig,
    StepConfig,
    TriggerConfig,
    WorkflowConfig,
)

# ---------------------------------------------------------------------------
# TC-1: YAML parse (happy path)
# ---------------------------------------------------------------------------

_EXTENDED_YAML = """\
name: stash-pipeline

triggers:
  - label: "pipeline:draft-ready"
    handler: brushup
  - label: "pipeline:develop-ready"
    handler: impl
  - label: "pipeline:merge-ready"
    handler: merge
  - label: "pipeline:reset"
    handler: reset

handlers:
  brushup:
    on_trigger:
      issue_context: true
    steps:
      - template: brushup
        model: claude-opus-4-6

  impl:
    on_trigger:
      issue_context: true
    steps:
      - id: p1
        template: p1-implement
        model: claude-sonnet-4-6
      - id: p2
        template: p2-verify
        model: claude-sonnet-4-6
        depends: [p1]
      - id: p3
        template: p3-report
        model: claude-sonnet-4-6
        depends: [p2]

  merge:
    steps:
      - template: merge
        model: claude-opus-4-6

  reset:
    type: reset

polling_interval: 30
"""


def _make_templates(base_dir: Path, *names: str) -> None:
    """Create placeholder template .md files in base_dir/templates/."""
    tdir = base_dir / "templates"
    tdir.mkdir(exist_ok=True)
    for name in names:
        (tdir / f"{name}.md").write_text(f"# {name}\n", encoding="utf-8")


def _setup_extended_yaml(tmp_path: Path) -> None:
    """Write _EXTENDED_YAML and its required template files to tmp_path."""
    (tmp_path / "test.yml").write_text(_EXTENDED_YAML, encoding="utf-8")
    _make_templates(tmp_path, "brushup", "p1-implement", "p2-verify", "p3-report", "merge")


class TestTC1YamlParseOk:
    def test_load_returns_config_list(self, tmp_path):
        _setup_extended_yaml(tmp_path)
        configs = load_workflows(tmp_path)
        assert len(configs) == 1

    def test_workflow_name(self, tmp_path):
        _setup_extended_yaml(tmp_path)
        config = load_workflows(tmp_path)[0]
        assert config.name == "stash-pipeline"

    def test_trigger_count(self, tmp_path):
        _setup_extended_yaml(tmp_path)
        config = load_workflows(tmp_path)[0]
        assert len(config.triggers) == 4

    def test_trigger_handler_field(self, tmp_path):
        _setup_extended_yaml(tmp_path)
        config = load_workflows(tmp_path)[0]
        assert config.triggers[0].label == "pipeline:draft-ready"
        assert config.triggers[0].handler == "brushup"

    def test_handlers_is_dict(self, tmp_path):
        _setup_extended_yaml(tmp_path)
        config = load_workflows(tmp_path)[0]
        assert isinstance(config.handlers, dict)
        assert "impl" in config.handlers

    def test_impl_handler_has_three_steps(self, tmp_path):
        _setup_extended_yaml(tmp_path)
        config = load_workflows(tmp_path)[0]
        assert len(config.handlers["impl"].steps) == 3

    def test_impl_steps_ids_and_models(self, tmp_path):
        _setup_extended_yaml(tmp_path)
        config = load_workflows(tmp_path)[0]
        steps = config.handlers["impl"].steps
        assert steps[0].id == "p1"
        assert steps[0].model == "claude-sonnet-4-6"
        assert steps[1].id == "p2"
        assert steps[1].depends == ["p1"]
        assert steps[2].id == "p3"
        assert steps[2].depends == ["p2"]

    def test_on_trigger_issue_context(self, tmp_path):
        _setup_extended_yaml(tmp_path)
        config = load_workflows(tmp_path)[0]
        assert config.handlers["brushup"].on_trigger is not None
        assert config.handlers["brushup"].on_trigger.issue_context is True

    def test_reset_handler_type(self, tmp_path):
        _setup_extended_yaml(tmp_path)
        config = load_workflows(tmp_path)[0]
        assert config.handlers["reset"].type == "reset"

    def test_steps_model_missing_raises_validation_error(self, tmp_path):
        yaml_no_model = """\
name: test
triggers:
  - label: "pipeline:draft-ready"
    handler: brushup
handlers:
  brushup:
    steps:
      - template: brushup
"""
        (tmp_path / "bad.yml").write_text(yaml_no_model, encoding="utf-8")
        with pytest.raises(ValidationError, match="model"):
            load_workflows(tmp_path)

    def test_trigger_missing_handler_raises_validation_error(self, tmp_path):
        yaml_no_handler = """\
name: test
triggers:
  - label: "pipeline:draft-ready"
handlers:
  brushup:
    steps:
      - template: brushup
        model: claude-sonnet-4-6
"""
        (tmp_path / "bad.yml").write_text(yaml_no_handler, encoding="utf-8")
        with pytest.raises(ValidationError, match="handler"):
            load_workflows(tmp_path)

    def test_trigger_missing_label_raises_validation_error(self, tmp_path):
        yaml_no_label = """\
name: test
triggers:
  - handler: brushup
handlers:
  brushup:
    steps:
      - template: brushup
        model: claude-sonnet-4-6
"""
        (tmp_path / "bad.yml").write_text(yaml_no_label, encoding="utf-8")
        with pytest.raises(ValidationError, match="label"):
            load_workflows(tmp_path)

    def test_unknown_keys_ignored(self, tmp_path):
        yaml_unknown = """\
name: test
triggers:
  - label: "pipeline:draft-ready"
    handler: brushup
handlers:
  brushup:
    steps:
      - template: brushup
        model: claude-sonnet-4-6
        unknown_future_key: some_value
polling_interval: 30
"""
        (tmp_path / "test.yml").write_text(yaml_unknown, encoding="utf-8")
        _make_templates(tmp_path, "brushup")
        with pytest.raises(ValidationError, match=r"handlers\.brushup\.steps\[0\]\.unknown_future_key") as exc_info:
            load_workflows(tmp_path)
        assert "Allowed:" in str(exc_info.value)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_extended_workflow() -> WorkflowConfig:
    return WorkflowConfig(
        name="stash-pipeline",
        triggers=[
            TriggerConfig(label="pipeline:draft-ready", handler="brushup"),
            TriggerConfig(label="pipeline:develop-ready", handler="impl"),
            TriggerConfig(label="pipeline:merge-ready", handler="merge"),
            TriggerConfig(label="pipeline:reset", handler="reset"),
        ],
        handlers={
            "brushup": HandlerConfig(
                steps=[StepConfig(template="brushup", model="claude-opus-4-6")],
                on_trigger=OnTriggerConfig(issue_context=True),
            ),
            "impl": HandlerConfig(
                steps=[
                    StepConfig(id="p1", template="p1-implement", model="claude-sonnet-4-6"),
                    StepConfig(id="p2", template="p2-verify", model="claude-sonnet-4-6", depends=["p1"]),
                    StepConfig(id="p3", template="p3-report", model="claude-sonnet-4-6", depends=["p2"]),
                ],
                on_trigger=OnTriggerConfig(issue_context=True),
            ),
            "merge": HandlerConfig(
                steps=[StepConfig(template="merge", model="claude-opus-4-6")],
            ),
            "reset": HandlerConfig(steps=[], type="reset"),
        },
        polling_interval=30,
    )


def _make_issue(number: int, labels: list[str] | None = None) -> dict:
    labels = labels or []
    return {
        "number": number,
        "title": f"Issue {number}",
        "body": "Issue body",
        "labels": [{"name": lb} for lb in labels],
        "url": f"https://github.com/owner/repo/issues/{number}",
    }


class _RecordingPipelineState:
    def __init__(self) -> None:
        self.idempotency_ok = True
        self.appended: list[list] = []
        self.remove_idempotency_calls: list[tuple[str, int]] = []
        self.remove_idempotency_return = 3

    def check_idempotency(self, *args, **kwargs) -> bool:
        return self.idempotency_ok

    def write_order_file(self, *args, **kwargs) -> str:
        return "ts-claude-order-uuid.md"

    def append_exec_records(self, records, audit_context=None) -> None:
        self.appended.append(records)

    def remove_idempotency_matching(self, workflow: str, issue: int) -> int:
        self.remove_idempotency_calls.append((workflow, issue))
        return self.remove_idempotency_return

    def get_generation(self, workflow_name: str, handler_name: str, issue_number: int) -> int:
        return 0

    def increment_generation(self, workflow_name: str, handler_name: str, issue_number: int) -> int:
        return 1

    def find_records_by_idempotency_key(self, key: str) -> list:
        return []

    def remove_idempotency_for_handler(
        self, workflow_name: str, handler_name: str, issue_number: int
    ) -> int:
        return 0


class _RecordingOrderBuilder:
    def __init__(self) -> None:
        self.contexts: list[dict] = []
        self.build_calls = 0

    def build_order(self, template, ctx) -> str:
        self.build_calls += 1
        self.contexts.append(ctx)
        return "order content"


class _RecordingGitHubClient:
    def __init__(self) -> None:
        self.list_all_issues_return: list = []
        self.comments: list = []
        self.removed_labels: list[tuple[int, str]] = []
        self.comments_fetched = False
        self.updated_labels: list[tuple] = []

    def list_all_issues(self, *args, **kwargs):
        return self.list_all_issues_return

    def get_last_rate_limit(self):
        return None

    def get_issue_comments(self, *args, **kwargs):
        self.comments_fetched = True
        return self.comments

    def remove_label(self, issue: int, label: str) -> None:
        self.removed_labels.append((issue, label))

    def update_label(self, *args, **kwargs) -> None:
        self.updated_labels.append((args, kwargs))


def _make_dispatcher(workflow: WorkflowConfig, queue_dir: str = "queue") -> tuple[WorkflowDispatcher, _RecordingGitHubClient, _RecordingPipelineState, _RecordingOrderBuilder]:
    github_client = _RecordingGitHubClient()
    pipeline_state = _RecordingPipelineState()
    order_builder = _RecordingOrderBuilder()
    pipeline = LLMPipelineAPI(
        pipeline_state=pipeline_state,
        order_builder=order_builder,
        queue_dir=queue_dir,
    )
    dispatcher = WorkflowDispatcher(
        workflows=[workflow],
        github_client=github_client,
        pipeline=pipeline,
        queue_dir=queue_dir,
    )
    dispatcher._write_design_md = MagicMock()
    return dispatcher, github_client, pipeline_state, order_builder


def _last_records(pipeline_state: _RecordingPipelineState) -> list:
    assert pipeline_state.appended, "expected append_exec_records to run"
    return pipeline_state.appended[-1]


_helpers = types.ModuleType("ghdag_workflow_conftest_helpers")
sys.modules["ghdag_workflow_conftest_helpers"] = _helpers
for _name in (
    "_EXTENDED_YAML",
    "_make_templates",
    "_setup_extended_yaml",
    "_make_extended_workflow",
    "_make_issue",
    "_RecordingPipelineState",
    "_RecordingOrderBuilder",
    "_RecordingGitHubClient",
    "_make_dispatcher",
    "_last_records",
):
    setattr(_helpers, _name, globals()[_name])
