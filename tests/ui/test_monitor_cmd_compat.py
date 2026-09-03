"""移行検証: extract_engine_model が旧形式・新形式の command で同じ結果を返すこと。"""

from __future__ import annotations

import pytest

from ghdag.ui.monitor import extract_engine_model


@pytest.mark.parametrize(
    ("old_cmd", "new_cmd", "expected"),
    [
        (
            "cat jobs/o.md | claude -p '受け取った内容を実行して' --model 'claude-sonnet-4-6' --output-format json --dangerously-skip-permissions",
            "claude -p --model 'claude-sonnet-4-6' --output-format json --dangerously-skip-permissions < jobs/o.md",
            "claude",
        ),
        (
            "cat jobs/o.md | gemini -p '受け取った内容を実行して' --model 'gemini-2.5-flash' --approval-mode yolo",
            "gemini -p --model 'gemini-2.5-flash' --approval-mode yolo < jobs/o.md",
            "gemini",
        ),
        (
            "agent --model 'auto' -p --force < jobs/o.md",
            "agent --model 'auto' -p --force < jobs/o.md",
            "cursor/auto",
        ),
        (
            "cat jobs/o.md | codex exec - --model 'gpt-5.6-terra' --json --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox",
            "codex exec - --model 'gpt-5.6-terra' --json --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox < jobs/o.md",
            "codex/gpt-5.6-terra",
        ),
    ],
)
def test_extract_engine_model_old_and_new_forms_match(old_cmd: str, new_cmd: str, expected: str) -> None:
    assert extract_engine_model(old_cmd) == expected
    assert extract_engine_model(new_cmd) == expected
    assert extract_engine_model(old_cmd) == extract_engine_model(new_cmd)
