"""Runtime-side checks for curated package ``__all__`` and import boundaries."""

from __future__ import annotations

import importlib
from typing import Any

import pytest

# Issue sumipan/nexus#6587 — runtime modules and their public ``__all__`` contracts.
CURATED_RUNTIME_EXPORTS: dict[str, frozenset[str]] = {
    "ghdag.audit": frozenset(),
    "ghdag.cleanup": frozenset(),
    "ghdag.config": frozenset(),
    "ghdag.core": frozenset(),
    "ghdag.dag": frozenset(
        {
            "AuditHooks",
            "DagConfig",
            "DagEngine",
            "DagHooks",
            "DONE_SUCCESS",
            "FanOutSpec",
            "LEGACY_DONE_DEFERRED",
            "PIPELINE_WAIT_QUOTA",
            "Task",
            "WaitQuotaSignal",
            "build_child_jsonl_record",
            "check_pipeline_status",
            "default_check_rejected",
            "execute_recover",
            "extract_tee_target",
            "parse_fanout_spec",
            "parse_wait_quota_signal",
            "plan_recover",
            "running_uuids_from_queue_dir",
        }
    ),
    "ghdag.files": frozenset({"md_read", "md_write"}),
    "ghdag.github_cli": frozenset(),
    "ghdag.io": frozenset({"done", "exec_jsonl", "queue", "redact", "sessions"}),
    "ghdag.llm": frozenset(
        {
            "ENGINE_DEFAULTS",
            "ENGINE_SPECS",
            "EngineError",
            "EngineModelError",
            "PERMISSIONS",
            "classify_common_failure",
            "container_prefix",
            "supports_capability",
            "Permission",
            "LLMResult",
            "SessionRecord",
            "TEXT_ONLY",
            "DANGEROUS_FULL_ACCESS",
            "call",
            "get_engine_models",
            "list_engines",
            "validate_engine_model",
        }
    ),
    "ghdag.mcp": frozenset(),
    "ghdag.schemas": frozenset(),
    "ghdag.status": frozenset({"StepStatus", "issue_status"}),
    "ghdag.tool": frozenset({"ToolDef"}),
    "ghdag.vcs": frozenset(
        {
            "CommitResult",
            "ConflictError",
            "LocalGitSink",
            "NullSink",
            "OwnershipError",
            "get_sink",
            "git_enabled",
        }
    ),
    "ghdag.quota": frozenset(
        {
            "QuotaGate",
            "QuotaSnapshot",
            "EngineQuotaState",
            "AdmissionDecision",
            "Missing",
        }
    ),
}

