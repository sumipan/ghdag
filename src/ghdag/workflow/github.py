"""workflow/github.py — GitHubIssueClient: gh CLI ラッパー"""

from __future__ import annotations

import json
import subprocess


class GitHubIssueClient:
    """gh CLI ラッパー。gh CLI が認証済みであることを前提とする。"""

    def list_issues(self, label: str, state: str = "open") -> list[dict]:
        """gh issue list --label <label> --json number,title,body,labels,url を実行。

        Args:
            label: フィルタラベル
            state: "open" or "closed"
        Returns:
            Issue dict のリスト
        Raises:
            subprocess.CalledProcessError: gh CLI 失敗時
        """
        result = subprocess.run(
            [
                "gh", "issue", "list",
                "--label", label,
                "--json", "number,title,body,labels,url",
                "--limit", "100",
                "--state", state,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(result.stdout)

    def get_issue_comments(self, number: int) -> list[dict]:
        """gh api repos/:owner/:repo/issues/{number}/comments を実行。

        Returns:
            [{author, created_at, body}, ...]
        Raises:
            subprocess.CalledProcessError: gh CLI 失敗時
        """
        result = subprocess.run(
            ["gh", "api", f"repos/:owner/:repo/issues/{number}/comments"],
            capture_output=True,
            text=True,
            check=True,
        )
        raw = json.loads(result.stdout)
        return [
            {
                "author": c.get("user", {}).get("login", ""),
                "created_at": c.get("created_at", ""),
                "body": c.get("body", ""),
            }
            for c in raw
        ]

    def update_label(self, number: int, remove: str, add: str) -> None:
        """gh issue edit {number} --remove-label {remove} --add-label {add}。

        Raises:
            subprocess.CalledProcessError: gh CLI 失敗時
        """
        subprocess.run(
            [
                "gh", "issue", "edit", str(number),
                "--remove-label", remove,
                "--add-label", add,
            ],
            capture_output=True,
            text=True,
            check=True,
        )

    def add_comment(self, number: int, body: str) -> None:
        """gh issue comment {number} --body {body}。

        Raises:
            subprocess.CalledProcessError: gh CLI 失敗時
        """
        subprocess.run(
            ["gh", "issue", "comment", str(number), "--body", body],
            capture_output=True,
            text=True,
            check=True,
        )

    def remove_label(self, number: int, label: str) -> None:
        """gh issue edit {number} --remove-label {label}。

        Raises:
            subprocess.CalledProcessError: gh CLI 失敗時
        """
        subprocess.run(
            [
                "gh", "issue", "edit", str(number),
                "--remove-label", label,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
