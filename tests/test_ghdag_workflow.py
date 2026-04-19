"""Tests for ghdag.workflow — TC-1 〜 TC-8 (Issue #79 extended schema)."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from ghdag.pipeline.llm_pipeline import LLMPipelineAPI
from ghdag.workflow.dispatcher import WorkflowDispatcher
from ghdag.workflow.github import GitHubIssueClient
from ghdag.workflow.loader import load_workflows
from ghdag.workflow.schema import (
    DispatchResult,
    HandlerConfig,
    OnTriggerConfig,
    StepConfig,
    TriggerConfig,
    WorkflowConfig,
)


# ---------------------------------------------------------------------------
# TC-1: YAML パース（正常系）
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


class TestTC1YamlParseOk:
    def test_load_returns_config_list(self, tmp_path):
        (tmp_path / "test.yml").write_text(_EXTENDED_YAML, encoding="utf-8")
        configs = load_workflows(tmp_path)
        assert len(configs) == 1

    def test_workflow_name(self, tmp_path):
        (tmp_path / "test.yml").write_text(_EXTENDED_YAML, encoding="utf-8")
        config = load_workflows(tmp_path)[0]
        assert config.name == "stash-pipeline"

    def test_trigger_count(self, tmp_path):
        (tmp_path / "test.yml").write_text(_EXTENDED_YAML, encoding="utf-8")
        config = load_workflows(tmp_path)[0]
        assert len(config.triggers) == 4

    def test_trigger_handler_field(self, tmp_path):
        (tmp_path / "test.yml").write_text(_EXTENDED_YAML, encoding="utf-8")
        config = load_workflows(tmp_path)[0]
        assert config.triggers[0].label == "pipeline:draft-ready"
        assert config.triggers[0].handler == "brushup"

    def test_handlers_is_dict(self, tmp_path):
        (tmp_path / "test.yml").write_text(_EXTENDED_YAML, encoding="utf-8")
        config = load_workflows(tmp_path)[0]
        assert isinstance(config.handlers, dict)
        assert "impl" in config.handlers

    def test_impl_handler_has_three_steps(self, tmp_path):
        (tmp_path / "test.yml").write_text(_EXTENDED_YAML, encoding="utf-8")
        config = load_workflows(tmp_path)[0]
        assert len(config.handlers["impl"].steps) == 3

    def test_impl_steps_ids_and_models(self, tmp_path):
        (tmp_path / "test.yml").write_text(_EXTENDED_YAML, encoding="utf-8")
        config = load_workflows(tmp_path)[0]
        steps = config.handlers["impl"].steps
        assert steps[0].id == "p1"
        assert steps[0].model == "claude-sonnet-4-6"
        assert steps[1].id == "p2"
        assert steps[1].depends == ["p1"]
        assert steps[2].id == "p3"
        assert steps[2].depends == ["p2"]

    def test_on_trigger_issue_context(self, tmp_path):
        (tmp_path / "test.yml").write_text(_EXTENDED_YAML, encoding="utf-8")
        config = load_workflows(tmp_path)[0]
        assert config.handlers["brushup"].on_trigger is not None
        assert config.handlers["brushup"].on_trigger.issue_context is True

    def test_reset_handler_type(self, tmp_path):
        (tmp_path / "test.yml").write_text(_EXTENDED_YAML, encoding="utf-8")
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
        with pytest.raises(ValueError, match="model"):
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
        with pytest.raises(ValueError, match="handler"):
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
        with pytest.raises(ValueError, match="label"):
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
        configs = load_workflows(tmp_path)
        assert len(configs) == 1


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


def _make_dispatcher(workflow: WorkflowConfig, queue_dir: str = "queue") -> tuple[WorkflowDispatcher, MagicMock, MagicMock, MagicMock]:
    github_client = MagicMock(spec=GitHubIssueClient)
    github_client.get_issue_comments.return_value = []
    pipeline_state = MagicMock()
    pipeline_state.check_idempotency.return_value = True
    pipeline_state.write_order_file.return_value = "ts-claude-order-uuid.md"
    order_builder = MagicMock()
    order_builder.build_order.return_value = "order content"
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
    # Patch _write_design_md to avoid filesystem writes in unit tests
    dispatcher._write_design_md = MagicMock()
    return dispatcher, github_client, pipeline_state, order_builder


# ---------------------------------------------------------------------------
# TC-2: 多段 DAG 投入（正常系）
# ---------------------------------------------------------------------------


class TestTC2MultiStepDag:
    def test_impl_handler_appends_three_exec_lines(self):
        workflow = _make_extended_workflow()
        dispatcher, _, pipeline_state, _ = _make_dispatcher(workflow)
        issue = _make_issue(42, ["pipeline:develop-ready"])
        handler = workflow.handlers["impl"]
        trigger = workflow.triggers[1]
        result = dispatcher.dispatch(issue, workflow, handler, trigger=trigger, trigger_rank=1)

        assert result.status == "dispatched"
        pipeline_state.append_exec.assert_called_once()
        exec_lines = pipeline_state.append_exec.call_args[0][0]
        # idempotency line + 3 step lines
        assert len(exec_lines) == 4

    def test_impl_p2_has_depends_p1(self):
        workflow = _make_extended_workflow()
        dispatcher, _, pipeline_state, _ = _make_dispatcher(workflow)
        issue = _make_issue(42, ["pipeline:develop-ready"])
        handler = workflow.handlers["impl"]
        trigger = workflow.triggers[1]
        dispatcher.dispatch(issue, workflow, handler, trigger=trigger, trigger_rank=1)

        exec_lines = pipeline_state.append_exec.call_args[0][0]
        # exec_lines[1] = p1, exec_lines[2] = p2[depends:...], exec_lines[3] = p3[depends:...]
        p1_line = exec_lines[1]
        p2_line = exec_lines[2]
        p3_line = exec_lines[3]

        # extract p1 uuid from p1_line
        p1_uuid = p1_line.split(":")[0]
        assert f"[depends:{p1_uuid}]" in p2_line

        p2_uuid = p2_line.split("[")[0]
        assert f"[depends:{p2_uuid}]" in p3_line

    def test_brushup_single_step_no_depends(self):
        workflow = _make_extended_workflow()
        dispatcher, _, pipeline_state, _ = _make_dispatcher(workflow)
        issue = _make_issue(10, ["pipeline:draft-ready"])
        handler = workflow.handlers["brushup"]
        trigger = workflow.triggers[0]
        result = dispatcher.dispatch(issue, workflow, handler, trigger=trigger, trigger_rank=0)

        assert result.status == "dispatched"
        exec_lines = pipeline_state.append_exec.call_args[0][0]
        assert len(exec_lines) == 2  # idempotency + 1 step
        assert "[depends:" not in exec_lines[1]


# ---------------------------------------------------------------------------
# TC-2b: context 拡張（result_filename, 依存先 result）
# ---------------------------------------------------------------------------


class TestTC2bContextExpansion:
    def test_context_contains_result_filename(self):
        """context に ts, order_uuid, result_uuid, result_filename が含まれる"""
        workflow = _make_extended_workflow()
        dispatcher, _, pipeline_state, order_builder = _make_dispatcher(workflow)
        issue = _make_issue(10, ["pipeline:draft-ready"])
        handler = workflow.handlers["brushup"]
        trigger = workflow.triggers[0]
        dispatcher.dispatch(issue, workflow, handler, trigger=trigger, trigger_rank=0)

        ctx = order_builder.build_order.call_args[0][1]
        assert "ts" in ctx
        assert "order_uuid" in ctx
        assert "result_uuid" in ctx
        assert "result_filename" in ctx
        assert ctx["result_filename"] == f"{ctx['ts']}-claude-result-{ctx['result_uuid']}.md"

    def test_context_contains_dep_result_filename(self):
        """P2 の context に p1_result_filename が含まれる"""
        workflow = _make_extended_workflow()
        dispatcher, _, pipeline_state, order_builder = _make_dispatcher(workflow)
        issue = _make_issue(42, ["pipeline:develop-ready"])
        handler = workflow.handlers["impl"]
        trigger = workflow.triggers[1]
        dispatcher.dispatch(issue, workflow, handler, trigger=trigger, trigger_rank=1)

        # build_order は 3 回呼ばれる (p1, p2, p3)
        calls = order_builder.build_order.call_args_list
        assert len(calls) == 3

        p1_ctx = calls[0][0][1]
        p2_ctx = calls[1][0][1]
        p3_ctx = calls[2][0][1]

        # p1 には依存先がないので dep_result_filename なし
        assert not any(k.endswith("_result_filename") and k != "result_filename" for k in p1_ctx)

        # p2 には p1_result_filename がある
        assert "p1_result_filename" in p2_ctx
        expected_p1_result = f"{p1_ctx['ts']}-claude-result-{p1_ctx['result_uuid']}.md"
        assert p2_ctx["p1_result_filename"] == expected_p1_result

        # p3 には p2_result_filename がある
        assert "p2_result_filename" in p3_ctx
        expected_p2_result = f"{p2_ctx['ts']}-claude-result-{p2_ctx['result_uuid']}.md"
        assert p3_ctx["p2_result_filename"] == expected_p2_result

    def test_context_preserves_original_fields(self):
        """context に issue_number, workflow_name, handler_name が引き続き含まれる"""
        workflow = _make_extended_workflow()
        dispatcher, _, _, order_builder = _make_dispatcher(workflow)
        issue = _make_issue(10, ["pipeline:draft-ready"])
        handler = workflow.handlers["brushup"]
        trigger = workflow.triggers[0]
        dispatcher.dispatch(issue, workflow, handler, trigger=trigger, trigger_rank=0)

        ctx = order_builder.build_order.call_args[0][1]
        assert ctx["issue_number"] == "10"
        assert ctx["workflow_name"] == "stash-pipeline"
        assert ctx["handler_name"] == "brushup"


# ---------------------------------------------------------------------------
# TC-3: --model フラグ生成
# ---------------------------------------------------------------------------


class TestTC3ModelFlag:
    def test_model_in_exec_line(self):
        workflow = _make_extended_workflow()
        dispatcher, _, pipeline_state, _ = _make_dispatcher(workflow)
        issue = _make_issue(42, ["pipeline:develop-ready"])
        handler = workflow.handlers["impl"]
        trigger = workflow.triggers[1]
        dispatcher.dispatch(issue, workflow, handler, trigger=trigger, trigger_rank=1)

        exec_lines = pipeline_state.append_exec.call_args[0][0]
        p1_line = exec_lines[1]
        assert "--model" in p1_line and "claude-sonnet-4-6" in p1_line

    def test_opus_model_in_brushup(self):
        workflow = _make_extended_workflow()
        dispatcher, _, pipeline_state, _ = _make_dispatcher(workflow)
        issue = _make_issue(10, ["pipeline:draft-ready"])
        handler = workflow.handlers["brushup"]
        trigger = workflow.triggers[0]
        dispatcher.dispatch(issue, workflow, handler, trigger=trigger, trigger_rank=0)

        exec_lines = pipeline_state.append_exec.call_args[0][0]
        assert "--model" in exec_lines[1] and "claude-opus-4-6" in exec_lines[1]


# ---------------------------------------------------------------------------
# TC-4: Issue コンテキスト取得
# ---------------------------------------------------------------------------


class TestTC4IssueContext:
    def test_issue_context_true_writes_design_md(self, tmp_path):
        workflow = _make_extended_workflow()
        dispatcher, github_client, pipeline_state, _ = _make_dispatcher(workflow, queue_dir=str(tmp_path))
        # Restore real _write_design_md for this test
        dispatcher._write_design_md = WorkflowDispatcher._write_design_md.__get__(dispatcher, WorkflowDispatcher)
        github_client.get_issue_comments.return_value = [
            {"author": "user1", "created_at": "2026-01-01T00:00:00Z", "body": "comment 1"},
            {"author": "user2", "created_at": "2026-01-02T00:00:00Z", "body": "comment 2"},
            {"author": "user3", "created_at": "2026-01-03T00:00:00Z", "body": "comment 3"},
        ]
        issue = _make_issue(79, ["pipeline:draft-ready"])
        handler = workflow.handlers["brushup"]
        trigger = workflow.triggers[0]
        dispatcher.dispatch(issue, workflow, handler, trigger=trigger, trigger_rank=0)

        design_md = tmp_path / "issue-79-design.md"
        assert design_md.exists()
        content = design_md.read_text(encoding="utf-8")
        assert "Issue body" in content
        assert "comment 1" in content
        assert "comment 2" in content
        assert "comment 3" in content

    def test_no_issue_context_no_design_md(self, tmp_path):
        workflow = _make_extended_workflow()
        dispatcher, github_client, pipeline_state, _ = _make_dispatcher(workflow, queue_dir=str(tmp_path))
        issue = _make_issue(42, ["pipeline:merge-ready"])
        handler = workflow.handlers["merge"]
        trigger = workflow.triggers[2]
        dispatcher.dispatch(issue, workflow, handler, trigger=trigger, trigger_rank=2)

        design_md = tmp_path / "issue-42-design.md"
        assert not design_md.exists()
        github_client.get_issue_comments.assert_not_called()


# ---------------------------------------------------------------------------
# TC-5: 後退遷移ガード
# ---------------------------------------------------------------------------


class TestTC5BackwardGuard:
    def test_backward_transition_blocked(self):
        """develop-running 中に draft-ready が発火 → skipped"""
        workflow = _make_extended_workflow()
        dispatcher, _, pipeline_state, _ = _make_dispatcher(workflow)
        # Issue has develop-running (rank 1)
        issue = _make_issue(42, ["pipeline:develop-running"])
        handler = workflow.handlers["brushup"]
        trigger = workflow.triggers[0]  # draft-ready = rank 0
        result = dispatcher.dispatch(issue, workflow, handler, trigger=trigger, trigger_rank=0)

        assert result.status == "skipped"
        assert "backward" in result.reason
        pipeline_state.append_exec.assert_not_called()

    def test_forward_transition_allowed(self):
        """draft-running 中に develop-ready が発火 → dispatched"""
        workflow = _make_extended_workflow()
        dispatcher, _, pipeline_state, _ = _make_dispatcher(workflow)
        # Issue has draft-running (rank 0)
        issue = _make_issue(42, ["pipeline:draft-running"])
        handler = workflow.handlers["impl"]
        trigger = workflow.triggers[1]  # develop-ready = rank 1
        result = dispatcher.dispatch(issue, workflow, handler, trigger=trigger, trigger_rank=1)

        assert result.status == "dispatched"
        pipeline_state.append_exec.assert_called_once()

    def test_no_running_labels_allows_dispatch(self):
        """running ラベルなし → dispatched"""
        workflow = _make_extended_workflow()
        dispatcher, _, pipeline_state, _ = _make_dispatcher(workflow)
        issue = _make_issue(42, ["pipeline:draft-ready"])
        handler = workflow.handlers["brushup"]
        trigger = workflow.triggers[0]
        result = dispatcher.dispatch(issue, workflow, handler, trigger=trigger, trigger_rank=0)

        assert result.status == "dispatched"


# ---------------------------------------------------------------------------
# TC-6: reset ハンドラー
# ---------------------------------------------------------------------------


class TestTC6ResetHandler:
    def test_reset_returns_reset_status(self):
        workflow = _make_extended_workflow()
        dispatcher, github_client, pipeline_state, _ = _make_dispatcher(workflow)
        pipeline_state.remove_idempotency_matching.return_value = 3
        issue = _make_issue(42, ["pipeline:develop-running"])
        handler = workflow.handlers["reset"]
        trigger = workflow.triggers[3]  # pipeline:reset (rank 3)
        result = dispatcher.dispatch(issue, workflow, handler, trigger=trigger, trigger_rank=3)

        assert result.status == "reset"

    def test_reset_calls_remove_idempotency(self):
        workflow = _make_extended_workflow()
        dispatcher, github_client, pipeline_state, _ = _make_dispatcher(workflow)
        pipeline_state.remove_idempotency_matching.return_value = 3
        issue = _make_issue(42, ["pipeline:develop-running"])
        handler = workflow.handlers["reset"]
        trigger = workflow.triggers[3]
        dispatcher.dispatch(issue, workflow, handler, trigger=trigger, trigger_rank=3)

        pipeline_state.remove_idempotency_matching.assert_called_once_with("stash-pipeline", 42)

    def test_reset_removes_pipeline_labels(self):
        workflow = _make_extended_workflow()
        dispatcher, github_client, pipeline_state, _ = _make_dispatcher(workflow)
        pipeline_state.remove_idempotency_matching.return_value = 3
        issue = _make_issue(42, ["pipeline:develop-running", "enhancement"])
        handler = workflow.handlers["reset"]
        trigger = workflow.triggers[3]
        dispatcher.dispatch(issue, workflow, handler, trigger=trigger, trigger_rank=3)

        # Only pipeline:* labels should be removed
        github_client.remove_label.assert_called_once_with(42, "pipeline:develop-running")

    def test_reset_no_exec_lines_appended(self):
        workflow = _make_extended_workflow()
        dispatcher, github_client, pipeline_state, _ = _make_dispatcher(workflow)
        pipeline_state.remove_idempotency_matching.return_value = 0
        issue = _make_issue(42, [])
        handler = workflow.handlers["reset"]
        trigger = workflow.triggers[3]
        dispatcher.dispatch(issue, workflow, handler, trigger=trigger, trigger_rank=3)

        pipeline_state.append_exec.assert_not_called()

    def test_reset_removes_labels_with_custom_prefix(self):
        """Issue #12: reset should use trigger label prefix, not hardcoded 'pipeline:'."""
        workflow = WorkflowConfig(
            name="issuesmith",
            triggers=[
                TriggerConfig(label="issuesmith:draft-ready", handler="brushup"),
                TriggerConfig(label="issuesmith:reset", handler="reset"),
            ],
            handlers={
                "brushup": HandlerConfig(
                    steps=[StepConfig(template="brushup", model="claude-opus-4-6")],
                ),
                "reset": HandlerConfig(steps=[], type="reset"),
            },
        )
        dispatcher, github_client, pipeline_state, _ = _make_dispatcher(workflow)
        pipeline_state.remove_idempotency_matching.return_value = 1
        issue = _make_issue(99, ["issuesmith:draft-running", "enhancement"])
        handler = workflow.handlers["reset"]
        trigger = workflow.triggers[1]  # issuesmith:reset
        dispatcher.dispatch(issue, workflow, handler, trigger=trigger, trigger_rank=1)

        github_client.remove_label.assert_called_once_with(99, "issuesmith:draft-running")


