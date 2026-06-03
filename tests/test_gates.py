"""Tests for ghdag.workflow.gates (Protocol, Registry, common, preflight CLI)."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from ghdag.workflow.gates import GATE_REGISTRY, Violation
from ghdag.workflow.gates.common import strip_code_regions

# --- Violation dataclass ---

def test_violation_instantiation():
    v = Violation(
        rule_id="test.rule",
        severity="fail",
        message="msg",
        location=None,
        auto_fixable=False,
        fix_hint=None,
    )
    assert v.rule_id == "test.rule"
    assert v.severity == "fail"
    assert v.message == "msg"
    assert v.location is None
    assert v.auto_fixable is False
    assert v.fix_hint is None


def test_violation_with_all_fields():
    v = Violation(
        rule_id="cp1.forbidden_word.tbd",
        severity="warn",
        message="TBD が残存",
        location="line 5",
        auto_fixable=True,
        fix_hint="具体的な方針に置き換えてください",
    )
    assert v.rule_id == "cp1.forbidden_word.tbd"
    assert v.severity == "warn"
    assert v.location == "line 5"
    assert v.auto_fixable is True
    assert v.fix_hint == "具体的な方針に置き換えてください"


# --- GATE_REGISTRY ---

def test_gate_registry_is_dict():
    assert isinstance(GATE_REGISTRY, dict)


def test_gate_registry_starts_empty_no_concrete_rules():
    # ghdag 版は具体ルールを auto-import しない。issuesmith 側が登録する。
    # GATE_REGISTRY が dict であることと、Protocol 準拠ルールを手動登録できることを確認。
    key = "_test_empty_check"
    assert key not in GATE_REGISTRY


def test_gate_registry_can_register_custom_rule():
    class MyRule:
        def check(self, body: str, labels: list[str]) -> list[Violation]:
            return []

    GATE_REGISTRY["_test_custom"] = MyRule
    try:
        assert "_test_custom" in GATE_REGISTRY
        rule = GATE_REGISTRY["_test_custom"]()
        result = rule.check("body", [])
        assert result == []
    finally:
        GATE_REGISTRY.pop("_test_custom", None)


def test_gate_rule_protocol_compliance():
    class DummyRule:
        def check(self, body: str, labels: list[str]) -> list[Violation]:
            return []

    rule = DummyRule()
    result = rule.check("body text", ["label1"])
    assert result == []


# --- strip_code_regions ---

def test_strip_fence_code_block():
    body = "before\n```python\ncode here\n```\nafter"
    result = strip_code_regions(body)
    assert "code here" not in result
    assert "before" in result
    assert "after" in result


def test_strip_inline_code():
    body = "text with `inline code` here"
    result = strip_code_regions(body)
    assert "inline code" not in result
    assert "text with" in result
    assert "here" in result


def test_no_code_regions_returns_input():
    body = "plain text without any code"
    result = strip_code_regions(body)
    assert result == body


def test_strip_multiple_fence_blocks():
    body = "a\n```\nblock1\n```\nb\n```\nblock2\n```\nc"
    result = strip_code_regions(body)
    assert "block1" not in result
    assert "block2" not in result
    assert "a" in result
    assert "b" in result
    assert "c" in result


def test_strip_multiple_inline_codes():
    body = "has `foo` and `bar` here"
    result = strip_code_regions(body)
    assert "foo" not in result
    assert "bar" not in result


def test_strip_code_regions_empty_string():
    assert strip_code_regions("") == ""


def test_strip_code_regions_acceptance_criteria():
    assert strip_code_regions("text `code` more") == "text  more"


# --- preflight CLI ---

def run_preflight_subprocess(*args):
    return subprocess.run(
        [sys.executable, "-m", "ghdag.workflow.gates", *args],
        capture_output=True,
        text=True,
        env={**__import__("os").environ, "PYTHONPATH": "src"},
        cwd=str(Path(__file__).parent.parent),
    )


def run_preflight_direct(argv):
    from ghdag.workflow.gates.__main__ import main
    with patch("sys.argv", ["ghdag.workflow.gates"] + argv):
        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            main()
            return mock_stdout.getvalue()


def test_unknown_gate_exits_1():
    result = run_preflight_subprocess("--gate", "nonexistent", "--body-file", "/dev/null")
    assert result.returncode == 1
    assert result.stderr


def test_nonexistent_body_file_exits_1():
    result = run_preflight_subprocess("--gate", "nonexistent", "--body-file", "/nonexistent/path")
    assert result.returncode == 1
    assert result.stderr


def test_known_gate_outputs_json_array():
    class NoopRule:
        def check(self, body: str, labels: list[str]) -> list[Violation]:
            return []

    GATE_REGISTRY["_test_noop"] = NoopRule
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("test body")
            body_path = f.name

        output = run_preflight_direct(["--gate", "_test_noop", "--body-file", body_path])
        result = json.loads(output)
        assert isinstance(result, list)
    finally:
        GATE_REGISTRY.pop("_test_noop", None)
        Path(body_path).unlink(missing_ok=True)


def test_gate_with_labels_file():
    received: dict = {}

    class CapturingRule:
        def check(self, body: str, labels: list[str]) -> list[Violation]:
            received["body"] = body
            received["labels"] = labels
            return []

    GATE_REGISTRY["_test_capture"] = CapturingRule
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("body content")
            body_path = f.name

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("label-a\nlabel-b\n")
            labels_path = f.name

        output = run_preflight_direct([
            "--gate", "_test_capture",
            "--body-file", body_path,
            "--labels-file", labels_path,
        ])
        result = json.loads(output)
        assert isinstance(result, list)
        assert received["body"] == "body content"
        assert received["labels"] == ["label-a", "label-b"]
    finally:
        GATE_REGISTRY.pop("_test_capture", None)
        Path(body_path).unlink(missing_ok=True)
        Path(labels_path).unlink(missing_ok=True)
