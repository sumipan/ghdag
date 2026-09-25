"""Convention test: Port/Protocol baseline compatibility for DagHooks, ForgePort, GitHubIssuePort."""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC = _REPO_ROOT / "src" / "ghdag"

_DAGHOOKS_BASELINE: frozenset[str] = frozenset({
    "on_task_start",
    "on_task_success",
    "on_task_failure",
    "on_task_rejected",
    "on_task_dep_failed",
    "on_task_empty_result",
    "on_task_cancelled",
    "on_task_progress",
    "on_shutdown",
    "check_rejected",
    "check_pipeline_status",
    "check_promote_target",
})

_FORGEPORT_BASELINE: frozenset[str] = frozenset({
    "repo",
    "issue_get",
    "issue_create",
    "issue_update",
    "issue_close",
    "reopen_issue",
    "issue_comment",
    "issue_timeline",
    "list_issues",
    "get_issue_comments",
    "update_label",
    "remove_label",
    "pr_list",
    "pr_get",
    "pr_create",
    "pr_merge",
    "pr_diff",
    "pr_checks",
    "pr_ready",
    "pr_update",
    "milestone_list",
    "milestone_create",
    "run_get",
    "run_logs_failed",
    "run_rerun_failed",
    "repo_exists",
    "dispatch_event",
    "get_rate_limit",
})

_GITHUB_ISSUE_PORT_BASELINE: frozenset[str] = frozenset({
    "get_issue",
    "reopen_issue",
    "list_issues",
    "list_all_issues",
    "get_issue_comments",
    "update_label",
    "add_comment",
    "remove_label",
    "dispatch_event",
    "get_rate_limit",
    "get_last_rate_limit",
    "add_sub_issue",
    "list_sub_issues",
    "remove_sub_issue",
    "sub_issues_summary",
})


def _body_is_stub(body: list[ast.stmt]) -> bool:
    """Return True when a function body contains only a docstring and/or ``...``."""
    stmts = list(body)
    if stmts and isinstance(stmts[0], ast.Expr) and isinstance(stmts[0].value, ast.Constant):
        if isinstance(stmts[0].value.value, str):
            stmts = stmts[1:]
    if not stmts:
        return True
    if (
        len(stmts) == 1
        and isinstance(stmts[0], ast.Expr)
        and isinstance(stmts[0].value, ast.Constant)
        and stmts[0].value.value is ...
    ):
        return True
    return False


def protocol_methods(source: str, class_name: str) -> dict[str, bool]:
    """Return ``{method_name: has_default_impl}`` for the named Protocol class.

    Properties are included.  A stub body (docstring + ``...``) counts as no
    default implementation.
    """
    tree = ast.parse(source)
    result: dict[str, bool] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != class_name:
            continue
        for item in node.body:
            if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            has_default = not _body_is_stub(item.body)
            result[item.name] = has_default
    return result


def compat_violations(
    methods: dict[str, bool],
    baseline: frozenset[str],
    changelog_text: str,
    protocol_name: str,
) -> list[str]:
    """Return violation strings for incompatible Protocol changes.

    A new method (not in baseline) without a default implementation is a
    violation unless the CHANGELOG ``### Changed`` or ``### Removed`` sections
    contain a backtick-quoted ``<Protocol>.<method>`` entry.

    A method present in baseline but absent from the Protocol is a violation
    unless similarly documented in the CHANGELOG.
    """
    violations: list[str] = []
    for name, has_default in methods.items():
        if name in baseline:
            continue
        if has_default:
            continue
        marker = f"`{protocol_name}.{name}`"
        if marker in changelog_text:
            continue
        violations.append(f"{protocol_name}.{name}: new method without default impl or CHANGELOG entry")

    for name in baseline:
        if name in methods:
            continue
        marker = f"`{protocol_name}.{name}`"
        if marker in changelog_text:
            continue
        violations.append(f"{protocol_name}.{name}: removed from Protocol without CHANGELOG entry")

    return violations


def _read_changelog_unreleased() -> str:
    changelog = _REPO_ROOT / "CHANGELOG.md"
    text = changelog.read_text(encoding="utf-8")
    start = text.find("## [Unreleased]")
    if start == -1:
        return ""
    end = text.find("\n## ", start + 1)
    return text[start:end] if end != -1 else text[start:]