# ---------------------------------------------------------------------------
# TC-7: 既存テスト互換（import / poll_once）
# ---------------------------------------------------------------------------


class TestTC7Compatibility:
    def test_schema_imports(self):
        assert WorkflowConfig is not None
        assert TriggerConfig is not None
        assert HandlerConfig is not None
        assert StepConfig is not None
        assert DispatchResult is not None

    def test_loader_import(self):
        assert load_workflows is not None

    def test_dispatcher_import(self):
        assert WorkflowDispatcher is not None

    def test_github_import(self):
        assert GitHubIssueClient is not None

    def test_poll_once_returns_match(self):
        workflow = _make_extended_workflow()
        dispatcher, github_client, _, _ = _make_dispatcher(workflow)
        issue = _make_issue(1, ["pipeline:draft-ready"])
        github_client.list_issues.side_effect = lambda label, **kw: (
            [issue] if label == "pipeline:draft-ready" else []
        )
        matches = dispatcher.poll_once()
        assert len(matches) >= 1
        assert matches[0]["issue"] == 1
        assert matches[0]["workflow"] == "stash-pipeline"
        assert matches[0]["handler"] == "brushup"


# ---------------------------------------------------------------------------
# TC-8: stash-pipeline.yml（diary worktree）パース
# ---------------------------------------------------------------------------


