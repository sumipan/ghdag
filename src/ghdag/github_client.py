"""GitHub REST/GraphQL client — urllib-based Layer 1 API.

Used by issuesmith and other tools that need gh-compatible GitHub access
without the gh CLI. Uses stdlib urllib only (no requests).
"""

from __future__ import annotations

import io
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from typing import Any, Protocol, cast, runtime_checkable

from ghdag.exceptions import (
    AuthError,
    GitHubApiError,
    NetworkError,
    PermissionDeniedError,
    RateLimitError,
)

API_BASE = "https://api.github.com"
GRAPHQL_URL = "https://api.github.com/graphql"
DEFAULT_REPO = "sumipan/nexus"


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


def _resolve_token(token: str | None = None) -> str:
    value = token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not value:
        raise AuthError("GITHUB_TOKEN is not set")
    return value


def _resolve_repos() -> list[tuple[str, str]]:
    """GITHUB_REPOSITORIES（カンマ区切り owner/repo リスト）を解決する。"""
    raw = os.environ.get("GITHUB_REPOSITORIES", "")
    repos: list[tuple[str, str]] = []
    for part in raw.split(","):
        part = part.strip()
        if not part or "/" not in part:
            continue
        owner, repo = (s.strip() for s in part.split("/", 1))
        if owner and repo:
            repos.append((owner, repo))
    if not repos:
        raise EnvironmentError(
            "GITHUB_REPOSITORIES environment variable is required "
            "(comma-separated 'owner/repo' list)"
        )
    return repos


def _resolve_repo(repo: str | None = None) -> tuple[str, str]:
    if repo:
        repo = repo.strip()
        if "/" not in repo:
            raise ValueError(f"Invalid repo format: {repo!r}")
        owner, name = (p.strip() for p in repo.split("/", 1))
        return owner, name

    raw = os.environ.get("GITHUB_REPOSITORIES", "").strip()
    if raw:
        first = raw.split(",")[0].strip()
        if "/" in first:
            owner, name = (p.strip() for p in first.split("/", 1))
            if owner and name:
                return owner, name

    owner, name = (p.strip() for p in DEFAULT_REPO.split("/", 1))
    return owner, name


def _api_path(path: str, owner: str, repo: str) -> str:
    """Expand :owner/:repo placeholders in gh-style API paths."""
    path = path.lstrip("/")
    path = path.replace(":owner/:repo", f"{owner}/{repo}")
    path = path.replace("{owner}", owner).replace("{repo}", repo)
    if not path.startswith("repos/"):
        path = f"repos/{owner}/{repo}/{path}"
    return f"/{path}"


