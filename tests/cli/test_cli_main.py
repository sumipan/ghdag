"""Tests for ghdag.cli — AC1 - AC11.

Test approach:
- Parse validation via main(argv=[...])
- Mock DagEngine / WorkflowDispatcher; assert correct args
- Error cases assert SystemExit code
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


class TestHelp:
    def test_global_help_exits_0(self, capsys):
        from ghdag.cli import main

        with pytest.raises(SystemExit) as exc:
            main(["--help"])
        assert exc.value.code == 0

    def test_run_help_exits_0(self, capsys):
        from ghdag.cli import main

        with pytest.raises(SystemExit) as exc:
            main(["run", "--help"])
        assert exc.value.code == 0
        captured = capsys.readouterr()
        assert "exec_jsonl" in captured.out
        assert "--interval" in captured.out

    def test_watch_help_exits_0(self, capsys):
        from ghdag.cli import main

        with pytest.raises(SystemExit) as exc:
            main(["watch", "--help"])
        assert exc.value.code == 0
        captured = capsys.readouterr()
        assert "workflows_dir" in captured.out
        assert "--interval" in captured.out
        assert "--exec-md" in captured.out
        assert "--pause-file" in captured.out


# ---------------------------------------------------------------------------
# AC2: version
# ---------------------------------------------------------------------------


class TestVersion:
    def test_version_outputs_version_string(self, capsys):
        from ghdag import __version__
        from ghdag.cli import main

        main(["version"])
        captured = capsys.readouterr()
        assert captured.out.strip() == __version__
class TestPythonM:
    def test_python_m_help_exits_0(self):
        import subprocess

        worktree_root = Path(__file__).resolve().parents[2]
        env = {**os.environ, "PYTHONPATH": str(worktree_root / "src")}
        result = subprocess.run(
            [sys.executable, "-m", "ghdag", "--help"],
            capture_output=True,
            text=True,
            cwd=str(worktree_root),
            env=env,
        )
        assert result.returncode == 0


# ---------------------------------------------------------------------------
# AC8: log level
# ---------------------------------------------------------------------------


class TestLogLevel:
    def _run_with_dummy_exec(self, argv: list[str], tmp_path) -> None:
        exec_md = tmp_path / "exec.jsonl"
        exec_md.write_text("")
        with patch("ghdag.dag.engine.DagEngine"), \
             patch("ghdag.dag.models.DagConfig"):
            from ghdag.cli import main
            main(argv + ["run", str(exec_md)])

    def test_verbose_sets_debug(self, tmp_path):
        self._run_with_dummy_exec(["--verbose"], tmp_path)
        assert logging.getLogger().level == logging.DEBUG

    def test_quiet_sets_warning(self, tmp_path):
        self._run_with_dummy_exec(["--quiet"], tmp_path)
        assert logging.getLogger().level == logging.WARNING

    def test_default_sets_info(self, tmp_path):
        self._run_with_dummy_exec([], tmp_path)
        assert logging.getLogger().level == logging.INFO