class TestTC8StashPipelineYaml:
    def test_stash_pipeline_parseable(self, tmp_path):
        stash_pipeline_content = """\
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
        (tmp_path / "stash-pipeline.yml").write_text(stash_pipeline_content, encoding="utf-8")
        configs = load_workflows(tmp_path)
        assert len(configs) == 1
        config = configs[0]
        assert config.name == "stash-pipeline"
        assert config.handlers["impl"].steps[0].id == "p1"


# ---------------------------------------------------------------------------
# TC-9: context_hook
# ---------------------------------------------------------------------------


class TestTC9ContextHook:
    def test_yaml_context_hook_parsed(self, tmp_path):
        """context_hook が YAML から HandlerConfig にパースされる"""
        yaml_content = """\
name: test-pipeline
triggers:
  - label: "pipeline:draft-ready"
    handler: brushup
handlers:
  brushup:
    context_hook: "python -m my_hook"
    steps:
      - template: brushup
        model: claude-opus-4-6
"""
        (tmp_path / "test.yml").write_text(yaml_content, encoding="utf-8")
        configs = load_workflows(tmp_path)
        assert configs[0].handlers["brushup"].context_hook == "python -m my_hook"

    def test_yaml_no_context_hook_is_none(self, tmp_path):
        """context_hook 未指定時は None"""
        yaml_content = """\
