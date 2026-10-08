"""CLI wiring for ghdag run --brake-state and DagConfig.brake_state_path."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from unittest.mock import MagicMock, patch

from ghdag.core.models.dag import DagConfig


def test_dag_config_brake_state_path_coerced_to_path():
    config = DagConfig(
        exec_jsonl_path="jobs/exec.jsonl",
        brake_state_path="x.json",
    )
    assert config.brake_state_path == Path("x.json")


def test_dag_config_brake_state_path_omitted_is_none():
    config = DagConfig(exec_jsonl_path="jobs/exec.jsonl")
    assert config.brake_state_path is None


def test_run_parser_brake_state_flag():
    from ghdag.cli.main import _build_parser

    parser = _build_parser()
    args = parser.parse_args(
        ["run", "jobs/exec.jsonl", "--brake-state", "jobs/issuesmith-brake.json"]
    )
    assert args.brake_state == "jobs/issuesmith-brake.json"


def test_run_parser_brake_state_omitted_is_none():
    from ghdag.cli.main import _build_parser

    parser = _build_parser()
    args = parser.parse_args(["run", "jobs/exec.jsonl"])
    assert args.brake_state is None


def test_cmd_run_passes_brake_state_path_to_dag_engine(tmp_path):
    exec_jsonl = tmp_path / "exec.jsonl"
    exec_jsonl.write_text("")

    captured_config: DagConfig | None = None

    def fake_dag_engine(config: DagConfig, hooks: object) -> MagicMock:
        nonlocal captured_config
        captured_config = config
        return MagicMock()

    with patch("ghdag.dag.engine.DagEngine", side_effect=fake_dag_engine):
        from ghdag.cli.commands.run import cmd_run

        cmd_run(
            Namespace(
                exec_jsonl=str(exec_jsonl),
                interval=1.0,
                max_concurrency=None,
                hooks=None,
                brake_state="b.json",
                cwd=None,
            )
        )

    assert captured_config is not None
    assert captured_config.brake_state_path == Path("b.json")
