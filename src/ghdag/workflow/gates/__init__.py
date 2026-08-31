from __future__ import annotations

from ghdag.core.ports.gate import GateRule, Violation

GATE_REGISTRY: dict[str, type[GateRule]] = {}

__all__ = ["Violation", "GateRule", "GATE_REGISTRY"]
