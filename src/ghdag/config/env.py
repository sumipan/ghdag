"""Centralized environment variable accessors for ghdag.

All ``os.environ`` reads for ghdag-owned and GitHub auth variables live here.
Callers receive values via these functions (or inject overrides as arguments).
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = [
    "github_token",
    "github_repositories_raw",
    "ghdag_audit_path",
    "ghdag_token_warn_threshold",
    "ghdag_safe_default_permission",
    "ghdag_llm_models",
    "latency_span_path",
    "session_compaction_enabled",
    "enable_git",
    "ghdag_vcs_config",
    "ghdag_state_dir",
    "state_dir",
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


def latency_span_path() -> str | None:
    """Return ``LATENCY_SPAN_PATH`` if set (nexus-compatible span JSONL override)."""
    return os.environ.get("LATENCY_SPAN_PATH")


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


def enable_git() -> bool:
    """Return whether ``ENABLE_GIT`` opts in (``1``/``true``/``yes``, case-insensitive)."""
    return os.environ.get("ENABLE_GIT", "").strip().lower() in {"1", "true", "yes"}


def ghdag_vcs_config() -> str | None:
    """Return ``GHDAG_VCS_CONFIG`` (path to the VCS sink YAML) if set and non-empty."""
    return os.environ.get("GHDAG_VCS_CONFIG") or None


def ghdag_state_dir() -> str | None:
    """Return raw ``GHDAG_STATE_DIR`` if set."""
    return os.environ.get("GHDAG_STATE_DIR")


def state_dir(default: str | Path) -> Path:
    """Resolve the runtime state directory.

    Returns ``Path(GHDAG_STATE_DIR).expanduser()`` when the variable is non-empty,
    otherwise ``Path(default)``. Callers pass their historical default so behavior
    is unchanged when the variable is unset.
    """
    raw = (ghdag_state_dir() or "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path(default)
