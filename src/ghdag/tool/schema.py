"""ghdag.tool.schema — ToolDef dataclass definitions."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FallbackEntry:
    engine: str
    model: str


@dataclass
class ToolDef:
    name: str
    engine: str
    model: str
    fallback: list[FallbackEntry] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("ToolDef.name must not be empty")
        if not self.engine:
            raise ValueError("ToolDef.engine must not be empty")
        if not self.model:
            raise ValueError("ToolDef.model must not be empty")
