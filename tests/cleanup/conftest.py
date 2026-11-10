"""Shared helpers for ghdag.cleanup tests."""

from __future__ import annotations

import json
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest

UUID_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
UUID_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
UUID_C = "cccccccc-cccc-cccc-cccc-cccccccccccc"
UUID_D = "dddddddd-dddd-dddd-dddd-dddddddddddd"
TS = "20260101120000"

FIXED_NOW = datetime(2026, 1, 8, 12, 0, tzinfo=timezone.utc)
FIXED_NOW_TS = FIXED_NOW.timestamp()


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):  # noqa: ANN001
        if tz is None:
            return FIXED_NOW
        return FIXED_NOW.astimezone(tz)


@pytest.fixture(autouse=True)
def frozen_cleanup_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    import ghdag.cleanup.orchestrator as orch

    monkeypatch.setattr(orch, "datetime", _FrozenDatetime)


def _make_queue_files(
    queue_dir: Path,
    uuid: str,
    ts: str = TS,
    tool: str = "claude",
    make_order: bool = True,
    make_result: bool = True,
) -> tuple[Path | None, Path | None]:
    order = result = None
    if make_order:
        order = queue_dir / f"{ts}-{tool}-order-{uuid}.md"
        order.write_text(f"order content for {uuid}")
    if make_result:
        result = queue_dir / f"{ts}-{tool}-result-{uuid}.md"
        result.write_text(f"result content for {uuid}")
    return order, result


def _set_mtime(path: Path, days_ago: float) -> None:
    import os

    t = FIXED_NOW_TS - days_ago * 86400
    os.utime(path, (t, t))


def _make_exec_jsonl(exec_jsonl: Path, entries: list[str]) -> None:
    lines = [json.dumps({"uuid": uuid, "command": "cat queue/order.md | claude"}) + "\n" for uuid in entries]
    exec_jsonl.write_text("".join(lines), encoding="utf-8")


def _make_done_flag(done_dir: Path, uuid: str) -> None:
    done_dir.mkdir(parents=True, exist_ok=True)
    (done_dir / uuid).touch()


def _setup_dirs(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    queue_dir = tmp_path / "jobs"
    archive_dir = tmp_path / "jobs" / "archive"
    done_dir = tmp_path / "jobs" / "done"
    exec_md = queue_dir / "exec.jsonl"
    queue_dir.mkdir()
    archive_dir.mkdir(parents=True)
    done_dir.mkdir(parents=True)
    return queue_dir, archive_dir, done_dir, exec_md


_helpers = types.ModuleType("ghdag_cleanup_conftest_helpers")
sys.modules["ghdag_cleanup_conftest_helpers"] = _helpers
for _name in (
    "UUID_A",
    "UUID_B",
    "UUID_C",
    "UUID_D",
    "TS",
    "FIXED_NOW",
    "FIXED_NOW_TS",
    "_make_queue_files",
    "_set_mtime",
    "_make_exec_jsonl",
    "_make_done_flag",
    "_setup_dirs",
):
    setattr(_helpers, _name, globals()[_name])
