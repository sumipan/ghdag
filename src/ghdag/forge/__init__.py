"""Forge factory — GitHub / local backend selection via GHDAG_FORGE."""

from __future__ import annotations

import os
from pathlib import Path

from ghdag.core.exceptions import ConfigError, GitHubApiError
from ghdag.core.ports.forge import ForgePort
from ghdag.forge.local import LocalForge
from ghdag.github_client import GitHubClient

__all__ = [
    "ForgePort",
    "GitHubApiError",
    "GitHubClient",
    "LocalForge",
    "get_forge",
]


def _forge_kind() -> str:
    kind = (os.environ.get("GHDAG_FORGE") or "github").strip().lower()
    if kind == "local":
        return "local"
    if kind == "github":
        return "github"
    raise ConfigError(
        f"Unknown GHDAG_FORGE={kind!r}; expected 'github' or 'local'",
        code="FORGE_UNKNOWN",
    )


def _local_forge() -> LocalForge:
    root = os.environ.get("GHDAG_FORGE_ROOT")
    if not root:
        raise ConfigError(
            "GHDAG_FORGE=local requires GHDAG_FORGE_ROOT",
            code="FORGE_ROOT_MISSING",
        )
    return LocalForge(Path(root))


def get_forges(*, rate_limit_max_wait_sec: int | None = None) -> list[ForgePort]:
    if _forge_kind() == "github":
        import ghdag.github_client as github_client_module

        return github_client_module.create_github_clients(
            rate_limit_max_wait_sec=rate_limit_max_wait_sec,
        )
    return [_local_forge()]


def get_forge(repo: str | None = None) -> ForgePort:
    """Return a ForgePort implementation based on ``GHDAG_FORGE``.

    - unset / ``github`` (default): :class:`~ghdag.github_client.GitHubClient`
    - ``local``: ``LocalForge`` using ``GHDAG_FORGE_ROOT`` as data directory
    """
    if _forge_kind() == "local":
        return _local_forge()
    return GitHubClient(repo=repo)
