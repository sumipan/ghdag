"""Orchestration-layer checks for pipeline / workflow ``__all__`` and README table."""

from __future__ import annotations

import importlib
import re
from pathlib import Path

# Issue sumipan/nexus#6587 — orchestration packages.
CURATED_ORCHESTRATION_EXPORTS: dict[str, frozenset[str]] = {
    "ghdag.pipeline": frozenset(
        {
            "AuditContext",
            "PipelineConfig",
            "PipelineState",
            "OrderBuilder",
            "TemplateOrderBuilder",
            "InlineOrderBuilder",
            "TemplateVariableError",
            "build_idempotency_key",
            "get_command_adapter",
            "resolve_models",
            "LLMPipelineAPI",
            "wait_for_result",
            "write_task_exit_audit",
        }
    ),
    "ghdag.workflow": frozenset(
        {
            "ConsumeEntry",
            "HandlerConfig",
            "SkillIOSpec",
            "StepConfig",
            "TriggerConfig",
            "WorkflowConfig",
            "load_workflows",
            "WorkflowDispatcher",
            "transition",
            "typecheck_dag",
        }
    ),
}

_README_PACKAGE_ROW = re.compile(
    r"^\|\s*`ghdag\.(?P<package>[a-z_.]+)`\s*\|\s*(?P<symbols>.+?)\s*\|\s*$"
)
_SYMBOL = re.compile(r"`([^`]+)`")


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


def test_orchestration_public_exports_match_curated_contract() -> None:
    """Issue sumipan/nexus#6587 — pipeline and workflow ``__all__`` match the curated set."""
    for module_name, expected in CURATED_ORCHESTRATION_EXPORTS.items():
        mod = importlib.import_module(module_name)
        assert frozenset(mod.__all__) == expected, module_name


def test_readme_package_exports_match_package_all() -> None:
    """Issue sumipan/nexus#6587 — README table matches runtime ``__all__`` (non-empty packages)."""
    readme_path = Path(__file__).resolve().parents[2] / "README.md"
    table = _parse_readme_package_all_table(readme_path.read_text(encoding="utf-8"))
    modules = list(CURATED_ORCHESTRATION_EXPORTS) + [
        "ghdag.dag",
        "ghdag.files",
        "ghdag.forge",
        "ghdag.io",
        "ghdag.llm",
        "ghdag.metrics",
        "ghdag.quota",
        "ghdag.status",
        "ghdag.tool",
        "ghdag.vcs",
    ]
    for module_name in modules:
        pkg = module_name.removeprefix("ghdag.")
        mod = importlib.import_module(module_name)
        expected = frozenset(mod.__all__)
        if not expected:
            assert pkg not in table, f"README should omit empty __all__ for ghdag.{pkg}"
            continue
        assert pkg in table, f"README missing Package __all__ row for ghdag.{pkg}"
        assert table[pkg] == expected, f"README ghdag.{pkg} symbols differ from __all__"