class GitHubClient:
    """GitHub REST API client backed by urllib."""

    def __init__(self, token: str | None = None, repo: str | None = None) -> None:
        self._token = _resolve_token(token)
        self._owner, self._repo = _resolve_repo(repo)
        self._repo_full = f"{self._owner}/{self._repo}"

    @property
    def repo(self) -> str:
        return self._repo_full

    def _headers(self, accept: str = "application/vnd.github+json") -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": accept,
            "User-Agent": "ghdag-github-client",
        }

    def _request(
        self,
        method: str,
        path: str,
        *,
        repo: str | None = None,
        params: dict[str, str] | None = None,
        body: dict[str, Any] | list[Any] | None = None,
        accept: str = "application/vnd.github+json",
        raw: bool = False,
    ) -> Any:
        if repo:
            owner, repo_name = _resolve_repo(repo)
        else:
            owner, repo_name = self._owner, self._repo

        if path.startswith("http"):
            url = path
        elif path.startswith("/"):
            url = f"{API_BASE}{path}"
        else:
            url = f"{API_BASE}{_api_path(path, owner, repo_name)}"

        if params:
            qs = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
            url = f"{url}?{qs}" if qs else url

        data: bytes | None = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")

        req = urllib.request.Request(
            url, data=data, method=method.upper(), headers=self._headers(accept)
        )
        if data is not None:
            req.add_header("Content-Type", "application/json")

        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                payload = resp.read()
        except urllib.error.HTTPError as exc:
            msg = exc.read().decode("utf-8", errors="replace")
            try:
                detail = json.loads(msg).get("message", msg)
            except json.JSONDecodeError:
                detail = msg or exc.reason
            error_msg = f"GitHub API {method} {url} failed ({exc.code}): {detail}"
            if exc.code == 401:
                raise AuthError(error_msg, status_code=401) from exc
            if exc.code == 403:
                remaining = exc.headers.get("X-RateLimit-Remaining") if exc.headers else None
                if remaining == "0":
                    raise RateLimitError(error_msg, status_code=403) from exc
                raise PermissionDeniedError(error_msg, status_code=403) from exc
            raise GitHubApiError(error_msg, status_code=exc.code) from exc
        except urllib.error.URLError as exc:
            raise NetworkError(f"Network error: {exc.reason}") from exc

        if raw:
            return payload.decode("utf-8", errors="replace")
        if not payload:
            return None
        return json.loads(payload.decode("utf-8"))

    def _paginate(self, path: str, *, repo: str | None = None) -> list[Any]:
        items: list[Any] = []
        url: str | None = path if path.startswith("http") else None
        while True:
            if url:
                chunk = self._request("GET", url)
            else:
                chunk = self._request("GET", path, repo=repo)
            if isinstance(chunk, list):
                items.extend(chunk)
            else:
                items.append(chunk)
            break
        return items

    def issue_get(self, number: int, fields: list[str] | None = None) -> dict:
        raw = self._request("GET", f"/repos/{self._owner}/{self._repo}/issues/{number}")
        if fields is None:
            return cast(dict[str, Any], raw)

        out: dict[str, Any] = {}
        for field in fields:
            if field == "comments":
                comments = self._request(
                    "GET", f"/repos/{self._owner}/{self._repo}/issues/{number}/comments"
                )
                out["comments"] = [
                    {
                        "body": c.get("body", ""),
                        "author": {"login": (c.get("user") or {}).get("login", "")},
                        "createdAt": c.get("created_at", ""),
                    }
                    for c in (comments or [])
                ]
            elif field == "labels":
                out["labels"] = [
                    {"name": label.get("name", "")} for label in raw.get("labels", [])
                ]
            elif field == "milestone":
                ms = raw.get("milestone")
                out["milestone"] = (
                    {
                        "number": ms.get("number"),
                        "title": ms.get("title"),
                    }
                    if ms
                    else None
                )
            elif field == "state":
                out["state"] = raw.get("state", "").upper()
            else:
                out[field] = raw.get(field)
        return out

    def issue_update(
        self,
        number: int,
        *,
        body: str | None = None,
        labels_add: list[str] | None = None,
        labels_remove: list[str] | None = None,
        milestone: int | None = None,
    ) -> None:
        patch: dict[str, Any] = {}
        if body is not None:
            patch["body"] = body
        if milestone is not None:
            patch["milestone"] = milestone
        if patch:
            self._request(
                "PATCH",
                f"/repos/{self._owner}/{self._repo}/issues/{number}",
                body=patch,
            )
        for label in labels_remove or []:
            enc = urllib.parse.quote(label, safe="")
            self._request(
                "DELETE",
                f"/repos/{self._owner}/{self._repo}/issues/{number}/labels/{enc}",
            )
        if labels_add:
            self._request(
                "POST",
                f"/repos/{self._owner}/{self._repo}/issues/{number}/labels",
                body=labels_add,
            )

    def issue_comment(self, number: int, body: str) -> dict:
        return cast(
            dict[str, Any],
            self._request(
                "POST",
                f"/repos/{self._owner}/{self._repo}/issues/{number}/comments",
                body={"body": body},
            ),
        )

    def issue_close(self, number: int) -> None:
        self._request(
            "PATCH",
            f"/repos/{self._owner}/{self._repo}/issues/{number}",
            body={"state": "closed"},
        )

    def issue_create(
        self,
        title: str,
        body: str,
        *,
        labels: list[str] | None = None,
        milestone: int | None = None,
    ) -> int:
        payload: dict[str, Any] = {"title": title, "body": body}
        if labels:
            payload["labels"] = labels
        if milestone is not None:
            payload["milestone"] = milestone
        result = self._request(
            "POST", f"/repos/{self._owner}/{self._repo}/issues", body=payload
        )
        return int(result["number"])

    def issue_timeline(self, number: int) -> list[dict]:
        return self._paginate(
            f"/repos/{self._owner}/{self._repo}/issues/{number}/timeline"
        )

    def pr_list(
        self,
        *,
        head: str | None = None,
        state: str | None = None,
        search: str | None = None,
        repo: str | None = None,
        limit: int = 30,
    ) -> list[dict]:
        owner, repo_name = _resolve_repo(repo) if repo else (self._owner, self._repo)
        params: dict[str, str] = {"per_page": str(min(limit, 100)), "state": state or "open"}
        if head:
            if ":" in head:
                params["head"] = head
            else:
                params["head"] = f"{owner}:{head}"
        pulls = self._request(
            "GET",
            f"/repos/{owner}/{repo_name}/pulls",
            params=params,
            repo=repo,
        )
        items = pulls or []
        if search:
            q = search.lower()
            items = [
                p
                for p in items
                if q in (p.get("title") or "").lower()
                or q in (p.get("head", {}).get("ref") or "").lower()
                or q.replace("head:", "") in (p.get("head", {}).get("ref") or "").lower()
            ]
        return self._normalize_prs(items[:limit], owner, repo_name)

    def _normalize_prs(self, pulls: list[dict], owner: str, repo: str) -> list[dict]:
        out = []
        for p in pulls:
            out.append(
                {
                    "number": p.get("number"),
                    "title": p.get("title"),
                    "url": p.get("html_url"),
                    "state": (p.get("state") or "").upper(),
                    "headRefName": (p.get("head") or {}).get("ref"),
                    "mergeStateStatus": p.get("mergeable_state", "").upper()
                    if p.get("mergeable_state")
                    else "UNKNOWN",
                    "mergeable": "MERGEABLE"
                    if p.get("mergeable") is True
                    else ("CONFLICTING" if p.get("mergeable") is False else "UNKNOWN"),
                    "additions": p.get("additions"),
                    "deletions": p.get("deletions"),
                }
            )
        return out

    def pr_get(self, number: int, *, repo: str | None = None) -> dict:
        owner, repo_name = _resolve_repo(repo) if repo else (self._owner, self._repo)
        p = self._request(
            "GET", f"/repos/{owner}/{repo_name}/pulls/{number}", repo=repo
        )
        files = self._request(
            "GET", f"/repos/{owner}/{repo_name}/pulls/{number}/files", repo=repo
        )
        return {
            "number": p.get("number"),
            "title": p.get("title"),
            "body": p.get("body"),
            "state": (p.get("state") or "").upper(),
            "url": p.get("html_url"),
            "additions": sum(f.get("additions", 0) for f in (files or [])),
            "deletions": sum(f.get("deletions", 0) for f in (files or [])),
            "changedFiles": len(files or []),
            "files": files or [],
            "mergeStateStatus": (p.get("mergeable_state") or "UNKNOWN").upper(),
            "mergeable": "MERGEABLE"
            if p.get("mergeable") is True
            else ("CONFLICTING" if p.get("mergeable") is False else "UNKNOWN"),
            "reviewDecision": "",
            "statusCheckRollup": [],
        }

    def pr_diff(self, number: int, *, repo: str | None = None) -> str:
        owner, repo_name = _resolve_repo(repo) if repo else (self._owner, self._repo)
        return cast(
            str,
            self._request(
                "GET",
                f"/repos/{owner}/{repo_name}/pulls/{number}",
                accept="application/vnd.github.diff",
                raw=True,
                repo=repo,
            ),
        )

    def pr_create(
        self,
        base: str,
        head: str,
        title: str,
        body: str,
        *,
        repo: str | None = None,
    ) -> str:
        owner, repo_name = _resolve_repo(repo) if repo else (self._owner, self._repo)
        if ":" not in head:
            head = f"{owner}:{head}"
        result = self._request(
            "POST",
            f"/repos/{owner}/{repo_name}/pulls",
            body={"title": title, "body": body, "head": head, "base": base},
            repo=repo,
        )
        return str(result.get("html_url", ""))

    def pr_merge(
        self,
        number: int,
        *,
        method: str = "merge",
        delete_branch: bool = True,
        repo: str | None = None,
    ) -> None:
        owner, repo_name = _resolve_repo(repo) if repo else (self._owner, self._repo)
        self._request(
            "PUT",
            f"/repos/{owner}/{repo_name}/pulls/{number}/merge",
            body={"merge_method": method},
            repo=repo,
        )
        if delete_branch:
            pr = self._request(
                "GET", f"/repos/{owner}/{repo_name}/pulls/{number}", repo=repo
            )
            ref = (pr.get("head") or {}).get("ref")
            if ref:
                try:
                    self._request(
                        "DELETE",
                        f"/repos/{owner}/{repo_name}/git/refs/heads/{ref}",
                        repo=repo,
                    )
                except GitHubApiError:
                    pass

    def pr_checks(self, number: int, *, repo: str | None = None) -> list[dict]:
        owner, repo_name = _resolve_repo(repo) if repo else (self._owner, self._repo)
        pr = self._request(
            "GET", f"/repos/{owner}/{repo_name}/pulls/{number}", repo=repo
        )
        sha = pr.get("head", {}).get("sha")
        if not sha:
            return []
        checks = self._request(
            "GET",
            f"/repos/{owner}/{repo_name}/commits/{sha}/check-runs",
            params={"per_page": "100"},
            repo=repo,
        )
        return cast(list[dict[str, Any]], (checks or {}).get("check_runs", []))

    def pr_ready(self, number: int, *, repo: str | None = None) -> None:
        owner, repo_name = _resolve_repo(repo) if repo else (self._owner, self._repo)
        pr = self._request(
            "GET", f"/repos/{owner}/{repo_name}/pulls/{number}", repo=repo
        )
        node_id = pr.get("node_id")
        if not node_id:
            raise RuntimeError(f"PR #{number}: node_id not found")
        mutation = """
        mutation($id: ID!) {
          markPullRequestReadyForReview(input: {pullRequestId: $id}) {
            pullRequest { isDraft }
          }
        }
        """
        payload = json.dumps({"query": mutation, "variables": {"id": node_id}}).encode()
        req = urllib.request.Request(
            GRAPHQL_URL,
            data=payload,
            method="POST",
            headers={
                **self._headers(),
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode())
        if result.get("errors"):
            raise RuntimeError(f"GraphQL error: {result['errors']}")

    def run_get(self, run_id: int, *, repo: str | None = None) -> dict:
        owner, repo_name = _resolve_repo(repo) if repo else (self._owner, self._repo)
        return cast(
            dict[str, Any],
            self._request(
                "GET",
                f"/repos/{owner}/{repo_name}/actions/runs/{run_id}",
                repo=repo,
            ),
        )

    def run_logs_failed(self, run_id: int, *, repo: str | None = None) -> str:
        owner, repo_name = _resolve_repo(repo) if repo else (self._owner, self._repo)
        run = self.run_get(run_id, repo=repo)
        jobs_url = (run.get("jobs_url") or "").replace(API_BASE, "")
        jobs_resp = self._request("GET", jobs_url)
        jobs = (jobs_resp or {}).get("jobs", [])
        failed = [j for j in jobs if j.get("conclusion") == "failure"]
        if not failed:
            return ""

        zip_url = f"{API_BASE}/repos/{owner}/{repo_name}/actions/runs/{run_id}/logs"
        req = urllib.request.Request(zip_url, headers=self._headers())
        with urllib.request.urlopen(req, timeout=120) as resp:
            zdata = resp.read()

        texts: list[str] = []
        with zipfile.ZipFile(io.BytesIO(zdata)) as zf:
            for job in failed:
                name = job.get("name", "")
                for info in zf.infolist():
                    if name.replace("/", "-") in info.filename or name in info.filename:
                        texts.append(zf.read(info).decode("utf-8", errors="replace"))
        return "\n".join(texts)

    def run_rerun_failed(self, run_id: int, *, repo: str | None = None) -> None:
        owner, repo_name = _resolve_repo(repo) if repo else (self._owner, self._repo)
        self._request(
            "POST",
            f"/repos/{owner}/{repo_name}/actions/runs/{run_id}/rerun-failed-jobs",
            repo=repo,
        )

    def milestone_list(self) -> list[dict]:
        return self._request(
            "GET",
            f"/repos/{self._owner}/{self._repo}/milestones",
            params={"state": "all", "per_page": "100"},
        ) or []

    def milestone_create(self, title: str, description: str = "") -> int:
        result = self._request(
            "POST",
            f"/repos/{self._owner}/{self._repo}/milestones",
            body={"title": title, "description": description},
        )
        return int(result["number"])

    def repo_exists(self, repo: str | None = None) -> bool:
        owner, repo_name = _resolve_repo(repo)
        try:
            self._request("GET", f"/repos/{owner}/{repo_name}", repo=repo)
            return True
        except GitHubApiError:
            return False

    def api_request(
        self,
        path: str,
        *,
        method: str = "GET",
        fields: dict[str, str] | None = None,
        repo: str | None = None,
        paginate: bool = False,
    ) -> Any:
        owner, repo_name = _resolve_repo(repo) if repo else (self._owner, self._repo)
        api_path = _api_path(path, owner, repo_name)
        if paginate and method.upper() == "GET":
            return self._paginate(api_path)
        body = dict(fields) if fields else None
        return self._request(method, api_path, body=body, repo=repo)

    # --- GitHubIssuePort compatible methods ---

    def get_issue(self, number: int) -> dict:
        return cast(dict[str, Any], self.issue_get(number))

    def list_issues(self, label: str, state: str = "open") -> list[dict]:
        return cast(
            list[dict[str, Any]],
            self._request(
                "GET",
                f"/repos/{self._owner}/{self._repo}/issues",
                params={"labels": label, "state": state, "per_page": "100"},
            ) or [],
        )

    def get_issue_comments(self, number: int) -> list[dict]:
        raw = self._request(
            "GET",
            f"/repos/{self._owner}/{self._repo}/issues/{number}/comments",
            params={"per_page": "100"},
        ) or []
        return [
            {
                "author": (c.get("user") or {}).get("login", ""),
                "created_at": c.get("created_at", ""),
                "body": c.get("body", ""),
            }
            for c in raw
        ]

    def update_label(self, number: int, remove: str, add: str) -> None:
        self.issue_update(number, labels_remove=[remove], labels_add=[add])

    def add_comment(self, number: int, body: str) -> None:
        self.issue_comment(number, body)

    def remove_label(self, number: int, label: str) -> None:
        self.issue_update(number, labels_remove=[label])

    def dispatch_event(self, event_type: str, payload: dict | None = None) -> None:
        self._request(
            "POST",
            f"/repos/{self._owner}/{self._repo}/dispatches",
            body={"event_type": event_type, "client_payload": payload or {}},
        )

    def get_rate_limit(self) -> dict | None:
        return cast(dict[str, Any], self._request("GET", "/rate_limit"))


def create_github_client(
    *,
    token: str | None = None,
    owner: str | None = None,
    repo: str | None = None,
) -> GitHubIssuePort:
    """単一リポジトリ用クライアントを生成する。

    owner/repo を明示しない場合は GITHUB_REPOSITORIES の先頭エントリを使う。
    """
    token_value = _resolve_token(token)
    if owner and repo:
        return GitHubClient(token=token_value, repo=f"{owner}/{repo}")
    resolved_owner, resolved_repo = _resolve_repos()[0]
    return GitHubClient(token=token_value, repo=f"{resolved_owner}/{resolved_repo}")


def create_github_clients(*, token: str | None = None) -> list[GitHubIssuePort]:
    """GITHUB_REPOSITORIES の各リポジトリに対するクライアントのリストを生成する。"""
    token_value = _resolve_token(token)
    return [
        GitHubClient(token=token_value, repo=f"{owner}/{repo}")
        for owner, repo in _resolve_repos()
    ]


GitHubIssueClient = GitHubClient
