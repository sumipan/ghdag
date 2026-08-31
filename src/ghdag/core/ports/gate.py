"""GateRule Protocol and Violation dataclass."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class Violation:
    rule_id: str
    severity: str
    message: str
    location: str | None
    auto_fixable: bool
    fix_hint: str | None


class GateRule(Protocol):
    def check(self, body: str, labels: list[str]) -> list[Violation]: ...
