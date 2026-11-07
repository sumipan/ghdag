"""Tests for GitHubIssuePort structural subtyping."""

from __future__ import annotations

from pathlib import Path

from ghdag.forge.local import LocalForge
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


def test_local_forge_satisfies_github_issue_port(tmp_path: Path) -> None:
    """Issue #6093 AC1: LocalForge implements GitHubIssuePort."""
    assert isinstance(LocalForge(tmp_path), GitHubIssuePort)
