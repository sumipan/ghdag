"""workflow/github.py — GitHub クライアント実装とファクトリ。"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Protocol
from urllib import error, request


class AuthError(ValueError):
    """認証情報が不足していることを示すエラー。"""


class GitHubIssuePort(Protocol):
    def get_issue(self, number: int) -> dict: ...
    def dispatch_event(self, event_type: str, payload: dict | None = None) -> None: ...
    def list_issues(self, label: str, state: str = "open") -> list[dict]: ...
    def get_issue_comments(self, number: int) -> list[dict]: ...
    def update_label(self, number: int, remove: str, add: str) -> None: ...
    def add_comment(self, number: int, body: str) -> None: ...
    def get_rate_limit(self) -> dict | None: ...
    def remove_label(self, number: int, label: str) -> None: ...


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
    """GitHub REST API を token で直接呼び出すクライアント。"""

    def __init__(self, token: str, owner: str, repo: str) -> None:
        self.token = token
        self.owner = owner
        self.repo = repo
        self._base_url = f"https://api.github.com/repos/{owner}/{repo}"

    def _request(self, method: str, path: str, body: dict | None = None) -> object:
        url = f"{self._base_url}{path}"
        payload = None if body is None else json.dumps(body).encode("utf-8")
        req = request.Request(
            url,
            method=method,
            data=payload,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "ghdag",
            },
        )
        try:
            with request.urlopen(req) as resp:
                data = resp.read().decode("utf-8")
        except error.HTTPError as exc:
            msg = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"GitHub API error ({exc.code}): {msg}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"GitHub API request failed: {exc}") from exc
        if not data:
            return {}
        return json.loads(data)

    def get_issue(self, number: int) -> dict:
        raw = self._request("GET", f"/issues/{number}")
        if not isinstance(raw, dict):
            raise RuntimeError("Unexpected issue response format")
        return {
            "number": raw.get("number"),
            "title": raw.get("title", ""),
            "body": raw.get("body", ""),
            "labels": raw.get("labels", []),
            "url": raw.get("html_url", ""),
        }

    def dispatch_event(self, event_type: str, payload: dict | None = None) -> None:
        body: dict = {"event_type": event_type}
        if payload:
            body["client_payload"] = payload
        self._request("POST", "/dispatches", body=body)

    def list_issues(self, label: str, state: str = "open") -> list[dict]:
        raw = self._request("GET", f"/issues?labels={label}&state={state}&per_page=100")
        if not isinstance(raw, list):
            raise RuntimeError("Unexpected issues response format")
        return [
            {
                "number": issue.get("number"),
                "title": issue.get("title", ""),
                "body": issue.get("body", ""),
                "labels": issue.get("labels", []),
                "url": issue.get("html_url", ""),
            }
            for issue in raw
            if isinstance(issue, dict)
        ]

    def get_issue_comments(self, number: int) -> list[dict]:
        raw = self._request("GET", f"/issues/{number}/comments")
        if not isinstance(raw, list):
            raise RuntimeError("Unexpected comments response format")
        return [
            {
                "author": c.get("user", {}).get("login", ""),
                "created_at": c.get("created_at", ""),
                "body": c.get("body", ""),
            }
            for c in raw
            if isinstance(c, dict)
        ]

    def update_label(self, number: int, remove: str, add: str) -> None:
        issue = self.get_issue(number)
        labels = [lbl.get("name", "") for lbl in issue.get("labels", []) if isinstance(lbl, dict)]
        labels = [label for label in labels if label and label != remove]
        if add not in labels:
            labels.append(add)
        self._request("PATCH", f"/issues/{number}", body={"labels": labels})

    def add_comment(self, number: int, body: str) -> None:
        self._request("POST", f"/issues/{number}/comments", body={"body": body})

    def get_rate_limit(self) -> dict | None:
        req = request.Request(
            "https://api.github.com/rate_limit",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "ghdag",
            },
        )
        try:
            with request.urlopen(req) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (error.HTTPError, error.URLError, json.JSONDecodeError):
            return None
        rate = data.get("rate", {})
        if not isinstance(rate, dict):
            return None
        return rate

    def remove_label(self, number: int, label: str) -> None:
        issue = self.get_issue(number)
        labels = [lbl.get("name", "") for lbl in issue.get("labels", []) if isinstance(lbl, dict)]
        labels = [name for name in labels if name and name != label]
        self._request("PATCH", f"/issues/{number}", body={"labels": labels})


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


GitHubIssueClient = GhCliGitHubClient
