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


def test_github_repositories_raw(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_REPOSITORIES", "a/b, c/d")
    assert env.github_repositories_raw() == "a/b, c/d"
    monkeypatch.delenv("GITHUB_REPOSITORIES", raising=False)
    assert env.github_repositories_raw() == ""


def test_ghdag_audit_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GHDAG_AUDIT_PATH", "/tmp/audit.jsonl")
    assert env.ghdag_audit_path() == "/tmp/audit.jsonl"
    monkeypatch.delenv("GHDAG_AUDIT_PATH", raising=False)
    assert env.ghdag_audit_path() is None


def test_latency_span_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LATENCY_SPAN_PATH", "/tmp/latency_span.jsonl")
    assert env.latency_span_path() == "/tmp/latency_span.jsonl"
    monkeypatch.delenv("LATENCY_SPAN_PATH", raising=False)
    assert env.latency_span_path() is None


def test_ghdag_token_warn_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GHDAG_TOKEN_WARN_THRESHOLD", "12345")
    assert env.ghdag_token_warn_threshold() == "12345"
    monkeypatch.delenv("GHDAG_TOKEN_WARN_THRESHOLD", raising=False)
    assert env.ghdag_token_warn_threshold() is None


def test_ghdag_safe_default_permission(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GHDAG_SAFE_DEFAULT_PERMISSION", "json_only")
    assert env.ghdag_safe_default_permission() == "json_only"
    monkeypatch.delenv("GHDAG_SAFE_DEFAULT_PERMISSION", raising=False)
    assert env.ghdag_safe_default_permission() is None


def test_ghdag_llm_models(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GHDAG_LLM_MODELS", "/cfg/models.yml")
    assert env.ghdag_llm_models() == "/cfg/models.yml"
    monkeypatch.delenv("GHDAG_LLM_MODELS", raising=False)
    assert env.ghdag_llm_models() is None


def test_session_compaction_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    for value in ("1", "true", "YES", "on"):
        monkeypatch.setenv("GHDAG_SESSION_COMPACTION", value)
        assert env.session_compaction_enabled() is True
    for value in ("", "0", "false", "no"):
        monkeypatch.setenv("GHDAG_SESSION_COMPACTION", value)
        assert env.session_compaction_enabled() is False
    monkeypatch.delenv("GHDAG_SESSION_COMPACTION", raising=False)
    assert env.session_compaction_enabled() is False


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


def test_ghdag_vcs_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GHDAG_VCS_CONFIG", "/tmp/vcs.yaml")
    assert env.ghdag_vcs_config() == "/tmp/vcs.yaml"
    monkeypatch.setenv("GHDAG_VCS_CONFIG", "")
    assert env.ghdag_vcs_config() is None
    monkeypatch.delenv("GHDAG_VCS_CONFIG", raising=False)
    assert env.ghdag_vcs_config() is None


def test_state_dir_unset_or_empty_returns_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GHDAG_STATE_DIR", raising=False)
    assert env.ghdag_state_dir() is None
    assert env.state_dir("jobs") == Path("jobs")
    monkeypatch.setenv("GHDAG_STATE_DIR", "")
    assert env.state_dir(Path("/x/jobs")) == Path("/x/jobs")


def test_state_dir_set(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GHDAG_STATE_DIR", str(tmp_path / "state"))
    assert env.ghdag_state_dir() == str(tmp_path / "state")
    assert env.state_dir("jobs") == tmp_path / "state"
