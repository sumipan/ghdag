"""Tests for ghdag.cli — AC1 - AC11.

Test approach:
- Parse validation via main(argv=[...])
- Mock DagEngine / WorkflowDispatcher; assert correct args
- Error cases assert SystemExit code
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from ghdag.core.exceptions import GhdagError


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

    def test_version_json_outputs_object(self, capsys):
        from ghdag import __version__
        from ghdag.cli import main

        main(["version", "--json"])
        captured = capsys.readouterr()
        assert json.loads(captured.out.strip()) == {"version": __version__}


def _ghdag_error_command(_args):
    raise GhdagError("m", code="C", hint="h")


class TestGhdagErrorExit:
    def test_text_error_on_stderr(self, capsys):
        from ghdag.cli import main

        with patch("ghdag.cli.main._cmd_version", _ghdag_error_command):
            with pytest.raises(SystemExit) as exc:
                main(["version"])
        assert exc.value.code == 1
        captured = capsys.readouterr()
        assert captured.err == "error[C]: m\n  hint: h\n"
        assert captured.out == ""

    def test_json_error_on_stdout(self, capsys):
        from ghdag.cli import main

        with patch("ghdag.cli.main._cmd_version", _ghdag_error_command):
            with pytest.raises(SystemExit) as exc:
                main(["version", "--json"])
        assert exc.value.code == 1
        captured = capsys.readouterr()
        assert captured.err == ""
        payload = json.loads(captured.out.strip())
        assert payload["error"] == {
            "code": "C",
            "message": "m",
            "hint": "h",
            "exit_code": 1,
        }


class TestLlmCapabilitiesPreset:
    def test_llm_help_lists_readonly_observe(self, capsys):
        from ghdag.cli import main

        with pytest.raises(SystemExit) as exc:
            main(["llm", "--help"])
        assert exc.value.code == 0
        captured = capsys.readouterr()
        assert "readonly_observe" in captured.out


class TestStatusJson:
    def test_running_only_json_empty_array(self, tmp_path, capsys):
        from ghdag.cli import main

        running_dir = tmp_path / "running"
        running_dir.mkdir()
        main(
            [
                "status",
                "--running",
                "--running-dir",
                str(running_dir),
                "--json",
            ]
        )
        captured = capsys.readouterr()
        assert json.loads(captured.out) == []

    def test_issue_and_running_json_single_object(self, tmp_path, capsys):
        from ghdag.cli import main
        from ghdag.status import IssueStatus, RunningTask, StepStatus

        mock_status = IssueStatus(
            generation=2,
            running=False,
            steps=[
                StepStatus(
                    uuid="uuid-a",
                    step_name="step-a",
                    depends=[],
                    status="success",
                    started_at=None,
                    elapsed_sec=None,
                    result_path=None,
                )
            ],
            orphan_uuids=["orphan-1"],
        )

        mock_task = RunningTask(
            uuid="run-1",
            engine="cursor",
            pid=99,
            started_at="2020-01-01T00:00:00Z",
            elapsed_sec=1.5,
        )

        with (
            patch("ghdag.status.issue_status", return_value=mock_status),
            patch("ghdag.status.running_tasks", return_value=[mock_task]),
        ):
            main(
                [
                    "status",
                    "--issue",
                    "1",
                    "--handler",
                    "h",
                    "--workflow",
                    "w",
                    "--running",
                    "--json",
                    "--exec-jsonl",
                    str(tmp_path / "jobs/exec.jsonl"),
                    "--state-dir",
                    str(tmp_path / "state"),
                    "--done-dir",
                    str(tmp_path / "done"),
                    "--running-dir",
                    str(tmp_path / "running"),
                ]
            )
        captured = capsys.readouterr()
        body = json.loads(captured.out)
        assert "steps" in body
        assert "running_tasks" in body
        assert len(body["running_tasks"]) == 1

    def test_status_json_without_target_exits_with_usage_error(self, capsys):
        from ghdag.cli import main

        with pytest.raises(SystemExit) as exc:
            main(["status", "--json"])
        assert exc.value.code == 2
        captured = capsys.readouterr()
        payload = json.loads(captured.out.strip())
        assert payload["error"]["code"] == "STATUS_TARGET_REQUIRED"


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
