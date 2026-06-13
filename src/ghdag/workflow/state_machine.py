"""Config-driven label transition state machine.

Usage:
    python -m ghdag.workflow.state_machine transition --workflow <YAML> <issue_number> <target_label>
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

import yaml

from ghdag.github_client import GitHubClient
from ghdag.workflow.loader import _parse
from ghdag.workflow.schema import WorkflowConfig


def get_current_phase(
    labels: list[str],
    transitions: dict[str, list[str]],
) -> str | None:
    """labels 内で transitions のキーにマッチする最初のラベルを返す。"""
    for label in labels:
        if label in transitions:
            return label
    return None


def validate_transition(
    current_labels: list[str],
    target: str,
    transitions: dict[str, list[str]] | None = None,
    reset_label: str | None = None,
) -> tuple[bool, str]:
    """(有効か, 理由) を返す。transitions=None の場合は (True, "バリデーションスキップ") を返す。"""
    if transitions is None:
        return True, "バリデーションスキップ"

    if reset_label is not None and target == reset_label:
        return True, f"{reset_label} は任意の状態から有効"

    current = get_current_phase(current_labels, transitions)
    if current is None:
        return False, "フェーズラベルがない — 遷移元を特定できない"

    allowed = transitions.get(current, [])
    if target in allowed:
        return True, f"{current} -> {target}"

    return False, (
        f"不正遷移: {current} -> {target} は許可されていない。"
        f"許可された遷移先: {allowed}"
    )


def _label_names(client: GitHubClient, issue_number: int) -> list[str]:
    data = client.issue_get(issue_number, fields=["labels"])
    return [lbl["name"] for lbl in data.get("labels", [])]


def transition(
    issue_number: int,
    target: str,
    transitions: dict[str, list[str]],
    reset_label: str | None = None,
) -> None:
    """バリデーション → ラベル付替 → 検証 の 3 ステップで遷移する。失敗時は ValueError。"""
    client = GitHubClient()
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
            f"#{issue_number}: ラベル付与後に {target} が確認できない。"
            f"現在のラベル: {new_labels}"
        )


def _load_workflow_config(path: Path) -> WorkflowConfig:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Invalid workflow YAML: {path}")
    config = _parse(data, workflow_dir=path.parent.resolve())
    return replace(
        config,
        label_namespace=data.get("label_namespace"),
        transitions=data.get("transitions"),
        reset_label=data.get("reset_label"),
    )


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
        print(f"#{args.issue_number}: {args.target_label} に遷移しました")
        return 0
    except (ValueError, RuntimeError) as e:
        print(str(e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
