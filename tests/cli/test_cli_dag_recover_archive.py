"""Tests for cmd_recover CLI output: dry-run, archive, and keep-results."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ghdag.io.exec_jsonl import build_idempotency_key
from ghdag.workflow.schema import HandlerConfig, StepConfig, TriggerConfig, WorkflowConfig

UUID_P2 = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
ISSUE_NUMBER = 2876


def _make_workflow() -> WorkflowConfig:
    return WorkflowConfig(
        name="issuesmith",
        triggers=[TriggerConfig(label="develop-ready", handler="impl")],
        handlers={
            "impl": HandlerConfig(
                steps=[StepConfig(id="p2", template="p2", model="claude-opus-4-6")],
            ),
        },
        polling_interval=0,
    )


def _setup(tmp_path: Path):
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    done = jobs / "done"
    done.mkdir()
    state_dir = tmp_path / ".pipeline-state"

    exec_jsonl = jobs / "exec.jsonl"
    order_path = jobs / f"20260905120000-shell-order-{UUID_P2}.md"
    order_path.write_text("shell order", encoding="utf-8")

    result_path = jobs / f"20260905120000-shell-result-{UUID_P2}.md"
    result_path.write_text("PIPELINE_STATUS: VERIFY_FAILED\n", encoding="utf-8")

    key = build_idempotency_key("issuesmith", "impl", ISSUE_NUMBER, 0)
    rec = {
        "uuid": UUID_P2,
        "engine": "shell",
        "command": f"bash -o pipefail {order_path}",
        "depends": [],
        "result_path": str(result_path),
        "idempotency_key": key,
        "annotations": {"step_name": "p2"},
    }
    exec_jsonl.write_text(json.dumps(rec) + "\n", encoding="utf-8")
    (done / UUID_P2).write_text("1", encoding="utf-8")

    return jobs, done, state_dir, exec_jsonl, result_path


def _make_args(
    jobs: Path,
    state_dir: Path,
    exec_jsonl: Path,
    workflows_dir: Path,
    *,
    dry_run: bool = False,
    keep_results: bool = False,
) -> argparse.Namespace:
    return argparse.Namespace(
        issue_number=ISSUE_NUMBER,
        handler="impl",
        from_step="p2",
        dry_run=dry_run,
        keep_results=keep_results,
        workflows_dir=str(workflows_dir),
        exec_jsonl=str(exec_jsonl),
        workflow=None,
        state_dir=str(state_dir),
    )


class TestCmdRecoverArchive:
    def test_dry_run_shows_would_archive(self, tmp_path, monkeypatch, capsys):
        """AC-2: dry_run outputs 'would archive' and leaves files unchanged."""
        from ghdag.cli.commands.recover import cmd_recover

        jobs, done, state_dir, exec_jsonl, result_path = _setup(tmp_path)
        workflows_dir = tmp_path / "workflows"
        workflows_dir.mkdir()

        monkeypatch.setattr(
            "ghdag.workflow.loader.load_workflows",
            lambda _path: [_make_workflow()],
        )

        args = _make_args(jobs, state_dir, exec_jsonl, workflows_dir, dry_run=True)
        cmd_recover(args)

        captured = capsys.readouterr()
        assert f"would archive: {result_path} ->" in captured.out
        assert ".prev-" in captured.out
        assert result_path.exists()
        assert (done / UUID_P2).exists()

    def test_run_shows_archived(self, tmp_path, monkeypatch, capsys):
        """AC-2b: normal run outputs 'archived', removes result, removes done marker."""
        from ghdag.cli.commands.recover import cmd_recover

        jobs, done, state_dir, exec_jsonl, result_path = _setup(tmp_path)
        workflows_dir = tmp_path / "workflows"
        workflows_dir.mkdir()

        monkeypatch.setattr(
            "ghdag.workflow.loader.load_workflows",
            lambda _path: [_make_workflow()],
        )

        args = _make_args(jobs, state_dir, exec_jsonl, workflows_dir, dry_run=False)
        cmd_recover(args)

        captured = capsys.readouterr()
        assert f"archived: {result_path} ->" in captured.out
        assert ".prev-" in captured.out
        assert not result_path.exists()
        assert len(list(jobs.glob(f"*{UUID_P2}*.prev-*"))) == 1
        assert not (done / UUID_P2).exists()

    def test_keep_results_no_archived_line(self, tmp_path, monkeypatch, capsys):
        """AC-2b keep_results: no 'archived:' in output; result file unchanged."""
        from ghdag.cli.commands.recover import cmd_recover

        jobs, done, state_dir, exec_jsonl, result_path = _setup(tmp_path)
        workflows_dir = tmp_path / "workflows"
        workflows_dir.mkdir()

        monkeypatch.setattr(
            "ghdag.workflow.loader.load_workflows",
            lambda _path: [_make_workflow()],
        )

        args = _make_args(jobs, state_dir, exec_jsonl, workflows_dir, keep_results=True)
        cmd_recover(args)

        captured = capsys.readouterr()
        assert "archived:" not in captured.out
        assert result_path.read_text(encoding="utf-8") == "PIPELINE_STATUS: VERIFY_FAILED\n"
