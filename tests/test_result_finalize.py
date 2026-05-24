"""Tests for result_finalize policy (AC1-AC3 from issue #1140)."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from unittest.mock import MagicMock


from ghdag.dag.engine import DagEngine
from ghdag.dag.models import DagConfig
from ghdag.dag.parser import parse_jsonl


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(tmp_path: Path, records: list[dict], **overrides) -> DagConfig:
    exec_jsonl = tmp_path / "exec.jsonl"
    exec_jsonl.write_text(
        "\n".join(json.dumps(r) for r in records) + "\n",
        encoding="utf-8",
    )
    defaults = dict(
        exec_jsonl_path=str(exec_jsonl),
        exec_done_dir=str(tmp_path / "jobs" / "done"),
        poll_interval=0.1,
        launch_stagger=0.0,
        lock_file=str(tmp_path / "lock"),
    )
    defaults.update(overrides)
    return DagConfig(**defaults)


def _run_engine(engine: DagEngine, timeout: float = 5.0) -> None:
    t = threading.Thread(target=engine.run, daemon=True)
    t.start()
    t.join(timeout=timeout)
    engine._shutdown = True
    t.join(timeout=2.0)


def _make_hooks(*, rejected: bool = False) -> MagicMock:
    hooks = MagicMock()
    hooks.check_rejected.return_value = rejected
    hooks.check_pipeline_status.return_value = None
    hooks.check_promote_target.return_value = None
    return hooks


# ---------------------------------------------------------------------------
# AC1: result_finalize ポリシー分岐テスト
# ---------------------------------------------------------------------------

class TestResultFinalizePolicy:
    """AC1: preserve_nonempty / stdout_only の動作確認"""

    def test_preserve_nonempty_keeps_existing_content(self, tmp_path):
        """preserve_nonempty + 非空ファイル → stdout を捨ててファイルを維持する"""
        result_path = tmp_path / "result.md"
        long_content = "X" * 500
        result_path.write_text(long_content, encoding="utf-8")

        config = _make_config(tmp_path, [
            {
                "uuid": "uuid-a",
                "command": "echo '完了しました'",
                "depends": [],
                "result_path": str(result_path),
                "retry": 0,
                "annotations": {},
                "result_finalize": "preserve_nonempty",
            }
        ])
        hooks = _make_hooks()
        engine = DagEngine(config, hooks)
        _run_engine(engine, timeout=5.0)

        assert result_path.read_text(encoding="utf-8") == long_content

    def test_preserve_nonempty_empty_file_writes_stdout(self, tmp_path):
        """preserve_nonempty + 空ファイル → stdout で書き込む"""
        result_path = tmp_path / "result.md"
        result_path.write_text("", encoding="utf-8")

        config = _make_config(tmp_path, [
            {
                "uuid": "uuid-a",
                "command": "printf '分析結果...'",
                "depends": [],
                "result_path": str(result_path),
                "retry": 0,
                "annotations": {},
                "result_finalize": "preserve_nonempty",
            }
        ])
        hooks = _make_hooks()
        engine = DagEngine(config, hooks)
        _run_engine(engine, timeout=5.0)

        content = result_path.read_text(encoding="utf-8")
        assert "分析結果..." in content

    def test_preserve_nonempty_missing_file_writes_stdout(self, tmp_path):
        """preserve_nonempty + ファイルなし → stdout でファイルを作成する"""
        result_path = tmp_path / "result.md"
        assert not result_path.exists()

        config = _make_config(tmp_path, [
            {
                "uuid": "uuid-a",
                "command": "printf '分析結果...'",
                "depends": [],
                "result_path": str(result_path),
                "retry": 0,
                "annotations": {},
                "result_finalize": "preserve_nonempty",
            }
        ])
        hooks = _make_hooks()
        engine = DagEngine(config, hooks)
        _run_engine(engine, timeout=5.0)

        assert result_path.exists()
        content = result_path.read_text(encoding="utf-8")
        assert "分析結果..." in content

    def test_stdout_only_overwrites_existing_content(self, tmp_path):
        """stdout_only + 非空ファイル → stdout で上書きする"""
        result_path = tmp_path / "result.md"
        result_path.write_text("X" * 500, encoding="utf-8")

        config = _make_config(tmp_path, [
            {
                "uuid": "uuid-a",
                "command": "printf '新結果'",
                "depends": [],
                "result_path": str(result_path),
                "retry": 0,
                "annotations": {},
                "result_finalize": "stdout_only",
            }
        ])
        hooks = _make_hooks()
        engine = DagEngine(config, hooks)
        _run_engine(engine, timeout=5.0)

        content = result_path.read_text(encoding="utf-8")
        assert content == "新結果"

    def test_result_finalize_none_defaults_to_preserve_nonempty(self, tmp_path):
        """result_finalize 未指定（None）→ preserve_nonempty と同じ動作"""
        result_path = tmp_path / "result.md"
        long_content = "Y" * 500
        result_path.write_text(long_content, encoding="utf-8")

        config = _make_config(tmp_path, [
            {
                "uuid": "uuid-a",
                "command": "echo '完了'",
                "depends": [],
                "result_path": str(result_path),
                "retry": 0,
                "annotations": {},
                # result_finalize を省略
            }
        ])
        hooks = _make_hooks()
        engine = DagEngine(config, hooks)
        _run_engine(engine, timeout=5.0)

        assert result_path.read_text(encoding="utf-8") == long_content


# ---------------------------------------------------------------------------
# AC2: parser が result_finalize をパースできる
# ---------------------------------------------------------------------------

class TestParserResultFinalize:
    """AC2: JSONL パーサが result_finalize フィールドを読み取れる"""

    def test_parse_stdout_only(self):
        """result_finalize: "stdout_only" をパースできる"""
        text = '{"uuid":"x","command":"echo hi","result_finalize":"stdout_only"}\n'
        tasks = parse_jsonl(text)
        assert len(tasks) == 1
        assert tasks[0].result_finalize == "stdout_only"

    def test_parse_preserve_nonempty(self):
        """result_finalize: "preserve_nonempty" をパースできる"""
        text = '{"uuid":"x","command":"echo hi","result_finalize":"preserve_nonempty"}\n'
        tasks = parse_jsonl(text)
        assert len(tasks) == 1
        assert tasks[0].result_finalize == "preserve_nonempty"

    def test_parse_missing_field_is_none(self):
        """result_finalize キーなし → task.result_finalize is None"""
        text = '{"uuid":"x","command":"echo hi"}\n'
        tasks = parse_jsonl(text)
        assert len(tasks) == 1
        assert tasks[0].result_finalize is None


# ---------------------------------------------------------------------------
# AC3: リトライ時の result_path 削除
# ---------------------------------------------------------------------------

class TestRetryResultPathCleanup:
    """AC3: rejected タスクがリトライされる前に result_path が削除される"""

    def test_retry_clears_result_path_before_callback(self, tmp_path):
        """rejected (is_final=False) 時に result_path が削除される"""
        result_path = tmp_path / "result.md"
        result_path.write_text("REJECTED: 理由\n古いコンテンツ", encoding="utf-8")

        result_path_at_callback: list[bool] = []

        def on_rejected(uuid, task, retry_depth, is_final, metrics):
            result_path_at_callback.append(result_path.exists())

        config = _make_config(tmp_path, [
            {
                "uuid": "uuid-a",
                "command": "printf 'REJECTED: 理由'",
                "depends": [],
                "result_path": str(result_path),
                "retry": 0,
                "annotations": {},
            }
        ], max_retry=1)

        hooks = _make_hooks(rejected=True)
        hooks.on_task_rejected.side_effect = on_rejected
        engine = DagEngine(config, hooks)
        _run_engine(engine, timeout=5.0)

        hooks.on_task_rejected.assert_called_once()
        _, kwargs_call = hooks.on_task_rejected.call_args
        args_call = hooks.on_task_rejected.call_args[0]
        # is_final=False のはず（retry_depth=0 < max_retry=1）
        assert args_call[3] is False  # is_final
        # コールバック時点で result_path が削除されているはず
        assert result_path_at_callback == [False]

    def test_final_rejected_does_not_clear_result_path(self, tmp_path):
        """REJECTED_FINAL 時は result_path を削除しない"""
        result_path = tmp_path / "result.md"
        original_content = "REJECTED: 最終\n古いコンテンツ"
        result_path.write_text(original_content, encoding="utf-8")

        result_path_at_callback: list[bool] = []

        def on_rejected(uuid, task, retry_depth, is_final, metrics):
            result_path_at_callback.append(result_path.exists())

        config = _make_config(tmp_path, [
            {
                "uuid": "uuid-a",
                "command": "printf 'REJECTED: 最終'",
                "depends": [],
                "result_path": str(result_path),
                "retry": 1,  # すでにリトライ済み
                "annotations": {},
            }
        ], max_retry=1)

        hooks = _make_hooks(rejected=True)
        hooks.on_task_rejected.side_effect = on_rejected
        engine = DagEngine(config, hooks)
        _run_engine(engine, timeout=5.0)

        hooks.on_task_rejected.assert_called_once()
        # REJECTED_FINAL なのでファイルは残っているはず
        assert result_path_at_callback == [True]
