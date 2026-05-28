from __future__ import annotations

from ghdag.workflow.github import GitHubIssueClient, GitHubIssuePort, TokenGitHubClient


def test_github_issue_client_satisfies_github_issue_port() -> None:
    client = GitHubIssueClient()
    assert isinstance(client, GitHubIssuePort)


def test_token_github_client_satisfies_github_issue_port() -> None:
    client = TokenGitHubClient("token", "owner", "repo")
    assert isinstance(client, GitHubIssuePort)
