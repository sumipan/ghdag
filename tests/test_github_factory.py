from __future__ import annotations

import pytest

from ghdag.workflow.github import (
    AuthError,
    TokenGitHubClient,
    create_github_client,
    create_github_clients,
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
    monkeypatch.setenv("GITHUB_REPOSITORIES", "env-owner/env-repo")
    client = create_github_client(token="x", owner="arg-owner", repo="arg-repo")
    assert isinstance(client, TokenGitHubClient)
    assert client._owner == "arg-owner"
    assert client._repo == "arg-repo"


def test_repo_resolution_uses_first_of_github_repositories(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORIES", "first-owner/first-repo,second-owner/second-repo")
    client = create_github_client(token="x")
    assert isinstance(client, TokenGitHubClient)
    assert client._owner == "first-owner"
    assert client._repo == "first-repo"


def test_repo_resolution_raises_when_github_repositories_missing(monkeypatch):
    monkeypatch.delenv("GITHUB_REPOSITORIES", raising=False)
    with pytest.raises(EnvironmentError, match="GITHUB_REPOSITORIES"):
        create_github_client(token="x")


# ---------------------------------------------------------------------------
# create_github_clients — 複数リポジトリ
# ---------------------------------------------------------------------------


def test_create_github_clients_returns_one_per_repo(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORIES", "o1/r1,o2/r2")
    clients = create_github_clients(token="t")
    assert len(clients) == 2
    assert all(isinstance(c, TokenGitHubClient) for c in clients)
    assert (clients[0]._owner, clients[0]._repo) == ("o1", "r1")
    assert (clients[1]._owner, clients[1]._repo) == ("o2", "r2")
    # 全クライアントが同一トークンを共有する
    for c in clients:
        assert c._session.headers.get("Authorization") == "Bearer t"


def test_create_github_clients_ignores_blank_and_malformed(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORIES", " o1/r1 , , no-slash , o2/r2 ")
    clients = create_github_clients(token="t")
    assert [(c._owner, c._repo) for c in clients] == [("o1", "r1"), ("o2", "r2")]


def test_create_github_clients_uses_env_token(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "g-token")
    monkeypatch.setenv("GITHUB_REPOSITORIES", "o/r")
    clients = create_github_clients()
    assert len(clients) == 1
    assert clients[0]._session.headers.get("Authorization") == "Bearer g-token"


def test_create_github_clients_raises_without_repositories(monkeypatch):
    monkeypatch.delenv("GITHUB_REPOSITORIES", raising=False)
    with pytest.raises(EnvironmentError, match="GITHUB_REPOSITORIES"):
        create_github_clients(token="t")


def test_create_github_clients_raises_without_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_REPOSITORIES", "o/r")
    with pytest.raises(AuthError):
        create_github_clients()
