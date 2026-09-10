"""Forge factory — GitHub / local backend selection via GHDAG_FORGE."""

from __future__ import annotations

import importlib
import os
from pathlib import Path

from ghdag.core.ports.forge import ForgePort
from ghdag.github_client import GitHubClient


def get_forge(repo: str | None = None) -> ForgePort:
    """Return a ForgePort implementation based on ``GHDAG_FORGE``.

    - unset / ``github`` (default): :class:`~ghdag.github_client.GitHubClient`
    - ``local``: ``LocalForge`` using ``GHDAG_FORGE_ROOT`` as data directory
      (loaded via importlib so mypy does not require the module until it exists)
    """
    kind = (os.environ.get("GHDAG_FORGE") or "github").strip().lower()
    if kind == "local":
        root = os.environ.get("GHDAG_FORGE_ROOT")
        if not root:
            raise ValueError("GHDAG_FORGE=local requires GHDAG_FORGE_ROOT")
        try:
            local_mod = importlib.import_module("ghdag.forge.local")
        except ModuleNotFoundError as exc:
            raise NotImplementedError(
                "GHDAG_FORGE=local requires ghdag.forge.local "
                "(LocalForge not yet available)"
            ) from exc
        local_forge_cls = getattr(local_mod, "LocalForge")
        forge: ForgePort = local_forge_cls(Path(root))
        return forge
    if kind != "github":
        raise ValueError(
            f"Unknown GHDAG_FORGE={kind!r}; expected 'github' or 'local'"
        )
    return GitHubClient(repo=repo)
