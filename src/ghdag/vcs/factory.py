"""ghdag.vcs.factory — ``ENABLE_GIT`` gate and ``GHDAG_VCS_CONFIG`` sink lookup."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from ghdag.config.env import enable_git, ghdag_audit_path, ghdag_vcs_config
from ghdag.vcs.sink import GitSink, NullSink

__all__ = ["get_sink", "git_enabled"]

logger = logging.getLogger(__name__)


def git_enabled() -> bool:
    """Return whether ``ENABLE_GIT`` allows sinks to write to git."""
    return enable_git()


def _load_config(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"GHDAG_VCS_CONFIG must be a mapping: {path}")
    return data


def get_sink(name: str) -> GitSink | NullSink:
    """Return the sink named ``name``.

    ``NullSink`` when ``ENABLE_GIT`` is off or ``GHDAG_VCS_CONFIG`` is unset;
    ``ValueError`` when the config has no ``sinks.<name>`` entry.
    """
    env_audit = ghdag_audit_path()
    if not git_enabled():
        return NullSink(name, reason="ENABLE_GIT unset", audit_path=Path(env_audit) if env_audit else None)

    config_path = ghdag_vcs_config()
    if config_path is None:
        logger.warning("ENABLE_GIT is set but GHDAG_VCS_CONFIG is unset; sink %r is disabled", name)
        return NullSink(
            name, reason="GHDAG_VCS_CONFIG unset", audit_path=Path(env_audit) if env_audit else None
        )

    data = _load_config(config_path)
    sinks = data.get("sinks") or {}
    if not isinstance(sinks, dict) or name not in sinks:
        raise ValueError(f"sink {name!r} is not defined in {config_path}")
    audit = data.get("audit_path") or env_audit
    try:
        return GitSink(**dict(sinks[name]), name=name, audit_path=Path(audit) if audit else None)
    except TypeError as exc:
        raise ValueError(f"invalid config for sink {name!r} in {config_path}: {exc}") from exc
