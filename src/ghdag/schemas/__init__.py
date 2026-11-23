"""Bundled JSON Schemas for ghdag file formats and CLI JSON output."""

from __future__ import annotations

import json
from importlib import resources
from typing import Any, cast

from ghdag.core.exceptions import ConfigError

SCHEMA_NAMES: tuple[str, ...] = (
    "workflow-config",
    "exec-record",
    "done-marker",
    "issue-status",
    "context-hook-output",
    "reply-block",
)


def load_schema(name: str) -> dict[Any, Any]:
    """Load a bundled schema by logical name (without ``.schema.json`` suffix)."""
    if name not in SCHEMA_NAMES:
        raise ConfigError(
            f"Unknown schema: {name!r}",
            code="SCHEMA_UNKNOWN",
            hint=f"available: {', '.join(SCHEMA_NAMES)}",
        )
    path = resources.files("ghdag.schemas").joinpath(f"{name}.schema.json")
    return cast(dict[Any, Any], json.loads(path.read_text(encoding="utf-8")))


__all__: list[str] = []
