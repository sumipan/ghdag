"""Tests for --state-dir option on ghdag trigger and watch commands (AC-1 to AC-4)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch


_WORKFLOW_YAML = """
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
""".strip()


def _setup_workflow_dir(root: Path) -> Path:
    workflows_dir = root / "workflows"
    workflows_dir.mkdir(exist_ok=True)
    templates = workflows_dir / "templates"
    templates.mkdir(exist_ok=True)
    (templates / "p1.md").write_text("order ${issue_number}", encoding="utf-8")
    (workflows_dir / "issuesmith.yml").write_text(_WORKFLOW_YAML, encoding="utf-8")
    return workflows_dir


def _trigger_args(exec_jsonl: str, workflows_dir: str, state_dir=None) -> MagicMock:
    args = MagicMock()
    args.exec_jsonl = exec_jsonl
    args.state_dir = state_dir
    args.workflows_dir = workflows_dir
    args.issue_number = 1
    args.handler = "impl"
    args.workflow = None
    args.redispatch = False
    args.reason = None
    return args


def _watch_args(exec_jsonl: str, workflows_dir: str, state_dir=None) -> MagicMock:
    args = MagicMock()
    args.exec_jsonl = exec_jsonl
    args.state_dir = state_dir
    args.workflows_dir = workflows_dir
    args.interval = 30.0
    args.once = True
    args.pause_file = None
    return args


class _CaptureStateDir(Exception):
    """Raised by the PipelineState mock to capture state_dir and stop execution."""
    def __init__(self, state_dir):
        self.state_dir = str(state_dir)


def _make_pipeline_state_mock(captured: dict):
    """Returns a PipelineState side_effect that records state_dir and aborts."""
    def _mock(state_dir, exec_jsonl_path):
        captured["state_dir"] = str(state_dir)
        raise _CaptureStateDir(state_dir)
    return _mock


class TestTriggerStateDirDerivation:
    """AC-1 to AC-3: ghdag trigger uses correct state_dir."""

    def test_ac1_absolute_exec_md_derives_state_dir(self, tmp_path, monkeypatch):
        """State dir is exec_jsonl.parent.parent/.pipeline-state, not cwd."""
        from ghdag.cli.commands.trigger import cmd_trigger

        worktree = tmp_path / "worktree"
        worktree.mkdir()
        monkeypatch.chdir(worktree)

        repo_root = tmp_path / "repo"
        exec_jsonl = repo_root / "jobs" / "exec.jsonl"
        exec_jsonl.parent.mkdir(parents=True)
        exec_jsonl.write_text("", encoding="utf-8")

        workflows_dir = _setup_workflow_dir(repo_root)
        captured = {}
        mock_github = MagicMock()
        mock_github.get_issue.return_value = {"number": 1, "labels": [], "title": "t", "body": ""}

        with patch("ghdag.pipeline.state.PipelineState", side_effect=_make_pipeline_state_mock(captured)):
            with patch("ghdag.github_client.create_github_client", return_value=mock_github):
                try:
                    args = _trigger_args(str(exec_jsonl), str(workflows_dir), state_dir=None)
                    cmd_trigger(args)
                except (_CaptureStateDir, SystemExit):
                    pass

        expected = str(repo_root / ".pipeline-state")
        assert captured.get("state_dir") == expected, (
            f"Expected state_dir={expected!r}, got {captured.get('state_dir')!r}. "
            "State dir must be derived from exec_jsonl's parent's parent, not cwd."
        )

    def test_ac2_explicit_state_dir_overrides_derived(self, tmp_path, monkeypatch):
        """Explicit --state-dir takes priority over derivation from --exec-md."""
        from ghdag.cli.commands.trigger import cmd_trigger

        monkeypatch.chdir(tmp_path)

        exec_jsonl = tmp_path / "jobs" / "exec.jsonl"
        exec_jsonl.parent.mkdir(parents=True)
        exec_jsonl.write_text("", encoding="utf-8")
        workflows_dir = _setup_workflow_dir(tmp_path)

        custom_state_dir = str(tmp_path / "custom-state")
        captured = {}
        mock_github = MagicMock()
        mock_github.get_issue.return_value = {"number": 1, "labels": [], "title": "t", "body": ""}

        with patch("ghdag.pipeline.state.PipelineState", side_effect=_make_pipeline_state_mock(captured)):
            with patch("ghdag.github_client.create_github_client", return_value=mock_github):
                try:
                    args = _trigger_args(str(exec_jsonl), str(workflows_dir), state_dir=custom_state_dir)
                    cmd_trigger(args)
                except (_CaptureStateDir, SystemExit):
                    pass

        assert captured.get("state_dir") == custom_state_dir, (
            f"Expected state_dir={custom_state_dir!r}, got {captured.get('state_dir')!r}."
        )

    def test_ac3_relative_exec_md_from_repo_root_matches_legacy(self, tmp_path, monkeypatch):
        """cwd=repo-root, relative --exec-md jobs/exec.jsonl → cwd/.pipeline-state."""
        from ghdag.cli.commands.trigger import cmd_trigger

        monkeypatch.chdir(tmp_path)

        exec_jsonl = tmp_path / "jobs" / "exec.jsonl"
        exec_jsonl.parent.mkdir(parents=True)
        exec_jsonl.write_text("", encoding="utf-8")
        workflows_dir = _setup_workflow_dir(tmp_path)

        captured = {}
        mock_github = MagicMock()
        mock_github.get_issue.return_value = {"number": 1, "labels": [], "title": "t", "body": ""}

        with patch("ghdag.pipeline.state.PipelineState", side_effect=_make_pipeline_state_mock(captured)):
            with patch("ghdag.github_client.create_github_client", return_value=mock_github):
                try:
                    args = _trigger_args("jobs/exec.jsonl", str(workflows_dir), state_dir=None)
                    cmd_trigger(args)
                except (_CaptureStateDir, SystemExit):
                    pass

        expected = str(tmp_path / ".pipeline-state")
        assert captured.get("state_dir") == expected, (
            f"Expected state_dir={expected!r} (same as legacy cwd-relative), "
            f"got {captured.get('state_dir')!r}."
        )


class TestWatchStateDirDerivation:
    """AC-4: ghdag watch has the same --state-dir behavior as trigger."""

    def test_ac4_absolute_exec_md_derives_state_dir(self, tmp_path, monkeypatch):
        """watch: state_dir = exec_jsonl.parent.parent/.pipeline-state when not given."""
        from ghdag.cli.commands.watch import cmd_watch

        worktree = tmp_path / "worktree"
        worktree.mkdir()
        monkeypatch.chdir(worktree)

        repo_root = tmp_path / "repo"
        exec_jsonl = repo_root / "jobs" / "exec.jsonl"
        exec_jsonl.parent.mkdir(parents=True)
        exec_jsonl.write_text("", encoding="utf-8")
        workflows_dir = _setup_workflow_dir(repo_root)

        captured = {}
        mock_github_clients = MagicMock()

        with patch("ghdag.pipeline.state.PipelineState", side_effect=_make_pipeline_state_mock(captured)):
            with patch("ghdag.github_client.create_github_clients", return_value=mock_github_clients):
                try:
                    args = _watch_args(str(exec_jsonl), str(workflows_dir), state_dir=None)
                    cmd_watch(args)
                except (_CaptureStateDir, SystemExit, Exception):
                    pass

        expected = str(repo_root / ".pipeline-state")
        assert captured.get("state_dir") == expected, (
            f"watch: Expected state_dir={expected!r}, got {captured.get('state_dir')!r}."
        )

    def test_ac4_explicit_state_dir_overrides_derived_watch(self, tmp_path, monkeypatch):
        """watch: explicit --state-dir overrides derivation."""
        from ghdag.cli.commands.watch import cmd_watch

        monkeypatch.chdir(tmp_path)

        exec_jsonl = tmp_path / "jobs" / "exec.jsonl"
        exec_jsonl.parent.mkdir(parents=True)
        exec_jsonl.write_text("", encoding="utf-8")
        workflows_dir = _setup_workflow_dir(tmp_path)

        custom_state_dir = str(tmp_path / "custom-state")
        captured = {}
        mock_github_clients = MagicMock()

        with patch("ghdag.pipeline.state.PipelineState", side_effect=_make_pipeline_state_mock(captured)):
            with patch("ghdag.github_client.create_github_clients", return_value=mock_github_clients):
                try:
                    args = _watch_args(str(exec_jsonl), str(workflows_dir), state_dir=custom_state_dir)
                    cmd_watch(args)
                except (_CaptureStateDir, SystemExit, Exception):
                    pass

        assert captured.get("state_dir") == custom_state_dir, (
            f"watch: Expected state_dir={custom_state_dir!r}, got {captured.get('state_dir')!r}."
        )

    def test_ac4_relative_exec_md_from_repo_root_matches_legacy_watch(self, tmp_path, monkeypatch):
        """watch: relative --exec-md from repo root yields cwd/.pipeline-state."""
        from ghdag.cli.commands.watch import cmd_watch

        monkeypatch.chdir(tmp_path)

        exec_jsonl = tmp_path / "jobs" / "exec.jsonl"
        exec_jsonl.parent.mkdir(parents=True)
        exec_jsonl.write_text("", encoding="utf-8")
        workflows_dir = _setup_workflow_dir(tmp_path)

        captured = {}
        mock_github_clients = MagicMock()

        with patch("ghdag.pipeline.state.PipelineState", side_effect=_make_pipeline_state_mock(captured)):
            with patch("ghdag.github_client.create_github_clients", return_value=mock_github_clients):
                try:
                    args = _watch_args("jobs/exec.jsonl", str(workflows_dir), state_dir=None)
                    cmd_watch(args)
                except (_CaptureStateDir, SystemExit, Exception):
                    pass

        expected = str(tmp_path / ".pipeline-state")
        assert captured.get("state_dir") == expected, (
            f"watch: Expected state_dir={expected!r} (legacy cwd-relative), "
            f"got {captured.get('state_dir')!r}."
        )


class TestStateDirArgParsing:
    """AC-2 / AC-4: --state-dir appears in argparse for trigger and watch."""

    def test_trigger_parser_has_state_dir(self):
        from ghdag.cli.main import _build_parser
        parser = _build_parser()
        args = parser.parse_args([
            "trigger", "1",
            "--handler", "impl",
            "--exec-md", "/abs/jobs/exec.jsonl",
            "--state-dir", "/custom/state",
        ])
        assert args.state_dir == "/custom/state"

    def test_trigger_parser_state_dir_defaults_to_none(self):
        from ghdag.cli.main import _build_parser
        parser = _build_parser()
        args = parser.parse_args([
            "trigger", "1",
            "--handler", "impl",
        ])
        assert args.state_dir is None

    def test_watch_parser_has_state_dir(self):
        from ghdag.cli.main import _build_parser
        parser = _build_parser()
        args = parser.parse_args([
            "watch", "workflows/",
            "--exec-md", "/abs/jobs/exec.jsonl",
            "--state-dir", "/custom/state",
        ])
        assert args.state_dir == "/custom/state"

    def test_watch_parser_state_dir_defaults_to_none(self):
        from ghdag.cli.main import _build_parser
        parser = _build_parser()
        args = parser.parse_args(["watch", "workflows/"])
        assert args.state_dir is None
