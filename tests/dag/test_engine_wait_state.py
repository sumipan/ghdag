"""WAIT_QUOTA protocol, engine pause, and legacy migration in DagEngine."""

from __future__ import annotations

import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from ghdag_dag_conftest_helpers import RUNNING_MONO_START, RUNNING_WALL_START

from ghdag.core.vocabulary import DONE_SUCCESS
from ghdag.dag._util import WaitQuotaParseError, parse_wait_quota_signal
from ghdag.dag.engine import DagEngine
from ghdag.dag.models import DagConfig, Task, _RunningProcess
from ghdag.llm.engines import list_engines

JST = timezone(timedelta(hours=9))


def _make_engine(tmp_path: Path):
    exec_path = tmp_path / "jobs" / "exec.jsonl"
    done_dir = tmp_path / "jobs" / "done"
    done_dir.mkdir(parents=True, exist_ok=True)
    exec_path.write_text(
        json.dumps({"uuid": "task-1", "command": "claude -p hi", "depends": []}) + "\n",
        encoding="utf-8",
    )
    config = DagConfig(exec_jsonl_path=exec_path, exec_done_dir=done_dir, poll_interval=0.01)
    hooks = MagicMock()
    hooks.check_rejected.return_value = False
    hooks.check_pipeline_status.side_effect = lambda path: _read_pipeline_status(path)
    return DagEngine(config, hooks), done_dir, config.quota_audit_path


def _read_pipeline_status(path: str) -> str | None:
    from ghdag.dag._util import check_pipeline_status

    return check_pipeline_status(path)


def test_parse_wait_quota_signal_unknown_engine_in_block() -> None:
    content = "```ghdag\nstatus: wait_quota\nengines: [nope]\n```\n"
    parsed = parse_wait_quota_signal(content, known_engines=frozenset(list_engines()))
    assert isinstance(parsed, WaitQuotaParseError)


@patch("ghdag.dag.task_launcher.state_mark_done")
def test_wait_quota_reply_block_without_after_pauses_engine(
    mock_mark_done, tmp_path: Path
) -> None:
    engine, _, _ = _make_engine(tmp_path)
    result_file = tmp_path / "result.md"
    result_file.write_text(
        "```ghdag\nstatus: wait_quota\nengines: [claude]\n```\n",
        encoding="utf-8",
    )
    task = Task(uuid="task-1", command="claude -p hi", engine="claude", result_path=str(result_file))
    proc = MagicMock()
    proc.poll.return_value = 0
    proc.returncode = 0
    engine._launcher._running[task.uuid] = _RunningProcess(
        uuid=task.uuid,
        task=task,
        proc=proc,
        started_at=RUNNING_WALL_START,
        started_at_mono=RUNNING_MONO_START,
        stderr_buf=io.BytesIO(b""),
        stdout_buf=io.BytesIO(b""),
    )
    engine._quota_gate.begin_run(task_uuid=task.uuid, engine="claude")

    engine._launcher.check_completions()

    mock_mark_done.assert_not_called()
    assert not result_file.exists()
    assert engine._quota_gate.snapshot().engines["claude"].status == "paused"


@patch("ghdag.dag.task_launcher.state_mark_done")
def test_legacy_wait_quota_lines_complete_without_pauses(
    mock_mark_done, tmp_path: Path
) -> None:
    """Refs sumipan/ghdag#7698 AC2."""
    engine, _, _ = _make_engine(tmp_path)
    result_file = tmp_path / "legacy_wq.md"
    result_file.write_text(
        "PIPELINE_STATUS: WAIT_QUOTA\n"
        "WAIT_QUOTA: after=2099-01-01T00:00:00+00:00 engines=claude\n",
        encoding="utf-8",
    )
    task = Task(uuid="task-1", command="claude -p hi", engine="claude", result_path=str(result_file))
    proc = MagicMock()
    proc.poll.return_value = 0
    proc.returncode = 0
    engine._launcher._running[task.uuid] = _RunningProcess(
        uuid=task.uuid,
        task=task,
        proc=proc,
        started_at=RUNNING_WALL_START,
        started_at_mono=RUNNING_MONO_START,
        stderr_buf=io.BytesIO(b""),
        stdout_buf=io.BytesIO(b""),
    )
    engine._quota_gate.begin_run(task_uuid=task.uuid, engine="claude")

    engine._launcher.check_completions()

    mock_mark_done.assert_called_once()
    _, uuid, status = mock_mark_done.call_args[0]
    assert uuid == task.uuid
    assert str(status) == DONE_SUCCESS
    claude_state = engine._quota_gate.snapshot().engines.get("claude")
    assert claude_state is None or claude_state.status != "paused"


def _shell_task_wait_quota_recorded(engine: DagEngine, tmp_path: Path) -> Task:
    """Refs sumipan/nexus#6632."""
    result_file = tmp_path / "wait_quota_result.md"
    result_file.write_text(
        '```ghdag\nstatus: wait_quota\n'
        'after: "2099-01-01T00:00:00+00:00"\n'
        "engines: [claude, codex]\n```\n",
        encoding="utf-8",
    )
    task = Task(
        uuid="task-1",
        command="true",
        engine="shell",
        result_path=str(result_file),
    )
    proc = MagicMock()
    proc.poll.return_value = 0
    proc.returncode = 0
    engine._launcher._running[task.uuid] = _RunningProcess(
        uuid=task.uuid,
        task=task,
        proc=proc,
        started_at=RUNNING_WALL_START,
        started_at_mono=RUNNING_MONO_START,
        stderr_buf=io.BytesIO(b""),
        stdout_buf=io.BytesIO(b""),
    )
    engine._quota_gate.begin_run(task_uuid=task.uuid, engine="shell")
    engine._launcher.check_completions()
    engine._quota_gate.finish_run(task_uuid=task.uuid)
    return task


