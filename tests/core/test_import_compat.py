"""旧 import パスの互換性テスト（nexus Issue #2655）。"""

from __future__ import annotations


def test_exceptions_import_compat() -> None:
    from ghdag.exceptions import (
        AuthError,
        GhdagError,
        GitHubApiError,
    )

    assert issubclass(GhdagError, Exception)
    assert issubclass(GitHubApiError, GhdagError)
    assert issubclass(AuthError, GitHubApiError)


def test_dag_models_import_compat() -> None:
    from ghdag.dag.models import DagConfig, RunningTask, Task

    assert Task.__name__ == "Task"
    assert DagConfig.__name__ == "DagConfig"
    assert RunningTask.__name__ == "RunningTask"


def test_metrics_models_import_compat() -> None:
    from ghdag.metrics.models import FailureClass, TaskMetrics, TokenUsage

    assert FailureClass.TIMEOUT.cause == "transient"
    assert TokenUsage.__name__ == "TokenUsage"
    assert TaskMetrics.__name__ == "TaskMetrics"


def test_files_models_import_compat() -> None:
    from ghdag.files.models import AppendResult, MdFile, PromoteResult, WriteResult

    assert MdFile.__name__ == "MdFile"
    assert AppendResult.__name__ == "AppendResult"
    assert WriteResult.__name__ == "WriteResult"
    assert PromoteResult.__name__ == "PromoteResult"


def test_workflow_schema_import_compat() -> None:
    from ghdag.workflow.schema import (
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


def test_llm_capabilities_import_compat() -> None:
    from ghdag.llm.capabilities import (
        JSON_ONLY,
        PRESETS,
        TEXT_ONLY,
        LLMCapabilities,
    )

    assert LLMCapabilities.__name__ == "LLMCapabilities"
    assert "text_only" in PRESETS
    assert TEXT_ONLY.output_format == "text"
    assert JSON_ONLY.output_format == "json"


def test_llm_spec_import_compat() -> None:
    from ghdag.llm.spec import (
        ENGINE_SPECS,
        DangerFlagPosition,
        EngineSpec,
        InputMode,
    )

    assert EngineSpec.__name__ == "EngineSpec"
    assert "claude" in ENGINE_SPECS
    assert InputMode is not None
    assert DangerFlagPosition is not None


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


def test_workflow_gates_import_compat() -> None:
    from ghdag.workflow.gates import GATE_REGISTRY, GateRule, Violation

    assert GateRule.__name__ == "GateRule"
    assert Violation.__name__ == "Violation"
    assert isinstance(GATE_REGISTRY, dict)


def test_workflow_import_side_effect_get_output_adapter() -> None:
    """import ghdag.workflow 直後に get_output_adapter が解決できること。"""
    import ghdag.workflow  # noqa: F401
    from ghdag.llm.adapters import get_output_adapter

    adapter = get_output_adapter("claude")
    assert adapter is not None


def test_residual_definitions_stay_in_domain_modules() -> None:
    import ghdag.dag.hooks as hooks_mod
    import ghdag.dag.models as dag_models_mod
    import ghdag.llm.capabilities as cap_mod

    assert "RunningTask" in dag_models_mod.__dict__
    assert "DefaultHooks" in hooks_mod.__dict__
    assert "LLMParseError" in cap_mod.__dict__


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
