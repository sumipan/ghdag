"""workflow/github.py — GitHubIssuePort Protocol + GitHubIssueClient: gh CLI ラッパー"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from typing import Protocol, runtime_checkable

import requests

logger = logging.getLogger(__name__)


@runtime_checkable
class GitHubIssuePort(Protocol):
    """GitHub Issues 操作の抽象インタフェース。dispatcher はこの Protocol にのみ依存する。"""

    def get_issue(self, number: int) -> dict: ...
    def list_issues(self, label: str, state: str = "open") -> list[dict]: ...
    def get_issue_comments(self, number: int) -> list[dict]: ...
    def update_label(self, number: int, remove: str, add: str) -> None: ...
    def add_comment(self, number: int, body: str) -> None: ...
    def remove_label(self, number: int, label: str) -> None: ...
    def dispatch_event(self, event_type: str, payload: dict | None = None) -> None: ...
    def get_rate_limit(self) -> dict | None: ...


class GitHubClientError(Exception):
    """Base exception for GitHub client errors."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class AuthError(GitHubClientError):
    """Authentication failed."""


class RateLimitError(GitHubClientError):
    """Rate limit exceeded."""


class PermissionDeniedError(GitHubClientError):
    """Permission denied."""


class NetworkError(GitHubClientError):
    """Network-level request error."""


class GhCliGitHubClient:
    """gh CLI ラッパー。gh CLI が認証済みであることを前提とする。"""

    def get_issue(self, number: int) -> dict:
        result = subprocess.run(
            [
                "gh", "issue", "view", str(number),
                "--json", "number,title,body,labels,url",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(result.stdout)

    def dispatch_event(self, event_type: str, payload: dict | None = None) -> None:
        body: dict = {"event_type": event_type}
        if payload:
            body["client_payload"] = payload
        subprocess.run(
            [
                "gh", "api", "repos/:owner/:repo/dispatches",
                "--method", "POST",
                "--input", "-",
            ],
            input=json.dumps(body),
            capture_output=True,
            text=True,
            check=True,
        )

    def list_issues(self, label: str, state: str = "open") -> list[dict]:
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
        subprocess.run(
            ["gh", "issue", "comment", str(number), "--body", body],
            capture_output=True,
            text=True,
            check=True,
        )

    def get_rate_limit(self) -> dict | None:
        try:
            result = subprocess.run(
                ["gh", "api", "rate_limit", "--jq", ".rate"],
                capture_output=True,
                text=True,
                check=True,
            )
            return json.loads(result.stdout)
        except (subprocess.CalledProcessError, json.JSONDecodeError):
            return None

    def remove_label(self, number: int, label: str) -> None:
        subprocess.run(
            [
                "gh", "issue", "edit", str(number),
                "--remove-label", label,
            ],
            capture_output=True,
            text=True,
            check=True,
        )


class TokenGitHubClient:
    """GitHub REST API client backed by requests.Session."""

    BASE_URL = "https://api.github.com"

    def __init__(self, token: str, owner: str, repo: str) -> None:
        self._owner = owner
        self._repo = repo
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            }
        )

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        url = f"{self.BASE_URL}{path}"
        try:
            response = self._session.request(method, url, **kwargs)
        except (requests.ConnectionError, requests.Timeout) as exc:
            raise NetworkError(f"Network error while calling {method} {url}") from exc

        remaining = response.headers.get("X-RateLimit-Remaining")
        reset = response.headers.get("X-RateLimit-Reset")
        logger.debug("GitHub rate limit: remaining=%s reset=%s", remaining, reset)

        if response.status_code == 401:
            raise AuthError("GitHub authentication failed", status_code=401)
        if response.status_code == 403 and remaining == "0":
            raise RateLimitError("GitHub API rate limit exceeded", status_code=403)
        if response.status_code == 403:
            raise PermissionDeniedError("GitHub API permission denied", status_code=403)
        response.raise_for_status()
        return response

    def get_issue(self, number: int) -> dict:
        path = f"/repos/{self._owner}/{self._repo}/issues/{number}"
        return self._request("GET", path).json()

    def list_issues(self, label: str, state: str = "open") -> list[dict]:
        path = f"/repos/{self._owner}/{self._repo}/issues"
        params = {"labels": label, "state": state, "per_page": 100}
        return self._request("GET", path, params=params).json()

    def get_issue_comments(self, number: int) -> list[dict]:
        path = f"/repos/{self._owner}/{self._repo}/issues/{number}/comments"
        return self._request("GET", path, params={"per_page": 100}).json()

    def update_label(self, number: int, remove: str, add: str) -> None:
        delete_path = f"/repos/{self._owner}/{self._repo}/issues/{number}/labels/{remove}"
        add_path = f"/repos/{self._owner}/{self._repo}/issues/{number}/labels"
        self._request("DELETE", delete_path)
        self._request("POST", add_path, json=[add])

    def add_comment(self, number: int, body: str) -> None:
        path = f"/repos/{self._owner}/{self._repo}/issues/{number}/comments"
        self._request("POST", path, json={"body": body})

    def remove_label(self, number: int, label: str) -> None:
        path = f"/repos/{self._owner}/{self._repo}/issues/{number}/labels/{label}"
        self._request("DELETE", path)

    def dispatch_event(self, event_type: str, payload: dict | None = None) -> None:
        path = f"/repos/{self._owner}/{self._repo}/dispatches"
        body: dict[str, object] = {"event_type": event_type, "client_payload": payload or {}}
        self._request("POST", path, json=body)

    def get_rate_limit(self) -> dict | None:
        return self._request("GET", "/rate_limit").json()


