from __future__ import annotations

import pytest

from ghdag.workflow.github import (
    AuthError,
    TokenGitHubClient,
    create_github_client,
)


def test_create_github_client_raises_without_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    with pytest.raises(AuthError):
        create_github_client()


def test_create_github_client_returns_token_client():
    client = create_github_client(token="xxx", owner="o", repo="r")
    assert isinstance(client, TokenGitHubClient)
    assert client._owner == "o"
    assert client._repo == "r"


def test_create_github_client_uses_github_token(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "g-token")
    monkeypatch.setenv("GH_TOKEN", "gh-token")
    client = create_github_client(owner="o", repo="r")
    assert isinstance(client, TokenGitHubClient)
    assert client._session.headers.get("Authorization") == "Bearer g-token"


def test_create_github_client_uses_gh_token_when_github_token_missing(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("GH_TOKEN", "gh-token")
    client = create_github_client(owner="o", repo="r")
    assert isinstance(client, TokenGitHubClient)
    assert client._session.headers.get("Authorization") == "Bearer gh-token"


def test_repo_resolution_prefers_explicit_owner_repo(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "env-owner/env-repo")
    client = create_github_client(token="x", owner="arg-owner", repo="arg-repo")
    assert isinstance(client, TokenGitHubClient)
    assert client._owner == "arg-owner"
    assert client._repo == "arg-repo"


def test_repo_resolution_uses_github_repository(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "env-owner/env-repo")
    client = create_github_client(token="x")
    assert isinstance(client, TokenGitHubClient)
    assert client._owner == "env-owner"
    assert client._repo == "env-repo"


def test_repo_resolution_raises_when_github_repository_missing(monkeypatch):
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    with pytest.raises(EnvironmentError, match="GITHUB_REPOSITORY"):
        create_github_client(token="x")
