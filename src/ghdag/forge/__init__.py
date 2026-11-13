"""Forge factory — GitHub / local backend selection via GHDAG_FORGE."""

from __future__ import annotations

import os
from pathlib import Path

from ghdag.core.exceptions import GitHubApiError
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


def get_forge(repo: str | None = None) -> ForgePort:
    """Return a ForgePort implementation based on ``GHDAG_FORGE``.

    - unset / ``github`` (default): :class:`~ghdag.github_client.GitHubClient`
    - ``local``: ``LocalForge`` using ``GHDAG_FORGE_ROOT`` as data directory
    """
    kind = (os.environ.get("GHDAG_FORGE") or "github").strip().lower()
    if kind == "local":
        root = os.environ.get("GHDAG_FORGE_ROOT")
        if not root:
            raise ValueError("GHDAG_FORGE=local requires GHDAG_FORGE_ROOT")
        return LocalForge(Path(root))
    if kind != "github":
        raise ValueError(f"Unknown GHDAG_FORGE={kind!r}; expected 'github' or 'local'")
    return GitHubClient(repo=repo)
