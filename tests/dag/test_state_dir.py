"""GHDAG_STATE_DIR resolution for DAG / pipeline / CLI state paths (nexus Issue #3831, AC-5)."""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ghdag.dag.circuit_breaker import CircuitBreakerPolicy
from ghdag.dag.hooks import DagHooks
from ghdag.dag.models import DagConfig
from ghdag.dag.task_launcher import TaskLauncher
from ghdag.pipeline.state import PipelineState


def _launcher(config: DagConfig) -> TaskLauncher:
    return TaskLauncher(
        config, MagicMock(spec=DagHooks), CircuitBreakerPolicy(float("inf"), 2**31), MagicMock(), lambda _: None
    )


@pytest.fixture
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    s = tmp_path / "state"
    monkeypatch.setenv("GHDAG_STATE_DIR", str(s))
    return s


@pytest.fixture
def unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GHDAG_STATE_DIR", raising=False)


# --- AC-5: GHDAG_STATE_DIR set ---










# --- AC-5b: unset => current defaults; explicit args win ---








def test_pipeline_state_defaults_unchanged(unset: None, tmp_path: Path) -> None:
    ps = PipelineState.from_repo_root(tmp_path)
    assert ps._state_dir == tmp_path / ".pipeline-state"
    assert ps._quota_gate._state_path == tmp_path / "jobs" / "quota-gate.json"




# --- CLI ---


def test_cancel_uses_state_dir_by_default(state: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from ghdag.cli.commands.cancel import cmd_cancel

    monkeypatch.chdir(tmp_path)
    (state / "running").mkdir(parents=True)
    (state / "running" / "u1.json").write_text("{}", encoding="utf-8")
    cmd_cancel(argparse.Namespace(uuid="u1", queue_dir=None))
    assert (state / "cancel" / "u1").exists()
    assert not (tmp_path / "jobs").exists()




def test_quota_state_path_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from ghdag.cli.commands.quota import _build_gate

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GHDAG_STATE_DIR", raising=False)
    assert _build_gate(None)._state_path == Path("jobs") / "quota-gate.json"
    monkeypatch.setenv("GHDAG_STATE_DIR", str(tmp_path / "s"))
    gate = _build_gate(None)
    assert gate._state_path == tmp_path / "s" / "quota-gate.json"
    assert gate._audit_path == Path("jobs") / "audit.jsonl"
    assert _build_gate(str(tmp_path / "x.json"))._state_path == tmp_path / "x.json"


