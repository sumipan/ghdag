"""LocalForge — file-backed ForgePort implementation (nexus #3100).

Data layout under ``<root>/.forge/``:

- ``issues/<n>.json`` — title / body / labels / state / milestone
- ``issues/<n>.comments.jsonl`` — one JSON object per comment
- ``pulls/<n>.json`` — head / base / state / merged / title / body
- ``milestones.json`` — milestone list
- ``counter`` — shared Issue/PR number allocator (locked via O_CREAT|O_EXCL)
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from ghdag.core.exceptions import GitHubApiError


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class LocalForge:
    """ForgePort implementation backed by a local ``.forge/`` directory."""

    def __init__(
        self,
        root: Path,
        *,
        repo: str = "local/forge",
        checks_command: list[str] | None = None,
    ) -> None:
        self._root = Path(root)
        self._forge = self._root / ".forge"
        self._issues_dir = self._forge / "issues"
        self._pulls_dir = self._forge / "pulls"
        self._milestones_path = self._forge / "milestones.json"
        self._counter_path = self._forge / "counter"
        self._lock_path = self._forge / "counter.lock"
        self._repo = repo
        self._checks_command = list(checks_command) if checks_command else None
        self._ensure_layout()

    # --- layout / persistence ---

    def _ensure_layout(self) -> None:
        self._issues_dir.mkdir(parents=True, exist_ok=True)
        self._pulls_dir.mkdir(parents=True, exist_ok=True)
        if not self._milestones_path.exists():
            self._milestones_path.write_text("[]\n", encoding="utf-8")
        if not self._counter_path.exists():
            self._counter_path.write_text("0\n", encoding="utf-8")

    @property
    def repo(self) -> str:
        return self._repo

    def _issue_path(self, number: int) -> Path:
        return self._issues_dir / f"{number}.json"

    def _comments_path(self, number: int) -> Path:
        return self._issues_dir / f"{number}.comments.jsonl"

    def _pull_path(self, number: int) -> Path:
        return self._pulls_dir / f"{number}.json"

    def _read_json(self, path: Path) -> Any:
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_json(self, path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _allocate_number(self) -> int:
        """Allocate the next Issue/PR number using O_CREAT|O_EXCL lock."""
        self._forge.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + 30.0
        while True:
            try:
                fd = os.open(
                    str(self._lock_path),
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                )
            except FileExistsError:
                if time.monotonic() > deadline:
                    raise TimeoutError(
                        f"timed out waiting for forge counter lock at {self._lock_path}"
                    )
                time.sleep(0.01)
                continue
            try:
                raw = (
                    self._counter_path.read_text(encoding="utf-8").strip()
                    if self._counter_path.exists()
                    else "0"
                )
                current = int(raw or "0")
                nxt = current + 1
                self._counter_path.write_text(f"{nxt}\n", encoding="utf-8")
                return nxt
            finally:
                os.close(fd)
                try:
                    self._lock_path.unlink()
                except FileNotFoundError:
                    pass

    def _force_next_number(self, number: int) -> None:
        """Test seam: set counter so the next allocate returns ``number``."""
        if number < 1:
            raise ValueError("number must be >= 1")
        self._ensure_layout()
        self._counter_path.write_text(f"{number - 1}\n", encoding="utf-8")

    def _write_pull(self, data: dict[str, Any]) -> None:
        """Test seam / internal: persist a pull record as-is."""
        number = int(data["number"])
        self._write_json(self._pull_path(number), data)
        # Keep counter ahead of manually written numbers
        current = int(self._counter_path.read_text(encoding="utf-8").strip() or "0")
        if number > current:
            self._counter_path.write_text(f"{number}\n", encoding="utf-8")

    def _load_issue(self, number: int) -> dict[str, Any]:
        path = self._issue_path(number)
        if not path.exists():
            # PRs are also addressable as issues for comment APIs
            pull = self._pull_path(number)
            if pull.exists():
                p = self._read_json(pull)
                return {
                    "number": number,
                    "title": p.get("title", ""),
                    "body": p.get("body", ""),
                    "state": p.get("state", "open"),
                    "labels": p.get("labels", []),
                    "milestone": p.get("milestone"),
                }
            raise GitHubApiError(
                f"Issue #{number} not found",
                status_code=404,
            )
        return cast(dict[str, Any], self._read_json(path))

    def _save_issue(self, data: dict[str, Any]) -> None:
        self._write_json(self._issue_path(int(data["number"])), data)

    def _load_pull(self, number: int) -> dict[str, Any]:
        path = self._pull_path(number)
        if not path.exists():
            raise GitHubApiError(f"PR #{number} not found", status_code=404)
        return cast(dict[str, Any], self._read_json(path))

    def _milestones(self) -> list[dict[str, Any]]:
        return list(self._read_json(self._milestones_path))

    def _save_milestones(self, items: list[dict[str, Any]]) -> None:
        self._write_json(self._milestones_path, items)

    def _milestone_ref(self, milestone: int | None) -> dict[str, Any] | None:
        if milestone is None:
            return None
        for m in self._milestones():
            if int(m.get("number", -1)) == int(milestone):
                return {"number": m["number"], "title": m.get("title", "")}
        return {"number": milestone, "title": ""}

    def _label_names(self, issue: dict[str, Any]) -> list[str]:
        labels = issue.get("labels") or []
        out: list[str] = []
        for lbl in labels:
            if isinstance(lbl, str):
                out.append(lbl)
            elif isinstance(lbl, dict):
                out.append(str(lbl.get("name", "")))
        return [x for x in out if x]

    def _set_labels(self, issue: dict[str, Any], names: list[str]) -> None:
        issue["labels"] = [{"name": n} for n in names]

    # --- Issue ---

    def issue_get(self, number: int, fields: list[str] | None = None) -> dict:
        raw = self._load_issue(number)
        state = str(raw.get("state", "open")).lower()
        full: dict[str, Any] = {
            "number": int(raw["number"]),
            "title": raw.get("title", ""),
            "body": raw.get("body", ""),
            "state": state,
            "labels": [
                {"name": n} for n in self._label_names(raw)
            ],
            "milestone": raw.get("milestone"),
        }
        if fields is None:
            return full

        out: dict[str, Any] = {}
        for field in fields:
            if field == "comments":
                out["comments"] = [
                    {
                        "body": c.get("body", ""),
                        "author": {"login": c.get("author", "")},
                        "createdAt": c.get("created_at", ""),
                    }
                    for c in self.get_issue_comments(number)
                ]
            elif field == "labels":
                out["labels"] = [{"name": n} for n in self._label_names(raw)]
            elif field == "milestone":
                ms = raw.get("milestone")
                if isinstance(ms, dict):
                    out["milestone"] = {
                        "number": ms.get("number"),
                        "title": ms.get("title"),
                    }
                elif ms is None:
                    out["milestone"] = None
                else:
                    out["milestone"] = self._milestone_ref(int(ms))
            elif field == "state":
                out["state"] = state.upper()
            elif field == "number":
                out["number"] = int(raw["number"])
            else:
                out[field] = raw.get(field, full.get(field))
        return out

    def issue_create(
        self,
        title: str,
        body: str,
        *,
        labels: list[str] | None = None,
        milestone: int | None = None,
    ) -> int:
        number = self._allocate_number()
        data: dict[str, Any] = {
            "number": number,
            "title": title,
            "body": body,
            "state": "open",
            "labels": [{"name": n} for n in (labels or [])],
            "milestone": self._milestone_ref(milestone),
        }
        self._save_issue(data)
        return number

    def issue_update(
        self,
        number: int,
        *,
        title: str | None = None,
        body: str | None = None,
        labels_add: list[str] | None = None,
        labels_remove: list[str] | None = None,
        milestone: int | None = None,
    ) -> None:
        data = self._load_issue(number)
        if title is not None:
            data["title"] = title
        if body is not None:
            data["body"] = body
        if milestone is not None:
            data["milestone"] = self._milestone_ref(milestone)
        names = self._label_names(data)
        if labels_remove:
            remove = set(labels_remove)
            names = [n for n in names if n not in remove]
        if labels_add:
            for n in labels_add:
                if n not in names:
                    names.append(n)
        if labels_add is not None or labels_remove is not None:
            self._set_labels(data, names)
        self._save_issue(data)

    def issue_close(self, number: int) -> None:
        data = self._load_issue(number)
        data["state"] = "closed"
        self._save_issue(data)
        pull_path = self._pull_path(number)
        if pull_path.exists():
            pull = self._read_json(pull_path)
            pull["state"] = "closed"
            self._write_json(pull_path, pull)

    def reopen_issue(self, number: int) -> None:
        data = self._load_issue(number)
        data["state"] = "open"
        self._save_issue(data)

    def issue_comment(self, number: int, body: str) -> dict:
        # Ensure issue/PR exists
        self._load_issue(number)
        created_at = _utcnow()
        comment = {
            "id": int(time.time() * 1000),
            "body": body,
            "user": {"login": "local"},
            "author": "local",
            "created_at": created_at,
        }
        path = self._comments_path(number)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(comment, ensure_ascii=False) + "\n")
        return {
            "id": comment["id"],
            "body": body,
            "user": {"login": "local"},
            "created_at": created_at,
        }

    def issue_timeline(self, number: int) -> list[dict]:
        self._load_issue(number)
        events: list[dict[str, Any]] = []
        for c in self.get_issue_comments(number):
            events.append(
                {
                    "event": "commented",
                    "body": c.get("body", ""),
                    "actor": {"login": c.get("author", "")},
                    "created_at": c.get("created_at", ""),
                }
            )
        return events

    def list_issues(self, label: str, state: str = "open") -> list[dict]:
        results: list[dict[str, Any]] = []
        for path in sorted(self._issues_dir.glob("*.json")):
            if path.name.endswith(".comments.json"):
                continue
            data = self._read_json(path)
            names = self._label_names(data)
            if label not in names:
                continue
            st = str(data.get("state", "open")).lower()
            if state != "all" and st != state.lower():
                continue
            results.append(data)
        return results

    def get_issue_comments(self, number: int) -> list[dict]:
        path = self._comments_path(number)
        if not path.exists():
            return []
        out: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            c = json.loads(line)
            out.append(
                {
                    "author": c.get("author")
                    or (c.get("user") or {}).get("login", ""),
                    "created_at": c.get("created_at", ""),
                    "body": c.get("body", ""),
                }
            )
        return out

    def update_label(self, number: int, remove: str, add: str) -> None:
        self.issue_update(number, labels_remove=[remove], labels_add=[add])

    def remove_label(self, number: int, label: str) -> None:
        self.issue_update(number, labels_remove=[label])

    # --- PR ---

    def _normalize_prs(self, pulls: list[dict], owner: str, repo: str) -> list[dict]:
        out = []
        for p in pulls:
            mergeable_raw = p.get("mergeable")
            if mergeable_raw is True:
                mergeable = "MERGEABLE"
            elif mergeable_raw is False:
                mergeable = "CONFLICTING"
            else:
                mergeable = "UNKNOWN"
            mergeable_state = p.get("mergeable_state") or p.get("mergeStateStatus")
            out.append(
                {
                    "number": p.get("number"),
                    "title": p.get("title"),
                    "url": p.get("url")
                    or p.get("html_url")
                    or f"https://github.com/{owner}/{repo}/pull/{p.get('number')}",
                    "state": (p.get("state") or "").upper(),
                    "headRefName": p.get("headRefName")
                    or (
                        p.get("head", {}).get("ref")
                        if isinstance(p.get("head"), dict)
                        else p.get("head")
                    ),
                    "mergeStateStatus": (
                        str(mergeable_state).upper() if mergeable_state else "UNKNOWN"
                    ),
                    "mergeable": mergeable,
                    "additions": p.get("additions"),
                    "deletions": p.get("deletions"),
                }
            )
        return out

    def _git_mergeable(self, base: str, head: str) -> bool | None:
        """Return True/False via git merge-tree, or None if unavailable."""
        if not (self._root / ".git").exists():
            return None
        try:
            # Prefer modern merge-tree --write-tree (git >= 2.38)
            proc = subprocess.run(
                ["git", "merge-tree", "--write-tree", base, head],
                cwd=self._root,
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode == 0:
                return True
            if proc.returncode == 1:
                return False
            # Fallback: classic merge-tree with merge-base
            base_sha = subprocess.check_output(
                ["git", "merge-base", base, head],
                cwd=self._root,
                text=True,
            ).strip()
            tree = subprocess.check_output(
                ["git", "merge-tree", base_sha, base, head],
                cwd=self._root,
                text=True,
            )
            conflicted = "changed in both" in tree.lower() or "CONFLICT" in tree
            return not conflicted
        except (subprocess.CalledProcessError, FileNotFoundError, OSError):
            return None

    def pr_list(
        self,
        *,
        head: str | None = None,
        state: str | None = None,
        search: str | None = None,
        repo: str | None = None,
        limit: int = 30,
    ) -> list[dict]:
        owner, repo_name = (self._repo.split("/", 1) + [""])[:2]
        if repo:
            owner, repo_name = (repo.split("/", 1) + [""])[:2]
        want_state = (state or "open").lower()
        items: list[dict[str, Any]] = []
        for path in sorted(self._pulls_dir.glob("*.json")):
            p = self._read_json(path)
            st = str(p.get("state", "open")).lower()
            if want_state != "all" and st != want_state:
                continue
            head_ref = p.get("head") or ""
            if isinstance(head_ref, dict):
                head_ref = head_ref.get("ref", "")
            if head:
                head_filter = head.split(":")[-1]
                if head_ref != head and head_ref != head_filter:
                    continue
            if search:
                q = search.lower()
                title = (p.get("title") or "").lower()
                if q not in title and q.replace("head:", "") not in head_ref.lower():
                    continue
            # Refresh mergeable when possible for open PRs
            if st == "open" and p.get("mergeable") is None:
                base = p.get("base") or "main"
                mergeable = self._git_mergeable(str(base), str(head_ref))
                p["mergeable"] = mergeable
            items.append(p)
        return self._normalize_prs(items[:limit], owner, repo_name)

    def pr_get(self, number: int, *, repo: str | None = None) -> dict:
        p = self._load_pull(number)
        owner, repo_name = (self._repo.split("/", 1) + [""])[:2]
        if repo:
            owner, repo_name = (repo.split("/", 1) + [""])[:2]
        head_ref = p.get("head") or ""
        if isinstance(head_ref, dict):
            head_ref = head_ref.get("ref", "")
        base = p.get("base") or "main"
        mergeable_raw = p.get("mergeable")
        if mergeable_raw is None and str(p.get("state", "open")).lower() == "open":
            mergeable_raw = self._git_mergeable(str(base), str(head_ref))
        if mergeable_raw is True:
            mergeable = "MERGEABLE"
        elif mergeable_raw is False:
            mergeable = "CONFLICTING"
        else:
            mergeable = "UNKNOWN"
        mergeable_state = p.get("mergeable_state")
        return {
            "number": p.get("number"),
            "title": p.get("title"),
            "body": p.get("body"),
            "state": (p.get("state") or "").upper(),
            "url": p.get("url")
            or f"https://github.com/{owner}/{repo_name}/pull/{number}",
            "headRefName": head_ref,
            "additions": p.get("additions", 0),
            "deletions": p.get("deletions", 0),
            "changedFiles": p.get("changedFiles", 0),
            "files": p.get("files", []),
            "mergeStateStatus": (
                str(mergeable_state).upper() if mergeable_state else "UNKNOWN"
            ),
            "mergeable": mergeable,
            "reviewDecision": "APPROVED",
            "statusCheckRollup": [],
        }

    def pr_create(
        self,
        base: str,
        head: str,
        title: str,
        body: str,
        *,
        repo: str | None = None,
    ) -> str:
        owner, repo_name = (self._repo.split("/", 1) + [""])[:2]
        if repo:
            owner, repo_name = (repo.split("/", 1) + [""])[:2]
        head_ref = head.split(":")[-1]
        number = self._allocate_number()
        mergeable = self._git_mergeable(base, head_ref)
        url = f"https://github.com/{owner}/{repo_name}/pull/{number}"
        data: dict[str, Any] = {
            "number": number,
            "title": title,
            "body": body,
            "state": "open",
            "head": head_ref,
            "base": base,
            "merged": False,
            "url": url,
            "mergeable": mergeable,
            "mergeable_state": None,
            "additions": None,
            "deletions": None,
            "labels": [],
        }
        self._write_json(self._pull_path(number), data)
        # Mirror as issue record for comment/label APIs
        if not self._issue_path(number).exists():
            self._save_issue(
                {
                    "number": number,
                    "title": title,
                    "body": body,
                    "state": "open",
                    "labels": [],
                    "milestone": None,
                }
            )
        return url

    def pr_merge(
        self,
        number: int,
        *,
        method: str = "merge",
        delete_branch: bool = True,
        repo: str | None = None,
    ) -> None:
        del method, repo  # local always uses git merge --no-ff
        p = self._load_pull(number)
        head = p.get("head") or ""
        base = p.get("base") or "main"
        if isinstance(head, dict):
            head = head.get("ref", "")
        if (self._root / ".git").exists():
            subprocess.run(
                ["git", "checkout", str(base)],
                cwd=self._root,
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                [
                    "git",
                    "merge",
                    "--no-ff",
                    str(head),
                    "-m",
                    f"Merge pull request #{number}",
                ],
                cwd=self._root,
                check=True,
                capture_output=True,
                text=True,
            )
            if delete_branch:
                subprocess.run(
                    ["git", "branch", "-d", str(head)],
                    cwd=self._root,
                    check=False,
                    capture_output=True,
                    text=True,
                )
        p["state"] = "closed"
        p["merged"] = True
        p["mergeable"] = None
        self._write_json(self._pull_path(number), p)
        if self._issue_path(number).exists():
            issue = self._read_json(self._issue_path(number))
            issue["state"] = "closed"
            self._save_issue(issue)

    def pr_diff(self, number: int, *, repo: str | None = None) -> str:
        del repo
        p = self._load_pull(number)
        head = p.get("head") or ""
        base = p.get("base") or "main"
        if isinstance(head, dict):
            head = head.get("ref", "")
        if not (self._root / ".git").exists():
            return ""
        try:
            return subprocess.check_output(
                ["git", "diff", f"{base}...{head}"],
                cwd=self._root,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            raise GitHubApiError(
                f"PR #{number} diff failed",
                status_code=500,
            ) from exc

    def pr_checks(self, number: int, *, repo: str | None = None) -> list[dict]:
        del repo
        self._load_pull(number)
        if self._checks_command is None:
            return [
                {
                    "name": "local",
                    "status": "completed",
                    "conclusion": "success",
                }
            ]
        proc = subprocess.run(
            self._checks_command,
            cwd=self._root,
            capture_output=True,
            text=True,
            check=False,
        )
        conclusion = "success" if proc.returncode == 0 else "failure"
        return [
            {
                "name": "local",
                "status": "completed",
                "conclusion": conclusion,
            }
        ]

    def pr_ready(self, number: int, *, repo: str | None = None) -> None:
        del repo
        # Local PRs are always ready-for-review; ensure record exists.
        self._load_pull(number)

    def pr_update(
        self,
        number: int,
        *,
        title: str | None = None,
        body: str | None = None,
    ) -> None:
        p = self._load_pull(number)
        if title is not None:
            p["title"] = title
        if body is not None:
            p["body"] = body
        self._write_json(self._pull_path(number), p)
        if self._issue_path(number).exists():
            issue = self._read_json(self._issue_path(number))
            if title is not None:
                issue["title"] = title
            if body is not None:
                issue["body"] = body
            self._save_issue(issue)

    # --- milestones / runners / misc ---

    def milestone_list(self) -> list[dict]:
        return self._milestones()

    def milestone_create(self, title: str, description: str = "") -> int:
        items = self._milestones()
        number = max((int(m.get("number", 0)) for m in items), default=0) + 1
        items.append(
            {
                "number": number,
                "title": title,
                "description": description,
                "state": "open",
            }
        )
        self._save_milestones(items)
        return number

    def run_get(self, run_id: int, *, repo: str | None = None) -> dict:
        del repo
        raise GitHubApiError(
            f"Actions run {run_id} not found (LocalForge has no Actions)",
            status_code=404,
        )

    def run_logs_failed(self, run_id: int, *, repo: str | None = None) -> str:
        del run_id, repo
        return ""

    def run_rerun_failed(self, run_id: int, *, repo: str | None = None) -> None:
        del run_id, repo
        return None

    def repo_exists(self, repo: str | None = None) -> bool:
        if repo is None:
            return True
        return repo == self._repo

    def dispatch_event(self, event_type: str, payload: dict | None = None) -> None:
        del event_type, payload
        return None

    def get_rate_limit(self) -> dict | None:
        return None
