"""Convention test: all list-endpoint GETs in GitHubClient must use _paginate.

Uses AST analysis to detect self._request("GET", <path>) calls where <path>
resolves to a collection endpoint (last path segment in COLLECTION_SEGMENTS).
A variable path (non-static) is always a violation: it prevents static analysis
and typically signals a missing _paginate migration.

ALLOWED must stay empty. Adding entries requires a reviewer-approved comment.
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

COLLECTION_SEGMENTS: frozenset[str] = frozenset(
    {
        "comments",
        "pulls",
        "issues",
        "milestones",
        "check-runs",
        "jobs",
        "files",
        "timeline",
        "sub_issues",
        "labels",
        "events",
        "commits",
        "runs",
    }
)

# Reviewer approval required before adding any entry.
ALLOWED: frozenset[str] = frozenset()


def _path_template(node: ast.expr) -> str | None:
    """Return a path template string, or None if the path is not statically known."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for val in node.values:
            if isinstance(val, ast.Constant) and isinstance(val.value, str):
                parts.append(val.value)
            else:
                parts.append("{}")
        return "".join(parts)
    return None


def _last_segment(template: str) -> str:
    path = template.split("?")[0]
    segments = [s for s in path.split("/") if s]
    return segments[-1] if segments else ""


def detect_violations(source: str) -> list[str]:
    """Return 'method:lineno' strings for each offending _request GET call."""
    tree = ast.parse(source)
    violations: list[str] = []

    for top in ast.walk(tree):
        if not isinstance(top, ast.ClassDef) or top.name != "GitHubClient":
            continue
        for item in top.body:
            if not isinstance(item, ast.FunctionDef):
                continue
            method_name = item.name
            if method_name == "_paginate":
                continue
            for node in ast.walk(item):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if not (
                    isinstance(func, ast.Attribute)
                    and func.attr == "_request"
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "self"
                ):
                    continue
                args = node.args
                if not args or not isinstance(args[0], ast.Constant):
                    continue
                if args[0].value != "GET":
                    continue
                if len(args) < 2:
                    continue
                path_node = args[1]
                template = _path_template(path_node)
                if template is None:
                    violations.append(f"{method_name}:{node.lineno}")
                    continue
                seg = _last_segment(template)
                if seg in COLLECTION_SEGMENTS:
                    violations.append(f"{method_name}:{node.lineno}")

    return violations


def test_no_unpaginated_list_endpoints() -> None:
    source = (
        Path(__file__).parent.parent.parent
        / "src"
        / "ghdag"
        / "github_client.py"
    ).read_text(encoding="utf-8")
    violations = set(detect_violations(source))
    unallowed = violations - ALLOWED
    assert not unallowed, (
        f"Unpaginated list-endpoint GETs found: {sorted(unallowed)}\n"
        "All list endpoints must use self._paginate(). "
        "To allow an exception, add a reviewer-approved entry to ALLOWED."
    )


# --- detector self-tests ---


def _wrap(body: str) -> str:
    dedented = textwrap.dedent(body).strip()
    indented = textwrap.indent(dedented, "        ")
    return "class GitHubClient:\n    def some_method(self):\n" + indented + "\n"


def test_detector_collection_path_is_violation() -> None:
    src = _wrap(
        """
        o = r = n = "x"
        self._request("GET", f"/repos/{o}/{r}/issues/{n}/comments")
        """
    )
    assert len(detect_violations(src)) == 1


def test_detector_single_resource_path_is_not_violation() -> None:
    src = _wrap(
        """
        o = r = n = "x"
        self._request("GET", f"/repos/{o}/{r}/issues/{n}")
        """
    )
    assert detect_violations(src) == []


def test_detector_variable_path_is_violation() -> None:
    src = _wrap(
        """
        jobs_url = "/repos/o/r/actions/runs/1/jobs"
        self._request("GET", jobs_url)
        """
    )
    assert len(detect_violations(src)) == 1


def test_detector_post_to_collection_is_not_violation() -> None:
    src = _wrap(
        """
        o = r = n = "x"
        self._request("POST", f"/repos/{o}/{r}/issues/{n}/comments")
        """
    )
    assert detect_violations(src) == []
