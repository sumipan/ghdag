"""Tests for ForgePort protocol and get_forge factory (nexus #3099)."""

from __future__ import annotations

from pathlib import Path
from typing import get_type_hints

import pytest

from ghdag.core.ports.forge import ForgePort
from ghdag.github_client import GitHubClient
from ghdag.workflow import state_machine


def test_forge_port_is_runtime_checkable() -> None:
    assert getattr(ForgePort, "_is_runtime_protocol", False) is True


def test_github_client_satisfies_forge_port() -> None:
    client = GitHubClient(token="token", repo="owner/repo")
    assert isinstance(client, ForgePort)


def test_github_client_and_local_forge_satisfy_merged_forge_port(tmp_path: Path) -> None:
    """nexus #7546 AC1: both forge implementations satisfy the merged ForgePort."""
    from ghdag.forge.local import LocalForge

    assert isinstance(GitHubClient(token="token", repo="owner/repo"), ForgePort)
    assert isinstance(LocalForge(tmp_path), ForgePort)


def test_factories_and_dispatcher_use_forge_port_type_hints() -> None:
    """nexus #7546 AC3: factory return types and dispatcher client hints are ForgePort."""
    from ghdag.github_client import create_github_client, create_github_clients
    from ghdag.workflow.dispatcher import WorkflowDispatcher

    assert get_type_hints(create_github_client)["return"] is ForgePort
    assert get_type_hints(create_github_clients)["return"] == list[ForgePort]
    hints = [
        get_type_hints(WorkflowDispatcher.__init__)["github_client"],
        get_type_hints(create_github_client)["return"],
        get_type_hints(create_github_clients)["return"],
    ]
    assert get_type_hints(WorkflowDispatcher.__init__)["github_client"] == (
        ForgePort | list[ForgePort]
    )
    assert all("GitHubIssuePort" not in repr(h) for h in hints)


def test_core_ports_reexports_forge_port() -> None:
    from ghdag.core.ports import ForgePort as Exported

    assert Exported is ForgePort


def test_label_names_accepts_forge_port_type_hint() -> None:
    hints = get_type_hints(state_machine._label_names)
    assert hints["client"] is ForgePort


_UNKNOWN_FORGE_MSG = (
    "Unknown GHDAG_FORGE='bogus'; expected 'github' or 'local'"
)


def _write_minimal_run_workflow(workflows_dir: Path) -> None:
    workflows_dir.mkdir(parents=True, exist_ok=True)
    tdir = workflows_dir / "templates"
    tdir.mkdir(exist_ok=True)
    (tdir / "t1.md").write_text("echo hello\n", encoding="utf-8")
    (workflows_dir / "sample.yml").write_text(
        """\
name: sample
triggers:
  - label: "pipeline:develop-ready"
    handler: run
handlers:
  run:
    steps:
      - id: step1
        template: t1
        engine: shell
""",
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    ("entrypoint", "argv"),
    [
        (
            "watch",
            lambda wd, ej: ["watch", str(wd), "--once", "--exec-jsonl", str(ej)],
        ),
        (
            "trigger",
            lambda wd, ej: [
                "trigger",
                "1",
                "--handler",
                "run",
                "--workflows-dir",
                str(wd),
                "--exec-jsonl",
                str(ej),
            ],
        ),
        (
            "github_cli",
            lambda _wd, _ej: ["issue", "view", "1"],
        ),
    ],
)
def test_unknown_forge_fails_with_same_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    entrypoint: str,
    argv,
) -> None:
    """Refs sumipan/nexus#6804 — unknown GHDAG_FORGE fails consistently."""
    monkeypatch.setenv("GHDAG_FORGE", "bogus")
    workflows_dir = tmp_path / "workflows"
    _write_minimal_run_workflow(workflows_dir)
    exec_jsonl = tmp_path / "exec.jsonl"
    exec_jsonl.write_text("", encoding="utf-8")

    if entrypoint == "github_cli":
        from ghdag.github_cli import cli_main

        rc = cli_main(argv(workflows_dir, exec_jsonl))
        assert rc != 0
    else:
        from ghdag.cli import main

        with pytest.raises(SystemExit) as exc:
            main(argv(workflows_dir, exec_jsonl))
        assert exc.value.code != 0

    assert _UNKNOWN_FORGE_MSG in capsys.readouterr().err