name: test-pipeline
triggers:
  - label: "pipeline:draft-ready"
    handler: brushup
handlers:
  brushup:
    steps:
      - template: brushup
        model: claude-opus-4-6
"""
        (tmp_path / "test.yml").write_text(yaml_content, encoding="utf-8")
        configs = load_workflows(tmp_path)
        assert configs[0].handlers["brushup"].context_hook is None

    def test_context_hook_merges_into_template_context(self):
        """context_hook の出力が template context にマージされる"""
        workflow = WorkflowConfig(
            name="test",
            triggers=[TriggerConfig(label="pipeline:draft-ready", handler="brushup")],
            handlers={
                "brushup": HandlerConfig(
                    steps=[StepConfig(template="brushup", model="opus")],
                    context_hook="echo hook",
                ),
            },
        )
        dispatcher, _, pipeline_state, order_builder = _make_dispatcher(workflow)
        # Mock _run_context_hook to return extra context
        dispatcher._run_context_hook = MagicMock(return_value={
            "pipeline_id": "test-123",
            "worktree_path": "/tmp/wt",
        })
        issue = _make_issue(10, ["pipeline:draft-ready"])
        handler = workflow.handlers["brushup"]
        trigger = workflow.triggers[0]
        dispatcher.dispatch(issue, workflow, handler, trigger=trigger, trigger_rank=0)

        dispatcher._run_context_hook.assert_called_once_with("echo hook", 10)
        ctx = order_builder.build_order.call_args[0][1]
        assert ctx["pipeline_id"] == "test-123"
        assert ctx["worktree_path"] == "/tmp/wt"
        # 基本 context も残っている
        assert ctx["issue_number"] == "10"

    def test_context_hook_not_called_when_none(self):
        """context_hook が None のとき _run_context_hook は呼ばれない"""
        workflow = _make_extended_workflow()
        dispatcher, _, _, _ = _make_dispatcher(workflow)
        dispatcher._run_context_hook = MagicMock()
        issue = _make_issue(10, ["pipeline:draft-ready"])
        handler = workflow.handlers["brushup"]
        trigger = workflow.triggers[0]
        dispatcher.dispatch(issue, workflow, handler, trigger=trigger, trigger_rank=0)

        dispatcher._run_context_hook.assert_not_called()

    def test_run_context_hook_parses_json(self):
        """_run_context_hook が stdout JSON をパースして dict を返す"""
        workflow = _make_extended_workflow()
        dispatcher, _, _, _ = _make_dispatcher(workflow)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '{"key": "value", "num": 42}'
        with patch("ghdag.workflow.dispatcher.subprocess.run", return_value=mock_result):
            result = dispatcher._run_context_hook("echo test", 10)

        assert result == {"key": "value", "num": "42"}

    def test_run_context_hook_returns_empty_on_failure(self):
        """_run_context_hook が失敗時に空 dict を返す"""
        workflow = _make_extended_workflow()
        dispatcher, _, _, _ = _make_dispatcher(workflow)

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "error"
        with patch("ghdag.workflow.dispatcher.subprocess.run", return_value=mock_result):
            result = dispatcher._run_context_hook("bad-cmd", 10)

        assert result == {}

    def test_run_context_hook_raises_on_invalid_json(self):
        """_run_context_hook が不正 JSON で ValueError を投げる"""
        workflow = _make_extended_workflow()
        dispatcher, _, _, _ = _make_dispatcher(workflow)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "not json"
        with patch("ghdag.workflow.dispatcher.subprocess.run", return_value=mock_result):
            with pytest.raises(ValueError, match="JSON"):
                dispatcher._run_context_hook("echo test", 10)

    def test_run_context_hook_returns_empty_on_empty_stdout(self):
        """_run_context_hook が空 stdout で空 dict を返す"""
        workflow = _make_extended_workflow()
        dispatcher, _, _, _ = _make_dispatcher(workflow)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        with patch("ghdag.workflow.dispatcher.subprocess.run", return_value=mock_result):
            result = dispatcher._run_context_hook("echo test", 10)

        assert result == {}


# ---------------------------------------------------------------------------
# GitHubIssueClient tests
# ---------------------------------------------------------------------------


class TestGitHubIssueClient:
    def test_list_issues_calls_subprocess(self):
        client = GitHubIssueClient()
        mock_result = MagicMock()
        mock_result.stdout = json.dumps([{"number": 1}])
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            result = client.list_issues("pipeline:draft-ready")
        mock_run.assert_called_once_with(
            [
                "gh", "issue", "list",
                "--label", "pipeline:draft-ready",
                "--json", "number,title,body,labels,url",
                "--limit", "100",
                "--state", "open",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert result == [{"number": 1}]

    def test_update_label_calls_subprocess(self):
        client = GitHubIssueClient()
        with patch("subprocess.run") as mock_run:
            client.update_label(42, "pipeline:draft-ready", "pipeline:draft-running")
        mock_run.assert_called_once_with(
            [
                "gh", "issue", "edit", "42",
                "--remove-label", "pipeline:draft-ready",
                "--add-label", "pipeline:draft-running",
            ],
            capture_output=True,
            text=True,
            check=True,
        )

    def test_remove_label_calls_subprocess(self):
        client = GitHubIssueClient()
        with patch("subprocess.run") as mock_run:
            client.remove_label(42, "pipeline:develop-running")
        mock_run.assert_called_once_with(
            [
                "gh", "issue", "edit", "42",
                "--remove-label", "pipeline:develop-running",
            ],
            capture_output=True,
            text=True,
            check=True,
        )

    def test_add_comment_calls_subprocess(self):
        client = GitHubIssueClient()
        with patch("subprocess.run") as mock_run:
            client.add_comment(42, "test body")
        mock_run.assert_called_once_with(
            ["gh", "issue", "comment", "42", "--body", "test body"],
            capture_output=True,
            text=True,
            check=True,
        )

    def test_gh_cli_failure_raises_called_process_error(self):
        client = GitHubIssueClient()
        with patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "gh")):
            with pytest.raises(subprocess.CalledProcessError):
                client.list_issues("pipeline:draft-ready")


# ---------------------------------------------------------------------------
# PipelineState.remove_idempotency_matching tests
# ---------------------------------------------------------------------------


class TestRemoveIdempotencyMatching:
    def test_removes_matching_lines(self, tmp_path):
        from ghdag.pipeline.state import PipelineState
        exec_md = tmp_path / "exec.md"
        exec_md.write_text(
            "# idempotency: stash-pipeline:brushup:42\n"
            "# idempotency: stash-pipeline:impl:42\n"
            "# idempotency: stash-pipeline:merge:42\n"
            "# idempotency: stash-pipeline:brushup:99\n"
            "some-uuid: cat queue/file.md | claude -p 'test'\n",
            encoding="utf-8",
        )
        state = PipelineState(state_dir=tmp_path / "state", exec_md_path=exec_md)
        removed = state.remove_idempotency_matching("stash-pipeline", 42)

        assert removed == 3
        content = exec_md.read_text(encoding="utf-8")
        assert "# idempotency: stash-pipeline:brushup:42" not in content
        assert "# idempotency: stash-pipeline:impl:42" not in content
        assert "# idempotency: stash-pipeline:merge:42" not in content
        assert "# idempotency: stash-pipeline:brushup:99" in content
        assert "some-uuid:" in content

    def test_returns_zero_if_no_match(self, tmp_path):
        from ghdag.pipeline.state import PipelineState
        exec_md = tmp_path / "exec.md"
        exec_md.write_text("# idempotency: stash-pipeline:brushup:99\n", encoding="utf-8")
        state = PipelineState(state_dir=tmp_path / "state", exec_md_path=exec_md)
        removed = state.remove_idempotency_matching("stash-pipeline", 42)
        assert removed == 0

    def test_returns_zero_if_file_not_exists(self, tmp_path):
        from ghdag.pipeline.state import PipelineState
        state = PipelineState(
            state_dir=tmp_path / "state",
            exec_md_path=tmp_path / "nonexistent.md",
        )
        removed = state.remove_idempotency_matching("stash-pipeline", 42)
        assert removed == 0


# ---------------------------------------------------------------------------
# TC-10: template_dir 設定（Issue #14）
# ---------------------------------------------------------------------------


class TestTC10TemplateDir:
    def test_template_dir_parsed_from_yaml(self, tmp_path):
        """template_dir が YAML から WorkflowConfig にパースされる"""
        yaml_content = """\
