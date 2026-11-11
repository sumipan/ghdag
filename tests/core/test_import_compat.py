"""Compatibility tests for legacy import paths (nexus Issue #2655)."""

from __future__ import annotations

import importlib

import pytest

_REMOVED_SHIM_MODULES = (
    "ghdag.exceptions",
    "ghdag.dag.stall_guard",
    "ghdag.dag.state",
    "ghdag.files._rotate",
    "ghdag.files.models",
    "ghdag.llm.capabilities",
    "ghdag.llm.spec",
    "ghdag.metrics.models",
    "ghdag.pipeline.audit",
    "ghdag.pipeline.audit_query",
    "ghdag.pipeline.hooks",
    "ghdag.pipeline.result",
    "ghdag.workflow.engine",
    "ghdag.workflow.schema",
)


@pytest.mark.parametrize("module_name", _REMOVED_SHIM_MODULES)
def test_removed_core_shim_module_is_not_importable(module_name: str) -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module_name)


def test_dag_models_import_compat() -> None:
    from ghdag.dag.models import DagConfig, Task, _RunningProcess

    assert Task.__name__ == "Task"
    assert DagConfig.__name__ == "DagConfig"
    assert _RunningProcess.__name__ == "_RunningProcess"


def test_workflow_schema_import_compat() -> None:
    from ghdag.core.models.workflow import (
        DispatchResult,
        HandlerConfig,
        OnTriggerConfig,
        StepConfig,
        TriggerConfig,
        WorkflowConfig,
    )

    assert StepConfig.__name__ == "StepConfig"
    assert OnTriggerConfig.__name__ == "OnTriggerConfig"
    assert HandlerConfig.__name__ == "HandlerConfig"
    assert TriggerConfig.__name__ == "TriggerConfig"
    assert DispatchResult.__name__ == "DispatchResult"
    assert WorkflowConfig.__name__ == "WorkflowConfig"


def test_workflow_engine_adapter_import_compat() -> None:
    from ghdag.core.command import EngineAdapter, get_adapter

    adapter = get_adapter("claude")
    assert adapter.name == "claude"
    assert hasattr(adapter, "build_exec_record")
    assert EngineAdapter is not None


def test_pipeline_hooks_audithooks_import_compat() -> None:
    from ghdag.dag import AuditHooks

    assert AuditHooks.__name__ == "AuditHooks"


def test_llm_engines_build_llm_cmd_import_compat() -> None:
    from ghdag.llm.engines import build_llm_cmd

    assert callable(build_llm_cmd)


def test_dag_hooks_import_compat() -> None:
    from ghdag.dag.hooks import DagHooks, DefaultHooks

    assert DagHooks.__name__ == "DagHooks"
    assert DefaultHooks.__name__ == "DefaultHooks"


def test_github_client_import_compat() -> None:
    from ghdag.github_client import GitHubClient, GitHubIssuePort

    assert GitHubIssuePort.__name__ == "GitHubIssuePort"
    assert GitHubClient.__name__ == "GitHubClient"


def test_pipeline_order_import_compat() -> None:
    from ghdag.pipeline.order import (
        InlineOrderBuilder,
        OrderBuilder,
        TemplateOrderBuilder,
    )

    assert OrderBuilder.__name__ == "OrderBuilder"
    assert InlineOrderBuilder.__name__ == "InlineOrderBuilder"
    assert TemplateOrderBuilder.__name__ == "TemplateOrderBuilder"


def test_llm_adapters_import_compat() -> None:
    from ghdag.llm.adapters import EngineOutputAdapter, get_output_adapter

    assert EngineOutputAdapter.__name__ == "EngineOutputAdapter"
    assert get_output_adapter("claude") is not None


def test_workflow_import_side_effect_get_output_adapter() -> None:
    """get_output_adapter resolves immediately after importing ghdag.workflow."""
    import ghdag.workflow  # noqa: F401
    from ghdag.llm.adapters import get_output_adapter

    adapter = get_output_adapter("claude")
    assert adapter is not None


def test_residual_definitions_stay_in_domain_modules() -> None:
    import ghdag.dag.hooks as hooks_mod
    import ghdag.dag.models as dag_models_mod

    assert "_RunningProcess" in dag_models_mod.__dict__
    assert "DefaultHooks" in hooks_mod.__dict__


def test_running_process_model_is_internal() -> None:
    from typing import get_type_hints

    import ghdag.dag
    import ghdag.dag.models as dag_models_mod
    import ghdag.dag.task_launcher as task_launcher
    from ghdag.dag.models import _RunningProcess
    from ghdag.dag.task_launcher import TaskLauncher

    assert "RunningTask" not in dag_models_mod.__all__
    assert "RunningTask" not in ghdag.dag.__all__
    assert not hasattr(ghdag.dag, "RunningTask")
    assert dag_models_mod._RunningProcess is _RunningProcess
    hints = get_type_hints(TaskLauncher._join_reader_threads, localns=vars(task_launcher))
    assert hints["rt"] is _RunningProcess


def test_no_llm_to_metrics_direct_import() -> None:
    import subprocess

    result = subprocess.run(
        ["grep", "-r", "from ghdag.metrics", "src/ghdag/llm/"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.stdout.strip() == "", f"Found direct llm→metrics imports:\n{result.stdout}"


def test_no_metrics_to_llm_direct_import() -> None:
    import subprocess

    result = subprocess.run(
        ["grep", "-r", "from ghdag.llm", "src/ghdag/metrics/"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.stdout.strip() == "", f"Found direct metrics→llm imports:\n{result.stdout}"


_HOST_TOP_LEVEL_REPLACEMENTS = (
    ("QueueTaskStore", "ghdag.io.queue", "QueueTaskStore"),
    ("QueueTask", "ghdag.io.queue", "QueueTask"),
    ("DagEngine", "ghdag.dag", "DagEngine"),
    ("WorkflowDispatcher", "ghdag.workflow", "WorkflowDispatcher"),
)


@pytest.mark.parametrize("_, module_name, attr", _HOST_TOP_LEVEL_REPLACEMENTS)
def test_v0129_host_definition_package_imports(_, module_name: str, attr: str) -> None:
    """Regression record: canonical imports for sumipan/nexus#6486 host consumers."""
    mod = importlib.import_module(module_name)
    assert getattr(mod, attr) is not None


def test_v0129_deprecated_top_level_queue_task_store_import() -> None:
    """Transition alias until sumipan/nexus#6486 lands (tools result_delivery / collect)."""
    with pytest.warns(DeprecationWarning, match="ghdag.io.queue.QueueTaskStore"):
        from ghdag import QueueTaskStore  # noqa: F401

    from ghdag.io.queue import QueueTaskStore as canonical

    assert QueueTaskStore is canonical
