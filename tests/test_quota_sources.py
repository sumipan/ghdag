"""QuotaGate multi-source pause model (Issue #5692)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ghdag.quota import QuotaGate

JST = timezone(timedelta(hours=9))


def _dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, 26, hour, minute, tzinfo=JST)


def test_clear_ttl_maps_to_manual_and_rejects_kind_combo(tmp_path: Path) -> None:
    gate = QuotaGate(tmp_path / "quota-gate.json")
    gate.clear(engine="claude", observed_at=_dt(12), ttl_seconds=600)
    raw = gate.read_state()["engines"]["claude"]["sources"]
    assert raw[0]["kind"] == "manual"
    assert raw[0]["available"] is True
    with pytest.raises(ValueError):
        gate.clear(engine="claude", observed_at=_dt(12, 1), kind="budget", ttl_seconds=10)


def test_manual_override_makes_source_backed_drain_temporarily_available(
    tmp_path: Path,
) -> None:
    gate = QuotaGate(tmp_path / "quota-gate.json", pause_ttl_seconds=None)
    gate.drain(engine="claude", reason="switch", now=_dt(12))
    gate.report(
        engine="claude",
        status="available",
        observed_at=_dt(12, 1),
        resume_at=_dt(13),
        kind="manual",
    )

    decision = gate.admit(
        task_uuid="task-1",
        engine="claude",
        phase="launch",
        now=_dt(12, 30),
    )
    assert decision.allowed is True
    assert "claude" in gate.snapshot(now=_dt(12, 30)).draining_engines
    after_expiry = gate.admit(
        task_uuid="task-2",
        engine="claude",
        phase="launch",
        now=_dt(13, 30),
    )
    assert after_expiry.allowed is False


def test_report_and_clear_drain_keep_compatibility_view_in_sync(tmp_path: Path) -> None:
    gate = QuotaGate(tmp_path / "quota-gate.json", pause_ttl_seconds=None)
    gate.report(
        engine="claude",
        status="paused",
        observed_at=_dt(12),
        kind="drain",
        reason="maintenance",
    )
    assert "claude" in gate.read_state()["draining_engines"]

    gate.clear(engine="claude", observed_at=_dt(12, 1), kind="drain")

    assert "claude" not in gate.read_state()["draining_engines"]


def test_engine_row_without_sources_has_no_pause_source(tmp_path: Path) -> None:
    """Issue sumipan/nexus#7416 — rows without ``sources`` are not treated as paused."""
    state_path = tmp_path / "quota-gate.json"
    observed = _dt(12).astimezone(timezone.utc).isoformat()
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "engines": {
                    "claude": {
                        "status": "paused",
                        "observed_at": observed,
                        "resume_at": None,
                        "reason": "legacy pause",
                    }
                },
                "deferred_tasks": {},
                "draining_engines": {},
                "running_tasks": {},
            }
        ),
        encoding="utf-8",
    )
    gate = QuotaGate(state_path, pause_ttl_seconds=None)
    snap = gate.snapshot(now=_dt(12, 1))
    assert snap.engines["claude"].status == "available"
    assert snap.engines["claude"].sources == ()
