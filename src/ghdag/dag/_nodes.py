"""Remote execution node definitions loaded from YAML."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

from ghdag.core.exceptions import GhdagError

__all__ = ["NodeSpec", "load_nodes", "pick_node"]


@dataclass(frozen=True)
class NodeSpec:
    name: str
    labels: tuple[str, ...]
    ssh: str
    workdir: str
    max_parallel: int = 1


def load_nodes(path: str | Path) -> list[NodeSpec]:
    """Load node definitions from a YAML file in file order."""
    p = Path(path)
    if not p.is_file():
        raise GhdagError(
            f"nodes file not found or not readable: {p}",
            code="NODES_FILE_INVALID",
        )
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise GhdagError(
            f"nodes file not found or not readable: {p}: {exc}",
            code="NODES_FILE_INVALID",
        ) from exc
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise GhdagError(
            f"invalid YAML in nodes file {p}: {exc}",
            code="NODES_FILE_INVALID",
        ) from exc
    if not isinstance(data, dict):
        raise GhdagError(
            f"nodes file {p}: top-level mapping with 'nodes' key required",
            code="NODES_FILE_INVALID",
        )
    entries = data.get("nodes")
    if not isinstance(entries, list):
        raise GhdagError(
            f"nodes file {p}: 'nodes' must be a list",
            code="NODES_FILE_INVALID",
        )
    specs: list[NodeSpec] = []
    seen_names: set[str] = set()
    for idx, item in enumerate(entries):
        prefix = f"nodes file {p}: nodes[{idx}]"
        if not isinstance(item, dict):
            raise GhdagError(f"{prefix}: must be a mapping", code="NODES_FILE_INVALID")
        try:
            spec = _parse_node_entry(item, prefix)
        except GhdagError:
            raise
        except Exception as exc:
            raise GhdagError(f"{prefix}: {exc}", code="NODES_FILE_INVALID") from exc
        if spec.name in seen_names:
            raise GhdagError(
                f"nodes file {p}: duplicate node name {spec.name!r}",
                code="NODES_FILE_INVALID",
            )
        seen_names.add(spec.name)
        specs.append(spec)
    return specs


def _parse_node_entry(item: dict[object, object], prefix: str) -> NodeSpec:
    name = item.get("name")
    if not isinstance(name, str) or not name:
        raise GhdagError(f"{prefix}: 'name' must be a non-empty string", code="NODES_FILE_INVALID")
    raw_labels = item.get("labels")
    if not isinstance(raw_labels, list) or not raw_labels:
        raise GhdagError(
            f"{prefix}: 'labels' must be a non-empty list of strings",
            code="NODES_FILE_INVALID",
        )
    labels: list[str] = []
    for i, lab in enumerate(raw_labels):
        if not isinstance(lab, str) or not lab:
            raise GhdagError(
                f"{prefix}: labels[{i}] must be a non-empty string",
                code="NODES_FILE_INVALID",
            )
        labels.append(lab)
    ssh = item.get("ssh")
    if not isinstance(ssh, str) or not ssh:
        raise GhdagError(f"{prefix}: 'ssh' must be a non-empty string", code="NODES_FILE_INVALID")
    workdir = item.get("workdir")
    if not isinstance(workdir, str) or not workdir:
        raise GhdagError(
            f"{prefix}: 'workdir' must be a non-empty string",
            code="NODES_FILE_INVALID",
        )
    max_parallel = item.get("max_parallel", 1)
    if isinstance(max_parallel, bool) or not isinstance(max_parallel, int):
        raise GhdagError(
            f"{prefix}: 'max_parallel' must be a positive integer",
            code="NODES_FILE_INVALID",
        )
    if max_parallel < 1:
        raise GhdagError(
            f"{prefix}: 'max_parallel' must be at least 1 (got {max_parallel})",
            code="NODES_FILE_INVALID",
        )
    return NodeSpec(
        name=name,
        labels=tuple(labels),
        ssh=ssh,
        workdir=workdir,
        max_parallel=max_parallel,
    )


def pick_node(
    nodes: Sequence[NodeSpec],
    label: str,
    busy: Mapping[str, int],
) -> NodeSpec | Literal["wait", "missing"]:
    """Pick the first matching node with capacity, or wait / missing."""
    matching = [n for n in nodes if label in n.labels]
    if not matching:
        return "missing"
    for node in matching:
        if busy.get(node.name, 0) < node.max_parallel:
            return node
    return "wait"
