"""Tests for ghdag.cli — AC1 - AC11.

Test approach:
- Parse validation via main(argv=[...])
- Mock DagEngine / WorkflowDispatcher; assert correct args
- Error cases assert SystemExit code
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestCleanupNormal:
    def _make_mock_result(self, **kwargs):
        mock_result = MagicMock()
        mock_result.archived_done = kwargs.get("archived_done", 0)
        mock_result.archived_orphan = kwargs.get("archived_orphan", 0)
        mock_result.pruned_exec = kwargs.get("pruned_exec", 0)
        mock_result.swept_extras = kwargs.get("swept_extras", 0)
        mock_result.detected_orphan = kwargs.get("detected_orphan", 0)
        mock_result.detected_dead = kwargs.get("detected_dead", 0)
        return mock_result

    def test_cleanup_calls_cleanup_queue(self, tmp_path):
        captured: dict = {}

        def fake_cleanup(**kwargs):
            captured.update(kwargs)
            return self._make_mock_result(archived_done=1, pruned_exec=1)

        with patch("ghdag.cleanup.orchestrator.cleanup_queue", side_effect=fake_cleanup):
            from ghdag.cli import main
            main(["cleanup", str(tmp_path)])

        assert captured["cutoff_days"] == 1
        assert captured["orphan_days"] == 7
        assert captured["dry_run"] is False
        assert captured["auto_repair"] is False

    def test_cleanup_auto_repair_flag(self, tmp_path):
        captured: dict = {}

        def fake_cleanup(**kwargs):
            captured.update(kwargs)
            return self._make_mock_result(archived_orphan=1, pruned_exec=1)

        with patch("ghdag.cleanup.orchestrator.cleanup_queue", side_effect=fake_cleanup):
            from ghdag.cli import main
            main(["cleanup", str(tmp_path), "--auto-repair"])

        assert captured["auto_repair"] is True

    def test_cleanup_dry_run_flag(self, tmp_path):
        captured: dict = {}

        def fake_cleanup(**kwargs):
            captured.update(kwargs)
            return self._make_mock_result()

        with patch("ghdag.cleanup.orchestrator.cleanup_queue", side_effect=fake_cleanup):
            from ghdag.cli import main
            main(["cleanup", str(tmp_path), "--dry-run"])

        assert captured["dry_run"] is True

    def test_cleanup_custom_days(self, tmp_path):
        captured: dict = {}

        def fake_cleanup(**kwargs):
            captured.update(kwargs)
            return self._make_mock_result()

        with patch("ghdag.cleanup.orchestrator.cleanup_queue", side_effect=fake_cleanup):
            from ghdag.cli import main
            main(["cleanup", str(tmp_path), "--cutoff-days", "3", "--orphan-days", "14"])

        assert captured["cutoff_days"] == 3
        assert captured["orphan_days"] == 14

    def test_cleanup_done_dir_follows_state_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Issue sumipan/nexus#6089: cleanup uses GHDAG_STATE_DIR for done_dir (AC6)."""
        state = tmp_path / "s"
        monkeypatch.setenv("GHDAG_STATE_DIR", str(state))
        captured: dict = {}

        def fake_cleanup(**kwargs):
            captured.update(kwargs)
            return self._make_mock_result()

        jobs = tmp_path / "jobs"
        jobs.mkdir(parents=True)
        (jobs / "exec.jsonl").write_text("", encoding="utf-8")

        with patch("ghdag.cleanup.orchestrator.cleanup_queue", side_effect=fake_cleanup):
            from ghdag.cli import main

            main(["cleanup", str(tmp_path)])

        assert captured["done_dir"] == state / "done"
        assert captured["queue_dir"] == jobs
        assert captured["archive_dir"] == jobs / "archive"
        assert captured["exec_md"] == jobs / "exec.jsonl"

    def test_cleanup_help_exits_0(self, capsys):

        from ghdag.cli import main

        with pytest.raises(SystemExit) as exc:
            main(["cleanup", "--help"])
        assert exc.value.code == 0
        captured = capsys.readouterr()
        assert "repo_root" in captured.out
        assert "--dry-run" in captured.out
        assert "--cutoff-days" in captured.out
        assert "--orphan-days" in captured.out


class TestCleanupError:
    def test_cleanup_no_args_exits_2(self):

        from ghdag.cli import main

        with pytest.raises(SystemExit) as exc:
            main(["cleanup"])
        assert exc.value.code == 2
