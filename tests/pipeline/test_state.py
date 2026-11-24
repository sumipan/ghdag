"""Tests for pipeline/state.py — append_exec audit integration (Issue #756)."""

from __future__ import annotations

import json

import pytest

from ghdag.pipeline.task_queue import TaskQueue

UUID1 = "38d6b791-1072-42f0-838d-45c7d10748ff"
UUID2 = "aaaabbbb-cccc-dddd-eeee-ffffffffffff"


@pytest.fixture
def pipeline(tmp_path):
    exec_jsonl = tmp_path / "exec.jsonl"
    exec_jsonl.write_text("", encoding="utf-8")
    return TaskQueue(state_dir=tmp_path / "state", exec_jsonl_path=exec_jsonl)


@pytest.fixture
def pipeline_jsonl(tmp_path):
    exec_jsonl = tmp_path / "exec.jsonl"
    exec_jsonl.write_text("", encoding="utf-8")
    return TaskQueue(state_dir=tmp_path / "state", exec_jsonl_path=exec_jsonl)


class TestWriteOrderFileFooter:
    def test_write_order_with_footer_appends_dag_section(self, tmp_path) -> None:
        queue_dir = tmp_path / "jobs"
        queue_dir.mkdir()
        state = TaskQueue(state_dir=tmp_path / "state", exec_jsonl_path=tmp_path / "exec.jsonl")
        order_uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        ts = "20260509120000"
        engine = "cursor"

        def order_footer(received_ts: str, received_uuid: str, received_engine: str) -> str:
            return f"\n<!-- footer:{received_ts}:{received_uuid}:{received_engine} -->\n"

        filename = state.write_order(
            ts,
            order_uuid,
            "order body",
            str(queue_dir),
            engine=engine,
            order_footer_fn=order_footer,
        )

        expected_footer = order_footer(ts, order_uuid, engine)
        content = (queue_dir / filename).read_text(encoding="utf-8")
        assert content == "order body" + expected_footer


@pytest.fixture
def jsonl_pipeline(tmp_path):
    exec_jsonl = tmp_path / "exec.jsonl"
    exec_jsonl.write_text("", encoding="utf-8")
    return TaskQueue(state_dir=tmp_path / "state", exec_jsonl_path=exec_jsonl)


# ---------------------------------------------------------------------------
# JSONL mode: check_idempotency
# ---------------------------------------------------------------------------

class TestCheckIdempotencyJsonlMode:

    def test_key_absent_returns_true(self, pipeline_jsonl):
        """Returns True when the idempotency_key is absent."""
        record = json.dumps({"uuid": UUID1, "command": "cmd"})
        pipeline_jsonl.exec_jsonl_path.write_text(record + "\n", encoding="utf-8")

        assert pipeline_jsonl.check_idempotency("scheduler:new-job:ts") is True

    def test_empty_file_returns_true(self, pipeline_jsonl):
        """Returns True for an empty file."""
        assert pipeline_jsonl.check_idempotency("any-key") is True

    def test_invalid_json_line_skipped(self, pipeline_jsonl):
        """JSON parse failures are skipped; only valid lines are evaluated (A1-2)."""
        key = "scheduler:diary_review:2026-05-08T23:00:00+09:00"
        valid_record = json.dumps({"uuid": UUID1, "command": "cmd", "idempotency_key": key})
        pipeline_jsonl.exec_jsonl_path.write_text(
            "this-is-broken-json\n"
            + valid_record + "\n",
            encoding="utf-8",
        )
        assert pipeline_jsonl.check_idempotency(key) is False


# ---------------------------------------------------------------------------
# JSONL mode: remove_idempotency_matching (AC2)
# ---------------------------------------------------------------------------


class TestRemoveIdempotencyMatchingJsonl:
    @pytest.fixture
    def jsonl_state(self, tmp_path):
        exec_jsonl = tmp_path / "exec.jsonl"
        exec_jsonl.write_text("", encoding="utf-8")
        return TaskQueue(state_dir=tmp_path / "state", exec_jsonl_path=exec_jsonl)

    def test_file_not_exists_returns_zero(self, tmp_path):
        """AC2-4: missing file → returns 0, no error."""
        state = TaskQueue(
            state_dir=tmp_path / "state",
            exec_jsonl_path=tmp_path / "nonexistent.jsonl",
        )
        assert state.remove_idempotency_matching("wf", 42) == 0

    def test_empty_lines_preserved(self, jsonl_state):
        """AC2-5: blank lines are preserved."""
        jsonl_state.exec_jsonl_path.write_text(
            json.dumps({"uuid": UUID1, "idempotency_key": "wf:handler_a:42"}) + "\n"
            + "\n"
            + json.dumps({"uuid": UUID2, "command": "cmd"}) + "\n",
            encoding="utf-8",
        )
        jsonl_state.remove_idempotency_matching("wf", 42)
        remaining = jsonl_state.exec_jsonl_path.read_text()
        assert "" in remaining.splitlines()
        assert "handler_a" not in remaining
        assert UUID2 in remaining


# ---------------------------------------------------------------------------
# JSONL mode: remove_idempotency_for_handler (Issue #2258)
# ---------------------------------------------------------------------------


class TestRemoveIdempotencyForHandler:
    @pytest.fixture
    def state(self, tmp_path):
        exec_jsonl = tmp_path / "exec.jsonl"
        exec_jsonl.write_text("", encoding="utf-8")
        return TaskQueue(state_dir=tmp_path / "state", exec_jsonl_path=exec_jsonl)

    def test_returns_count_for_multiple_matches(self, state):
        """Two records with the same key → returns 2."""
        state.exec_jsonl_path.write_text(
            json.dumps({"uuid": UUID1, "idempotency_key": "wf:handler_a:42"}) + "\n"
            + json.dumps({"uuid": UUID2, "idempotency_key": "wf:handler_a:42"}) + "\n",
            encoding="utf-8",
        )
        removed = state.remove_idempotency_for_handler("wf", "handler_a", 42)
        assert removed == 2

    def test_no_match_returns_zero_and_file_unchanged(self, state):
        """No match → returns 0, file content unchanged."""
        original = json.dumps({"uuid": UUID1, "idempotency_key": "wf:handler_b:42"}) + "\n"
        state.exec_jsonl_path.write_text(original, encoding="utf-8")
        removed = state.remove_idempotency_for_handler("wf", "handler_a", 42)
        assert removed == 0
        assert state.exec_jsonl_path.read_text() == original
