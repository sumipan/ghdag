"""Tests for ForgePort dispatcher-oriented methods on clients (Issue #7546)."""

from __future__ import annotations

from pathlib import Path

from ghdag.core.ports.forge import ForgePort
from ghdag.forge.local import LocalForge
from ghdag.github_client import GitHubClient


def test_github_client_satisfies_forge_port_dispatcher_api() -> None:
    client = GitHubClient(token="token", repo="owner/repo")
    assert isinstance(client, ForgePort)


def test_local_forge_satisfies_forge_port_dispatcher_api(tmp_path: Path) -> None:
    """Issue #6093 AC1: LocalForge implements dispatcher forge methods."""
    assert isinstance(LocalForge(tmp_path), ForgePort)
