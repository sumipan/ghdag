"""Tests for ghdag.cli — AC1 - AC11.

Test approach:
- Parse validation via main(argv=[...])
- Mock DagEngine / WorkflowDispatcher; assert correct args
- Error cases assert SystemExit code
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest


def _rate_limit_line(
    *,
    five_util: float = 0.43,
    seven_util: float = 0.76,
) -> str:
    return json.dumps(
        {
            "type": "rate_limit_event",
            "rate_limit_info": {
                "unifiedWindows": {
                    "five_hour": {"utilization": five_util, "resetsAt": 1794667200},
                    "seven_day": {"utilization": seven_util, "resetsAt": 1794682800},
                },
            },
        }
    )


def _install_claude_script(bin_dir: Path, body: str) -> None:
    script = bin_dir / "claude"
    script.write_text(body, encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _worktree_src() -> Path:
    return Path(__file__).resolve().parents[2] / "src"


def _probe_env(bin_dir: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["PYTHONPATH"] = str(_worktree_src())
    return env


def _run_quota_probe(
    *extra: str,
    state_dir: Path,
    bin_dir: Path,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "ghdag", "quota", "probe", "claude", "--state-dir", str(state_dir), *extra],
        capture_output=True,
        text=True,
        env=_probe_env(bin_dir),
    )


class TestQuotaCli:
    def test_quota_report_and_status(self, tmp_path, capsys):
        from ghdag.cli import main

        state_path = tmp_path / "quota-gate.json"
        exec_path = tmp_path / "exec.jsonl"
        done_dir = tmp_path / "done"
        done_dir.mkdir(parents=True, exist_ok=True)
        exec_path.write_text(
            "\n".join(
                [
                    json.dumps({"uuid": "u-queued", "command": "claude -p queued", "depends": []}),
                    json.dumps({"uuid": "u-deferred", "command": "claude -p deferred", "depends": []}),
                    json.dumps({"uuid": "u-unresolved", "command": "echo hello", "depends": []}),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        main([
            "quota",
            "report",
            "claude",
            "--status",
            "paused",
            "--observed-at",
            "2026-09-02T12:00:00+09:00",
            "--resume-at",
            "2026-09-02T17:00:00+09:00",
            "--state-dir",
            str(state_path.parent),
        ])
        capsys.readouterr()
        main(["quota", "drain", "claude", "--state-dir", str(state_path.parent)])
        capsys.readouterr()
        main([
            "quota",
            "report",
            "claude",
            "--status",
            "paused",
            "--observed-at",
            "2026-09-02T12:01:00+09:00",
            "--state-dir",
            str(state_path.parent),
        ])
        capsys.readouterr()
        main([
            "quota",
            "report",
            "codex",
            "--status",
            "available",
            "--observed-at",
            "2026-09-02T12:00:00+09:00",
            "--state-dir",
            str(state_path.parent),
        ])
        capsys.readouterr()
        main([
            "quota",
            "resume",
            "claude",
            "--state-dir",
            str(state_path.parent),
        ])
        capsys.readouterr()
        main([
            "quota",
            "report",
            "claude",
            "--status",
            "paused",
            "--observed-at",
            "2026-09-02T12:02:00+09:00",
            "--resume-at",
            "2099-01-01T00:00:00+00:00",
            "--state-dir",
            str(state_path.parent),
        ])
        capsys.readouterr()
        main([
            "quota",
            "status",
            "--state-dir",
            str(state_path.parent),
            "--exec-jsonl",
            str(exec_path),
            "--done-dir",
            str(done_dir),
        ])
        payload = json.loads(capsys.readouterr().out.strip())
        assert payload["engines"]["claude"]["status"] == "paused"
        assert payload["engines"]["claude"]["observed_at"] == "2026-09-02T03:02:00+00:00"
        assert payload["engines"]["claude"]["resume_at"] == "2099-01-01T00:00:00+00:00"
        assert payload["engines"]["claude"]["reason"] is None
        assert payload["engines"]["claude"]["quota_status"] == "paused"
        assert payload["engines"]["claude"]["queued"] == 2
        assert payload["engines"]["claude"]["running"] == 0
        assert payload["engines"]["claude"]["idle"] is True
        assert payload["engines"]["claude"]["limit"] is None
        assert payload["engines"]["codex"]["limit"] is None
        assert "deferred_tasks" not in payload
        assert payload["unresolved"] == 1

    def test_quota_status_includes_engine_limit(self, tmp_path, capsys):
        from ghdag.cli import main

        state_path = tmp_path / "quota-gate.json"
        state_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "engines": {},
                    "deferred_tasks": {},
                    "draining_engines": {},
                    "running_tasks": {
                        "codex-task": {"engine": "codex", "started_at": "2026-10-10T00:00:00+00:00"},
                    },
                    "limits": {"claude": 2},
                }
            ),
            encoding="utf-8",
        )
        exec_path = tmp_path / "exec.jsonl"
        exec_path.write_text("", encoding="utf-8")
        done_dir = tmp_path / "done"
        done_dir.mkdir()
        main([
            "quota",
            "status",
            "--state-dir",
            str(state_path.parent),
            "--exec-jsonl",
            str(exec_path),
            "--done-dir",
            str(done_dir),
        ])
        payload = json.loads(capsys.readouterr().out.strip())
        assert payload["engines"]["claude"]["limit"] == 2
        assert payload["engines"]["claude"]["running"] == 0
        assert payload["engines"]["codex"]["limit"] is None
        assert payload["engines"]["codex"]["running"] == 1

    def test_quota_report_naive_timestamp_exits_1(self, tmp_path):
        from ghdag.cli import main

        with pytest.raises(SystemExit) as exc:
            main([
                "quota",
                "report",
                "claude",
                "--status",
                "paused",
                "--observed-at",
                "2026-09-02T12:00:00",
                "--state-dir",
                str(tmp_path),
            ])
        assert exc.value.code == 1

    def test_quota_report_naive_timestamp_json_error(self, tmp_path, capsys):
        """Refs sumipan/ghdag#7551 AC1."""
        import json

        from ghdag.cli import main

        with pytest.raises(SystemExit) as exc:
            main([
                "quota",
                "report",
                "claude",
                "--status",
                "paused",
                "--observed-at",
                "2026-09-02T12:00:00",
                "--state-dir",
                str(tmp_path),
                "--json",
            ])
        assert exc.value.code == 1
        payload = json.loads(capsys.readouterr().out.strip())
        assert payload["error"]["code"] == "QUOTA_INVALID_INPUT"
        assert payload["error"]["message"] == "--observed-at must be timezone-aware"
        assert payload["error"]["exit_code"] == 1

    def test_quota_invalid_engine_exits_1(self, tmp_path):
        from ghdag.cli import main

        with pytest.raises(SystemExit) as exc:
            main(["quota", "drain", "", "--state-dir", str(tmp_path)])
        assert exc.value.code == 1

    def test_quota_status_includes_engine_usage(self, tmp_path, capsys):
        """Refs sumipan/nexus#6717 — status JSON exposes latest usage windows."""
        from datetime import datetime, timezone

        from ghdag.cli import main
        from ghdag.core.models.metrics import QuotaWindow
        from ghdag.quota import QuotaGate

        state_path = tmp_path / "quota-gate.json"
        gate = QuotaGate(state_path)
        observed = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
        gate.observe(
            engine="claude",
            windows=(
                QuotaWindow(
                    window_minutes=300,
                    used_percent=34.0,
                    resets_at=datetime(2026, 11, 14, 17, 0, tzinfo=timezone.utc),
                ),
            ),
            observed_at=observed,
        )
        exec_path = tmp_path / "exec.jsonl"
        exec_path.write_text("", encoding="utf-8")
        done_dir = tmp_path / "done"
        done_dir.mkdir()
        main([
            "quota",
            "status",
            "--state-dir",
            str(state_path.parent),
            "--exec-jsonl",
            str(exec_path),
            "--done-dir",
            str(done_dir),
        ])
        payload = json.loads(capsys.readouterr().out.strip())
        claude_usage = payload["engines"]["claude"]["usage"]
        assert claude_usage["observed_at"] == observed.isoformat()
        assert claude_usage["windows"][0]["window_minutes"] == 300
        for name, entry in payload["engines"].items():
            if name != "claude":
                assert entry["usage"] is None

    def test_quota_status_resolves_done_dir_from_ghdag_state_dir(
        self, tmp_path, capsys, monkeypatch
    ):
        """Refs sumipan/nexus#6801."""
        from ghdag.cli import main

        monkeypatch.setenv("GHDAG_STATE_DIR", str(tmp_path / "state"))
        jobs = tmp_path / "jobs"
        jobs.mkdir()
        exec_path = jobs / "exec.jsonl"
        uid = "u-done"
        exec_path.write_text(
            json.dumps({"uuid": uid, "command": "claude -p x", "depends": []}) + "\n",
            encoding="utf-8",
        )
        state_path = tmp_path / "state" / "quota-gate.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "engines": {},
                    "deferred_tasks": {},
                    "draining_engines": {},
                    "running_tasks": {},
                    "limits": {},
                }
            ),
            encoding="utf-8",
        )
        state_done = tmp_path / "state" / "done"
        state_done.mkdir(parents=True)
        (state_done / uid).write_text("0\n", encoding="utf-8")

        main([
            "quota",
            "status",
            "--state-dir",
            str(state_path.parent),
            "--exec-jsonl",
            str(exec_path),
        ])
        payload = json.loads(capsys.readouterr().out.strip())
        queued_total = sum(
            entry.get("queued", 0) for entry in payload["engines"].values()
        )
        assert queued_total == 0

    def test_quota_state_dir_writes_audit_in_state_dir(self, tmp_path, capsys):
        """Refs #7550 — drain writes quota-gate.json and audit.jsonl under --state-dir."""
        from ghdag.cli import main

        state_dir = tmp_path / "state"
        state_dir.mkdir()
        capsys.readouterr()
        main(["quota", "drain", "claude", "--state-dir", str(state_dir)])
        capsys.readouterr()
        assert (state_dir / "quota-gate.json").is_file()
        assert (state_dir / "audit.jsonl").is_file()

    @pytest.mark.parametrize(
        "argv",
        [
            [
                "quota",
                "report",
                "claude",
                "--status",
                "paused",
                "--observed-at",
                "2026-09-02T12:00:00+09:00",
                "--state-path",
                "x",
            ],
            [
                "quota",
                "clear",
                "claude",
                "--observed-at",
                "2026-09-02T12:00:00+09:00",
                "--state-path",
                "x",
            ],
            ["quota", "drain", "claude", "--state-path", "x"],
            ["quota", "resume", "claude", "--state-path", "x"],
            ["quota", "status", "--state-path", "x"],
        ],
        ids=["report", "clear", "drain", "resume", "status"],
    )
    def test_quota_rejects_state_path(self, argv):
        """Refs #7550 — legacy --state-path is rejected by argparse."""
        from ghdag.cli import main

        with pytest.raises(SystemExit) as exc:
            main(argv)
        assert exc.value.code == 2

    def test_quota_probe_records_while_paused_and_draining(self, tmp_path: Path) -> None:
        """Refs sumipan/ghdag#8087 AC8."""
        from datetime import datetime, timezone

        from ghdag.quota import QuotaGate

        state_dir = tmp_path / "state"
        state_dir.mkdir()
        gate = QuotaGate(state_dir / "quota-gate.json", pause_ttl_seconds=None)
        observed = datetime(2026, 11, 26, 10, 0, tzinfo=timezone.utc)
        gate.report(
            engine="claude",
            status="paused",
            observed_at=observed,
            kind="budget",
            reason="budget pause",
        )
        gate.drain(engine="claude", reason="drain", now=observed)
        before = gate.read_state()
        before_claude = before.get("engines", {}).get("claude", {})
        before_kinds = tuple(s.get("kind") for s in before_claude.get("sources", []))
        before_draining = "claude" in before.get("draining_engines", {})

        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _install_claude_script(
            bin_dir,
            f"""#!/bin/sh
cat <<'EOF'
{_rate_limit_line()}
EOF
exit 0
""",
        )
        proc = _run_quota_probe(state_dir=state_dir, bin_dir=bin_dir)
        assert proc.returncode == 0, proc.stderr
        payload = json.loads(proc.stdout.strip())
        assert payload["recorded"] is True
        usage = payload["usage"]
        percents = {w["window_minutes"]: w["used_percent"] for w in usage["windows"]}
        assert percents[300] == 43.0
        assert percents[10080] == 76.0

        after = gate.read_state()
        after_claude = after.get("engines", {}).get("claude", {})
        after_kinds = tuple(s.get("kind") for s in after_claude.get("sources", []))
        after_draining = "claude" in after.get("draining_engines", {})
        assert after_kinds == before_kinds
        assert after_draining == before_draining

    def test_quota_probe_records_windows_from_nonzero_child(self, tmp_path: Path) -> None:
        """Refs sumipan/ghdag#8087 AC9."""
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _install_claude_script(
            bin_dir,
            f"""#!/bin/sh
cat <<'EOF'
{_rate_limit_line()}
EOF
exit 1
""",
        )
        proc = _run_quota_probe(state_dir=state_dir, bin_dir=bin_dir)
        assert proc.returncode == 0, proc.stderr
        payload = json.loads(proc.stdout.strip())
        assert payload["recorded"] is True
        assert payload["returncode"] == 1
        assert payload["usage"] is not None

    def test_quota_probe_without_windows_preserves_usage(self, tmp_path: Path) -> None:
        """Refs sumipan/ghdag#8087 AC10."""
        from datetime import datetime, timezone

        from ghdag.core.models.metrics import QuotaWindow
        from ghdag.quota import QuotaGate

        state_dir = tmp_path / "state"
        state_dir.mkdir()
        gate = QuotaGate(state_dir / "quota-gate.json")
        observed = datetime(2026, 11, 25, 10, 26, 58, tzinfo=timezone.utc)
        gate.observe(
            engine="claude",
            windows=(
                QuotaWindow(
                    window_minutes=300,
                    used_percent=35.0,
                    resets_at=datetime(2026, 11, 25, 15, 0, tzinfo=timezone.utc),
                ),
            ),
            observed_at=observed,
        )
        before_usage = gate.read_state()["usage"]["claude"]

        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _install_claude_script(
            bin_dir,
            """#!/bin/sh
echo no windows >&2
exit 1
""",
        )
        proc = _run_quota_probe(state_dir=state_dir, bin_dir=bin_dir)
        assert proc.returncode == 1, proc.stdout
        payload = json.loads(proc.stdout.strip())
        assert payload["recorded"] is False
        assert payload["usage"] is None
        assert gate.read_state()["usage"]["claude"] == before_usage

    def test_quota_probe_help_lists_arguments(self) -> None:
        """Refs sumipan/ghdag#8087 AC11."""
        proc = subprocess.run(
            [sys.executable, "-m", "ghdag", "quota", "probe", "--help"],
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPATH": str(_worktree_src())},
        )
        assert proc.returncode == 0
        help_text = proc.stdout
        assert "ENGINE" in help_text or "engine" in help_text.lower()
        assert "--model" in help_text
        assert "--timeout" in help_text
        assert "--state-dir" in help_text