def _check_impl_coverage(
    protocol_cls: type,
    impl_cls: type,
    protocol_name: str,
    impl_name: str,
) -> list[str]:
    violations: list[str] = []
    for attr in dir(protocol_cls):
        if attr.startswith("_"):
            continue
        if not hasattr(impl_cls, attr):
            violations.append(
                f"{impl_name} missing {protocol_name}.{attr}"
            )
    return violations


def test_daghooks_compat() -> None:
    source = (_SRC / "core" / "ports" / "dag_hooks.py").read_text(encoding="utf-8")
    methods = protocol_methods(source, "DagHooks")
    changelog = _read_changelog_unreleased()
    violations = compat_violations(methods, _DAGHOOKS_BASELINE, changelog, "DagHooks")
    assert violations == [], violations


def test_forgeport_compat() -> None:
    source = (_SRC / "core" / "ports" / "forge.py").read_text(encoding="utf-8")
    methods = protocol_methods(source, "ForgePort")
    changelog = _read_changelog_unreleased()
    violations = compat_violations(methods, _FORGEPORT_BASELINE, changelog, "ForgePort")
    assert violations == [], violations


def test_github_issue_port_compat() -> None:
    source = (_SRC / "core" / "ports" / "github.py").read_text(encoding="utf-8")
    methods = protocol_methods(source, "GitHubIssuePort")
    changelog = _read_changelog_unreleased()
    violations = compat_violations(methods, _GITHUB_ISSUE_PORT_BASELINE, changelog, "GitHubIssuePort")
    assert violations == [], violations


def test_daghooks_impl_coverage() -> None:
    from ghdag.core.ports.dag_hooks import DagHooks
    from ghdag.dag.hooks import DefaultHooks
    violations = _check_impl_coverage(DagHooks, DefaultHooks, "DagHooks", "DefaultHooks")
    assert violations == [], violations


def test_forgeport_impl_coverage() -> None:
    from ghdag.core.ports.forge import ForgePort
    from ghdag.github_client import GitHubClient
    violations = _check_impl_coverage(ForgePort, GitHubClient, "ForgePort", "GitHubClient")
    assert violations == [], violations


def test_github_issue_port_impl_coverage() -> None:
    from ghdag.core.ports.github import GitHubIssuePort
    from ghdag.github_client import GitHubClient
    violations = _check_impl_coverage(GitHubIssuePort, GitHubClient, "GitHubIssuePort", "GitHubClient")
    assert violations == [], violations


def test_compat_violation_detected_for_new_method_no_default() -> None:
    fixture = """\
from typing import Protocol

class DagHooks(Protocol):
    def on_task_start(self, uuid: str) -> None: ...
    def on_new_event(self, uuid: str) -> None: ...
"""
    methods = protocol_methods(fixture, "DagHooks")
    violations = compat_violations(methods, _DAGHOOKS_BASELINE, "", "DagHooks")
    names = [v.split(":")[0] for v in violations]
    assert "DagHooks.on_new_event" in names


def test_compat_no_violation_when_new_method_has_default() -> None:
    fixture = """\
from typing import Protocol

class DagHooks(Protocol):
    def on_task_start(self, uuid: str) -> None: ...
    def on_new_event(self, uuid: str) -> None:
        return None
"""
    methods = protocol_methods(fixture, "DagHooks")
    violations = compat_violations(methods, _DAGHOOKS_BASELINE, "", "DagHooks")
    names = [v.split(":")[0] for v in violations]
    assert "DagHooks.on_new_event" not in names


def test_compat_no_violation_when_changelog_documents_new_method() -> None:
    fixture = """\
from typing import Protocol

class DagHooks(Protocol):
    def on_task_start(self, uuid: str) -> None: ...
    def on_new_event(self, uuid: str) -> None: ...
"""
    methods = protocol_methods(fixture, "DagHooks")
    changelog = "### Changed\n- `DagHooks.on_new_event`: new hook\n"
    violations = compat_violations(methods, _DAGHOOKS_BASELINE, changelog, "DagHooks")
    names = [v.split(":")[0] for v in violations]
    assert "DagHooks.on_new_event" not in names
