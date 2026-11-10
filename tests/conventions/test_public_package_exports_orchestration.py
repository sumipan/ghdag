"""Orchestration-layer checks for metrics / pipeline / workflow public exports and README."""

from __future__ import annotations

import importlib
import re
import types
from pathlib import Path
from typing import Any

_README_PACKAGE_ROW = re.compile(
    r"^\|\s*`ghdag\.(?P<package>[a-z_]+)`\s*\|\s*(?P<symbols>.+?)\s*\|\s*$"
)
_SYMBOL = re.compile(r"`([^`]+)`")


def _assert_public_exports(
    package: types.ModuleType,
    names: frozenset[str],
    sources: dict[str, Any],
) -> None:
    assert names <= frozenset(package.__all__)
    for name in names:
        assert hasattr(package, name), f"{package.__name__} missing attribute {name!r}"
        assert getattr(package, name) is sources[name], f"{package.__name__}.{name} is not the canonical definition"


def test_metrics_public_exports_match_requested_contract() -> None:
    """Issue #6331 — ghdag.metrics re-exports core metric models."""
    import ghdag.metrics as metrics
    from ghdag.core.models import metrics as core_metrics

    names = frozenset({"FailureClass", "TokenUsage"})
    sources = {
        "FailureClass": core_metrics.FailureClass,
        "TokenUsage": core_metrics.TokenUsage,
    }
    _assert_public_exports(metrics, names, sources)


def test_pipeline_public_exports_match_requested_contract() -> None:
    """Issue #6331 — ghdag.pipeline re-exports audit, order, state, and command helpers."""
    import ghdag.pipeline as pipeline
    from ghdag.core import command
    from ghdag.io import audit, exec_jsonl
    from ghdag.pipeline import order

    names = frozenset(
        {
            "AuditContext",
            "write_task_exit_audit",
            "TemplateVariableError",
            "build_idempotency_key",
            "get_adapter",
        }
    )
    sources = {
        "AuditContext": audit.AuditContext,
        "write_task_exit_audit": audit.write_task_exit_audit,
        "TemplateVariableError": order.TemplateVariableError,
        "build_idempotency_key": exec_jsonl.build_idempotency_key,
        "get_adapter": command.get_adapter,
    }
    _assert_public_exports(pipeline, names, sources)


def test_workflow_public_exports_match_requested_contract() -> None:
    """Issue #6331 — ghdag.workflow re-exports state machine and DAG typecheck helpers."""
    import ghdag.workflow as workflow
    from ghdag.workflow import state_machine, typecheck

    names = frozenset({"transition", "ConsumeEntry", "SkillIOSpec", "typecheck_dag"})
    sources = {
        "transition": state_machine.transition,
        "ConsumeEntry": typecheck.ConsumeEntry,
        "SkillIOSpec": typecheck.SkillIOSpec,
        "typecheck_dag": typecheck.typecheck_dag,
    }
    _assert_public_exports(workflow, names, sources)


def _parse_readme_package_all_table(readme_text: str) -> dict[str, frozenset[str]]:
    rows: dict[str, frozenset[str]] = {}
    in_table = False
    for line in readme_text.splitlines():
        if line.strip() == "### Package `__all__`":
            in_table = True
            continue
        if in_table and line.startswith("### "):
            break
        if not in_table:
            continue
        match = _README_PACKAGE_ROW.match(line)
        if not match:
            continue
        package = match.group("package")
        symbols = frozenset(_SYMBOL.findall(match.group("symbols")))
        rows[package] = symbols
    return rows


def test_readme_package_exports_match_package_all() -> None:
    """Issue #6331 — README Package __all__ rows match runtime package __all__ for milestone packages."""
    readme_path = Path(__file__).resolve().parents[2] / "README.md"
    table = _parse_readme_package_all_table(readme_path.read_text(encoding="utf-8"))
    packages = ("dag", "forge", "io", "llm", "metrics", "pipeline", "workflow")
    for pkg in packages:
        mod = importlib.import_module(f"ghdag.{pkg}")
        expected = frozenset(mod.__all__)
        assert pkg in table, f"README missing Package __all__ row for ghdag.{pkg}"
        assert table[pkg] == expected, f"README ghdag.{pkg} symbols differ from __all__"
