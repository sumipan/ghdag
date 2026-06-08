"""ラベル遷移の State Machine（非推奨ラッパー）。

GitHub API 経由でラベル遷移をバリデーション付きで実行する。
ghdag.workflow.state_machine への移行を推奨。

Usage:
    python -m ghdag.workflow.label_state_machine transition <issue_number> <target_label>
"""
from __future__ import annotations

import sys
import warnings

from ghdag.workflow import state_machine

_DEPRECATION_MSG = (
    "ghdag.workflow.label_state_machine は非推奨です。"
    "ghdag.workflow.state_machine を使用してください"
)

TRANSITIONS: dict[str, list[str]] = {
    "issuesmith:draft-running":   ["issuesmith:draft-done", "issuesmith:develop-ready"],
    "issuesmith:draft-done":      ["issuesmith:develop-ready"],
    "issuesmith:develop-running": ["issuesmith:develop-done", "issuesmith:merge-ready"],
    "issuesmith:develop-done":    ["issuesmith:merge-ready"],
    "issuesmith:merge-running":   ["issuesmith:merge-done", "issuesmith:migrate-ready"],
    "issuesmith:migrate-running": ["issuesmith:merge-ready", "issuesmith:reset"],
}

RESET_LABEL = "issuesmith:reset"


def _warn_deprecated() -> None:
    warnings.warn(_DEPRECATION_MSG, RuntimeWarning, stacklevel=3)


def get_current_phase(labels: list[str]) -> str | None:
    """issuesmith フェーズラベル（TRANSITIONS のソース）のうち最初にマッチしたものを返す。"""
    _warn_deprecated()
    return state_machine.get_current_phase(labels, TRANSITIONS)


def validate_transition(current_labels: list[str], target: str) -> tuple[bool, str]:
    """(有効か, 理由) を返す。issuesmith:reset は任意の状態から常に有効。"""
    _warn_deprecated()
    return state_machine.validate_transition(
        current_labels, target, TRANSITIONS, RESET_LABEL
    )


def transition(issue_number: int, target: str) -> None:
    """バリデーション → 実行 → 検証 の3ステップで遷移する。失敗時は ValueError。"""
    _warn_deprecated()
    state_machine.transition(issue_number, target, TRANSITIONS, RESET_LABEL)


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
