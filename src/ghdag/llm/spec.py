"""llm/spec.py — EngineSpec / render_exec_command re-export shim."""

from __future__ import annotations

from ghdag.core.command import _dedupe_extra_args, render_exec_command
from ghdag.core.engine_spec import (
    ENGINE_SPECS,
    DangerFlagPosition,
    EngineSpec,
    InputMode,
)

__all__ = [
    "InputMode",
    "DangerFlagPosition",
    "EngineSpec",
    "ENGINE_SPECS",
    "render_exec_command",
    "_dedupe_extra_args",
]
