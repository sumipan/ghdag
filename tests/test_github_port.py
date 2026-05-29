"""Tests for GitHubIssuePort structural subtyping."""

from __future__ import annotations

from ghdag.workflow.github import (
    GitHubIssueClient,
    GitHubIssuePort,
    TokenGitHubClient,
)


def test_github_issue_client_alias_satisfies_github_issue_port():
    assert isinstance(GitHubIssueClient("token", "owner", "repo"), GitHubIssuePort)


def test_github_issue_client_alias_is_token_github_client():
    assert GitHubIssueClient is TokenGitHubClient


def test_token_github_client_satisfies_github_issue_port() -> None:
    client = TokenGitHubClient("token", "owner", "repo")
    assert isinstance(client, GitHubIssuePort)
