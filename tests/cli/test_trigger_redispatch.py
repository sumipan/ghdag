"""Tests for ghdag trigger --redispatch."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch


class TestTriggerRedispatch:
    def test_redispatch_reason_recorded_in_audit(self, tmp_path, monkeypatch):
        from ghdag.cli.commands.trigger import cmd_trigger

        monkeypatch.chdir(tmp_path)
        exec_jsonl = tmp_path / "jobs" / "exec.jsonl"
        exec_jsonl.parent.mkdir(parents=True)
        exec_jsonl.write_text("", encoding="utf-8")
        workflows_dir = tmp_path / "workflows"
        workflows_dir.mkdir()
        templates = workflows_dir / "templates"
        templates.mkdir()
        (templates / "p1.md").write_text("order ${issue_number}", encoding="utf-8")
        (workflows_dir / "issuesmith.yml").write_text(
            """
name: issuesmith
template_dir: templates
polling_interval: 30
triggers:
  - label: develop-ready
    handler: impl
handlers:
  impl:
    steps:
      - id: p1
        template: p1
        model: claude-opus-4-6
""".strip(),
            encoding="utf-8",
        )

        issue = {"number": 2876, "labels": [], "title": "test", "body": ""}
        mock_github = MagicMock()
        mock_github.issue_get.return_value = issue

        args = MagicMock()
        args.workflows_dir = str(workflows_dir)
        args.issue_number = 2876
        args.handler = "impl"
        args.exec_jsonl = str(exec_jsonl)
        args.workflow = None
        args.redispatch = True
        args.reason = "worktree lost"

        with patch(
            "ghdag.github_client.create_github_clients",
            return_value=[mock_github],
        ):
            cmd_trigger(args)

        mock_github.issue_get.assert_called_once_with(2876)
        audit_path = exec_jsonl.parent / "audit.jsonl"
        assert audit_path.exists()
        records = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        redispatch_records = [r for r in records if r.get("event_type") == "redispatch"]
        assert len(redispatch_records) == 1
        assert redispatch_records[0]["reason"] == "worktree lost"
        assert redispatch_records[0]["generation"] == 1
