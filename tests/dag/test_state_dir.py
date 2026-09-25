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


def test_dag_config_follows_state_dir(state: Path, tmp_path: Path) -> None:
    config = DagConfig(exec_jsonl_path=tmp_path / "jobs" / "exec.jsonl")
    assert config.exec_done_dir == state / "done"
    assert config.quota_state_path == state / "quota-gate.json"
    # exec.jsonl neighbours stay in jobs/.
    assert config.lock_file == tmp_path / "jobs" / ".ghdag.lock"
    assert config.audit_path == tmp_path / "jobs" / "audit.jsonl"
    assert config.quota_audit_path == tmp_path / "jobs" / "audit.jsonl"


def test_task_launcher_paths_follow_state_dir(state: Path, tmp_path: Path) -> None:
    launcher = _launcher(DagConfig(exec_jsonl_path=tmp_path / "jobs" / "exec.jsonl"))
    assert launcher._running_path("u1") == state / "running" / "u1.json"
    assert launcher._events_path("u1") == state / "events" / "u1.jsonl"
    assert launcher._cancel_path("u1") == state / "cancel" / "u1"
    assert launcher._session_store._store_dir == state / ".sessions"
    assert launcher.quota_gate._state_path == state / "quota-gate.json"
    # Quota audit must not leak into the state dir.
    assert launcher.quota_gate._audit_path == tmp_path / "jobs" / "audit.jsonl"
    assert not (tmp_path / "jobs" / "running").exists()


def test_pipeline_state_follows_state_dir(state: Path, tmp_path: Path) -> None:
    ps = PipelineState.from_repo_root(tmp_path)
    assert ps._state_dir == state / ".pipeline-state"
    assert ps._quota_gate._state_path == state / "quota-gate.json"
    assert ps._quota_gate._audit_path == tmp_path / "jobs" / "audit.jsonl"


def test_state_dir_expands_user(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GHDAG_STATE_DIR", "~/ghdag-state")
    config = DagConfig(exec_jsonl_path=tmp_path / "jobs" / "exec.jsonl")
    assert config.exec_done_dir == tmp_path / "ghdag-state" / "done"


# --- AC-5b: unset => current defaults; explicit args win ---


def test_dag_config_defaults_unchanged(unset: None, tmp_path: Path) -> None:
    config = DagConfig(exec_jsonl_path=tmp_path / "jobs" / "exec.jsonl")
    assert config.exec_done_dir == Path("jobs/done")
    assert config.quota_state_path == tmp_path / "jobs" / "quota-gate.json"


def test_task_launcher_defaults_unchanged(unset: None, tmp_path: Path) -> None:
    config = DagConfig(exec_jsonl_path=tmp_path / "jobs" / "exec.jsonl", exec_done_dir=tmp_path / "jobs" / "done")
    launcher = _launcher(config)
    assert launcher._running_path("u1") == tmp_path / "jobs" / "running" / "u1.json"
    assert launcher._session_store._store_dir == tmp_path / "jobs" / ".sessions"
    assert launcher.quota_gate._state_path == tmp_path / "jobs" / "quota-gate.json"


def test_pipeline_state_defaults_unchanged(unset: None, tmp_path: Path) -> None:
    ps = PipelineState.from_repo_root(tmp_path)
    assert ps._state_dir == tmp_path / ".pipeline-state"
    assert ps._quota_gate._state_path == tmp_path / "jobs" / "quota-gate.json"


def test_explicit_dag_config_args_win(state: Path, tmp_path: Path) -> None:
    config = DagConfig(
        exec_jsonl_path=tmp_path / "jobs" / "exec.jsonl",
        exec_done_dir=tmp_path / "explicit" / "done",
        quota_state_path=tmp_path / "explicit" / "quota.json",
    )
    assert config.exec_done_dir == tmp_path / "explicit" / "done"
    assert config.quota_state_path == tmp_path / "explicit" / "quota.json"


# --- CLI ---


def test_cancel_uses_state_dir_by_default(state: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from ghdag.cli.commands.cancel import cmd_cancel

    monkeypatch.chdir(tmp_path)
    (state / "running").mkdir(parents=True)
    (state / "running" / "u1.json").write_text("{}", encoding="utf-8")
    cmd_cancel(argparse.Namespace(uuid="u1", queue_dir=None))
    assert (state / "cancel" / "u1").exists()
    assert not (tmp_path / "jobs").exists()


def test_cancel_explicit_queue_dir_wins(state: Path, tmp_path: Path) -> None:
    from ghdag.cli.commands.cancel import cmd_cancel

    q = tmp_path / "q"
    (q / "running").mkdir(parents=True)
    (q / "running" / "u1.json").write_text("{}", encoding="utf-8")
    cmd_cancel(argparse.Namespace(uuid="u1", queue_dir=str(q)))
    assert (q / "cancel" / "u1").exists()
    assert not state.exists()


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


def test_cli_parser_defaults_are_none() -> None:
    from ghdag.cli.main import _build_parser

    parser = _build_parser()
    assert parser.parse_args(["status", "--issue", "1"]).state_dir is None
    assert parser.parse_args(["quota", "status"]).state_path is None
    assert parser.parse_args(["dag", "cancel", "u1"]).queue_dir is None
