"""Tests for ghdag.cli — AC1 - AC11.

Test approach:
- Parse validation via main(argv=[...])
- Mock DagEngine / WorkflowDispatcher; assert correct args
- Error cases assert SystemExit code
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


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
