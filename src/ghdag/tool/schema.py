"""ghdag.tool.schema — ToolDef dataclass definitions."""

from __future__ import annotations

from dataclasses import dataclass, field

TOOL_EXIT_CODES: frozenset[str] = frozenset({
    "success",
    "failure",
    "retry",
    "skip",
})

_DEFAULT_EXIT_CODES: list[str] = ["success", "failure"]


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
    exit_codes: list[str] = field(default_factory=lambda: list(_DEFAULT_EXIT_CODES))

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("ToolDef.name must not be empty")
        if not self.engine:
            raise ValueError("ToolDef.engine must not be empty")
        if not self.model:
            raise ValueError("ToolDef.model must not be empty")
        if not self.exit_codes:
            raise ValueError("ToolDef.exit_codes must not be empty")
        invalid = [code for code in self.exit_codes if code not in TOOL_EXIT_CODES]
        if invalid:
            raise ValueError(
                f"ToolDef has invalid exit_code(s): {invalid!r}; "
                f"must be subset of {sorted(TOOL_EXIT_CODES)}"
            )
