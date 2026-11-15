"""Tests for ghdag.dag.parser — §5.2 acceptance criteria."""

from ghdag.dag.models import Task
from ghdag.dag.parser import parse_jsonl, validate_dependencies


class TestParseJsonl:
    """JSONL parser test cases (P1–P9)"""

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


class TestValidateDependencies:
    """AC 3-1 ~ 3-5: validate_dependencies"""

    def _make_task(self, uuid: str, depends: list[str]) -> Task:
        return Task(uuid=uuid, command=f"echo {uuid}", depends=depends)

    def test_self_reference_detected(self):
        """3-5: detect self-reference (self-loop) as a cycle"""
        tasks = [self._make_task("uuid-a", ["uuid-a"])]
        result = validate_dependencies(tasks, done=set())
        assert result.get("uuid-a") == "cycle"
