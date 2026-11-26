"""Tests for ghdag.llm.quota_probe — quota usage probe (Issue sumipan/ghdag#8087)."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from ghdag.llm.engines import EngineModelError
from ghdag.llm.quota_probe import (
    QuotaProbeUnsupportedError,
    probe_quota_windows,
)


def _rate_limit_line(
    *,
    five_util: float = 0.43,
    seven_util: float = 0.76,
    five_reset: int = 1794667200,
    seven_reset: int = 1794682800,
) -> str:
    return json.dumps(
        {
            "type": "rate_limit_event",
            "rate_limit_info": {
                "unifiedWindows": {
                    "five_hour": {"utilization": five_util, "resetsAt": five_reset},
                    "seven_day": {"utilization": seven_util, "resetsAt": seven_reset},
                },
            },
        }
    )


def _install_claude_script(tmp_path: Path, body: str) -> Path:
    script = tmp_path / "claude"
    script.write_text(body, encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return script


def _prepend_path(monkeypatch: pytest.MonkeyPatch, directory: Path) -> None:
    monkeypatch.setenv(
        "PATH",
        f"{directory}{os.pathsep}{os.environ.get('PATH', '')}",
    )


def test_probe_extracts_windows_and_builds_isolated_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refs sumipan/ghdag#8087 AC1."""
    argv_log = tmp_path / "argv.log"
    _install_claude_script(
        tmp_path,
        f"""#!/bin/sh
printf '%s\\n' "$@" > "{argv_log}"
cat <<'EOF'
{_rate_limit_line()}
EOF
exit 0
""",
    )
    _prepend_path(monkeypatch, tmp_path)

    result = probe_quota_windows("claude")

    assert len(result.windows) == 2
    by_minutes = {w.window_minutes: w for w in result.windows}
    assert by_minutes[300].used_percent == 43.0
    assert by_minutes[10080].used_percent == 76.0
    argv = argv_log.read_text(encoding="utf-8").splitlines()
    joined = " ".join(argv)
    assert "--model" in argv and "claude-haiku-4-5-20251001" in argv
    assert "--output-format" in argv and "stream-json" in argv
    tools_idx = argv.index("--tools")
    assert argv[tools_idx + 1] == ""
    assert "--no-session-persistence" in argv
    assert "--strict-mcp-config" in argv
    assert "--disable-slash-commands" in argv
    assert "--dangerously-skip-permissions" not in joined


def test_probe_returns_empty_windows_for_nonzero_without_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refs sumipan/ghdag#8087 AC2."""
    _install_claude_script(
        tmp_path,
        """#!/bin/sh
echo no-event >&2
exit 1
""",
    )
    _prepend_path(monkeypatch, tmp_path)

    result = probe_quota_windows("claude")

    assert result.returncode == 1
    assert result.windows == ()


def test_probe_keeps_windows_from_nonzero_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refs sumipan/ghdag#8087 AC3."""
    _install_claude_script(
        tmp_path,
        f"""#!/bin/sh
cat <<'EOF'
{_rate_limit_line()}
EOF
exit 1
""",
    )
    _prepend_path(monkeypatch, tmp_path)

    result = probe_quota_windows("claude")

    assert result.returncode == 1
    assert len(result.windows) == 2


def test_probe_rejects_disallowed_model_before_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refs sumipan/ghdag#8087 AC4."""
    argv_log = tmp_path / "argv.log"
    _install_claude_script(
        tmp_path,
        f"""#!/bin/sh
touch "{argv_log}"
exit 0
""",
    )
    _prepend_path(monkeypatch, tmp_path)

    with pytest.raises(EngineModelError):
        probe_quota_windows("claude", model="not-allowed-model")

    assert not argv_log.exists()


def test_probe_rejects_unsupported_engine_before_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refs sumipan/ghdag#8087 AC5."""
    argv_log = tmp_path / "claude"
    argv_log.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    argv_log.chmod(argv_log.stat().st_mode | stat.S_IXUSR)
    spawn_stamp = tmp_path / "spawned"
    _prepend_path(monkeypatch, tmp_path)

    with pytest.raises(QuotaProbeUnsupportedError) as exc_info:
        probe_quota_windows("cursor")

    assert exc_info.value.code == "QUOTA_PROBE_UNSUPPORTED_ENGINE"
    assert exc_info.value.exit_code == 2
    assert not spawn_stamp.exists()


def test_probe_maps_missing_executable_to_127(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refs sumipan/ghdag#8087 AC6."""
    empty_bin = tmp_path / "bin"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))

    result = probe_quota_windows("claude")

    assert result.returncode == 127
    assert result.windows == ()
    assert "failed to launch" in result.stderr.lower() or "claude" in result.stderr.lower()


def test_probe_maps_timeout_to_124(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refs sumipan/ghdag#8087 AC7."""
    spawn_log = tmp_path / "spawns"
    _install_claude_script(
        tmp_path,
        f"""#!/bin/sh
echo 1 >> "{spawn_log}"
sleep 30
""",
    )
    _prepend_path(monkeypatch, tmp_path)

    result = probe_quota_windows("claude", timeout=1)

    assert result.returncode == 124
    assert result.windows == ()
    assert spawn_log.read_text(encoding="utf-8").strip() == "1"
