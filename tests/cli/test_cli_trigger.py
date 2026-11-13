"""Tests for ghdag.cli — AC1 - AC11.

Test approach:
- Parse validation via main(argv=[...])
- Mock DagEngine / WorkflowDispatcher; assert correct args
- Error cases assert SystemExit code
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock, patch

import pytest

from ghdag.forge.local import LocalForge


class TestTriggerNormal:
    def _write_workflow_yaml(self, workflows_dir: Path) -> None:
        yaml_content = """\
name: test-pipeline
triggers:
  - label: "pipeline:draft-ready"
    handler: brushup
  - label: "pipeline:develop-ready"
    handler: impl
handlers:
  brushup:
    steps:
      - template: brushup
        model: claude-opus-4-6
  impl:
    steps:
      - id: p1
        template: p1-implement
        model: claude-sonnet-4-6
"""
        (workflows_dir / "test.yml").write_text(yaml_content, encoding="utf-8")
        tdir = workflows_dir / "templates"
        tdir.mkdir(exist_ok=True)
        (tdir / "brushup.md").write_text("# brushup\n", encoding="utf-8")
        (tdir / "p1-implement.md").write_text("# p1-implement\n", encoding="utf-8")

    def test_trigger_calls_dispatch(self, tmp_path, capsys):
        workflows_dir = tmp_path / "workflows"
        workflows_dir.mkdir()
        self._write_workflow_yaml(workflows_dir)
        exec_md = tmp_path / "exec.jsonl"
        exec_md.write_text("")

        mock_dispatch_result = MagicMock()
        mock_dispatch_result.status = "dispatched"
        mock_dispatch_result.reason = ""
        mock_dispatch_result.exec_lines = [
            '{"uuid": "11111111-1111-1111-1111-111111111111", "command": "cat a"}',
            '{"uuid": "22222222-2222-2222-2222-222222222222", "command": "cat b"}',
        ]
        mock_github_client = MagicMock()
        mock_github_client.get_issue.return_value = {
            "number": 42,
            "title": "Test",
            "body": "body",
            "labels": [],
            "url": "https://example.com",
        }
        trigger_state = {"dispatch_issue": None}

        class FakeDispatcher:
            def __init__(self, *args, **kwargs):
                pass

            def dispatch(self, issue, *args, **kwargs):
                trigger_state["dispatch_issue"] = issue
                return mock_dispatch_result

        with patch("ghdag.workflow.dispatcher.WorkflowDispatcher", FakeDispatcher), \
             patch("ghdag.github_client.create_github_client", return_value=mock_github_client), \
             patch("ghdag.pipeline.state.PipelineState"), \
             patch("ghdag.pipeline.order.TemplateOrderBuilder"):
            from ghdag.cli import main
            main([
                "trigger", "42",
                "--handler", "brushup",
                "--workflows-dir", str(workflows_dir),
                "--exec-jsonl", str(exec_md),
            ])

        assert trigger_state["dispatch_issue"]["number"] == 42
        out = capsys.readouterr().out
        assert "exec_lines: 2 step(s)" in out

    def test_trigger_prints_zero_steps_when_exec_lines_empty(self, tmp_path, capsys):
        workflows_dir = tmp_path / "workflows"
        workflows_dir.mkdir()
        self._write_workflow_yaml(workflows_dir)
        exec_md = tmp_path / "exec.jsonl"
        exec_md.write_text("")

        mock_dispatch_result = MagicMock()
        mock_dispatch_result.status = "dispatched"
        mock_dispatch_result.reason = ""
        mock_dispatch_result.exec_lines = []
        mock_github_client = MagicMock()
        mock_github_client.get_issue.return_value = {
            "number": 7, "title": "T", "body": "", "labels": [], "url": "",
        }

        class FakeDispatcher:
            def __init__(self, *args, **kwargs):
                pass

            def dispatch(self, *args, **kwargs):
                return mock_dispatch_result

        with patch("ghdag.workflow.dispatcher.WorkflowDispatcher", FakeDispatcher), \
             patch("ghdag.github_client.create_github_client", return_value=mock_github_client), \
             patch("ghdag.pipeline.state.PipelineState"), \
             patch("ghdag.pipeline.order.TemplateOrderBuilder"):
            from ghdag.cli import main
            main([
                "trigger", "7",
                "--handler", "brushup",
                "--workflows-dir", str(workflows_dir),
                "--exec-jsonl", str(exec_md),
            ])

        out = capsys.readouterr().out
        assert "exec_lines: 0 step(s)" in out

    def test_trigger_with_workflow_option(self, tmp_path):
        workflows_dir = tmp_path / "workflows"
        workflows_dir.mkdir()
        self._write_workflow_yaml(workflows_dir)
        exec_md = tmp_path / "exec.jsonl"
        exec_md.write_text("")

        mock_dispatch_result = MagicMock()
        mock_dispatch_result.status = "dispatched"
        mock_dispatch_result.reason = ""
        mock_dispatch_result.exec_lines = [
            '{"uuid": "33333333-3333-3333-3333-333333333333", "command": "cat"}',
        ]
        mock_github_client = MagicMock()
        mock_github_client.get_issue.return_value = {
            "number": 10, "title": "T", "body": "", "labels": [], "url": "",
        }
        trigger_state = {"dispatched": False}

        class FakeDispatcher:
            def __init__(self, *args, **kwargs):
                pass

            def dispatch(self, *args, **kwargs):
                trigger_state["dispatched"] = True
                return mock_dispatch_result

        with patch("ghdag.workflow.dispatcher.WorkflowDispatcher", FakeDispatcher), \
             patch("ghdag.github_client.create_github_client", return_value=mock_github_client), \
             patch("ghdag.pipeline.state.PipelineState"), \
             patch("ghdag.pipeline.order.TemplateOrderBuilder"):
            from ghdag.cli import main
            main([
                "trigger", "10",
                "--handler", "impl",
                "--workflow", "test-pipeline",
                "--workflows-dir", str(workflows_dir),
                "--exec-jsonl", str(exec_md),
            ])

        assert trigger_state["dispatched"] is True


# ---------------------------------------------------------------------------
# AC10: ghdag trigger error cases
# ---------------------------------------------------------------------------


class TestTriggerError:
    def _write_workflow_yaml(self, workflows_dir: Path) -> None:
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
        (workflows_dir / "test.yml").write_text(yaml_content, encoding="utf-8")
        tdir = workflows_dir / "templates"
        tdir.mkdir(exist_ok=True)
        (tdir / "brushup.md").write_text("# brushup\n", encoding="utf-8")

    def test_trigger_no_args_exits_2(self):
        from ghdag.cli import main
        with pytest.raises(SystemExit) as exc:
            main(["trigger"])
        assert exc.value.code == 2

    def test_trigger_missing_handler_exits_2(self):
        from ghdag.cli import main
        with pytest.raises(SystemExit) as exc:
            main(["trigger", "42"])
        assert exc.value.code == 2

    def test_trigger_nonexistent_dir_exits_2(self, capsys):
        from ghdag.cli import main
        with pytest.raises(SystemExit) as exc:
            main([
                "trigger", "42",
                "--handler", "brushup",
                "--workflows-dir", "nonexistent/",
            ])
        assert exc.value.code == 2
        err = capsys.readouterr().err
        assert err.splitlines()[0].startswith("error[WORKFLOWS_DIR_NOT_FOUND]:")

    def test_trigger_unknown_handler_exits_2(self, tmp_path, capsys):
        workflows_dir = tmp_path / "workflows"
        workflows_dir.mkdir()
        self._write_workflow_yaml(workflows_dir)

        from ghdag.cli import main
        with pytest.raises(SystemExit) as exc:
            main([
                "trigger", "42",
                "--handler", "nonexistent",
                "--workflows-dir", str(workflows_dir),
            ])
        assert exc.value.code == 2
        err_lines = capsys.readouterr().err.splitlines()
        assert err_lines[0].startswith("error[HANDLER_NOT_FOUND]:")
        assert err_lines[1].startswith("  hint: available:")

    def test_trigger_unknown_workflow_exits_2(self, tmp_path, capsys):
        workflows_dir = tmp_path / "workflows"
        workflows_dir.mkdir()
        self._write_workflow_yaml(workflows_dir)

        from ghdag.cli import main
        with pytest.raises(SystemExit) as exc:
            main([
                "trigger", "42",
                "--handler", "brushup",
                "--workflow", "wrong-name",
                "--workflows-dir", str(workflows_dir),
            ])
        assert exc.value.code == 2
        assert "error[WORKFLOW_NOT_FOUND]:" in capsys.readouterr().err

    def test_trigger_skipped_exits_1(self, tmp_path, capsys):
        """exit 1 when dispatch returns skipped"""
        workflows_dir = tmp_path / "workflows"
        workflows_dir.mkdir()
        self._write_workflow_yaml(workflows_dir)
        exec_md = tmp_path / "exec.jsonl"
        exec_md.write_text("")

        mock_dispatch_result = MagicMock()
        mock_dispatch_result.status = "skipped"
        mock_dispatch_result.reason = "already dispatched"
        mock_dispatch_result.exec_lines = []
        mock_dispatcher_cls = MagicMock()
        mock_dispatcher_cls.return_value.dispatch.return_value = mock_dispatch_result
        mock_github_client = MagicMock()
        mock_github_client.get_issue.return_value = {
            "number": 42, "title": "T", "body": "", "labels": [], "url": "",
        }
        mock_create_github_client = MagicMock(return_value=mock_github_client)

        with patch("ghdag.workflow.dispatcher.WorkflowDispatcher", mock_dispatcher_cls), \
             patch("ghdag.github_client.create_github_client", mock_create_github_client), \
             patch("ghdag.pipeline.state.PipelineState"), \
             patch("ghdag.pipeline.order.TemplateOrderBuilder"):
            from ghdag.cli import main
            with pytest.raises(SystemExit) as exc:
                main([
                    "trigger", "42",
                    "--handler", "brushup",
                    "--workflows-dir", str(workflows_dir),
                    "--exec-jsonl", str(exec_md),
                ])
            assert exc.value.code == 1


class TestTriggerStructuredErrors:
    def test_invalid_yaml_exits_3_without_traceback(self, tmp_path, capsys):
        workflows_dir = tmp_path / "workflows"
        workflows_dir.mkdir()
        (workflows_dir / "bad.yml").write_text("name: [\n", encoding="utf-8")
        from ghdag.cli import main

        with pytest.raises(SystemExit) as exc:
            main([
                "trigger", "1",
                "--handler", "impl",
                "--workflows-dir", str(workflows_dir),
            ])
        assert exc.value.code == 3
        err = capsys.readouterr().err
        assert err.splitlines()[0].startswith("error[WORKFLOW_")
        assert "Traceback" not in err

    def test_trigger_json_success_output(self, tmp_path, capsys):
        workflows_dir = tmp_path / "workflows"
        workflows_dir.mkdir()
        TestTriggerNormal()._write_workflow_yaml(workflows_dir)
        exec_md = tmp_path / "exec.jsonl"
        exec_md.write_text("")

        mock_dispatch_result = MagicMock()
        mock_dispatch_result.status = "dispatched"
        mock_dispatch_result.reason = ""
        mock_dispatch_result.exec_lines = ['{"uuid": "u", "command": "c"}']
        mock_github_client = MagicMock()
        mock_github_client.get_issue.return_value = {
            "number": 1, "title": "T", "body": "", "labels": [], "url": "",
        }

        class FakeDispatcher:
            def __init__(self, *args, **kwargs):
                pass

            def dispatch(self, *args, **kwargs):
                return mock_dispatch_result

        with patch("ghdag.workflow.dispatcher.WorkflowDispatcher", FakeDispatcher), \
             patch("ghdag.github_client.create_github_client", return_value=mock_github_client), \
             patch("ghdag.pipeline.state.PipelineState"), \
             patch("ghdag.pipeline.order.TemplateOrderBuilder"):
            from ghdag.cli import main
            main([
                "trigger", "1",
                "--handler", "brushup",
                "--workflows-dir", str(workflows_dir),
                "--exec-jsonl", str(exec_md),
                "--json",
            ])

        import json as _json

        payload = _json.loads(capsys.readouterr().out.strip())
        assert payload["issue"] == 1
        assert payload["handler"] == "brushup"
        assert payload["status"] == "dispatched"
        assert payload["exec_lines"] == 1


def _git(cwd: Path, *args: str) -> None:
    subprocess.check_call(
        ["git", *args],
        cwd=cwd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _init_git_repo(root: Path) -> None:
    _git(root, "init")
    _git(root, "config", "user.email", "trigger-cli@test")
    _git(root, "config", "user.name", "Trigger CLI Test")
    (root / "README").write_text("base\n", encoding="utf-8")
    _git(root, "add", "README")
    _git(root, "commit", "-m", "initial")
    try:
        _git(root, "branch", "-M", "main")
    except subprocess.CalledProcessError:
        pass


def _write_local_shell_workflow(workflows_dir: Path) -> None:
    yaml_content = """\