def test_wait_quota_task_is_not_relaunched_while_its_engines_are_paused(
    tmp_path: Path,
) -> None:
    """Refs sumipan/nexus#6632."""
    engine, _, _ = _make_engine(tmp_path)
    task = _shell_task_wait_quota_recorded(engine, tmp_path)
    gate = engine._quota_gate.snapshot()
    assert gate.engines["claude"].status == "paused"
    assert gate.engines["codex"].status == "paused"

    proc = MagicMock()
    proc.poll.return_value = None
    proc.stdout = io.BytesIO(b"")
    proc.stderr = io.BytesIO(b"")
    with patch("ghdag.dag._process_host.subprocess.Popen", return_value=proc) as mock_popen:
        for _ in range(3):
            assert engine._launcher.launch("task-1", task) is False
    mock_popen.assert_not_called()


def test_wait_quota_task_relaunches_when_one_engine_becomes_available(
    tmp_path: Path,
) -> None:
    """Refs sumipan/nexus#6632."""
    engine, _, _ = _make_engine(tmp_path)
    task = _shell_task_wait_quota_recorded(engine, tmp_path)

    proc = MagicMock()
    proc.poll.return_value = None
    proc.stdout = io.BytesIO(b"")
    proc.stderr = io.BytesIO(b"")
    with patch("ghdag.dag._process_host.subprocess.Popen", return_value=proc) as mock_popen:
        assert engine._launcher.launch("task-1", task) is False
        mock_popen.assert_not_called()

        engine._quota_gate.clear(
            engine="codex",
            observed_at=datetime(2099, 6, 1, tzinfo=timezone.utc),
        )
        assert engine._launcher.launch("task-1", task) is True
    mock_popen.assert_called_once()


def _record_wait_quota_codex(engine: DagEngine, tmp_path: Path) -> Task:
    """Refs sumipan/nexus#6936 AC3."""
    result_file = tmp_path / "wait_codex.md"
    result_file.write_text(
        "```ghdag\nstatus: wait_quota\nengines: [codex]\n```\n",
        encoding="utf-8",
    )
    task = Task(
        uuid="task-wq",
        command="claude -p hi",
        engine="claude",
        result_path=str(result_file),
    )
    proc = MagicMock()
    proc.poll.return_value = 0
    proc.returncode = 0
    engine._launcher._running[task.uuid] = _RunningProcess(
        uuid=task.uuid,
        task=task,
        proc=proc,
        started_at=RUNNING_WALL_START,
        started_at_mono=RUNNING_MONO_START,
        stderr_buf=io.BytesIO(b""),
        stdout_buf=io.BytesIO(b""),
    )
    engine._quota_gate.begin_run(task_uuid=task.uuid, engine="claude")
    engine._launcher.check_completions()
    engine._quota_gate.finish_run(task_uuid=task.uuid)
    return task


class TestPersistedQuotaWaiting:
    def test_restored_wait_quota_defers_until_listed_engine_admits(
        self, tmp_path: Path
    ) -> None:
        """Refs sumipan/nexus#6936 AC3."""
        engine1, _, _ = _make_engine(tmp_path)
        task = _record_wait_quota_codex(engine1, tmp_path)

        engine2, _, _ = _make_engine(tmp_path)
        tasks = {task.uuid: task}
        assert engine2._launcher.restore_waiting(tasks, set()) == [task.uuid]

        proc = MagicMock()
        proc.poll.return_value = None
        proc.stdout = io.BytesIO(b"")
        proc.stderr = io.BytesIO(b"")
        with patch("ghdag.dag._process_host.subprocess.Popen", return_value=proc) as mock_popen:
            assert engine2._launcher.launch(task.uuid, task) is False
        mock_popen.assert_not_called()

    def test_launch_after_waiting_engine_available_clears_quota_file(
        self, tmp_path: Path
    ) -> None:
        """Refs sumipan/nexus#6936 AC4."""
        engine1, _, _ = _make_engine(tmp_path)
        task = _record_wait_quota_codex(engine1, tmp_path)

        engine2, _, _ = _make_engine(tmp_path)
        engine2._launcher.restore_waiting({task.uuid: task}, set())

        quota_path = tmp_path / "jobs" / "quota-waiting" / f"{task.uuid}.json"
        assert quota_path.exists()

        proc = MagicMock()
        proc.poll.return_value = None
        proc.stdout = io.BytesIO(b"")
        proc.stderr = io.BytesIO(b"")
        engine2._quota_gate.clear(
            engine="codex",
            observed_at=datetime(2099, 6, 1, tzinfo=timezone.utc),
        )
        with patch("ghdag.dag._process_host.subprocess.Popen", return_value=proc):
            assert engine2._launcher.launch(task.uuid, task) is True
        assert not quota_path.exists()


