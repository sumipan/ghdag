"""Config-driven label transition state machine.

Usage:
    python -m ghdag.workflow.state_machine transition --workflow <YAML> <issue_number> <target_label>
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ghdag.core.ports.forge import ForgePort
from ghdag.forge import get_forge
from ghdag.workflow.loader import load_workflow_file
from ghdag.workflow.schema import WorkflowConfig


def get_current_phase(
    labels: list[str],
    transitions: dict[str, list[str]],
) -> str | None:
    """Return the last (most advanced) phase label in transitions definition order.

    When labels contain several phase labels, the one that comes last in the insertion
    order of transitions (pipeline progression order) is the current phase. The order of
    the label list does not matter.
    """
    label_set = set(labels)
    current = None
    for phase in transitions:
        if phase in label_set:
            current = phase
    return current


def validate_transition(
    current_labels: list[str],
    target: str,
    transitions: dict[str, list[str]] | None = None,
    reset_label: str | None = None,
) -> tuple[bool, str]:
    """Return (is_valid, reason). Returns (True, "validation skipped") when transitions is None."""
    if transitions is None:
        return True, "validation skipped"

    if reset_label is not None and target == reset_label:
        return True, f"{reset_label} is valid from any state"

    current = get_current_phase(current_labels, transitions)
    if current is None:
        return False, "no phase label: cannot determine the source state"

    allowed = transitions.get(current, [])
    if target in allowed:
        return True, f"{current} -> {target}"

    return False, (
        f"invalid transition: {current} -> {target} is not allowed. "
        f"allowed targets: {allowed}"
    )


def _label_names(client: ForgePort, issue_number: int) -> list[str]:
    data = client.issue_get(issue_number, fields=["labels"])
    return [lbl["name"] for lbl in data.get("labels", [])]


def transition(
    issue_number: int,
    target: str,
    transitions: dict[str, list[str]],
    reset_label: str | None = None,
) -> None:
    """Transition in 3 steps: validate -> relabel -> verify. Raises ValueError on failure."""
    client = get_forge()
    current_labels = _label_names(client, issue_number)

    valid, reason = validate_transition(
        current_labels, target, transitions, reset_label
    )
    if not valid:
        raise ValueError(f"#{issue_number}: {reason}")

    current_phase = get_current_phase(current_labels, transitions)
    if current_phase is not None:
        client.issue_update(
            issue_number,
            labels_remove=[current_phase],
            labels_add=[target],
        )
    else:
        client.issue_update(issue_number, labels_add=[target])

    new_labels = _label_names(client, issue_number)
    if target not in new_labels:
        raise ValueError(
            f"#{issue_number}: {target} not found after adding the label. "
            f"current labels: {new_labels}"
        )


def _load_workflow_config(path: Path) -> WorkflowConfig:
    """Deprecated: use ghdag.workflow.loader.load_workflow_file."""
    return load_workflow_file(path)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Config-driven label transition state machine"
    )
    subparsers = parser.add_subparsers(dest="command")

    transition_parser = subparsers.add_parser("transition", help="Transition issue labels")
    transition_parser.add_argument(
        "--workflow", required=True, help="Path to workflow YAML file"
    )
    transition_parser.add_argument("issue_number", type=int)
    transition_parser.add_argument("target_label")

    args = parser.parse_args()
    if args.command != "transition":
        parser.print_usage(sys.stderr)
        return 1

    workflow_path = Path(args.workflow)
    if not workflow_path.exists():
        print(f"Workflow file not found: {workflow_path}", file=sys.stderr)
        return 1

    config = _load_workflow_config(workflow_path)
    if config.transitions is None:
        print(
            f"Workflow {config.name} does not define transitions",
            file=sys.stderr,
        )
        return 1

    try:
        transition(
            args.issue_number,
            args.target_label,
            config.transitions,
            config.reset_label,
        )
        print(f"#{args.issue_number}: transitioned to {args.target_label}")
        return 0
    except (ValueError, RuntimeError) as e:
        print(str(e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
