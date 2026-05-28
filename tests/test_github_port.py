"""Tests for GitHubIssuePort structural subtyping."""

from ghdag.workflow.github import GitHubIssueClient, GitHubIssuePort


def test_github_issue_client_satisfies_github_issue_port():
    assert isinstance(GitHubIssueClient(), GitHubIssuePort)