# package import path, symbol, definition module for ``from ... import symbol``.
REMOVED_REEXPORTS: tuple[tuple[str, str, str], ...] = (
    ("ghdag.audit", "emit_span", "ghdag.audit.span"),
    ("ghdag.audit", "make_span_id", "ghdag.audit.span"),
    ("ghdag.cleanup", "cleanup_queue", "ghdag.cleanup.orchestrator"),
    ("ghdag.cleanup", "QUEUE_FILE_RE", "ghdag.core.vocabulary"),
    ("ghdag.dag", "DefaultHooks", "ghdag.dag.hooks"),
    ("ghdag.dag", "WAIT_QUOTA_COMPANION_RE", "ghdag.core.vocabulary"),
    ("ghdag.dag", "parse_jsonl", "ghdag.dag.parser"),
    ("ghdag.files", "AppendResult", "ghdag.core.models.files"),
    ("ghdag.files", "AppendStatus", "ghdag.core.models.files"),
    ("ghdag.files", "MdFile", "ghdag.core.models.files"),
    ("ghdag.files", "PathTraversalError", "ghdag.core.models.files"),
    ("ghdag.files", "PromoteResult", "ghdag.core.models.files"),
    ("ghdag.files", "PromoteStatus", "ghdag.core.models.files"),
    ("ghdag.files", "WriteResult", "ghdag.core.models.files"),
    ("ghdag.files", "md_append", "ghdag.files.append"),
    ("ghdag.files", "md_promote", "ghdag.files.promote"),
    ("ghdag.github_cli", "API_BASE", "ghdag.github_client"),
    ("ghdag.github_cli", "GRAPHQL_URL", "ghdag.github_client"),
    ("ghdag.github_cli", "GitHubClient", "ghdag.github_client"),
    ("ghdag.llm", "DEFAULT_ENGINE_MODELS", "ghdag.llm._constants"),
    ("ghdag.llm", "EngineSpec", "ghdag.core.engine_spec"),
    ("ghdag.llm", "InputMode", "ghdag.core.engine_spec"),
    ("ghdag.llm", "JSON_ONLY", "ghdag.core.capabilities"),
    ("ghdag.llm", "LLMParseError", "ghdag.core.exceptions"),
    ("ghdag.llm", "PromptFlag", "ghdag.core.engine_spec"),
    ("ghdag.llm", "SessionStore", "ghdag.llm.session"),
    ("ghdag.llm", "WEB_RESEARCH", "ghdag.core.capabilities"),
    ("ghdag.llm", "build_llm_cmd", "ghdag.llm.engines"),
    ("ghdag.llm", "list_models", "ghdag.llm.engines"),
    ("ghdag.mcp", "McpServer", "ghdag.mcp.server"),
    ("ghdag.mcp", "McpToolRegistry", "ghdag.mcp.tools"),
    ("ghdag.mcp", "TOOL_NAMES", "ghdag.mcp.tools"),
    ("ghdag.pipeline", "ModelValidationError", "ghdag.pipeline.config"),
    ("ghdag.pipeline", "SubmittedStep", "ghdag.pipeline.llm_pipeline"),
    ("ghdag.pipeline", "get_latest_status", "ghdag.io.audit_query"),
    ("ghdag.pipeline", "parse_frontmatter", "ghdag.pipeline.state"),
    ("ghdag.pipeline", "read_task_exit_events", "ghdag.io.audit_query"),
    ("ghdag.tool", "FallbackEntry", "ghdag.tool.schema"),
    ("ghdag.tool", "TOOL_EXIT_CODES", "ghdag.tool.schema"),
    ("ghdag.tool", "ToolRegistry", "ghdag.tool.registry"),
    ("ghdag.tool", "write_tool_fallback_audit", "ghdag.tool.audit"),
    ("ghdag.vcs", "GitSink", "ghdag.vcs.sink"),
    ("ghdag.workflow", "DispatchResult", "ghdag.core.models.workflow"),
    ("ghdag.workflow", "OnTriggerConfig", "ghdag.core.models.workflow"),
)


def _import_from(module: str, name: str) -> Any:
    mod = importlib.import_module(module)
    return getattr(mod, name)


def test_runtime_public_exports_match_curated_contract() -> None:
    """Issue sumipan/nexus#6587 — runtime packages expose only the curated ``__all__`` set."""
    for module_name, expected in CURATED_RUNTIME_EXPORTS.items():
        mod = importlib.import_module(module_name)
        assert frozenset(mod.__all__) == expected, module_name


def test_removed_reexports_require_definition_module() -> None:
    """Issue sumipan/nexus#6587 — former re-exports are not package attributes."""
    for package, name, definition_module in REMOVED_REEXPORTS:
        pkg = importlib.import_module(package)
        assert name not in pkg.__all__, f"{package}.{name} still listed in __all__"
        assert not hasattr(pkg, name), f"{package} still exposes {name!r}"
        snippet = f"from {package} import {name}"
        with pytest.raises(ImportError):
            exec(snippet, {"__name__": "__test__"})
        canonical = _import_from(definition_module, name)
        assert canonical is not None


def test_retained_explicit_imports_remain_available() -> None:
    """Issue sumipan/nexus#6587 — module-defined and submodule symbols stay importable."""
    from ghdag.cleanup import CleanupResult, file_timestamp
    from ghdag.cli.main import main as cli_main
    from ghdag.config import env
    from ghdag.core import command
    from ghdag.dag import AuditHooks
    from ghdag.github_cli import get_forge
    from ghdag.io import audit, audit_query
    from ghdag.llm import _config
    from ghdag.schemas import SCHEMA_NAMES, load_schema
    from ghdag.status import IssueStatus, RunningTask, running_tasks

    assert CleanupResult is not None
    assert callable(file_timestamp)
    assert env is not None
    assert command is not None
    assert AuditHooks is not None
    assert callable(get_forge)
    assert audit is not None
    assert audit_query is not None
    assert _config is not None
    assert SCHEMA_NAMES
    assert callable(load_schema)
    assert IssueStatus is not None
    assert RunningTask is not None
    assert callable(running_tasks)
    assert callable(cli_main)
