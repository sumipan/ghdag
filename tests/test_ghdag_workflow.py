"""Tests for ghdag.workflow — AC-1 〜 AC-7."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ghdag.workflow.dispatcher import WorkflowDispatcher
from ghdag.workflow.github import GitHubIssueClient
from ghdag.workflow.loader import load_workflows
from ghdag.workflow.schema import PhaseHandler, TriggerConfig, WorkflowConfig


# ---------------------------------------------------------------------------
# AC-1: モジュール import（正常系）
# ---------------------------------------------------------------------------


class TestImports:
    def test_schema_imports(self):
        assert WorkflowConfig is not None
        assert TriggerConfig is not None
        assert PhaseHandler is not None

    def test_loader_import(self):
        assert load_workflows is not None

    def test_dispatcher_import(self):
        assert WorkflowDispatcher is not None

    def test_github_import(self):
        assert GitHubIssueClient is not None


# ---------------------------------------------------------------------------
# AC-2: YAML パース（正常系）
# ---------------------------------------------------------------------------

_SAMPLE_YAML = """\
name: stash-pipeline
triggers:
  - label: "pipeline:draft-ready"
  - label: "pipeline:develop-ready"
  - label: "pipeline:merge-ready"
handlers:
  - name: draft_design
    template: draft-design
    agent: claude
  - name: impl
    template: impl
    agent: claude
  - name: merge
    template: merge
    agent: claude
polling_interval: 30
"""


class TestYamlParseOk:
    def test_load_returns_config_list(self, tmp_path):
        (tmp_path / "test.yml").write_text(_SAMPLE_YAML, encoding="utf-8")
        configs = load_workflows(tmp_path)
        assert len(configs) == 1

    def test_workflow_name(self, tmp_path):
        (tmp_path / "test.yml").write_text(_SAMPLE_YAML, encoding="utf-8")
        config = load_workflows(tmp_path)[0]
        assert config.name == "stash-pipeline"

    def test_trigger_count(self, tmp_path):
        (tmp_path / "test.yml").write_text(_SAMPLE_YAML, encoding="utf-8")
        config = load_workflows(tmp_path)[0]
        assert len(config.triggers) == 3

    def test_handler_count(self, tmp_path):
        (tmp_path / "test.yml").write_text(_SAMPLE_YAML, encoding="utf-8")
        config = load_workflows(tmp_path)[0]
        assert len(config.handlers) == 3

    def test_first_trigger_label(self, tmp_path):
        (tmp_path / "test.yml").write_text(_SAMPLE_YAML, encoding="utf-8")
        config = load_workflows(tmp_path)[0]
        assert config.triggers[0].label == "pipeline:draft-ready"

    def test_first_handler(self, tmp_path):
        (tmp_path / "test.yml").write_text(_SAMPLE_YAML, encoding="utf-8")
        config = load_workflows(tmp_path)[0]
        assert config.handlers[0].name == "draft_design"
        assert config.handlers[0].template == "draft-design"

    def test_polling_interval(self, tmp_path):
        (tmp_path / "test.yml").write_text(_SAMPLE_YAML, encoding="utf-8")
        config = load_workflows(tmp_path)[0]
        assert config.polling_interval == 30

    def test_default_agent(self, tmp_path):
        yaml_no_agent = """\
name: minimal
triggers:
  - label: "pipeline:draft-ready"
handlers:
  - name: draft_design
    template: draft-design
"""
        (tmp_path / "minimal.yml").write_text(yaml_no_agent, encoding="utf-8")
        config = load_workflows(tmp_path)[0]
        assert config.handlers[0].agent == "claude"
        assert config.handlers[0].model is None


# ---------------------------------------------------------------------------
# AC-3: YAML パース（異常系）
# ---------------------------------------------------------------------------


class TestYamlParseError:
    def test_missing_name_raises_value_error(self, tmp_path):
        yaml_no_name = """\
triggers:
  - label: "pipeline:draft-ready"
handlers:
  - name: draft_design
    template: draft-design
