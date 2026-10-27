"""Migration of legacy quota state keys (deferred_tasks)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ghdag.quota import QuotaGate

UTC = timezone.utc


def _dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, 22, hour, minute, tzinfo=UTC)


def test_legacy_deferred_tasks_key_ignored_on_read_and_dropped_on_write(tmp_path: Path) -> None:
    state_path = tmp_path / "quota-gate.json"
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "engines": {
                    "claude": {
                        "status": "available",
                        "observed_at": _dt(11).isoformat(),
                    }
                },
                "deferred_tasks": {
                    "legacy-task": {
                        "engine": "claude",
                        "phase": "launch",
                        "deferred_at": _dt(11).isoformat(),
                    }
                },
                "draining_engines": {},
                "running_tasks": {},
            }
        ),
        encoding="utf-8",
    )

    gate = QuotaGate(state_path)
    snap = gate.snapshot()
    assert snap.engines["claude"].status == "available"
    gate.report(engine="codex", status="paused", observed_at=_dt(12))

    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert "deferred_tasks" not in payload
    assert "codex" in payload["engines"]
