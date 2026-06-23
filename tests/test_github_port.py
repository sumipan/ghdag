"""Tests for GitHubIssuePort structural subtyping."""

from __future__ import annotations

from ghdag.github_client import (
    GitHubClient,
    GitHubIssueClient,
    GitHubIssuePort,
)


def test_github_issue_client_alias_satisfies_github_issue_port():
    assert isinstance(GitHubIssueClient(token="token", repo="owner/repo"), GitHubIssuePort)


def test_github_issue_client_alias_is_github_client():
    assert GitHubIssueClient is GitHubClient


def test_github_client_satisfies_github_issue_port() -> None:
    client = GitHubClient(token="token", repo="owner/repo")
    assert isinstance(client, GitHubIssuePort)
