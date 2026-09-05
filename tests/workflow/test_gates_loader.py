"""Tests for ghdag.workflow.gates entry-point loading (Issue #2822)."""

from __future__ import annotations

import json
import sys
from importlib.metadata import EntryPoint
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ghdag.workflow.gates import GATE_REGISTRY, Violation, get_gate
from ghdag.workflow.gates import loader as gates_loader


class _OkRule:
    def check(self, body: str, labels: list[str]) -> list[Violation]:
        return [
            Violation(
                rule_id="ok.rule",
                severity="fail",
                message=f"body={body}",
                location=None,
                auto_fixable=False,
                fix_hint=None,
            )
        ]


class _OtherRule:
    def check(self, body: str, labels: list[str]) -> list[Violation]:
        return []


@pytest.fixture(autouse=True)
def _reset_ep_cache():
    gates_loader._ep_cache = None
    yield
    gates_loader._ep_cache = None


def _make_ep(name: str, load_result=None, *, raises: BaseException | None = None) -> MagicMock:
    ep = MagicMock(spec=EntryPoint)
    ep.name = name
    if raises is not None:
        ep.load.side_effect = raises
    else:
        ep.load.return_value = load_result
    return ep


def test_entry_point_gate_resolved_by_get_gate_and_cli(tmp_path: Path) -> None:
    """ダミー ghdag.gates EP が get_gate / CLI で解決され、issuesmith なしで JSON を返す。"""
    body_path = tmp_path / "body.txt"
    body_path.write_text("hello", encoding="utf-8")

    with patch.object(
        gates_loader,
        "entry_points",
        return_value=[_make_ep("dummy_ep", _OkRule)],
    ):
        assert "dummy_ep" not in GATE_REGISTRY
        assert "issuesmith" not in sys.modules
        cls = get_gate("dummy_ep")
        assert cls is _OkRule

        from ghdag.workflow.gates.__main__ import main

        with patch("sys.argv", ["ghdag.workflow.gates", "--gate", "dummy_ep", "--body-file", str(body_path)]):
            with patch("sys.stdout", new_callable=StringIO) as out:
                main()
                payload = json.loads(out.getvalue())
        assert payload[0]["rule_id"] == "ok.rule"
        assert payload[0]["message"] == "body=hello"
        assert "issuesmith" not in sys.modules


def test_entry_point_load_failure_is_fail_open(capsys: pytest.CaptureFixture[str]) -> None:
    """1 EP の ImportError/AttributeError でも他ゲートは登録され stderr に失敗が残る。"""
    bad = _make_ep("broken", raises=ImportError("boom"))
    good = _make_ep("good", _OtherRule)

    with patch.object(gates_loader, "entry_points", return_value=[bad, good]):
        loaded = gates_loader.load_entry_point_gates()

    assert "good" in loaded
    assert loaded["good"] is _OtherRule
    assert "broken" not in loaded
    err = capsys.readouterr().err
    assert "broken" in err
    assert "boom" in err


def test_gate_registry_wins_over_entry_point() -> None:
    """同名時は GATE_REGISTRY（import 副作用）が entry-point より優先される。"""

    class RegistryRule:
        def check(self, body: str, labels: list[str]) -> list[Violation]:
            return []

    GATE_REGISTRY["collision"] = RegistryRule
    try:
        with patch.object(
            gates_loader,
            "entry_points",
            return_value=[_make_ep("collision", _OkRule)],
        ):
            assert get_gate("collision") is RegistryRule
    finally:
        GATE_REGISTRY.pop("collision", None)


def test_get_gate_returns_none_when_unregistered() -> None:
    with patch.object(gates_loader, "entry_points", return_value=[]):
        assert get_gate("no_such_gate") is None


def test_load_entry_point_gates_caches() -> None:
    eps = [_make_ep("cached", _OkRule)]
    with patch.object(gates_loader, "entry_points", return_value=eps) as mocked:
        first = gates_loader.load_entry_point_gates()
        second = gates_loader.load_entry_point_gates()
    assert first is second
    assert mocked.call_count == 1
