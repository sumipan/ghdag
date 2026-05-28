from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from ghdag.workflow.github import (
    AuthError,
    GhCliGitHubClient,
    TokenGitHubClient,
    create_github_client,
)


def test_create_github_client_gh_always_returns_gh_client(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "token-from-env")
    client = create_github_client("gh")
    assert isinstance(client, GhCliGitHubClient)


def test_create_github_client_token_raises_without_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    with pytest.raises(AuthError):
        create_github_client("token")


def test_create_github_client_token_returns_token_client():
    client = create_github_client("token", token="xxx", owner="o", repo="r")
    assert isinstance(client, TokenGitHubClient)
    assert client._owner == "o"
    assert client._repo == "r"


def test_create_github_client_auto_uses_github_token(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "g-token")
    monkeypatch.setenv("GH_TOKEN", "gh-token")
    client = create_github_client("auto", owner="o", repo="r")
    assert isinstance(client, TokenGitHubClient)
    assert client._session.headers.get("Authorization") == "Bearer g-token"


def test_create_github_client_auto_uses_gh_token_when_github_token_missing(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("GH_TOKEN", "gh-token")
    client = create_github_client("auto", owner="o", repo="r")
    assert isinstance(client, TokenGitHubClient)
    assert client._session.headers.get("Authorization") == "Bearer gh-token"


def test_create_github_client_auto_falls_back_to_gh_client_without_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    client = create_github_client("auto")
    assert isinstance(client, GhCliGitHubClient)


def test_token_repo_resolution_prefers_explicit_owner_repo(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "env-owner/env-repo")
    with patch("subprocess.run") as mock_run:
        client = create_github_client("token", token="x", owner="arg-owner", repo="arg-repo")
    assert isinstance(client, TokenGitHubClient)
    assert client._owner == "arg-owner"
    assert client._repo == "arg-repo"
    mock_run.assert_not_called()


def test_token_repo_resolution_uses_github_repository(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "env-owner/env-repo")
    client = create_github_client("token", token="x")
    assert isinstance(client, TokenGitHubClient)
    assert client._owner == "env-owner"
    assert client._repo == "env-repo"


def test_token_repo_resolution_uses_gh_repo_view(monkeypatch):
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    mock_result = MagicMock()
    mock_result.stdout = "cli-owner/cli-repo\n"
    with patch("subprocess.run", return_value=mock_result) as mock_run:
        client = create_github_client("token", token="x")
    assert isinstance(client, TokenGitHubClient)
    assert client._owner == "cli-owner"
    assert client._repo == "cli-repo"
    mock_run.assert_called_once()


def test_token_repo_resolution_raises_when_unresolvable(monkeypatch):
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    with patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, ["gh"])):
        with pytest.raises(ValueError):
            create_github_client("token", token="x")
