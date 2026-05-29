"""workflow/github.py — GitHubIssuePort Protocol + TokenGitHubClient"""

from __future__ import annotations

import logging
import os
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
        raw = self._request("GET", path, params={"per_page": 100}).json()
        return [
            {
                "author": (c.get("user") or {}).get("login", ""),
                "created_at": c.get("created_at", ""),
                "body": c.get("body", ""),
            }
            for c in raw
        ]

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
    raise EnvironmentError("GITHUB_REPOSITORY environment variable is required")


def create_github_client(
    *,
    token: str | None = None,
    owner: str | None = None,
    repo: str | None = None,
) -> GitHubIssuePort:
    token_value = token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token_value:
        raise AuthError("GitHub token is required")
    if owner and repo:
        resolved_owner, resolved_repo = owner, repo
    else:
        resolved_owner, resolved_repo = _resolve_repo()
    return TokenGitHubClient(token_value, resolved_owner, resolved_repo)


GitHubIssueClient = TokenGitHubClient