"""
        (tmp_path / "bad.yml").write_text(yaml_no_name, encoding="utf-8")
        with pytest.raises(ValueError, match="name"):
            load_workflows(tmp_path)

    def test_empty_triggers_raises_value_error(self, tmp_path):
        yaml_empty_triggers = """\
name: test
triggers: []
handlers:
  - name: draft_design
    template: draft-design
"""
        (tmp_path / "bad.yml").write_text(yaml_empty_triggers, encoding="utf-8")
        with pytest.raises(ValueError, match="triggers"):
            load_workflows(tmp_path)

    def test_missing_handlers_raises_value_error(self, tmp_path):
        yaml_no_handlers = """\
name: test
triggers:
  - label: "pipeline:draft-ready"
"""
        (tmp_path / "bad.yml").write_text(yaml_no_handlers, encoding="utf-8")
        with pytest.raises(ValueError, match="handlers"):
            load_workflows(tmp_path)

    def test_invalid_yaml_syntax_raises_value_error(self, tmp_path):
        (tmp_path / "bad.yml").write_text(":\t: invalid: yaml: [}", encoding="utf-8")
        with pytest.raises(ValueError, match="YAML"):
            load_workflows(tmp_path)

    def test_nonexistent_directory_raises_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_workflows(tmp_path / "nonexistent")


# ---------------------------------------------------------------------------
# AC-4: GitHubIssueClient（モックテスト）
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
# AC-5: WorkflowDispatcher イベントマッチング
# ---------------------------------------------------------------------------


def _make_workflow() -> WorkflowConfig:
    return WorkflowConfig(
        name="stash-pipeline",
        triggers=[
            TriggerConfig(label="pipeline:draft-ready"),
            TriggerConfig(label="pipeline:develop-ready"),
        ],
        handlers=[
            PhaseHandler(name="draft_design", template="draft-design"),
            PhaseHandler(name="impl", template="impl"),
        ],
        polling_interval=30,
    )


def _make_issue(number: int, label: str) -> dict:
    return {
        "number": number,
        "title": f"Issue {number}",
        "body": "",
        "labels": [{"name": label}],
        "url": f"https://github.com/owner/repo/issues/{number}",
    }


class TestWorkflowDispatcherMatching:
    def _make_dispatcher(self, workflow: WorkflowConfig) -> tuple[WorkflowDispatcher, MagicMock, MagicMock, MagicMock]:
        github_client = MagicMock(spec=GitHubIssueClient)
        pipeline_state = MagicMock()
        order_builder = MagicMock()
        dispatcher = WorkflowDispatcher(
            workflows=[workflow],
            github_client=github_client,
            pipeline_state=pipeline_state,
            order_builder=order_builder,
        )
        return dispatcher, github_client, pipeline_state, order_builder

    def test_matching_label_returns_match(self):
        workflow = _make_workflow()
        dispatcher, github_client, _, _ = self._make_dispatcher(workflow)
        issue = _make_issue(1, "pipeline:draft-ready")
        github_client.list_issues.side_effect = lambda label, **kw: (
            [issue] if label == "pipeline:draft-ready" else []
        )
        matches = dispatcher.poll_once()
        assert len(matches) >= 1
        assert matches[0]["issue"] == 1
        assert matches[0]["workflow"] == "stash-pipeline"
        assert matches[0]["handler"] == "draft_design"

    def test_non_matching_label_skipped(self):
        workflow = _make_workflow()
        dispatcher, github_client, _, _ = self._make_dispatcher(workflow)
        github_client.list_issues.return_value = []
        matches = dispatcher.poll_once()
        assert matches == []

    def test_idempotency_false_skips_dispatch(self):
        workflow = _make_workflow()
        dispatcher, github_client, pipeline_state, order_builder = self._make_dispatcher(workflow)
        pipeline_state.check_idempotency.return_value = False
        issue = _make_issue(1, "pipeline:draft-ready")
        dispatcher.dispatch(issue, workflow, workflow.handlers[0])
        order_builder.build_order.assert_not_called()
        pipeline_state.append_exec.assert_not_called()


# ---------------------------------------------------------------------------
# AC-6: WorkflowDispatcher ディスパッチ（exec.md 経由）
# ---------------------------------------------------------------------------


class TestWorkflowDispatcherDispatch:
    def _make_dispatcher(self, workflow: WorkflowConfig) -> tuple[WorkflowDispatcher, MagicMock, MagicMock, MagicMock]:
        github_client = MagicMock(spec=GitHubIssueClient)
        pipeline_state = MagicMock()
        pipeline_state.check_idempotency.return_value = True
        pipeline_state.write_order_file.return_value = "ts-claude-order-uuid.md"
        order_builder = MagicMock()
        order_builder.build_order.return_value = "order content"
        dispatcher = WorkflowDispatcher(
            workflows=[workflow],
            github_client=github_client,
            pipeline_state=pipeline_state,
            order_builder=order_builder,
        )
        return dispatcher, github_client, pipeline_state, order_builder

    def test_dispatch_calls_append_exec(self):
        workflow = _make_workflow()
        dispatcher, _, pipeline_state, _ = self._make_dispatcher(workflow)
        issue = _make_issue(42, "pipeline:draft-ready")
        dispatcher.dispatch(issue, workflow, workflow.handlers[0])
        pipeline_state.append_exec.assert_called_once()

    def test_dispatch_transitions_label_ready_to_running(self):
        workflow = _make_workflow()
        dispatcher, github_client, _, _ = self._make_dispatcher(workflow)
        issue = _make_issue(42, "pipeline:draft-ready")
        dispatcher.dispatch(issue, workflow, workflow.handlers[0])
        github_client.update_label.assert_called_once_with(
            42, "pipeline:draft-ready", "pipeline:draft-running"
        )

    def test_dispatch_idempotency_key_format(self):
        workflow = _make_workflow()
        dispatcher, _, pipeline_state, _ = self._make_dispatcher(workflow)
        issue = _make_issue(42, "pipeline:draft-ready")
        dispatcher.dispatch(issue, workflow, workflow.handlers[0])
        pipeline_state.check_idempotency.assert_called_once_with(
            "stash-pipeline:draft_design:42"
        )

    def test_dispatch_exec_lines_contain_idempotency_marker(self):
        workflow = _make_workflow()
        dispatcher, _, pipeline_state, _ = self._make_dispatcher(workflow)
        issue = _make_issue(42, "pipeline:draft-ready")
        dispatcher.dispatch(issue, workflow, workflow.handlers[0])
        args = pipeline_state.append_exec.call_args[0][0]
        assert any("idempotency: stash-pipeline:draft_design:42" in line for line in args)


# ---------------------------------------------------------------------------
# AC-7: 既存ワークフローの YAML 表現
# ---------------------------------------------------------------------------


class TestExistingWorkflowYaml:
    def test_sample_pipeline_yaml_parseable(self):
        """tests/fixtures/sample-workflow.yml が load_workflows() でパース可能なこと"""
        fixtures_dir = Path(__file__).resolve().parent / "fixtures"
        assert fixtures_dir.exists(), f"fixtures/ ディレクトリが存在しません: {fixtures_dir}"
        configs = load_workflows(fixtures_dir)
        assert len(configs) >= 1

    def test_sample_pipeline_yaml_has_correct_triggers(self):
        fixtures_dir = Path(__file__).resolve().parent / "fixtures"
        configs = load_workflows(fixtures_dir)
        config = next((c for c in configs if c.name == "sample-pipeline"), None)
        assert config is not None, "sample-pipeline ワークフローが見つかりません"
        trigger_labels = [t.label for t in config.triggers]
        assert "pipeline:draft-ready" in trigger_labels
        assert "pipeline:develop-ready" in trigger_labels
        assert "pipeline:merge-ready" in trigger_labels

    def test_sample_pipeline_yaml_has_correct_handlers(self):
        fixtures_dir = Path(__file__).resolve().parent / "fixtures"
        configs = load_workflows(fixtures_dir)
        config = next((c for c in configs if c.name == "sample-pipeline"), None)
        assert config is not None
        handler_names = [h.name for h in config.handlers]
        assert "draft_design" in handler_names
        assert "impl" in handler_names
        assert "merge" in handler_names
