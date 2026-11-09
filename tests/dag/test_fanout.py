"""Unit tests for dag/fanout.py — parse_fanout_spec, build_child_* functions."""

from __future__ import annotations

import json

import pytest

from ghdag.dag.fanout import (
    FanoutError,
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

    def test_parse_fanout_spec_returns_three_children(self, tmp_path):
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

    def test_invalid_yaml_after_anchor_raises(self, tmp_path):
        f = tmp_path / "result.md"
        f.write_text("---\nghdag_fanout:\n  children: [\n")
        with pytest.raises(FanoutError, match="YAML"):
            parse_fanout_spec(str(f))

    def test_empty_children_list_returns_none(self, tmp_path):
        f = tmp_path / "result.md"
        f.write_text("content\n---\nghdag_fanout:\n  children: []\n")
        assert parse_fanout_spec(str(f)) is None

    def test_duplicate_child_ids_raises_fanout_error(self, tmp_path):
        f = tmp_path / "result.md"
        f.write_text(
            "---\nghdag_fanout:\n  children:\n"
            "    - id: dup\n      command: echo 1\n"
            "    - id: dup\n      command: echo 2\n"
        )
        with pytest.raises(FanoutError, match="[Dd]uplicate") as exc_info:
            parse_fanout_spec(str(f))
        from ghdag.exceptions import ConfigError

        err = exc_info.value
        assert isinstance(err, ConfigError)
        assert err.code == "FANOUT_INVALID"
        assert err.hint is not None

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

    def test_child_missing_command_raises(self, tmp_path):
        f = tmp_path / "result.md"
        f.write_text("---\nghdag_fanout:\n  children:\n    - id: x\n")
        with pytest.raises(FanoutError, match="command"):
            parse_fanout_spec(str(f))

    def test_t2_child_id_contains_fo_separator_raises(self, tmp_path):
        """T2: child.id = 'foo--fo--bar' → ValueError (contains reserved separator)."""
        f = tmp_path / "result.md"
        f.write_text(
            "---\nghdag_fanout:\n  children:\n"
            "    - id: foo--fo--bar\n      command: echo 1\n"
        )
        with pytest.raises(FanoutError, match="--fo--"):
            parse_fanout_spec(str(f))

    def test_t3_child_id_leading_fo_separator_raises(self, tmp_path):
        """T3: child.id = '--fo--leading' → ValueError (leading separator)."""
        f = tmp_path / "result.md"
        f.write_text(
            "---\nghdag_fanout:\n  children:\n"
            "    - id: \"--fo--leading\"\n      command: echo 1\n"
        )
        with pytest.raises(FanoutError, match="--fo--"):
            parse_fanout_spec(str(f))

    def test_t4_child_id_trailing_fo_separator_raises(self, tmp_path):
        """T4: child.id = 'trailing--fo--' → ValueError (trailing separator)."""
        f = tmp_path / "result.md"
        f.write_text(
            "---\nghdag_fanout:\n  children:\n"
            "    - id: \"trailing--fo--\"\n      command: echo 1\n"
        )
        with pytest.raises(FanoutError, match="--fo--"):
            parse_fanout_spec(str(f))

    def test_t5_child_id_is_separator_itself_raises(self, tmp_path):
        """T5: child.id = '--fo--' → ValueError (separator itself)."""
        f = tmp_path / "result.md"
        f.write_text(
            "---\nghdag_fanout:\n  children:\n"
            "    - id: \"--fo--\"\n      command: echo 1\n"
        )
        with pytest.raises(FanoutError, match="--fo--"):
            parse_fanout_spec(str(f))

    def test_markdown_hr_without_fanout_key_returns_none(self, tmp_path):
        """AC3: --- in Claude output without ghdag_fanout: → None (no warning)."""
        f = tmp_path / "result.md"
        f.write_text("Implementation complete.\n\n---\n\n**Changes:**\nPR created")
        assert parse_fanout_spec(str(f)) is None

    def test_fanout_after_second_separator_is_parsed(self, tmp_path):
        """AC4: --- + markdown + --- + ghdag_fanout: → correct extraction."""
        f = tmp_path / "result.md"
        f.write_text(
            "text\n\n---\n\nMore text.\n\n---\nghdag_fanout:\n"
            "  children:\n    - id: x\n      command: echo 1\n"
        )
        spec = parse_fanout_spec(str(f))
        assert spec is not None
        assert len(spec.children) == 1
        assert spec.children[0].id == "x"

    def test_anchor_without_separator_raises(self, tmp_path):
        f = tmp_path / "result.md"
        f.write_text(
            "ghdag_fanout:\n"
            "  children:\n"
            "    - id: x\n      command: echo 1\n"
        )
        with pytest.raises(FanoutError, match="---"):
            parse_fanout_spec(str(f))

    def test_other_yaml_keys_before_fanout_still_parses(self, tmp_path):
        """AC6: Other root-level YAML keys between --- and ghdag_fanout: → correct parse."""
        f = tmp_path / "result.md"
        f.write_text(
            "---\nmetadata: v1\nghdag_fanout:\n"
            "  children:\n    - id: x\n      command: echo 1\n"
        )
        spec = parse_fanout_spec(str(f))
        assert spec is not None
        assert len(spec.children) == 1
        assert spec.children[0].id == "x"

    def test_multiple_fanout_anchors_last_wins(self, tmp_path):
        """Multiple ghdag_fanout: anchors → last one (with --- before it) is used."""
        f = tmp_path / "result.md"
        f.write_text(
            "---\nghdag_fanout:\n"
            "  children:\n"
            "    - id: first-ignored\n      command: echo 0\n"
            "\n"
            "---\nghdag_fanout:\n"
            "  children:\n"
            "    - id: last-one\n      command: echo 1\n"
        )
        spec = parse_fanout_spec(str(f))
        assert spec is not None
        assert len(spec.children) == 1
        assert spec.children[0].id == "last-one"

    def test_indented_anchor_not_detected(self, tmp_path):
        """Indented '  ghdag_fanout:' is not detected as anchor → None."""
        f = tmp_path / "result.md"
        f.write_text(
            "---\n"
            "content\n"
            "  ghdag_fanout:\n"
            "    children:\n"
            "      - id: x\n        command: echo 1\n"
        )
        assert parse_fanout_spec(str(f)) is None

    def test_fanout_value_not_mapping_raises(self, tmp_path):
        f = tmp_path / "result.md"
        f.write_text("---\nghdag_fanout: plain-string-value\n")
        with pytest.raises(FanoutError, match="mapping"):
            parse_fanout_spec(str(f))

    def test_children_key_missing_raises(self, tmp_path):
        f = tmp_path / "result.md"
        f.write_text("---\nghdag_fanout:\n  other: 1\n")
        with pytest.raises(FanoutError, match="children"):
            parse_fanout_spec(str(f))

    def test_children_not_list_raises(self, tmp_path):
        f = tmp_path / "result.md"
        f.write_text(
            "---\nghdag_fanout:\n"
            "  children:\n"
            "    id: x\n"
            "    command: echo 1\n"
        )
        with pytest.raises(FanoutError, match="list"):
            parse_fanout_spec(str(f))

    def test_child_id_not_string_raises(self, tmp_path):
        f = tmp_path / "result.md"
        f.write_text(
            "---\nghdag_fanout:\n"
            "  children:\n"
            "    - id: 1\n"
            "      command: echo 1\n"
        )
        with pytest.raises(FanoutError, match="id"):
            parse_fanout_spec(str(f))


BLOCK_FANOUT_OK = """\
Done.

```ghdag
status: ok
children:
  - id: a
    prompt: do a
  - id: b
    prompt: do b
    depends: [a]
```
"""


class TestReplyBlockFanout:
    """Fan-out via reply block children (#6249)."""

    def test_block_children_parsed_with_depends(self, tmp_path):
        f = tmp_path / "result.md"
        f.write_text(BLOCK_FANOUT_OK)
        spec = parse_fanout_spec(str(f))
        assert spec is not None
        assert len(spec.children) == 2
        assert spec.children[1].depends == ("a",)

    def test_missing_prompt_and_command_raises(self, tmp_path):
        f = tmp_path / "result.md"
        f.write_text(
            "```ghdag\nstatus: ok\nchildren:\n  - id: a\n```\n",
        )
        with pytest.raises(FanoutError) as exc_info:
            parse_fanout_spec(str(f))
        assert exc_info.value.code == "FANOUT_INVALID"
        hint = exc_info.value.hint or ""
        assert "prompt" in hint and "command" in hint

    def test_prompt_and_command_both_raises(self, tmp_path):
        f = tmp_path / "result.md"
        f.write_text(
            "```ghdag\nstatus: ok\nchildren:\n"
            "  - id: a\n    prompt: x\n    command: y\n```\n",
        )
        with pytest.raises(FanoutError):
            parse_fanout_spec(str(f))

    def test_unknown_engine_hint_lists_engines(self, tmp_path):
        f = tmp_path / "result.md"
        f.write_text(
            "```ghdag\nstatus: ok\nchildren:\n"
            "  - id: a\n    prompt: x\n    engine: nope\n```\n",
        )
        with pytest.raises(FanoutError) as exc_info:
            parse_fanout_spec(str(f))
        assert "claude" in (exc_info.value.hint or "")

    def test_command_child_rejects_permission(self, tmp_path):
        f = tmp_path / "result.md"
        f.write_text(
            "```ghdag\nstatus: ok\nchildren:\n"
            "  - id: a\n    command: echo x\n    permission: text_only\n```\n",
        )
        with pytest.raises(FanoutError):
            parse_fanout_spec(str(f))

    def test_unknown_depends_id_in_hint(self, tmp_path):
        f = tmp_path / "result.md"
        f.write_text(
            "```ghdag\nstatus: ok\nchildren:\n"
            "  - id: a\n    prompt: x\n    depends: [zzz]\n```\n",
        )
        with pytest.raises(FanoutError) as exc_info:
            parse_fanout_spec(str(f))
        assert "zzz" in (exc_info.value.hint or "")

    def test_reply_block_without_children_does_not_fall_back_to_legacy(self, tmp_path):
        f = tmp_path / "result.md"
        f.write_text(
            "---\nghdag_fanout:\n  children:\n"
            "    - id: legacy\n      command: echo legacy\n"
            "```ghdag\nstatus: ok\n```\n",
        )

        assert parse_fanout_spec(str(f)) is None


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
