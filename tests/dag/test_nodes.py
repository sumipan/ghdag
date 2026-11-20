"""Tests for remote node configuration loading and selection."""

from __future__ import annotations

from pathlib import Path

import pytest

from ghdag.core.exceptions import GhdagError
from ghdag.dag._nodes import NodeSpec, load_nodes, pick_node


def test_load_nodes_reads_valid_file(tmp_path: Path) -> None:
    """Refs sumipan/ghdag#7651 — valid nodes file returns NodeSpec list in file order."""
    path = tmp_path / "nodes.yml"
    path.write_text(
        "nodes:\n"
        "  - name: build-1\n"
        "    labels: [linux, shell]\n"
        "    ssh: build-1\n"
        "    workdir: /srv/a\n"
        "  - name: build-2\n"
        "    labels: [mac]\n"
        "    ssh: build-2\n"
        "    workdir: /srv/b\n"
        "    max_parallel: 2\n",
        encoding="utf-8",
    )
    specs = load_nodes(path)
    assert len(specs) == 2
    assert specs[0] == NodeSpec(
        name="build-1",
        labels=("linux", "shell"),
        ssh="build-1",
        workdir="/srv/a",
        max_parallel=1,
    )
    assert specs[1].name == "build-2"
    assert specs[1].max_parallel == 2


@pytest.mark.parametrize(
    ("body", "needle"),
    [
        (
            "nodes:\n"
            "  - {name: a, labels: [x], ssh: h, workdir: /w}\n"
            "  - {name: a, labels: [y], ssh: h2, workdir: /w2}\n",
            "duplicate",
        ),
        (
            "nodes:\n  - {name: a, labels: [], ssh: h, workdir: /w}\n",
            "labels",
        ),
        (
            "nodes:\n  - {name: a, labels: [x], ssh: h, workdir: /w, max_parallel: 0}\n",
            "max_parallel",
        ),
    ],
)
def test_load_nodes_rejects_invalid(tmp_path: Path, body: str, needle: str) -> None:
    """Refs sumipan/ghdag#7651 — invalid nodes YAML raises GhdagError exit_code 1."""
    path = tmp_path / "bad.yml"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(GhdagError) as exc_info:
        load_nodes(path)
    assert exc_info.value.exit_code == 1
    assert needle in exc_info.value.message.lower()


def test_pick_node_first_free_wait_missing() -> None:
    """Refs sumipan/ghdag#7651 — pick_node returns free node, wait, or missing."""
    nodes = [
        NodeSpec("a", ("linux",), "h1", "/w", max_parallel=1),
        NodeSpec("b", ("linux", "shell"), "h2", "/w", max_parallel=2),
    ]
    assert pick_node(nodes, "linux", {"a": 0}) is nodes[0]
    assert pick_node(nodes, "linux", {"a": 1}) is nodes[1]
    assert pick_node(nodes, "linux", {"a": 1, "b": 2}) == "wait"
    assert pick_node(nodes, "gpu", {}) == "missing"
