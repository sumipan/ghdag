"""ghdag.core.stall_guard is the canonical home; ghdag.dag.stall_guard re-exports it."""

from __future__ import annotations

import pytest

import ghdag.core.stall_guard as core_sg
import ghdag.dag.stall_guard as dag_sg
import ghdag.llm.engines as engines


@pytest.mark.parametrize(
    "name",
    [
        "ProcInfo",
        "StallEvent",
        "StallTracker",
        "find_stalled_state_readers",
        "parse_ps_output",
        "scan_process_tree",
    ],
)
def test_dag_reexports_core_objects(name: str) -> None:
    assert getattr(dag_sg, name) is getattr(core_sg, name)


def test_dag_read_ps_is_engines_read_ps() -> None:
    # ps runs in a subprocess, which core's purity guard forbids.
    assert dag_sg.read_ps is engines.read_ps
    assert not hasattr(core_sg, "read_ps")


def test_dag_all_matches_exports() -> None:
    assert sorted(dag_sg.__all__) == sorted(
        [
            "ProcInfo",
            "StallEvent",
            "StallTracker",
            "find_stalled_state_readers",
            "parse_ps_output",
            "read_ps",
            "scan_process_tree",
        ]
    )
    for name in dag_sg.__all__:
        assert hasattr(dag_sg, name)
