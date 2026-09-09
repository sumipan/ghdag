"""退行検査: task_launcher が adapter 経由で stdout を処理することを保証する。

このテストが失敗した場合、adapter 配線が再度切断されていることを意味する。
"""

from __future__ import annotations

import io
import json
import time
from unittest.mock import MagicMock, patch

from ghdag.dag.engine import DagEngine
from ghdag.dag.hooks import DagHooks
from ghdag.dag.models import DagConfig, RunningTask, Task


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


_CLAUDE_JSON_STDOUT = json.dumps({
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "result": "Hello, world!",
    "usage": {"input_tokens": 100, "output_tokens": 50},
    "total_cost_usd": 0.00375,
    "cache_read_input_tokens": 20,
    "cache_creation_input_tokens": 5,
}).encode("utf-8")


@patch("ghdag.dag.task_launcher.state_mark_done")
def test_claude_json_result_path_contains_text_only(mock_mark_done, tmp_path):
    """claude JSON stdout → result_path に JSON ではなく 'result' テキストのみが書かれる。"""
    engine, hooks = _make_engine(tmp_path)
    result_file = tmp_path / "result.md"

    task = Task(
        uuid="test-claude-text",
        command="claude -p ...",
        engine="claude",
        result_path=str(result_file),
    )
    rt = RunningTask(
        uuid="test-claude-text",
        task=task,
        proc=_make_proc(0),
        started_at=time.time() - 1.0,
        started_at_mono=time.monotonic() - 1.0,
        stderr_buf=io.BytesIO(b""),
        stdout_buf=io.BytesIO(_CLAUDE_JSON_STDOUT),
    )
    engine._launcher._running["test-claude-text"] = rt
    engine._launcher.check_completions()

    content = result_file.read_text(encoding="utf-8")
    assert content == "Hello, world!"
    assert '"type"' not in content
    assert "is_error" not in content
    assert "total_cost_usd" not in content


@patch("ghdag.dag.task_launcher.state_mark_done")
def test_claude_json_metrics_are_non_null(mock_mark_done, tmp_path):
    """claude engine → TaskMetrics の token_count / cost_usd / cache 各フィールドが非 null。"""
    engine, hooks = _make_engine(tmp_path)
    result_file = tmp_path / "result.md"

    task = Task(
        uuid="test-claude-metrics",
        command="claude -p ...",
        engine="claude",
        result_path=str(result_file),
    )
    rt = RunningTask(
        uuid="test-claude-metrics",
        task=task,
        proc=_make_proc(0),
        started_at=time.time() - 1.0,
        started_at_mono=time.monotonic() - 1.0,
        stderr_buf=io.BytesIO(b""),
        stdout_buf=io.BytesIO(_CLAUDE_JSON_STDOUT),
    )
    engine._launcher._running["test-claude-metrics"] = rt
    engine._launcher.check_completions()

    hooks.on_task_success.assert_called_once()
    metrics = hooks.on_task_success.call_args[0][2]
    assert metrics.token_count == 150  # input(100) + output(50)
    assert metrics.cost_usd == 0.00375
    assert metrics.cache_read_tokens == 20
    assert metrics.cache_creation_tokens == 5


@patch("ghdag.dag.task_launcher.state_mark_done")
def test_cursor_plain_text_result_path_unchanged(mock_mark_done, tmp_path):
    """cursor engine → result_path に stdout がそのまま書かれる（互換維持）。"""
    engine, hooks = _make_engine(tmp_path)
    result_file = tmp_path / "result.md"
    plain_text = b"This is a plain text result."

    task = Task(
        uuid="test-cursor-text",
        command="cursor ...",
        engine="cursor",
        result_path=str(result_file),
    )
    rt = RunningTask(
        uuid="test-cursor-text",
        task=task,
        proc=_make_proc(0),
        started_at=time.time() - 1.0,
        started_at_mono=time.monotonic() - 1.0,
        stderr_buf=io.BytesIO(b""),
        stdout_buf=io.BytesIO(plain_text),
    )
    engine._launcher._running["test-cursor-text"] = rt
    engine._launcher.check_completions()

    assert result_file.read_bytes() == plain_text


