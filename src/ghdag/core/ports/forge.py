"""ForgePort Protocol — Issue / PR / milestone / runner 操作の抽象 I/F."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ForgePort(Protocol):
    """Forge（Issue / PR / label / milestone / Actions）操作の抽象インタフェース。

    メソッド名は GitHubClient の primary 名に合わせる。
    既存 GitHubIssuePort（get_issue / add_comment 等）は互換のため別途維持する。
    """

    @property
    def repo(self) -> str: ...

    def issue_get(self, number: int, fields: list[str] | None = None) -> dict: ...

    def issue_create(
        self,
        title: str,
        body: str,
        *,
        labels: list[str] | None = None,
        milestone: int | None = None,
    ) -> int: ...

    def issue_update(
        self,
        number: int,
        *,
        title: str | None = None,
        body: str | None = None,
        labels_add: list[str] | None = None,
        labels_remove: list[str] | None = None,
        milestone: int | None = None,
    ) -> None: ...

    def issue_close(self, number: int) -> None: ...

    def reopen_issue(self, number: int) -> None: ...

    def issue_comment(self, number: int, body: str) -> dict: ...

    def issue_timeline(self, number: int) -> list[dict]: ...

    def list_issues(self, label: str, state: str = "open") -> list[dict]: ...

    def get_issue_comments(self, number: int) -> list[dict]: ...

    def update_label(self, number: int, remove: str, add: str) -> None: ...

    def remove_label(self, number: int, label: str) -> None: ...

    def pr_list(
        self,
        *,
        head: str | None = None,
        state: str | None = None,
        search: str | None = None,
        repo: str | None = None,
        limit: int = 30,
    ) -> list[dict]: ...

    def pr_get(self, number: int, *, repo: str | None = None) -> dict: ...

    def pr_create(
        self,
        base: str,
        head: str,
        title: str,
        body: str,
        *,
        repo: str | None = None,
    ) -> str: ...

    def pr_merge(
        self,
        number: int,
        *,
        method: str = "merge",
        delete_branch: bool = True,
        repo: str | None = None,
    ) -> None: ...

    def pr_diff(self, number: int, *, repo: str | None = None) -> str: ...

    def pr_checks(self, number: int, *, repo: str | None = None) -> list[dict]: ...

    def pr_ready(self, number: int, *, repo: str | None = None) -> None: ...

    def pr_update(
        self,
        number: int,
        *,
        title: str | None = None,
        body: str | None = None,
    ) -> None: ...

    def milestone_list(self) -> list[dict]: ...

    def milestone_create(self, title: str, description: str = "") -> int: ...

    def run_get(self, run_id: int, *, repo: str | None = None) -> dict: ...

    def run_logs_failed(self, run_id: int, *, repo: str | None = None) -> str: ...

    def run_rerun_failed(self, run_id: int, *, repo: str | None = None) -> None: ...

    def repo_exists(self, repo: str | None = None) -> bool: ...

    def dispatch_event(self, event_type: str, payload: dict | None = None) -> None: ...

    def get_rate_limit(self) -> dict | None: ...