name: test-pipeline
template_dir: my-templates
triggers:
  - label: "pipeline:draft-ready"
    handler: brushup
handlers:
  brushup:
    steps:
      - template: brushup
        model: claude-opus-4-6
"""
        (tmp_path / "test.yml").write_text(yaml_content, encoding="utf-8")
        configs = load_workflows(tmp_path)
        assert configs[0].template_dir is not None
        # Relative path resolved against workflow directory
        assert configs[0].template_dir == str(tmp_path.resolve() / "my-templates")

    def test_template_dir_none_when_not_specified(self, tmp_path):
        """template_dir 未指定時は None"""
        yaml_content = """\
name: test-pipeline
triggers:
  - label: "pipeline:draft-ready"
    handler: brushup
handlers:
  brushup:
    steps:
      - template: brushup
        model: claude-opus-4-6
"""
        (tmp_path / "test.yml").write_text(yaml_content, encoding="utf-8")
        configs = load_workflows(tmp_path)
        assert configs[0].template_dir is None

    def test_template_dir_absolute_path_preserved(self, tmp_path):
        """template_dir が絶対パスの場合はそのまま保持される"""
        yaml_content = """\
name: test-pipeline
template_dir: /absolute/path/to/templates
triggers:
  - label: "pipeline:draft-ready"
    handler: brushup
