"""Tests for the ENABLE_GIT gate and get_sink lookup order (nexus Issue #3831, AC-1)."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import pytest
import yaml

from ghdag.vcs import GitSink, LocalGitSink, NullSink, get_sink, git_enabled


def _count(repo: Path) -> int:
    out = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout
    return int(out.strip())


@pytest.fixture
def configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Local repo + GHDAG_VCS_CONFIG pointing a ``notes`` sink at it. Returns the work tree."""
    LocalGitSink.create(tmp_path, owner="host", allow_prefixes=["diary/"])
    work = tmp_path / "work"
    config = tmp_path / "vcs.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "audit_path": str(tmp_path / "audit.jsonl"),
                "sinks": {
                    "notes": {
                        "repo_root": str(work),
                        "branch": "main",
                        "owner": "host",
                        "allow_prefixes": ["diary/"],
                        "push": "immediate",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("GHDAG_VCS_CONFIG", str(config))
    monkeypatch.delenv("GHDAG_AUDIT_PATH", raising=False)
    (work / "diary").mkdir()
    (work / "diary" / "2026-09-25.md").write_text("entry\n", encoding="utf-8")
    return work


def test_enable_git_unset_returns_null_sink(configured: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENABLE_GIT", raising=False)
    before = _count(configured)

    result = get_sink("notes").commit(["diary/2026-09-25.md"], "host(diary): 2026-09-25")

    assert result.skipped is True
    assert result.reason == "ENABLE_GIT unset"
    assert _count(configured) == before


def test_enable_git_on_commits(configured: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENABLE_GIT", "1")
    before = _count(configured)

    sink = get_sink("notes")
    result = sink.commit(["diary/2026-09-25.md"], "host(diary): 2026-09-25")

    assert isinstance(sink, GitSink)
    assert result.committed is True and result.pushed is True
    assert _count(configured) == before + 1
    assert sink.audit_path == configured.parent / "audit.jsonl"


@pytest.mark.parametrize("value", ["1", "true", "YES", " 1 ", "True"])
def test_git_enabled_truthy(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("ENABLE_GIT", value)
    assert git_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "", "on"])
def test_git_enabled_falsy(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("ENABLE_GIT", value)
    assert git_enabled() is False


def test_git_enabled_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENABLE_GIT", raising=False)
    assert git_enabled() is False


def test_missing_config_warns_once(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setenv("ENABLE_GIT", "1")
    monkeypatch.delenv("GHDAG_VCS_CONFIG", raising=False)
    with caplog.at_level(logging.WARNING, logger="ghdag.vcs.factory"):
        sink = get_sink("notes")
    assert isinstance(sink, NullSink)
    assert sink.reason == "GHDAG_VCS_CONFIG unset"
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1


def test_unknown_sink_name_raises(configured: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENABLE_GIT", "1")
    with pytest.raises(ValueError):
        get_sink("missing")


def test_null_sink_audit_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENABLE_GIT", raising=False)
    monkeypatch.setenv("GHDAG_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    get_sink("notes").commit(["diary/a.md"], "host(diary): a")
    assert '"vcs_skipped"' in (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
