"""GHDAG_FORGE=local end-to-end integration (nexus #3101).

Exercises github_cli via get_forge → LocalForge without GitHub network:
issue create → label transition → PR create → merge.
Also asserts typed routing for former raw ``api repos/...`` paths.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from ghdag.forge.local import LocalForge
from ghdag.github_cli import cli_main
from ghdag.workflow.state_machine import transition


def _git(cwd: Path, *args: str) -> None:
    subprocess.check_call(
        ["git", *args],
        cwd=cwd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


@pytest.fixture
def forge_root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "user.name", "Forge Integration")
    _git(root, "config", "user.email", "forge@example.com")
    (root / "README.md").write_text("init\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-m", "init")
    # Ensure default branch is main
    _git(root, "branch", "-M", "main")
    return root


@pytest.fixture
def local_env(
    forge_root: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    monkeypatch.setenv("GHDAG_FORGE", "local")
    monkeypatch.setenv("GHDAG_FORGE_ROOT", str(forge_root))
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    return forge_root


def test_github_cli_source_has_no_bare_github_client_outside_factory() -> None:
    """Acceptance: GitHubClient( in github_cli.py is only inside get_forge."""
    src = (
        Path(__file__).resolve().parents[1] / "src" / "ghdag" / "github_cli.py"
    ).read_text(encoding="utf-8")
    # Strip the local get_forge factory body (from its def to next top-level def)
    factory_match = re.search(
        r"^def get_forge\(.*?\n(?=def |\Z)",
        src,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert factory_match is not None, "get_forge factory must exist in github_cli.py"
    outside = src[: factory_match.start()] + src[factory_match.end() :]
    assert "GitHubClient(" not in outside


def test_local_forge_watch_label_pr_merge_roundtrip(
    local_env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """GHDAG_FORGE=local: issue → label transition → PR → merge (no GitHub)."""
    root = local_env

    # Block any accidental network to api.github.com
    with mock.patch("urllib.request.urlopen") as urlopen:
        urlopen.side_effect = AssertionError("GitHub network must not be used")

        rc = cli_main(
            ["issue", "create", "--title", "feat: local forge", "--body", "body"]
        )
        assert rc == 0
        issue_no = int(capsys.readouterr().out.strip())

        rc = cli_main(
            [
                "issue",
                "edit",
                str(issue_no),
                "--add-label",
                "phase:plan",
            ]
        )
        assert rc == 0

        # Label-driven transition (same path state_machine / watch uses via get_forge)
        transition(
            issue_no,
            "phase:develop",
            transitions={"phase:plan": ["phase:develop"], "phase:develop": []},
        )

        _git(root, "checkout", "-b", "feat/local-1")
        (root / "feature.txt").write_text("x\n", encoding="utf-8")
        _git(root, "add", "feature.txt")
        _git(root, "commit", "-m", "feature")
        _git(root, "checkout", "main")

        rc = cli_main(
            [
                "pr",
                "create",
                "--base",
                "main",
                "--head",
                "feat/local-1",
                "--title",
                "PR local",
                "--body",
                f"Refs #{issue_no}",
            ]
        )
        assert rc == 0
        pr_url = capsys.readouterr().out.strip()
        pr_no = int(pr_url.rstrip("/").split("/")[-1])

        rc = cli_main(["pr", "checks", str(pr_no)])
        assert rc == 0
        capsys.readouterr()  # discard checks TSV

        rc = cli_main(["pr", "merge", str(pr_no), "--merge"])
        assert rc == 0

        rc = cli_main(
            ["pr", "view", str(pr_no), "--json", "state", "--jq", ".state"]
        )
        assert rc == 0
        assert capsys.readouterr().out.strip() == "CLOSED"

        forge = LocalForge(root)
        issue = forge.issue_get(issue_no, fields=["labels"])
        names = [lbl["name"] for lbl in issue.get("labels", [])]
        assert "phase:develop" in names
        assert "phase:plan" not in names
        assert (root / "feature.txt").exists()
        assert urlopen.call_count == 0


def test_cli_api_milestones_timeline_pulls_repo_via_typed_methods(
    local_env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Raw api repos/... paths route to typed ForgePort methods under local forge."""
    root = local_env

    with mock.patch("urllib.request.urlopen") as urlopen:
        urlopen.side_effect = AssertionError("GitHub network must not be used")

        rc = cli_main(
            [
                "api",
                "repos/:owner/:repo/milestones",
                "-X",
                "POST",
                "-f",
                "title=M-local",
                "-f",
                "description=d",
            ]
        )
        assert rc == 0
        created = capsys.readouterr().out.strip()
        assert created  # milestone number or JSON

        rc = cli_main(["api", "repos/:owner/:repo/milestones"])
        assert rc == 0
        listed = capsys.readouterr().out
        assert "M-local" in listed

        rc = cli_main(
            ["issue", "create", "--title", "tl", "--body", "b", "--json", "number"]
        )
        assert rc == 0
        import json

        issue_no = json.loads(capsys.readouterr().out.strip())["number"]
        rc = cli_main(["issue", "comment", str(issue_no), "--body", "c1"])
        assert rc == 0

        rc = cli_main(
            ["api", f"repos/local/forge/issues/{issue_no}/timeline", "--paginate"]
        )
        assert rc == 0
        timeline_out = capsys.readouterr().out
        assert "c1" in timeline_out or "commented" in timeline_out or "[" in timeline_out

        _git(root, "checkout", "-b", "feat/api-pr")
        (root / "a.txt").write_text("a\n", encoding="utf-8")
        _git(root, "add", "a.txt")
        _git(root, "commit", "-m", "a")
        _git(root, "checkout", "main")
        rc = cli_main(
            [
                "pr",
                "create",
                "--base",
                "main",
                "--head",
                "feat/api-pr",
                "--title",
                "api-pr",
                "--body",
                "",
            ]
        )
        assert rc == 0
        pr_no = int(capsys.readouterr().out.strip().rstrip("/").split("/")[-1])

        rc = cli_main(
            ["api", f"repos/local/forge/pulls/{pr_no}", "--jq", ".number"]
        )
        assert rc == 0
        assert capsys.readouterr().out.strip() == str(pr_no)

        rc = cli_main(["pr", "merge", str(pr_no), "--merge"])
        assert rc == 0
        rc = cli_main(
            ["api", f"repos/local/forge/pulls/{pr_no}", "--jq", ".merged"]
        )
        assert rc == 0
        assert capsys.readouterr().out.strip() in {"true", "True"}

        rc = cli_main(["api", "repos/local/forge", "--jq", ".permissions.push"])
        assert rc == 0
        assert capsys.readouterr().out.strip() in {"true", "True"}

        rc = cli_main(["repo", "view", "local/forge"])
        assert rc == 0
        assert capsys.readouterr().out.strip() == "local/forge"

        assert urlopen.call_count == 0
