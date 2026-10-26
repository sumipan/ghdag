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


def test_budget_and_failure_sources_coexist(tmp_path: Path) -> None:
    gate = QuotaGate(tmp_path / "quota-gate.json", pause_ttl_seconds=None)
    gate.report(
        engine="claude",
        status="paused",
        observed_at=_dt(12),
        resume_at=_dt(14),
        reason="budget cap",
        kind="budget",
    )
    gate.report(
        engine="claude",
        status="paused",
        observed_at=_dt(12, 5),
        resume_at=_dt(13),
        reason="auth",
        kind="failure",
    )
    snap = gate.snapshot(now=_dt(12, 10))
    eng = snap.engines["claude"]
    assert eng.status == "paused"
    assert len(eng.sources) == 2
    assert eng.resume_at == _dt(14).astimezone(timezone.utc)


def test_indefinite_budget_overrides_finite_failure_resume_at(tmp_path: Path) -> None:
    gate = QuotaGate(tmp_path / "quota-gate.json", pause_ttl_seconds=None)
    gate.report(
        engine="claude",
        status="paused",
        observed_at=_dt(12),
        reason="budget",
        kind="budget",
    )
    gate.report(
        engine="claude",
        status="paused",
        observed_at=_dt(12, 1),
        resume_at=_dt(13),
        reason="failure",
        kind="failure",
    )
    snap = gate.snapshot(now=_dt(12, 5))
    assert snap.engines["claude"].resume_at is None
    snap_after_failure = gate.snapshot(now=_dt(13, 30))
    assert snap_after_failure.engines["claude"].status == "paused"


def test_manual_override_then_expiry(tmp_path: Path) -> None:
    gate = QuotaGate(tmp_path / "quota-gate.json", pause_ttl_seconds=None)
    gate.report(engine="claude", status="paused", observed_at=_dt(12), kind="failure")
    gate.report(
        engine="claude",
        status="available",
        observed_at=_dt(12, 1),
        resume_at=_dt(13),
        kind="manual",
        reason="override",
    )
    assert gate.snapshot(now=_dt(12, 30)).engines["claude"].status == "available"
    assert gate.snapshot(now=_dt(13, 30)).engines["claude"].status == "paused"


def test_pause_reported_during_manual_override_takes_effect_after_expiry(
    tmp_path: Path,
) -> None:
    gate = QuotaGate(tmp_path / "quota-gate.json", pause_ttl_seconds=None)
    gate.report(
        engine="claude",
        status="available",
        observed_at=_dt(12),
        resume_at=_dt(13),
        kind="manual",
    )
    result = gate.report(
        engine="claude",
        status="paused",
        observed_at=_dt(12, 1),
        kind="failure",
        reason="auth",
    )
    assert result.applied is False
    assert gate.snapshot(now=_dt(12, 30)).engines["claude"].status == "available"
    after_expiry = gate.snapshot(now=_dt(13, 30)).engines["claude"]
    assert after_expiry.status == "paused"
    assert {source.kind for source in after_expiry.sources} == {"failure"}


def test_clear_kind_budget_leaves_failure(tmp_path: Path) -> None:
    gate = QuotaGate(tmp_path / "quota-gate.json", pause_ttl_seconds=None)
    gate.report(engine="claude", status="paused", observed_at=_dt(12), kind="budget")
    gate.report(engine="claude", status="paused", observed_at=_dt(12, 1), kind="failure")
    gate.report(engine="claude", status="available", observed_at=_dt(12, 2), kind="budget")
    kinds = {s.kind for s in gate.snapshot(now=_dt(12, 3)).engines["claude"].sources}
    assert kinds == {"failure"}
    gate.clear(engine="claude", observed_at=_dt(12, 4), kind="budget")
    assert gate.snapshot(now=_dt(12, 5)).engines["claude"].status == "paused"
    gate.clear(engine="claude", observed_at=_dt(12, 6))
    assert "claude" not in gate.snapshot(now=_dt(12, 7)).engines


def test_clear_ttl_maps_to_manual_and_rejects_kind_combo(tmp_path: Path) -> None:
    gate = QuotaGate(tmp_path / "quota-gate.json")
    gate.clear(engine="claude", observed_at=_dt(12), ttl_seconds=600)
    raw = gate.read_state()["engines"]["claude"]["sources"]
    assert raw[0]["kind"] == "manual"
    assert raw[0]["available"] is True
    with pytest.raises(ValueError):
        gate.clear(engine="claude", observed_at=_dt(12, 1), kind="budget", ttl_seconds=10)


def test_clear_manual_removes_paused_manual_source(tmp_path: Path) -> None:
    gate = QuotaGate(tmp_path / "quota-gate.json", pause_ttl_seconds=None)
    gate.report(
        engine="claude",
        status="paused",
        observed_at=_dt(12),
        kind="manual",
        reason="manual maintenance",
    )

    gate.clear(engine="claude", observed_at=_dt(12, 1), kind="manual")

    assert "claude" not in gate.snapshot(now=_dt(12, 2)).engines


def test_stale_per_kind_not_cross_kind(tmp_path: Path) -> None:
    gate = QuotaGate(tmp_path / "quota-gate.json", pause_ttl_seconds=None)
    gate.report(
        engine="claude",
        status="paused",
        observed_at=_dt(12, 10),
        kind="failure",
        reason="failure first",
    )
    gate.report(
        engine="claude",
        status="paused",
        observed_at=_dt(12, 15),
        kind="budget",
        reason="new budget",
    )
    stale = gate.report(
        engine="claude",
        status="paused",
        observed_at=_dt(12, 8),
        kind="failure",
        reason="old failure",
    )
    assert stale.stale_ignored is True
    applied = gate.report(
        engine="claude",
        status="paused",
        observed_at=_dt(12, 11),
        kind="failure",
        reason="fresh failure",
    )
    assert applied.stale_ignored is False
    assert applied.applied is True


def test_clear_stale_check_is_scoped_to_requested_kind(tmp_path: Path) -> None:
    gate = QuotaGate(tmp_path / "quota-gate.json", pause_ttl_seconds=None)
    gate.report(engine="claude", status="paused", observed_at=_dt(12), kind="budget")
    gate.report(engine="claude", status="paused", observed_at=_dt(12, 10), kind="failure")

    result = gate.clear(engine="claude", observed_at=_dt(12, 1), kind="budget")

    assert result.applied is True
    sources = gate.snapshot(now=_dt(12, 11)).engines["claude"].sources
    assert {source.kind for source in sources} == {"failure"}


def test_drain_source_and_resume(tmp_path: Path) -> None:
    gate = QuotaGate(tmp_path / "quota-gate.json", pause_ttl_seconds=None)
    gate.report(engine="claude", status="paused", observed_at=_dt(12), kind="failure")
    gate.drain(engine="claude", reason="switch", now=_dt(12, 1))
    snap = gate.snapshot(now=_dt(12, 2))
    assert "claude" in snap.draining_engines
    gate.resume(engine="claude", now=_dt(12, 3))
    snap2 = gate.snapshot(now=_dt(12, 4))
    assert "claude" not in snap2.draining_engines
    assert snap2.engines["claude"].status == "paused"


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


def test_legacy_paused_payload_migrates_on_write(tmp_path: Path) -> None:
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
    assert len(snap.engines["claude"].sources) == 1
    assert snap.engines["claude"].sources[0].set_by == "legacy"
    gate.report(engine="claude", status="available", observed_at=_dt(12, 2), kind="failure")
    written = gate.read_state()["engines"]["claude"]
    assert written["status"] == "available"
    assert "sources" in written
