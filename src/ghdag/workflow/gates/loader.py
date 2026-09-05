"""Load GateRule classes from importlib.metadata entry-points (group=ghdag.gates)."""

from __future__ import annotations

import sys
from importlib.metadata import entry_points
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ghdag.core.ports.gate import GateRule

_ep_cache: dict[str, type[GateRule]] | None = None


def load_entry_point_gates() -> dict[str, type[GateRule]]:
    """Scan ``ghdag.gates`` entry-points and return name → GateRule class.

    Load failures are fail-open: the gate name and exception go to stderr, and
    remaining entry-points continue to load. Results are cached after the first
    successful scan.
    """
    global _ep_cache
    if _ep_cache is not None:
        return _ep_cache

    loaded: dict[str, type[GateRule]] = {}
    try:
        eps = entry_points(group="ghdag.gates")
    except TypeError:
        # Python < 3.10 select() API fallback (kept for safety; requires-python>=3.10)
        eps = entry_points().select(group="ghdag.gates")  # type: ignore[attr-defined]

    for ep in eps:
        try:
            loaded[ep.name] = ep.load()
        except Exception as exc:  # noqa: BLE001 — fail-open per gate
            print(
                f"ghdag.gates: failed to load entry-point {ep.name!r}: {exc}",
                file=sys.stderr,
            )
    _ep_cache = loaded
    return loaded
