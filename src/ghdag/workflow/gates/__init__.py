from __future__ import annotations

from ghdag.core.ports.gate import GateRule, Violation
from ghdag.workflow.gates.loader import load_entry_point_gates

GATE_REGISTRY: dict[str, type[GateRule]] = {}


def get_gate(name: str) -> type[GateRule] | None:
    """Resolve a gate by name.

    Priority: ``GATE_REGISTRY`` (import-side-effect registration) then
    ``ghdag.gates`` entry-points. Returns ``None`` if neither has ``name``.
    """
    if name in GATE_REGISTRY:
        return GATE_REGISTRY[name]
    return load_entry_point_gates().get(name)


__all__ = ["Violation", "GateRule", "GATE_REGISTRY", "get_gate"]
