"""Convention tests for removed compatibility shim modules (sumipan/nexus#6451)."""

from __future__ import annotations

import importlib

import pytest

_PIPELINE_WORKFLOW_SHIMS = (
    "ghdag.pipeline.audit",
    "ghdag.pipeline.audit_query",
    "ghdag.pipeline.hooks",
    "ghdag.pipeline.result",
    "ghdag.pipeline.submit",
    "ghdag.workflow.engine",
    "ghdag.workflow.schema",
)


@pytest.mark.parametrize("module_name", _PIPELINE_WORKFLOW_SHIMS)
def test_removed_pipeline_workflow_shim_is_not_importable(module_name: str) -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module_name)
