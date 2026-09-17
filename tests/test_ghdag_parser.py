"""Tests for ghdag.dag.parser — §5.2 acceptance criteria."""

from pathlib import Path

import pytest

from ghdag.dag.models import Task
from ghdag.dag.parser import parse_jsonl, validate_dependencies


class TestParseJsonl:
    """JSONL parser test cases (P1–P9)"""

    def test_p1_normal_line(self):
        """P1: can parse a valid JSONL line"""
        text = '{"uuid":"a","command":"echo hi","depends":[]}\n'
        tasks = parse_jsonl(text)
        assert len(tasks) == 1
        assert tasks[0].uuid == "a"
        assert tasks[0].command == "echo hi"
        assert tasks[0].depends == []
        assert tasks[0].result_path is None
        assert tasks[0].idempotency_key is None

    def test_p2_result_path(self):
        """P2: can read result_path"""
        text = '{"uuid":"a","command":"echo hi","depends":[],"result_path":"out.md"}\n'
        tasks = parse_jsonl(text)
        assert len(tasks) == 1
        assert tasks[0].result_path == "out.md"

    def test_p3_idempotency_key(self):
        """P3: can read idempotency_key"""
        text = '{"uuid":"a","command":"echo hi","depends":[],"idempotency_key":"iss:b:1"}\n'
        tasks = parse_jsonl(text)
        assert len(tasks) == 1
        assert tasks[0].idempotency_key == "iss:b:1"

    def test_p4_invalid_json_skipped(self):
        """P4: skip invalid JSON lines"""
        text = 'not json\n{"uuid":"a","command":"echo hi","depends":[]}\n'
        tasks = parse_jsonl(text)
        assert len(tasks) == 1
        assert tasks[0].uuid == "a"

    def test_p5_empty_lines_skipped(self):
        """P5: skip empty lines"""
        text = '\n\n{"uuid":"a","command":"echo hi","depends":[]}\n'
        tasks = parse_jsonl(text)
        assert len(tasks) == 1
        assert tasks[0].uuid == "a"

    def test_p6_missing_uuid_skipped(self):
        """P6: skip lines missing required field (uuid)"""
        text = '{"command":"echo hi","depends":[]}\n{"uuid":"a","command":"echo hi","depends":[]}\n'
        tasks = parse_jsonl(text)
        assert len(tasks) == 1
        assert tasks[0].uuid == "a"

    def test_p7_missing_command_skipped(self):
        """P7: skip lines missing required field (command)"""
        text = '{"uuid":"a","depends":[]}\n{"uuid":"b","command":"echo hi","depends":[]}\n'
        tasks = parse_jsonl(text)
        assert len(tasks) == 1
        assert tasks[0].uuid == "b"

    def test_p8_duplicate_uuid_last_wins(self):
        """P8: duplicate uuid — last write wins"""
        text = '{"uuid":"a","command":"first","depends":[]}\n{"uuid":"a","command":"second","depends":[]}\n'
        tasks = parse_jsonl(text)
        assert len(tasks) == 1
        assert tasks[0].uuid == "a"
        assert tasks[0].command == "second"

    def test_p9_retry_and_annotations(self):
        """P9: can read retry / annotations"""
        text = '{"uuid":"a","command":"echo","depends":[],"retry":2,"annotations":{"model":"sonnet"}}\n'
        tasks = parse_jsonl(text)
        assert len(tasks) == 1
        assert tasks[0].retry == 2
        assert tasks[0].annotations == {"model": "sonnet"}

    def test_p10_engine_and_model_fields(self):
        """P10: when engine/model fields exist, they are set on Task (AC2)"""
        text = '{"uuid":"a","command":"claude -p \'x\'","engine":"claude","model":"claude-sonnet-4-6"}\n'
        tasks = parse_jsonl(text)
        assert len(tasks) == 1
        assert tasks[0].engine == "claude"
        assert tasks[0].model == "claude-sonnet-4-6"

    def test_p11_missing_engine_model_defaults_to_none(self):
        """P11: when engine/model fields are absent (legacy), Task.engine/model are None (compat) (AC2)"""
        text = '{"uuid":"b","command":"agent -p < order.md"}\n'
        tasks = parse_jsonl(text)
        assert len(tasks) == 1
        assert tasks[0].engine is None
        assert tasks[0].model is None

    def test_p12_engine_present_model_absent(self):
        """P12: when only engine exists and model is missing, model is None (AC2)"""
        text = '{"uuid":"c","command":"bash -o pipefail run.sh","engine":"shell"}\n'
        tasks = parse_jsonl(text)
        assert len(tasks) == 1
        assert tasks[0].engine == "shell"
        assert tasks[0].model is None


class TestValidateDependencies:
    """AC 3-1 ~ 3-5: validate_dependencies"""

    def _make_task(self, uuid: str, depends: list[str]) -> Task:
        return Task(uuid=uuid, command=f"echo {uuid}", depends=depends)

    def test_orphan_dep_detected(self):
        """3-1: detect orphan dependency (absent from both exec.jsonl and done)"""
        tasks = [self._make_task("uuid-b", ["uuid-x"])]
        result = validate_dependencies(tasks, done=set())
        assert result == {"uuid-b": "orphan_dep:uuid-x"}

    def test_mutual_cycle_detected(self):
        """3-2: detect mutual-dependency cycle"""
        tasks = [
            self._make_task("uuid-a", ["uuid-b"]),
            self._make_task("uuid-b", ["uuid-a"]),
        ]
        result = validate_dependencies(tasks, done=set())
        assert result.get("uuid-a") == "cycle"
        assert result.get("uuid-b") == "cycle"

    def test_done_dep_is_not_orphan(self):
        """3-3: dependency present in jobs/done/ is not an orphan"""
        tasks = [self._make_task("uuid-b", ["uuid-a"])]
        result = validate_dependencies(tasks, done={"uuid-a"})
        assert result == {}

    def test_normal_linear_graph(self):
        """3-4: valid linear dependency graph returns empty dict"""
        tasks = [
            self._make_task("uuid-a", []),
            self._make_task("uuid-b", ["uuid-a"]),
        ]
        result = validate_dependencies(tasks, done=set())
        assert result == {}

    def test_self_reference_detected(self):
        """3-5: detect self-reference (self-loop) as a cycle"""
        tasks = [self._make_task("uuid-a", ["uuid-a"])]
        result = validate_dependencies(tasks, done=set())
        assert result.get("uuid-a") == "cycle"
