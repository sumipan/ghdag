"""Temporarily block launches for broken engines."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class EngineQuarantine:
    _quarantined: dict[str, float] = field(default_factory=dict)

    def has_entry(self, engine: str) -> bool:
        return engine in self._quarantined

    def enter(self, engine: str, cooldown: int = 300) -> None:
        self._quarantined[engine] = time.monotonic() + max(cooldown, 0)

    def is_quarantined(self, engine: str) -> bool:
        expiry = self._quarantined.get(engine)
        if expiry is None:
            return False
        if time.monotonic() >= expiry:
            del self._quarantined[engine]
            return False
        return True
