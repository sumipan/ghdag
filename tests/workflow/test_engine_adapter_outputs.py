"""Tests for ghdag.core.command adapter exec records."""

from __future__ import annotations

import pytest

from ghdag.core.command import get_command_adapter


@pytest.mark.parametrize(
    ("engine", "kwargs", "command_assertions"),
    [
        (
            "claude",
            {
                "uuid": "u1",
                "order_path": "queue/order.md",
                "result_path": "results/result.md",
                "model": "claude-sonnet-4-6",
                "depends": [],
            },
            lambda cmd: cmd
            == (
                "claude -p --model 'claude-sonnet-4-6' --output-format stream-json --verbose"
                " --dangerously-skip-permissions < queue/order.md"
            ),
        ),
        (
            "gemini",
            {
                "uuid": "u1",
                "order_path": "queue/order.md",
                "result_path": "results/result.md",
                "model": "gemini-2.5-flash",
                "depends": [],
            },
            lambda cmd: " -m " not in cmd and "--model 'gemini-2.5-flash'" in cmd,
        ),
        (
            "cursor",
            {
                "uuid": "u1",
                "order_path": "queue/order.md",
                "result_path": "results/result.md",
                "model": "auto",
                "depends": [],
            },
            lambda cmd: "-p --force" in cmd,
        ),
        (
            "shell",
            {
                "uuid": "u1",
                "order_path": "queue/order.sh",
                "result_path": "results/result.md",
                "model": None,
                "depends": [],
            },
            lambda cmd: cmd == "bash -o pipefail queue/order.sh",
        ),
    ],
)
def test_get_command_adapter_preserves_engine_commands(
    engine: str,
    kwargs: dict[str, object],
    command_assertions,
) -> None:
    """Issue sumipan/nexus#6535 — renamed API preserves per-engine command tokens."""
    adapter = get_command_adapter(engine)
    record = adapter.build_exec_record(**kwargs)
    command_assertions(record["command"])