def _resolve_repo() -> tuple[str, str]:
    env_repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if env_repo and "/" in env_repo:
        owner, repo = env_repo.split("/", 1)
        if owner and repo:
            return owner, repo
    try:
        result = subprocess.run(
            ["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise ValueError("owner/repo を特定できませんでした") from exc
    repo_full_name = result.stdout.strip()
    if "/" not in repo_full_name:
        raise ValueError("owner/repo を特定できませんでした")
    owner, repo = repo_full_name.split("/", 1)
    if not owner or not repo:
        raise ValueError("owner/repo を特定できませんでした")
    return owner, repo


def create_github_client(
    backend: str = "auto",
    *,
    token: str | None = None,
    owner: str | None = None,
    repo: str | None = None,
) -> GitHubIssuePort:
    token_value = token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if backend == "gh":
        return GhCliGitHubClient()
    if backend == "token":
        if not token_value:
            raise AuthError("GitHub token is required for token backend")
        if owner and repo:
            resolved_owner, resolved_repo = owner, repo
        else:
            resolved_owner, resolved_repo = _resolve_repo()
        return TokenGitHubClient(token_value, resolved_owner, resolved_repo)
    if backend == "auto":
        if token_value:
            if owner and repo:
                resolved_owner, resolved_repo = owner, repo
            else:
                resolved_owner, resolved_repo = _resolve_repo()
            return TokenGitHubClient(token_value, resolved_owner, resolved_repo)
        return GhCliGitHubClient()
    raise ValueError(f"Unsupported backend: {backend}")


@runtime_checkable
class GitHubIssuePort(Protocol):
    def get_issue(self, number: int) -> dict: ...

    def list_issues(self, label: str, state: str = "open") -> list[dict]: ...

    def get_issue_comments(self, number: int) -> list[dict]: ...

    def update_label(self, number: int, remove: str, add: str) -> None: ...

    def add_comment(self, number: int, body: str) -> None: ...

    def remove_label(self, number: int, label: str) -> None: ...

    def dispatch_event(self, event_type: str, payload: dict | None = None) -> None: ...

    def get_rate_limit(self) -> dict | None: ...


GitHubIssueClient = GhCliGitHubClient
