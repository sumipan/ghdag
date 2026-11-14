"""tests/llm/test_managed.py — route-based call() (Issue sumipan/nexus#6679)."""

from __future__ import annotations

import os
import stat
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ghdag.core.engine_spec import ENGINE_SPECS
from ghdag.core.models.metrics import FailureClass
from ghdag.llm.engines import TIMEOUT_RETURNCODE, call
from ghdag.quota import QuotaGate

def _quota_now() -> datetime:
    return datetime.now(timezone.utc)


def _cli_binary(engine: str) -> str:
    spec = ENGINE_SPECS.get(engine)
    return spec.cli if spec else engine


def _install_script(tmp_path: Path, engine: str, body: str) -> None:
    path = tmp_path / _cli_binary(engine)
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _prepend_bin_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    stamps = tmp_path / "stamps"
    stamps.mkdir()
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}")
    return stamps


def test_call_empty_route_raises_value_error() -> None:
    """Issue sumipan/nexus#6679 — AC1 empty route."""
    with pytest.raises(ValueError, match="route must not be empty"):
        call("p", route=[])


def test_call_skips_paused_first_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue sumipan/nexus#6679 — AC2 skip paused head, run second engine."""
    stamps = _prepend_bin_dir(monkeypatch, tmp_path)
    claude_stamp = stamps / "claude"
    codex_stamp = stamps / "codex"
    _install_script(
        tmp_path,
        "claude",
        f"""#!/bin/sh
touch "{claude_stamp}"
echo "session limit" >&2
exit 1
""",
    )
    _install_script(
        tmp_path,
        "codex",
        f"""#!/bin/sh
touch "{codex_stamp}"
echo ok
exit 0
""",
    )
    gate = QuotaGate(tmp_path / "quota.json")
    gate.report(
        engine="claude",
        status="paused",
        observed_at=_quota_now(),
        reason="test",
        kind="failure",
    )

    result = call(
        "p",
        route=[("claude", None), ("codex", None)],
        quota_gate=gate,
    )

    assert result.ok is True
    assert result.engine_used == "codex"
    assert result.attempts == 1
    assert not claude_stamp.exists()
    assert codex_stamp.exists()


def test_call_walks_route_past_two_quota_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue sumipan/nexus#6679 — AC3 two QUOTA_EXHAUSTED then success."""
    stamps = _prepend_bin_dir(monkeypatch, tmp_path)
    quota_body = """#!/bin/sh
echo "session limit" >&2
exit 1
"""
    _install_script(tmp_path, "claude", quota_body)
    _install_script(tmp_path, "codex", quota_body)
    _install_script(
        tmp_path,
        "cursor",
        f"""#!/bin/sh
touch "{stamps / 'cursor'}"
echo ok
exit 0
""",
    )

    result = call(
        "p",
        route=[("claude", None), ("codex", None), ("cursor", None)],
    )

    assert result.ok is True
    assert result.attempts == 3
    assert result.engine_used == "cursor"


def test_call_engine_environment_error_moves_to_next(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue sumipan/nexus#6679 — AC4 ENGINE_ENVIRONMENT_ERROR advances route."""
    stamps = _prepend_bin_dir(monkeypatch, tmp_path)
    _install_script(
        tmp_path,
        "claude",
        f"""#!/bin/sh
touch "{stamps / 'claude'}"
echo "claude: command not found" >&2
exit 127
""",
    )
    _install_script(
        tmp_path,
        "codex",
        f"""#!/bin/sh
touch "{stamps / 'codex'}"
echo ok
exit 0
""",
    )
    gate = QuotaGate(tmp_path / "quota.json")

    result = call(
        "p",
        route=[("claude", None), ("codex", None)],
        quota_gate=gate,
    )

    assert result.ok is True
    assert result.engine_used == "codex"
    assert gate.snapshot().engines.get("claude") is None


def test_call_timeout_stops_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue sumipan/nexus#6679 — AC5 TIMEOUT does not advance route."""
    stamps = _prepend_bin_dir(monkeypatch, tmp_path)
    codex_stamp = stamps / "codex"
    _install_script(
        tmp_path,
        "claude",
        """#!/bin/sh
sleep 30
exit 0
""",
    )
    _install_script(
        tmp_path,
        "codex",
        f"""#!/bin/sh
touch "{codex_stamp}"
echo ok
exit 0
""",
    )

    result = call(
        "p",
        route=[("claude", None), ("codex", None)],
        timeout=1,
    )

    assert result.failure_class is FailureClass.TIMEOUT
    assert result.attempts == 1
    assert result.returncode == TIMEOUT_RETURNCODE
    assert not codex_stamp.exists()


def test_call_all_paused_launches_nothing(tmp_path: Path) -> None:
    """Issue sumipan/nexus#6679 — AC6 all candidates paused."""
    gate = QuotaGate(tmp_path / "quota.json")
    observed = _quota_now()
    gate.report(
        engine="claude",
        status="paused",
        observed_at=observed,
        reason="test",
        kind="failure",
    )
    gate.report(
        engine="codex",
        status="paused",
        observed_at=observed,
        reason="test",
        kind="failure",
    )

    result = call(
        "p",
        route=[("claude", None), ("codex", None)],
        quota_gate=gate,
    )

    assert result.attempts == 0
    assert result.failure_class is FailureClass.QUOTA_EXHAUSTED
    assert result.engine_used == ""
    assert "all route candidates paused: claude, codex" in result.stderr


def test_call_resume_session_only_for_first_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue sumipan/nexus#6679 — AC7 resume only on route head launch."""
    stamps = _prepend_bin_dir(monkeypatch, tmp_path)
    claude_args = stamps / "claude-args"
    codex_args = stamps / "codex-args"
    _install_script(
        tmp_path,
        "claude",
        f"""#!/bin/sh
echo "$@" > "{claude_args}"
echo "session limit" >&2
exit 1
""",
    )
    _install_script(
        tmp_path,
        "codex",
        f"""#!/bin/sh
echo "$@" > "{codex_args}"
echo ok
exit 0
""",
    )

    call(
        "p",
        route=[("claude", None), ("codex", None)],
        resume_session_id="s1",
    )

    assert "s1" in claude_args.read_text(encoding="utf-8")
    assert codex_args.exists()
    assert "s1" not in codex_args.read_text(encoding="utf-8")
