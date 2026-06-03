"""ラベル遷移の State Machine。

GitHub API 経由でラベル遷移をバリデーション付きで実行する。

Usage:
    python -m ghdag.workflow.label_state_machine transition <issue_number> <target_label>
"""
from __future__ import annotations

import sys

from ghdag.github_client import GitHubClient

TRANSITIONS: dict[str, list[str]] = {
    "issuesmith:draft-running":   ["issuesmith:draft-done", "issuesmith:develop-ready"],
    "issuesmith:draft-done":      ["issuesmith:develop-ready"],
    "issuesmith:develop-running": ["issuesmith:develop-done", "issuesmith:merge-ready"],
    "issuesmith:develop-done":    ["issuesmith:merge-ready"],
    "issuesmith:merge-running":   ["issuesmith:merge-done", "issuesmith:migrate-ready"],
    "issuesmith:migrate-running": ["issuesmith:merge-ready", "issuesmith:reset"],
}


def get_current_phase(labels: list[str]) -> str | None:
    """issuesmith フェーズラベル（TRANSITIONS のソース）のうち最初にマッチしたものを返す。"""
    for label in labels:
        if label in TRANSITIONS:
            return label
    return None


def validate_transition(current_labels: list[str], target: str) -> tuple[bool, str]:
    """(有効か, 理由) を返す。issuesmith:reset は任意の状態から常に有効。"""
    if target == "issuesmith:reset":
        return True, "reset は任意の状態から有効"

    current = get_current_phase(current_labels)
    if current is None:
        return False, "issuesmith フェーズラベルがない — 遷移元を特定できない"

    allowed = TRANSITIONS.get(current, [])
    if target in allowed:
        return True, f"{current} -> {target}"

    return False, (
        f"不正遷移: {current} -> {target} は許可されていない。"
        f"許可された遷移先: {allowed}"
    )


def _label_names(client: GitHubClient, issue_number: int) -> list[str]:
    data = client.issue_get(issue_number, fields=["labels"])
    return [lbl["name"] for lbl in data.get("labels", [])]


def transition(issue_number: int, target: str) -> None:
    """バリデーション → 実行 → 検証 の3ステップで遷移する。失敗時は ValueError。"""
    client = GitHubClient()
    current_labels = _label_names(client, issue_number)

    valid, reason = validate_transition(current_labels, target)
    if not valid:
        raise ValueError(f"#{issue_number}: {reason}")

    current_phase = get_current_phase(current_labels)
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


def main() -> int:
    if len(sys.argv) != 4 or sys.argv[1] != "transition":
        print(
            "Usage: python -m ghdag.workflow.label_state_machine transition"
            " <issue_number> <target_label>",
            file=sys.stderr,
        )
        return 1

    try:
        issue_number = int(sys.argv[2])
    except ValueError:
        print(f"issue_number は整数で指定してください: {sys.argv[2]}", file=sys.stderr)
        return 1

    target = sys.argv[3]
    try:
        transition(issue_number, target)
        print(f"#{issue_number}: {target} に遷移しました")
        return 0
    except (ValueError, RuntimeError) as e:
        print(str(e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
