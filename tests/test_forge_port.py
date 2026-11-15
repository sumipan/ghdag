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


def test_core_ports_reexports_forge_port() -> None:
    from ghdag.core.ports import ForgePort as Exported

    assert Exported is ForgePort


def test_label_names_accepts_forge_port_type_hint() -> None:
    hints = get_type_hints(state_machine._label_names)
    assert hints["client"] is ForgePort


def test_only_forge_package_reads_ghdag_forge_env() -> None:
    """Refs sumipan/nexus#6804 — GHDAG_FORGE env reads live only under ghdag.forge."""
    src = Path(__file__).resolve().parents[1] / "src" / "ghdag"
    offenders: list[str] = []
    for py in sorted(src.rglob("*.py")):
        rel = py.relative_to(src)
        if rel.parts and rel.parts[0] == "forge":
            continue
        for lineno, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            if "GHDAG_FORGE" not in line:
                continue
            if "os.environ" in line or "os.getenv" in line:
                offenders.append(f"{rel}:{lineno}: {line.strip()}")
    assert offenders == []


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
