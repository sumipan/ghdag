"""Tests for ghdag.cli — AC1 - AC11.

Test approach:
- Parse validation via main(argv=[...])
- Mock DagEngine / WorkflowDispatcher; assert correct args
- Error cases assert SystemExit code
"""

from __future__ import annotations

import json

import pytest


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
            "--state-path",
            str(state_path),
        ])
        capsys.readouterr()
        main(["quota", "drain", "claude", "--state-path", str(state_path)])
        capsys.readouterr()
        main([
            "quota",
            "report",
            "claude",
            "--status",
            "paused",
            "--observed-at",
            "2026-09-02T12:01:00+09:00",
            "--state-path",
            str(state_path),
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
            "--state-path",
            str(state_path),
        ])
        capsys.readouterr()
        main([
            "quota",
            "resume",
            "claude",
            "--state-path",
            str(state_path),
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
            "--state-path",
            str(state_path),
        ])
        capsys.readouterr()
        main([
            "quota",
            "status",
            "--state-path",
            str(state_path),
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
            "--state-path",
            str(state_path),
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

    def test_quota_status_permanent_pause_when_ttl_disabled(self, tmp_path, capsys):
        from datetime import datetime, timezone

        from ghdag.cli import main
        from ghdag.quota import QuotaGate

        state_path = tmp_path / "quota-gate.json"
        QuotaGate(state_path, pause_ttl_seconds=None).report(
            engine="codex",
            status="paused",
            observed_at=datetime(2026, 10, 10, tzinfo=timezone.utc),
        )
        exec_path = tmp_path / "exec.jsonl"
        exec_path.write_text("", encoding="utf-8")
        done_dir = tmp_path / "done"
        done_dir.mkdir()
        main([
            "quota",
            "status",
            "--state-path",
            str(state_path),
            "--exec-jsonl",
            str(exec_path),
            "--done-dir",
            str(done_dir),
        ])
        payload = json.loads(capsys.readouterr().out.strip())
        assert payload["engines"]["codex"]["permanent_pause"] is True
        assert payload["engines"]["codex"]["resume_at"] is None

    def test_quota_drain_and_resume(self, tmp_path, capsys):
        from ghdag.cli import main

        state_path = tmp_path / "quota-gate.json"
        main(["quota", "drain", "claude", "--reason", "model switch", "--state-path", str(state_path)])
        drain_payload = json.loads(capsys.readouterr().out.strip())
        assert drain_payload["engine"] == "claude"
        assert drain_payload["draining"] is True

        main(["quota", "resume", "claude", "--state-path", str(state_path)])
        resume_payload = json.loads(capsys.readouterr().out.strip())
        assert resume_payload["draining"] is False
        assert resume_payload["released"] == []

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
                "--state-path",
                str(tmp_path / "quota-gate.json"),
            ])
        assert exc.value.code == 1

    def test_quota_invalid_engine_exits_1(self, tmp_path):
        from ghdag.cli import main

        with pytest.raises(SystemExit) as exc:
            main(["quota", "drain", "", "--state-path", str(tmp_path / "quota-gate.json")])
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
            "--state-path",
            str(state_path),
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
