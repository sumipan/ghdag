"""Convention tests for removed compatibility shim modules (sumipan/nexus#6451)."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_README = _REPO_ROOT / "README.md"

_PIPELINE_WORKFLOW_SHIMS = (
    "ghdag.pipeline.audit",
    "ghdag.pipeline.audit_query",
    "ghdag.pipeline.hooks",
    "ghdag.pipeline.result",
    "ghdag.pipeline.submit",
    "ghdag.workflow.engine",
    "ghdag.workflow.schema",
)

_ALL_REMOVED_SHIM_FQNS = (
    "ghdag.exceptions",
    "ghdag.dag.stall_guard",
    "ghdag.dag.state",
    "ghdag.files._rotate",
    "ghdag.files.models",
    "ghdag.llm.capabilities",
    "ghdag.llm.spec",
    "ghdag.metrics.models",
    *_PIPELINE_WORKFLOW_SHIMS,
)


@pytest.mark.parametrize("module_name", _PIPELINE_WORKFLOW_SHIMS)
def test_removed_pipeline_workflow_shim_is_not_importable(module_name: str) -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module_name)


def test_readme_omits_removed_shim_paths() -> None:
    """README must not document removed shim module paths or the Module __all__ section."""
    text = _README.read_text(encoding="utf-8")
    assert "### Module `__all__`" not in text
    for fqn in _ALL_REMOVED_SHIM_FQNS:
        assert fqn not in text, f"README still references removed shim {fqn!r}"
    for forbidden in (
        "ghdag.pipeline.submit_order",
        "make_order_record",
        "pipeline/submit.py",
    ):
        assert forbidden not in text, f"README still references removed submission helper {forbidden!r}"
