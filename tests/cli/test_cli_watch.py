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
from unittest.mock import patch

import pytest

from ghdag.forge.local import LocalForge


class TestWatchNormal:
    def test_watch_builds_clients_without_rate_limit_wait(self, tmp_path):
        workflows_dir = tmp_path / "workflows"
        workflows_dir.mkdir()
        create_calls: list[dict] = []

        def fake_create_github_clients(**kwargs):
            create_calls.append(kwargs)
            return []

        with patch("ghdag.workflow.loader.load_workflows", return_value=[]), \
             patch("ghdag.workflow.dispatcher.WorkflowDispatcher") as mock_disp_cls, \
             patch("ghdag.github_client.create_github_clients", side_effect=fake_create_github_clients), \
             patch("ghdag.pipeline.state.PipelineState"), \
             patch("ghdag.pipeline.order.TemplateOrderBuilder"):
            mock_disp_cls.return_value.run = lambda *a, **k: None
            from ghdag.cli import main
            main(["watch", str(workflows_dir)])

        assert len(create_calls) == 1
        assert create_calls[0] == {"rate_limit_max_wait_sec": 0}


# ---------------------------------------------------------------------------
# AC6: ghdag watch error cases
# ---------------------------------------------------------------------------


class TestWatchError:
    def test_watch_nonexistent_dir_exits_2(self, capsys):
        from ghdag.cli import main

        with pytest.raises(SystemExit) as exc:
            main(["watch", "nonexistent_workflows_dir/"])
        assert exc.value.code == 2
        assert capsys.readouterr().err.splitlines()[0].startswith(
            "error[WORKFLOWS_DIR_NOT_FOUND]:"
        )


def _git(cwd: Path, *args: str) -> None:
    subprocess.check_call(
        ["git", *args],
        cwd=cwd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _init_git_repo(root: Path) -> None:
    _git(root, "init")
    _git(root, "config", "user.email", "watch-cli@test")
    _git(root, "config", "user.name", "Watch CLI Test")
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


class TestWatchForgeBackend:
    def test_watch_local_forge_dispatches_without_github_credentials(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Refs sumipan/nexus#6606 — watch uses LocalForge when GHDAG_FORGE=local."""
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
        forge.issue_create("title", "body", labels=["pipeline:develop-ready"])

        with mock.patch("urllib.request.urlopen") as urlopen:
            urlopen.side_effect = AssertionError("GitHub network must not be used")
            from ghdag.cli import main
            main([
                "watch",
                str(workflows_dir),
                "--exec-jsonl",
                str(exec_jsonl),
                "--once",
            ])

        lines = [ln for ln in exec_jsonl.read_text(encoding="utf-8").splitlines() if ln.strip()]
        assert len(lines) == 1
