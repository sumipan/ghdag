"""Tests for ghdag.config.env — environment variable accessors (issue #3039)."""

from __future__ import annotations

from pathlib import Path

import pytest

from ghdag.config import env


def test_github_token_prefers_github_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "gh-token")
    monkeypatch.setenv("GH_TOKEN", "legacy")
    assert env.github_token() == "gh-token"


def test_github_token_falls_back_to_gh_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("GH_TOKEN", "legacy")
    assert env.github_token() == "legacy"


def test_github_token_none_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    assert env.github_token() is None


def test_engine_isolation_enabled_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GHDAG_ENGINE_ISOLATION", raising=False)
    assert env.engine_isolation_enabled() is False
    assert "engine_isolation_enabled" in env.__all__


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "YES", " 1 "])
def test_enable_git_truthy(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("ENABLE_GIT", value)
    assert env.enable_git() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "", "  ", "on"])
def test_enable_git_falsy(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("ENABLE_GIT", value)
    assert env.enable_git() is False


def test_enable_git_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENABLE_GIT", raising=False)
    assert env.enable_git() is False


def test_state_dir_set(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GHDAG_STATE_DIR", str(tmp_path / "state"))
    assert env.ghdag_state_dir() == str(tmp_path / "state")
    assert env.state_dir("jobs") == tmp_path / "state"


