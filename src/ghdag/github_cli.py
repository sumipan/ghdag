"""GitHub REST/GraphQL CLI client for ghdag — gh CLI replacement.

Layer 1: GitHubClient — re-exported from ghdag.github_client.
Layer 2: CLI — gh-compatible subcommands for workflow templates.
Forge backend is selected via :func:`get_forge` (``GHDAG_FORGE``).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from typing import Any

from ghdag.core.ports.forge import ForgePort
from ghdag.forge import get_forge as _package_get_forge
from ghdag.github_client import (
    API_BASE,
    DEFAULT_REPO,
    GRAPHQL_URL,
    GitHubClient,
)

__all__ = [
    "GitHubClient",
    "DEFAULT_REPO",
    "API_BASE",
    "GRAPHQL_URL",
    "get_forge",
]

_SIMPLE_JQ_RE = re.compile(
    r"^(\.[a-zA-Z_][\w]*"
    r"|\[\.\w+\[\]\.\w+\]"
    r"|\.\[\d+\]\.\w+"
    r"|\.\w+\.\w+"
    r"|\{\s*[\w:]+\s*:\s*\.\w+.*\}"
    r")$"
)

# repos[/owner/repo]/rest — also accepts :owner/:repo placeholders
_API_REPOS_RE = re.compile(
    r"^repos/(?P<owner>[^/]+)/(?P<repo>[^/]+)(?:/(?P<rest>.*))?$"
)


def get_forge(repo: str | None = None) -> ForgePort:
    """Return a ForgePort for CLI handlers.

    ``GitHubClient(`` appears only inside this factory so unit tests can
    monkeypatch ``ghdag.github_cli.GitHubClient`` while production local mode
    delegates to :func:`ghdag.forge.get_forge`.
    """
    kind = (os.environ.get("GHDAG_FORGE") or "github").strip().lower()
    if kind == "local":
        return _package_get_forge(repo=repo)
    return GitHubClient(repo=repo)


# --- jq helpers ---


def _is_simple_jq(query: str) -> bool:
    q = query.strip()
    if _SIMPLE_JQ_RE.match(q):
        return True
    if q.startswith(".") and "|" not in q and "select" not in q and "//" not in q:
        return True
    return False


def _simple_jq(data: Any, query: str) -> Any:
    q = query.strip()
    if q == ".":
        return data
    if q.startswith("[.") and q.endswith("]"):
        inner = q[2:-1]
        if inner.endswith("[].name") or inner.endswith("[].number"):
            key = inner.split("[].")[-1]
            arr = data if isinstance(data, list) else data.get(inner.split("[")[0].lstrip("."), [])
            if isinstance(arr, list):
                return [item.get(key) if isinstance(item, dict) else item for item in arr]
        if ".labels[].name" in q:
            labels = data.get("labels", []) if isinstance(data, dict) else []
            return [lbl.get("name") for lbl in labels]
    if q.startswith(".[") and ".number" in q:
        idx = int(q.split("[")[1].split("]")[0])
        rest = q.split("]", 1)[1]
        item = data[idx] if isinstance(data, list) else None
        if rest.startswith(".") and isinstance(item, dict):
            return item.get(rest[1:])
    parts = q.lstrip(".").split(".")
    cur: Any = data
    for part in parts:
        if cur is None:
            return None
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def apply_jq(data: Any, query: str | None) -> Any:
    if not query:
        return data
    q = query.strip()
    if _is_simple_jq(q):
        try:
            return _simple_jq(data, q)
        except (IndexError, KeyError, TypeError, ValueError):
            pass
    jq_bin = shutil.which("jq")
    if not jq_bin:
        raise RuntimeError(f"Complex --jq query requires jq binary: {q!r}")
    proc = subprocess.run(
        [jq_bin, "-r" if q.startswith(".") and "{" not in q else "", q],
        input=json.dumps(data),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        proc = subprocess.run(
            [jq_bin, q],
            input=json.dumps(data),
            capture_output=True,
            text=True,
            check=True,
        )
    out = proc.stdout.rstrip("\n")
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return out


def _parse_with_warning(
    parser: argparse.ArgumentParser, args: list[str]
) -> argparse.Namespace:
    ns, unknown = parser.parse_known_args(args)
    if unknown:
        print(f"warning: unknown args ignored: {unknown!r}", file=sys.stderr)
    return ns


def _emit_json(data: Any, jq_query: str | None = None) -> None:
    result = apply_jq(data, jq_query) if jq_query else data
    if isinstance(result, str):
        print(result)
    else:
        print(json.dumps(result, ensure_ascii=False))


def _parse_fields(json_arg: str | None) -> list[str] | None:
    if not json_arg:
        return None
    return [f.strip() for f in json_arg.split(",") if f.strip()]


def _with_repo(client: ForgePort, repo: str | None) -> ForgePort:
    if not repo:
        return client
    return get_forge(repo=repo)


def _expand_api_path(path: str, client: ForgePort) -> str:
    """Normalize gh-style api path; expand :owner/:repo from client.repo."""
    raw = path.lstrip("/")
    owner, _, repo_name = client.repo.partition("/")
    raw = raw.replace(":owner/:repo", f"{owner}/{repo_name}")
    raw = raw.replace("{owner}", owner).replace("{repo}", repo_name)
    if not raw.startswith("repos/"):
        raw = f"repos/{owner}/{repo_name}/{raw}"
    return raw


def _api_via_typed(
    client: ForgePort,
    path: str,
    *,
    method: str,
    fields: dict[str, str],
    repo: str | None,
) -> Any | None:
    """Map common ``api repos/...`` paths to ForgePort methods.

    Returns ``None`` when the path is not a known typed route (caller may
    fall back to ``api_request`` on GitHubClient).
    """
    expanded = _expand_api_path(path, client)
    m = _API_REPOS_RE.match(expanded)
    if not m:
        return None
    owner, repo_name, rest = m.group("owner"), m.group("repo"), m.group("rest") or ""
    slug = f"{owner}/{repo_name}"
    method_u = method.upper()
    rest = rest.rstrip("/")

    if rest == "milestones":
        if method_u == "GET":
            return client.milestone_list()
        if method_u == "POST":
            number = client.milestone_create(
                fields.get("title", ""),
                fields.get("description", ""),
            )
            return {
                "number": number,
                "title": fields.get("title", ""),
                "description": fields.get("description", ""),
            }
        return None

    timeline_m = re.fullmatch(r"issues/(\d+)/timeline", rest)
    if timeline_m and method_u == "GET":
        return client.issue_timeline(int(timeline_m.group(1)))

    pulls_m = re.fullmatch(r"pulls/(\d+)", rest)
    if pulls_m and method_u == "GET":
        data = dict(client.pr_get(int(pulls_m.group(1)), repo=repo or slug))
        # REST ``pulls/{n}`` exposes ``merged``; ForgePort.pr_get omits it.
        if "merged" not in data:
            data["merged"] = str(data.get("state", "")).upper() == "CLOSED"
        return data

    if rest == "" and method_u == "GET":
        if not client.repo_exists(slug):
            from ghdag.core.exceptions import GitHubApiError

            raise GitHubApiError(f"repository {slug} not found", status_code=404)
        return {
            "full_name": slug,
            "name": repo_name,
            "permissions": {"push": True, "pull": True, "admin": False},
        }

    issue_m = re.fullmatch(r"issues/(\d+)", rest)
    if issue_m:
        number = int(issue_m.group(1))
        if method_u == "GET":
            return client.issue_get(number)
        if method_u == "PATCH":
            kwargs: dict[str, Any] = {}
            if "title" in fields:
                kwargs["title"] = fields["title"]
            if "body" in fields:
                kwargs["body"] = fields["body"]
            if "state" in fields:
                state = fields["state"].lower()
                if state == "closed":
                    client.issue_close(number)
                elif state == "open":
                    client.reopen_issue(number)
            if kwargs:
                client.issue_update(number, **kwargs)
            return client.issue_get(number)
        return None

    if rest == "issues" and method_u == "POST":
        number = client.issue_create(
            fields.get("title", ""),
            fields.get("body", ""),
        )
        return {"number": number, "title": fields.get("title", "")}

    if (rest == "pulls" or rest.startswith("pulls?")) and method_u == "GET":
        # Query string is not parsed here; callers use ``pr list`` for filters.
        return client.pr_list(repo=repo or slug)

    return None


def _cli_issue_view(client: ForgePort, args: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="issue view", add_help=False)
    parser.add_argument("number", type=int)
    parser.add_argument("--json")
    parser.add_argument("--jq")
    parser.add_argument("--repo")
    ns = _parse_with_warning(parser, args)
    client = _with_repo(client, ns.repo)
    fields = _parse_fields(ns.json)
    data = client.issue_get(ns.number, fields)
    if fields:
        _emit_json(data, ns.jq)
    else:
        print(data.get("body", ""))
    return 0


def _cli_issue_edit(client: ForgePort, args: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="issue edit", add_help=False)
    parser.add_argument("number", type=int)
    parser.add_argument("--title")
    parser.add_argument("--body")
    parser.add_argument("--body-file")
    parser.add_argument("--add-label", action="append", default=[])
    parser.add_argument("--remove-label", action="append", default=[])
    parser.add_argument("--repo")
    ns = _parse_with_warning(parser, args)
    client = _with_repo(client, ns.repo)
    body = ns.body
    if ns.body_file:
        with open(ns.body_file, encoding="utf-8") as f:
            body = f.read()
    client.issue_update(
        ns.number,
        title=ns.title,
        body=body,
        labels_add=ns.add_label or None,
        labels_remove=ns.remove_label or None,
    )
    return 0


def _cli_issue_comment(client: ForgePort, args: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="issue comment", add_help=False)
    parser.add_argument("number", type=int)
    parser.add_argument("--body", required=True)
    parser.add_argument("--repo")
    ns = _parse_with_warning(parser, args)
    client = _with_repo(client, ns.repo)
    client.issue_comment(ns.number, ns.body)
    return 0


def _cli_issue_close(client: ForgePort, args: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="issue close", add_help=False)
    parser.add_argument("number", type=int)
    ns = _parse_with_warning(parser, args)
    client.issue_close(ns.number)
    return 0


def _cli_issue_create(client: ForgePort, args: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="issue create", add_help=False)
    parser.add_argument("--title", required=True)
    parser.add_argument("--body", default="")
    parser.add_argument("--body-file")
    parser.add_argument("--json")
    parser.add_argument("--jq")
    parser.add_argument("--repo")
    ns = _parse_with_warning(parser, args)
    client = _with_repo(client, ns.repo)
    body = ns.body
    if ns.body_file:
        with open(ns.body_file, encoding="utf-8") as f:
            body = f.read()
    number = client.issue_create(ns.title, body)
    if ns.json:
        _emit_json({"number": number}, ns.jq)
    else:
        print(number)
    return 0


def _cli_pr_edit(client: ForgePort, args: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="pr edit", add_help=False)
    parser.add_argument("number")
    parser.add_argument("--title")
    parser.add_argument("--body")
    parser.add_argument("--repo")
    ns = _parse_with_warning(parser, args)
    client = _with_repo(client, ns.repo)
    num = int(ns.number.lstrip("#").split("/")[-1])
    client.pr_update(num, title=ns.title, body=ns.body)
    return 0


def _cli_pr_list(client: ForgePort, args: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="pr list", add_help=False)
    parser.add_argument("--head")
    parser.add_argument("--state")
    parser.add_argument("--search")
    parser.add_argument("--json")
    parser.add_argument("--jq")
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--repo")
    ns = _parse_with_warning(parser, args)
    client = _with_repo(client, ns.repo)
    data = client.pr_list(
        head=ns.head,
        state=ns.state,
        search=ns.search,
        repo=ns.repo,
        limit=ns.limit,
    )
    _emit_json(data, ns.jq)
    return 0


def _cli_pr_view(client: ForgePort, args: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="pr view", add_help=False)
    parser.add_argument("number")
    parser.add_argument("--json")
    parser.add_argument("--jq")
    parser.add_argument("--repo")
    ns = _parse_with_warning(parser, args)
    client = _with_repo(client, ns.repo)
    num = int(ns.number.lstrip("#").split("/")[-1])
    data = client.pr_get(num, repo=ns.repo)
    if ns.json:
        fields = _parse_fields(ns.json)
        if fields:
            data = {k: data.get(k) for k in fields}
        _emit_json(data, ns.jq)
    else:
        print(json.dumps(data, ensure_ascii=False))
    return 0


def _cli_pr_diff(client: ForgePort, args: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="pr diff", add_help=False)
    parser.add_argument("number")
    parser.add_argument("--repo")
    ns = _parse_with_warning(parser, args)
    client = _with_repo(client, ns.repo)
    num = int(ns.number.lstrip("#"))
    print(client.pr_diff(num, repo=ns.repo), end="")
    return 0


def _cli_pr_create(client: ForgePort, args: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="pr create", add_help=False)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--body", default="")
    parser.add_argument("--repo")
    ns = _parse_with_warning(parser, args)
    client = _with_repo(client, ns.repo)
    url = client.pr_create(ns.base, ns.head, ns.title, ns.body, repo=ns.repo)
    print(url)
    return 0


def _cli_pr_merge(client: ForgePort, args: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="pr merge", add_help=False)
    parser.add_argument("number")
    parser.add_argument("--merge", action="store_true")
    parser.add_argument("--delete-branch", action="store_true", default=False)
    parser.add_argument("--repo")
    ns = _parse_with_warning(parser, args)
    client = _with_repo(client, ns.repo)
    num = int(str(ns.number).lstrip("#"))
    delete = ns.delete_branch or "--delete-branch" in args
    client.pr_merge(num, method="merge", delete_branch=delete, repo=ns.repo)
    return 0


def _cli_pr_checks(client: ForgePort, args: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="pr checks", add_help=False)
    parser.add_argument("number")
    parser.add_argument("--watch", default="true")
    parser.add_argument("--repo")
    ns = _parse_with_warning(parser, args)
    client = _with_repo(client, ns.repo)
    num = int(str(ns.number).lstrip("#"))
    checks = client.pr_checks(num, repo=ns.repo)
    for c in checks:
        print(f"{c.get('name')}\t{c.get('status')}\t{c.get('conclusion')}")
    return 0


def _cli_pr_ready(client: ForgePort, args: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="pr ready", add_help=False)
    parser.add_argument("number")
    parser.add_argument("--repo")
    ns = _parse_with_warning(parser, args)
    client = _with_repo(client, ns.repo)
    num = int(str(ns.number).lstrip("#"))
    client.pr_ready(num, repo=ns.repo)
    return 0


def _cli_api(client: ForgePort, args: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="api", add_help=False)
    parser.add_argument("path")
    parser.add_argument("-X", "--method", default="GET")
    parser.add_argument("-f", "--field", action="append", default=[])
    parser.add_argument("--jq")
    parser.add_argument("--paginate", action="store_true")
    parser.add_argument("--repo")
    ns = _parse_with_warning(parser, args)
    client = _with_repo(client, ns.repo)
    fields: dict[str, str] = {}
    for item in ns.field:
        if "=" in item:
            k, v = item.split("=", 1)
            fields[k] = v
    typed = _api_via_typed(
        client,
        ns.path,
        method=ns.method,
        fields=fields,
        repo=ns.repo,
    )
    if typed is not None:
        data = typed
    else:
        api_request = getattr(client, "api_request", None)
        if api_request is None:
            raise RuntimeError(
                f"Unsupported api path for forge without api_request: {ns.path!r}"
            )
        data = api_request(
            ns.path,
            method=ns.method,
            fields=fields or None,
            repo=ns.repo,
            paginate=ns.paginate,
        )
    _emit_json(data, ns.jq)
    return 0


def _cli_run_view(client: ForgePort, args: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="run view", add_help=False)
    parser.add_argument("run_id", type=int)
    parser.add_argument("--log-failed", action="store_true")
    parser.add_argument("--repo")
    ns = _parse_with_warning(parser, args)
    client = _with_repo(client, ns.repo)
    if ns.log_failed:
        print(client.run_logs_failed(ns.run_id, repo=ns.repo), end="")
    else:
        _emit_json(client.run_get(ns.run_id, repo=ns.repo), None)
    return 0


def _cli_run_rerun(client: ForgePort, args: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="run rerun", add_help=False)
    parser.add_argument("run_id", type=int)
    parser.add_argument("--failed", action="store_true")
    parser.add_argument("--repo")
    ns = _parse_with_warning(parser, args)
    client = _with_repo(client, ns.repo)
    if ns.failed:
        client.run_rerun_failed(ns.run_id, repo=ns.repo)
    return 0


def _cli_repo_view(client: ForgePort, args: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="repo view", add_help=False)
    parser.add_argument("repo", nargs="?")
    ns = _parse_with_warning(parser, args)
    target = ns.repo or client.repo
    if not get_forge(repo=target).repo_exists(target):
        print(f"repository {target} not found", file=sys.stderr)
        return 1
    print(target)
    return 0


def cli_main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    if not argv:
        print("Usage: python -m ghdag.github_cli <command> ...", file=sys.stderr)
        return 1

    try:
        client = get_forge()
    except Exception as exc:
        from ghdag.exceptions import GhdagError
        if isinstance(exc, (GhdagError, ValueError)):
            print(str(exc), file=sys.stderr)
            return 1
        raise

    cmd = argv[0]
    rest = argv[1:]

    handlers: dict[str, Any] = {
        "issue": {
            "view": _cli_issue_view,
            "edit": _cli_issue_edit,
            "comment": _cli_issue_comment,
            "close": _cli_issue_close,
            "create": _cli_issue_create,
        },
        "pr": {
            "list": _cli_pr_list,
            "view": _cli_pr_view,
            "edit": _cli_pr_edit,
            "diff": _cli_pr_diff,
            "create": _cli_pr_create,
            "merge": _cli_pr_merge,
            "checks": _cli_pr_checks,
            "ready": _cli_pr_ready,
        },
        "api": lambda c, a: _cli_api(c, ["api", *a] if a and a[0] != "api" else a),
        "run": {
            "view": _cli_run_view,
            "rerun": _cli_run_rerun,
        },
        "repo": {"view": _cli_repo_view},
    }

    try:
        if cmd == "issue" and rest:
            return int(handlers["issue"][rest[0]](client, rest[1:]))
        if cmd == "pr" and rest:
            return int(handlers["pr"][rest[0]](client, rest[1:]))
        if cmd == "api":
            return _cli_api(client, rest)
        if cmd == "run" and rest:
            return int(handlers["run"][rest[0]](client, rest[1:]))
        if cmd == "repo" and rest:
            return int(handlers["repo"][rest[0]](client, rest[1:]))
    except KeyError as exc:
        print(f"Unknown subcommand: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        from ghdag.exceptions import GhdagError
        if not isinstance(exc, (GhdagError, RuntimeError, ValueError, KeyError)):
            raise
        print(str(exc), file=sys.stderr)
        return 1

    print(f"Unknown command: {cmd}", file=sys.stderr)
    return 1


def main() -> None:
    raise SystemExit(cli_main())


if __name__ == "__main__":
    main()