@patch("ghdag.dag.task_launcher.state_mark_done")
def test_cursor_token_count_is_none(mock_mark_done, tmp_path):
    """cursor engine → 非 JSON stdout では TaskMetrics.token_count が None。"""
    engine, hooks = _make_engine(tmp_path)
    result_file = tmp_path / "result.md"

    task = Task(
        uuid="test-cursor-none",
        command="cursor ...",
        engine="cursor",
        result_path=str(result_file),
    )
    rt = RunningTask(
        uuid="test-cursor-none",
        task=task,
        proc=_make_proc(0),
        started_at=time.time() - 1.0,
        started_at_mono=time.monotonic() - 1.0,
        stderr_buf=io.BytesIO(b""),
        stdout_buf=io.BytesIO(b"some output"),
    )
    engine._launcher._running["test-cursor-none"] = rt
    engine._launcher.check_completions()

    hooks.on_task_success.assert_called_once()
    metrics = hooks.on_task_success.call_args[0][2]
    assert metrics.token_count is None


_CURSOR_JSON_STDOUT = (
    b'{"type":"result","subtype":"success","is_error":false,'
    b'"result":"pong",'
    b'"session_id":"85105031-11df-48a2-a791-812a0128b4cf",'
    b'"usage":{"inputTokens":7144,"outputTokens":73}}'
)

_CODEX_JSONL_STDOUT = (
    b'{"type":"thread.started","thread_id":"01a0842c-88a5-7993-afd5-13562483ca9b"}\n'
    b'{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"pong"}}\n'
    b'{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":1,'
    b'"cached_input_tokens":0,"cache_write_input_tokens":0}}\n'
)


@patch("ghdag.dag.task_launcher.state_mark_done")
def test_cursor_json_writes_result_text_and_session(mock_mark_done, tmp_path):
    """cursor JSON stdout → result は pong、SessionStore に session_id を記録する。"""
    engine, hooks = _make_engine(tmp_path)
    result_file = tmp_path / "result.md"
    parent_uuid = "test-cursor-json-parent"

    task = Task(
        uuid=parent_uuid,
        command="agent -p --force --output-format json < order.md",
        engine="cursor",
        result_path=str(result_file),
    )
    rt = RunningTask(
        uuid=parent_uuid,
        task=task,
        proc=_make_proc(0),
        started_at=time.time() - 1.0,
        started_at_mono=time.monotonic() - 1.0,
        stderr_buf=io.BytesIO(b""),
        stdout_buf=io.BytesIO(_CURSOR_JSON_STDOUT),
    )
    engine._launcher._running[parent_uuid] = rt
    engine._launcher.check_completions()

    assert result_file.read_text(encoding="utf-8") == "pong"
    assert '"type"' not in result_file.read_text(encoding="utf-8")
    recorded = engine._launcher._session_store.lookup(parent_uuid)
    assert recorded is not None
    assert recorded.engine == "cursor"
    assert recorded.session_id == "85105031-11df-48a2-a791-812a0128b4cf"
    hooks.on_task_success.assert_called_once()
    metrics = hooks.on_task_success.call_args[0][2]
    assert metrics.token_count == 7217

    child = Task(
        uuid="test-cursor-json-child",
        command="agent -p --force --output-format json < child.md",
        engine="cursor",
        annotations={"resume_from_uuid": parent_uuid},
    )
    with patch("ghdag.dag.task_launcher.write_compaction_audit"):
        engine._launcher._apply_resume_if_available(child.uuid, child)

    assert child.annotations["resumed_session_id"] == (
        "85105031-11df-48a2-a791-812a0128b4cf"
    )
    assert "--resume '85105031-11df-48a2-a791-812a0128b4cf'" in child.command


@patch("ghdag.dag.task_launcher.state_mark_done")
def test_codex_jsonl_writes_result_text_and_session(mock_mark_done, tmp_path):
    """codex JSONL stdout → result は pong、SessionStore に thread_id を記録する。"""
    engine, hooks = _make_engine(tmp_path)
    result_file = tmp_path / "result.md"
    parent_uuid = "test-codex-jsonl-parent"

    task = Task(
        uuid=parent_uuid,
        command="codex exec - --json --skip-git-repo-check < order.md",
        engine="codex",
        result_path=str(result_file),
    )
    rt = RunningTask(
        uuid=parent_uuid,
        task=task,
        proc=_make_proc(0),
        started_at=time.time() - 1.0,
        started_at_mono=time.monotonic() - 1.0,
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
