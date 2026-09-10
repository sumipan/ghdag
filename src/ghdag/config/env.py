"""Centralized environment variable accessors for ghdag.

All ``os.environ`` reads for ghdag-owned and GitHub auth variables live here.
Callers receive values via these functions (or inject overrides as arguments).
"""

from __future__ import annotations

import os

__all__ = [
    "github_token",
    "github_repositories_raw",
    "ghdag_audit_path",
    "ghdag_token_warn_threshold",
    "ghdag_safe_default_permission",
    "ghdag_llm_models",
    "session_compaction_enabled",
]


def github_token() -> str | None:
    """Return ``GITHUB_TOKEN``, falling back to ``GH_TOKEN``."""
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")


def github_repositories_raw() -> str:
    """Return raw ``GITHUB_REPOSITORIES`` (comma-separated ``owner/repo`` list)."""
    return os.environ.get("GITHUB_REPOSITORIES", "")


def ghdag_audit_path() -> str | None:
    """Return ``GHDAG_AUDIT_PATH`` if set."""
    return os.environ.get("GHDAG_AUDIT_PATH")


def ghdag_token_warn_threshold() -> str | None:
    """Return raw ``GHDAG_TOKEN_WARN_THRESHOLD`` if set."""
    return os.environ.get("GHDAG_TOKEN_WARN_THRESHOLD")


def ghdag_safe_default_permission() -> str | None:
    """Return ``GHDAG_SAFE_DEFAULT_PERMISSION`` if set."""
    return os.environ.get("GHDAG_SAFE_DEFAULT_PERMISSION")


def ghdag_llm_models() -> str | None:
    """Return ``GHDAG_LLM_MODELS`` path if set."""
    return os.environ.get("GHDAG_LLM_MODELS")


def session_compaction_enabled() -> bool:
    """Return whether ``GHDAG_SESSION_COMPACTION`` opts in (``1``/``true``/``yes``/``on``)."""
    return os.environ.get("GHDAG_SESSION_COMPACTION", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