name: sample
template_dir: templates
triggers:
  - label: "pipeline:develop-ready"
    handler: run
handlers:
  run:
    steps:
      - id: hello
        template: hello
        engine: shell
"""
    (workflows_dir / "sample.yml").write_text(yaml_content, encoding="utf-8")
    tdir = workflows_dir / "templates"
    tdir.mkdir()
    (tdir / "hello.md").write_text('echo "ok"\n', encoding="utf-8")


def _clear_github_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GHDAG_FORGE", raising=False)
    for key in ("GITHUB_TOKEN", "GH_TOKEN", "GITHUB_REPOSITORIES"):
        monkeypatch.delenv(key, raising=False)


class TestTriggerForgeBackend:
    def test_trigger_local_forge_dispatches_without_github_credentials(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Refs sumipan/nexus#6606 — trigger uses LocalForge when GHDAG_FORGE=local."""
        forge_root = tmp_path / "forge"
        forge_root.mkdir()
        _init_git_repo(forge_root)
        workflows_dir = tmp_path / "workflows"
        workflows_dir.mkdir()
        _write_local_shell_workflow(workflows_dir)
        exec_jsonl = tmp_path / "exec.jsonl"
        exec_jsonl.write_text("", encoding="utf-8")

        _clear_github_env(monkeypatch)
        monkeypatch.setenv("GHDAG_FORGE", "local")
        monkeypatch.setenv("GHDAG_FORGE_ROOT", str(forge_root))

        forge = LocalForge(forge_root)
        issue_no = forge.issue_create("title", "body", labels=[])

        with mock.patch("urllib.request.urlopen") as urlopen:
            urlopen.side_effect = AssertionError("GitHub network must not be used")
            from ghdag.cli import main
            main([
                "trigger",
                str(issue_no),
                "--handler",
                "run",
                "--workflows-dir",
                str(workflows_dir),
                "--exec-jsonl",
                str(exec_jsonl),
            ])

        lines = [ln for ln in exec_jsonl.read_text(encoding="utf-8").splitlines() if ln.strip()]
        assert len(lines) == 1

    def test_trigger_github_mode_preserves_client_factory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Refs sumipan/nexus#6606 — default github mode keeps single-client factory."""
        workflows_dir = tmp_path / "workflows"
        workflows_dir.mkdir()
        TestTriggerNormal()._write_workflow_yaml(workflows_dir)
        exec_jsonl = tmp_path / "exec.jsonl"
        exec_jsonl.write_text("", encoding="utf-8")

        monkeypatch.setenv("GITHUB_TOKEN", "token")
        monkeypatch.setenv("GITHUB_REPOSITORIES", "o/r")
        _clear_github_env(monkeypatch)

        mock_client = MagicMock()
        mock_client.get_issue.return_value = {
            "number": 5,
            "title": "T",
            "body": "",
            "labels": [],
            "url": "https://example.com/5",
        }
        mock_dispatch_result = MagicMock()
        mock_dispatch_result.status = "dispatched"
        mock_dispatch_result.reason = ""
        mock_dispatch_result.exec_lines = ['{"uuid": "u", "command": "c"}']
        capture: dict = {}

        class FakeDispatcher:
            def __init__(self, **kwargs):
                capture["github_client"] = kwargs.get("github_client")

            def dispatch(self, issue, *args, **kwargs):
                capture["issue"] = issue
                return mock_dispatch_result

        with patch(
            "ghdag.github_client.create_github_client",
            return_value=mock_client,
        ) as mock_create, \
             patch("ghdag.workflow.dispatcher.WorkflowDispatcher", FakeDispatcher), \
             patch("ghdag.pipeline.state.PipelineState"), \
             patch("ghdag.pipeline.order.TemplateOrderBuilder"):
            from ghdag.cli import main
            main([
                "trigger",
                "5",
                "--handler",
                "brushup",
                "--workflows-dir",
                str(workflows_dir),
                "--exec-jsonl",
                str(exec_jsonl),
            ])

        mock_create.assert_called_once_with()
        assert capture["github_client"] is mock_client
        assert capture["issue"]["number"] == 5

    def test_trigger_unset_forge_without_token_reports_github_auth(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
    ) -> None:
        """Refs sumipan/nexus#6606 — unset forge stays on GitHub auth, not local."""
        workflows_dir = tmp_path / "workflows"
        workflows_dir.mkdir()
        TestTriggerNormal()._write_workflow_yaml(workflows_dir)
        _clear_github_env(monkeypatch)

        from ghdag.cli import main
        with pytest.raises(SystemExit) as exc:
            main([
                "trigger",
                "1",
                "--handler",
                "brushup",
                "--workflows-dir",
                str(workflows_dir),
            ])
        assert exc.value.code == 4
        err = capsys.readouterr().err
        assert err.splitlines()[0].startswith("error[GITHUB_AUTH]:")
