"""Tests for ghdag.config.env — environment variable accessors (issue #3039)."""

from __future__ import annotations

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
