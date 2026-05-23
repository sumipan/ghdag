"""Unit tests for dag/fanout.py — parse_fanout_spec, build_child_* functions."""

from __future__ import annotations

import json
import logging

import pytest

from ghdag.dag.fanout import (
    FanOutItem,
    FanOutSpec,
    build_child_exec_line,
    build_child_jsonl_record,
    parse_fanout_spec,
)

VALID_FANOUT_YAML = """\
Some prior content.

---
ghdag_fanout:
  children:
    - id: item-001
      command: "bash -c 'process foo'"
    - id: item-002
      command: "bash -c 'process bar'"
    - id: item-003
      command: "bash -c 'process baz'"
"""


class TestParseFanoutSpec:
    def test_none_path_returns_none(self):
        assert parse_fanout_spec(None) is None

    def test_nonexistent_file_returns_none(self):
        assert parse_fanout_spec("/tmp/__nonexistent_ghdag_test__.md") is None

    def test_ac1_three_children(self, tmp_path):
        f = tmp_path / "result.md"
        f.write_text(VALID_FANOUT_YAML)
        spec = parse_fanout_spec(str(f))
        assert spec is not None
        assert len(spec.children) == 3
        assert spec.children[0].id == "item-001"
        assert spec.children[0].command == "bash -c 'process foo'"
        assert spec.children[1].id == "item-002"
        assert spec.children[2].id == "item-003"

    def test_no_separator_returns_none(self, tmp_path):
        f = tmp_path / "result.md"
        f.write_text("Just normal output\nNo YAML here\n")
        assert parse_fanout_spec(str(f)) is None

    def test_separator_without_fanout_key_returns_none(self, tmp_path):
        f = tmp_path / "result.md"
        f.write_text("content\n---\nsome_other_key: value\n")
        assert parse_fanout_spec(str(f)) is None

    def test_ac5_invalid_yaml_returns_none_and_warns(self, tmp_path, caplog):
        f = tmp_path / "result.md"
        f.write_text("content\n---\nghdag_fanout: [invalid: yaml: {\n")
        with caplog.at_level(logging.WARNING, logger="ghdag.dag.fanout"):
            result = parse_fanout_spec(str(f))
        assert result is None
        assert any("invalid" in r.message.lower() or "yaml" in r.message.lower() for r in caplog.records)

    def test_ac6_empty_children_returns_none(self, tmp_path):
        f = tmp_path / "result.md"
        f.write_text("content\n---\nghdag_fanout:\n  children: []\n")
        assert parse_fanout_spec(str(f)) is None

    def test_ac7_duplicate_ids_raises(self, tmp_path):
        f = tmp_path / "result.md"
        f.write_text(
            "---\nghdag_fanout:\n  children:\n"
            "    - id: dup\n      command: echo 1\n"
            "    - id: dup\n      command: echo 2\n"
        )
        with pytest.raises(ValueError, match="[Dd]uplicate"):
            parse_fanout_spec(str(f))

    def test_uses_last_separator(self, tmp_path):
        """Only the content after the LAST '---' is parsed."""
        f = tmp_path / "result.md"
        f.write_text(
            "---\nnot_fanout: true\n\n"
            "---\nghdag_fanout:\n  children:\n"
            "    - id: only-one\n      command: echo 1\n"
        )
        spec = parse_fanout_spec(str(f))
        assert spec is not None
        assert len(spec.children) == 1
        assert spec.children[0].id == "only-one"

    def test_children_missing_field_returns_none(self, tmp_path, caplog):
        """Children without required fields should be handled gracefully."""
        f = tmp_path / "result.md"
        f.write_text("---\nghdag_fanout:\n  children:\n    - id: x\n")
        with caplog.at_level(logging.WARNING, logger="ghdag.dag.fanout"):
            result = parse_fanout_spec(str(f))
        assert result is None


class TestBuildChildExecLine:
    def test_format(self):
        line = build_child_exec_line("parent--fo--item-001", "bash -c 'process foo'")
        assert line == "parent--fo--item-001: bash -c 'process foo'"

    def test_uuid_derivation_pattern(self):
        parent = "inv-20260523-abc"
        line = build_child_exec_line(f"{parent}--fo--item-001", "echo 1")
        assert line.startswith(f"{parent}--fo--item-001:")


class TestBuildChildJsonlRecord:
    def test_format(self):
        line = build_child_jsonl_record("parent--fo--item-001", "bash -c 'process foo'")
        data = json.loads(line)
        assert data["uuid"] == "parent--fo--item-001"
        assert data["command"] == "bash -c 'process foo'"

    def test_is_valid_json(self):
        line = build_child_jsonl_record("x--fo--y", "echo hello")
        parsed = json.loads(line)
        assert isinstance(parsed, dict)
