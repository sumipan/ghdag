"""Regression: task_launcher must process stdout via the adapter.

If this test fails, the adapter wiring has been disconnected again.
"""

from __future__ import annotations

import io
from unittest.mock import MagicMock, patch

from ghdag.dag.engine import DagEngine
from ghdag.dag.hooks import DagHooks
from ghdag.dag.models import DagConfig, Task, _RunningProcess


def _make_config(tmp_path):
    return DagConfig(
        exec_jsonl_path=str(tmp_path / "exec.jsonl"),
        exec_done_dir=str(tmp_path / "done"),
    )


def _make_engine(tmp_path):
    hooks = MagicMock(spec=DagHooks)
    hooks.check_rejected.return_value = False
    hooks.check_pipeline_status.return_value = None
    return DagEngine(_make_config(tmp_path), hooks), hooks


def _make_proc(returncode=0):
    proc = MagicMock()
    proc.poll.return_value = returncode
    proc.returncode = returncode
    return proc


_CODEX_JSONL_STDOUT = (
    b'{"type":"thread.started","thread_id":"01a0842c-88a5-7993-afd5-13562483ca9b"}\n'
    b'{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"pong"}}\n'
    b'{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":1,'
    b'"cached_input_tokens":0,"cache_write_input_tokens":0}}\n'
)


@patch("ghdag.dag.task_launcher.state_mark_done")
def test_codex_jsonl_writes_result_text_and_session(mock_mark_done, tmp_path):
    """codex JSONL stdout → result is pong; SessionStore records thread_id."""
    engine, hooks = _make_engine(tmp_path)
    result_file = tmp_path / "result.md"
    parent_uuid = "test-codex-jsonl-parent"

    task = Task(
        uuid=parent_uuid,
        command="codex exec - --json --skip-git-repo-check < order.md",
        engine="codex",
        result_path=str(result_file),
    )
    rt = _RunningProcess(
        uuid=parent_uuid,
        task=task,
        proc=_make_proc(0),
        started_at=1_700_000_000.0 - 1.0,
        started_at_mono=10_000.0 - 1.0,
        stderr_buf=io.BytesIO(b""),
        stdout_buf=io.BytesIO(_CODEX_JSONL_STDOUT),
    )
    engine._launcher._running[parent_uuid] = rt
    engine._launcher.check_completions()

    assert result_file.read_text(encoding="utf-8") == "pong"
    recorded = engine._launcher._session_store.lookup(parent_uuid)
    assert recorded is not None
    assert recorded.engine == "codex"
    assert recorded.session_id == "01a0842c-88a5-7993-afd5-13562483ca9b"

    child = Task(
        uuid="test-codex-jsonl-child",
        command="codex exec - --json --skip-git-repo-check < child.md",
        engine="codex",
        annotations={"resume_from_uuid": parent_uuid},
    )
    with patch("ghdag.dag.task_launcher.write_compaction_audit"):
        engine._launcher._apply_resume_if_available(child.uuid, child)

    assert child.annotations["resumed_session_id"] == (
        "01a0842c-88a5-7993-afd5-13562483ca9b"
    )
    assert "codex exec resume '01a0842c-88a5-7993-afd5-13562483ca9b'" in child.command
