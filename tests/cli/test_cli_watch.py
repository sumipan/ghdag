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


class TestWatchNormal:
    def test_watch_calls_dispatcher_run(self, tmp_path):
        workflows_dir = tmp_path / "workflows"
        workflows_dir.mkdir()

        watch_state = {"workflows_loaded": False, "dispatcher_ran": False, "dispatcher_kwargs": {}}

        def fake_load(_dir):
            watch_state["workflows_loaded"] = True
            return []

        class FakeDispatcher:
            def __init__(self, **kwargs):
                watch_state["dispatcher_kwargs"] = kwargs

            def run(self, *args, **kwargs):
                watch_state["dispatcher_ran"] = True

        with patch("ghdag.workflow.loader.load_workflows", side_effect=fake_load), \
             patch("ghdag.workflow.dispatcher.WorkflowDispatcher", FakeDispatcher), \
             patch("ghdag.github_client.create_github_clients", return_value=[]), \
             patch("ghdag.pipeline.state.PipelineState"), \
             patch("ghdag.pipeline.order.TemplateOrderBuilder"):
            from ghdag.cli import main
            main(["watch", str(workflows_dir)])

        assert watch_state["workflows_loaded"] is True
        assert watch_state["dispatcher_ran"] is True

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

    def test_watch_with_options(self, tmp_path):
        workflows_dir = tmp_path / "workflows"
        workflows_dir.mkdir()
        exec_jsonl_path = str(tmp_path / "out.md")
        pause_file_path = str(tmp_path / "pause.txt")

        pipeline_state_kwargs = {}
        watch_state = {"dispatcher_ran": False, "dispatcher_kwargs": {}}

        class FakePipelineState:
            def __init__(self, **kwargs):
                pipeline_state_kwargs.update(kwargs)

        class FakeDispatcher:
            def __init__(self, **kwargs):
                watch_state["dispatcher_kwargs"] = kwargs

            def run(self, *args, **kwargs):
                watch_state["dispatcher_ran"] = True

        with patch("ghdag.workflow.loader.load_workflows", return_value=[]), \
             patch("ghdag.workflow.dispatcher.WorkflowDispatcher", FakeDispatcher), \
             patch("ghdag.github_client.create_github_clients", return_value=[]), \
             patch("ghdag.pipeline.state.PipelineState", FakePipelineState), \
             patch("ghdag.pipeline.order.TemplateOrderBuilder"):
            from ghdag.cli import main
            main([
                "watch", str(workflows_dir),
                "--interval-sec", "60",
                "--exec-jsonl", exec_jsonl_path,
                "--pause-file", pause_file_path,
            ])

        assert pipeline_state_kwargs == {
            "state_dir": ".pipeline-state",
            "exec_jsonl_path": exec_jsonl_path,
        }
        assert watch_state["dispatcher_kwargs"]["pause_file"] == pause_file_path
        assert watch_state["dispatcher_ran"] is True


# ---------------------------------------------------------------------------
# AC6: ghdag watch error cases
# ---------------------------------------------------------------------------


class TestWatchError:
    def test_watch_no_args_exits_2(self):
        from ghdag.cli import main

        with pytest.raises(SystemExit) as exc:
            main(["watch"])
        assert exc.value.code == 2

    def test_watch_nonexistent_dir_exits_2(self, capsys):
        from ghdag.cli import main

        with pytest.raises(SystemExit) as exc:
            main(["watch", "nonexistent_workflows_dir/"])
        assert exc.value.code == 2
        assert capsys.readouterr().err.splitlines()[0].startswith(
            "error[WORKFLOWS_DIR_NOT_FOUND]:"
        )


class TestWatchOrderBuilderConstruction:
    def test_watch_builds_order_builders_via_build_order_builders(self, tmp_path):
        """watch delegates order builder construction to build_order_builders (#6092)."""
        from unittest.mock import MagicMock

        from ghdag.core.models.workflow import WorkflowConfig

        workflows_dir = tmp_path / "workflows"
        workflows_dir.mkdir()
        loaded = [WorkflowConfig(name="stub", triggers=[], handlers={}, polling_interval=0)]

        fake_mapping = {"stub-wf": MagicMock()}

        def fake_build(workflows):
            assert workflows is loaded
            return fake_mapping

        llm_kwargs: dict = {}

        class FakeLLMPipelineAPI:
            def __init__(self, **kwargs):
                llm_kwargs.update(kwargs)

        with patch("ghdag.workflow.loader.load_workflows", return_value=loaded), \
             patch("ghdag.pipeline.llm_pipeline.build_order_builders", side_effect=fake_build), \
             patch("ghdag.workflow.dispatcher.WorkflowDispatcher") as mock_disp_cls, \
             patch("ghdag.github_client.create_github_clients", return_value=[]), \
             patch("ghdag.pipeline.state.PipelineState"), \
             patch("ghdag.pipeline.llm_pipeline.LLMPipelineAPI", FakeLLMPipelineAPI), \
             patch("ghdag.pipeline.order.TemplateOrderBuilder"):
            mock_disp_cls.return_value.run = lambda *a, **k: None
            from ghdag.cli import main
            main(["watch", str(workflows_dir)])

        assert llm_kwargs["order_builders"] is fake_mapping


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

    def test_watch_github_mode_preserves_multi_repo_factory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Refs sumipan/nexus#6606 — default github mode keeps multi-client factory."""
        workflows_dir = tmp_path / "workflows"
        workflows_dir.mkdir()
        _clear_github_env(monkeypatch)
        monkeypatch.setenv("GITHUB_TOKEN", "token")
        monkeypatch.setenv("GITHUB_REPOSITORIES", "a/r1,b/r2")

        client_a, client_b = MagicMock(), MagicMock()
        captured_clients: list = []

        class FakeDispatcher:
            def __init__(self, **kwargs):
                captured_clients.append(kwargs.get("github_client"))

            def run(self, *args, **kwargs):
                pass

        with patch("ghdag.workflow.loader.load_workflows", return_value=[]), \
             patch(
                 "ghdag.github_client.create_github_clients",
                 return_value=[client_a, client_b],
             ) as mock_create, \
             patch("ghdag.workflow.dispatcher.WorkflowDispatcher", FakeDispatcher), \
             patch("ghdag.pipeline.state.PipelineState"), \
             patch("ghdag.pipeline.order.TemplateOrderBuilder"):
            from ghdag.cli import main
            main(["watch", str(workflows_dir)])

        mock_create.assert_called_once_with(rate_limit_max_wait_sec=0)
        assert captured_clients == [[client_a, client_b]]

    def test_watch_unset_forge_without_token_reports_github_auth(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
    ) -> None:
        """Refs sumipan/nexus#6606 — unset forge stays on GitHub auth, not local."""
        workflows_dir = tmp_path / "workflows"
        workflows_dir.mkdir()
        _clear_github_env(monkeypatch)

        from ghdag.cli import main
        with pytest.raises(SystemExit) as exc:
            main(["watch", str(workflows_dir), "--once"])
        assert exc.value.code == 4
        err = capsys.readouterr().err
        assert err.splitlines()[0].startswith("error[GITHUB_AUTH]:")