handlers:
  brushup:
    steps:
      - template: brushup
        model: claude-opus-4-6
"""
        (tmp_path / "test.yml").write_text(yaml_content, encoding="utf-8")
        configs = load_workflows(tmp_path)
        assert configs[0].template_dir == "/absolute/path/to/templates"

    def test_template_dir_relative_resolved_against_workflow_dir(self, tmp_path):
        """template_dir の相対パスがワークフローディレクトリ基準で解決される"""
        workflows_dir = tmp_path / "workflows"
        workflows_dir.mkdir()
        yaml_content = """\
name: test-pipeline
template_dir: ../shared-templates
triggers:
  - label: "pipeline:draft-ready"
    handler: brushup
handlers:
  brushup:
    steps:
      - template: brushup
        model: claude-opus-4-6
"""
        (workflows_dir / "test.yml").write_text(yaml_content, encoding="utf-8")
        configs = load_workflows(workflows_dir)
        expected = str(workflows_dir.resolve() / ".." / "shared-templates")
        assert configs[0].template_dir == expected

    def test_workflow_config_template_dir_default(self):
        """WorkflowConfig の template_dir デフォルト値は None"""
        config = WorkflowConfig(
            name="test",
            triggers=[TriggerConfig(label="x", handler="y")],
            handlers={},
        )
        assert config.template_dir is None
