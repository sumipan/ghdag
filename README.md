# ghdag

ghdag is a generic DAG execution engine extracted from graph-watcher.
Unlike CI-centric orchestrators, ghdag focuses on label-driven GitHub workflow dispatch and local dependency-aware queue execution in one Python package and CLI.

## Status

![stability](https://img.shields.io/badge/stability-pre--1.0-orange)
![version](https://img.shields.io/badge/version-v0.34.0-blue)
![ci](https://github.com/sumipan/ghdag/actions/workflows/test.yml/badge.svg?branch=main)
![python](https://img.shields.io/badge/python-%3E%3D3.10-blue)
![license](https://img.shields.io/badge/license-MIT-green)

Current release is **v0.34.0** (pre-1.0). Interfaces may evolve before `1.0.0`.

## Installation

```bash
pip install ghdag
```

Install from a specific tag:

```bash
pip install git+https://github.com/sumipan/ghdag.git@v0.34.0
```

| Item | Value |
|---|---|
| Python requirement | `>=3.10` |
| Runtime dependencies | `watchdog>=4.0.0`, `pyyaml>=6.0`, `requests>=2.28.0` |
| Dev dependencies | `pip install "ghdag[dev]"` |

## Quick Start

Create an execution queue and run it:

```jsonl
{"uuid":"demo-0001","command":"echo hello from ghdag","depends":[]}
```

```bash
ghdag run jobs/exec.jsonl
```

Create a minimal workflow file (based on `tests/fixtures/sample-workflow.yml`) and run one dispatch poll:

```yaml
name: sample-pipeline
triggers:
  - label: "pipeline:develop-ready"
    handler: impl
handlers:
  impl:
    steps:
      - template: impl
        model: claude-sonnet-4-6
polling_interval: 30
```

```bash
export GITHUB_TOKEN="ghp_xxx"           # or GH_TOKEN
export GITHUB_REPOSITORIES="owner/repo"
ghdag watch workflows --exec-md jobs/exec.jsonl --once
```

## CLI Reference

Global options: `--verbose`/`-v`, `--quiet`/`-q`.

| Command | Description | Key options/arguments |
|---|---|---|
| `ghdag run` | Run `exec.jsonl` via `DagEngine` | `exec_jsonl`, `--interval`, `--hooks`, `--max-concurrency` |
| `ghdag watch` | Poll GitHub issues and dispatch workflow handlers | `workflows_dir`, `--interval`, `--exec-md`, `--once`, `--pause-file` |
| `ghdag ui` | Launch dashboard server | `--repo-root`, `--host`, `--port`, `--interval`, `--max-visible` |
| `ghdag llm` | Execute one LLM call without workflow dispatch | `prompt`, `--engine`, `--model`, `--timeout`, `--dangerously-skip-permissions`, `--permission-mode`, `--capabilities-preset`, `--stdin`, `--list-engines`, `--list-models`, `--audit-path`, `--correlation-id`, `--request-id` |
| `ghdag version` | Print installed package version | none |
| `ghdag cleanup` | Archive completed/orphaned queue tasks | `repo_root`, `--dry-run`, `--cutoff-days`, `--orphan-days`, `--auto-repair` |
| `ghdag trigger` | Trigger one workflow handler for one issue | `issue_number`, `--handler`, `--workflows-dir`, `--exec-md`, `--workflow` |
| `ghdag audit-query` | Query `audit.jsonl` or detect bursts | `--correlation-id`, `--burst-detect`, `--since`, `--audit-path`, `--window-sec`, `--threshold` |
| `ghdag quota report` | Report engine quota availability | `engine`, `--status`, `--observed-at`, `--resume-at`, `--reason`, `--state-path` |
| `ghdag quota clear` | Clear engine quota pause state | `engine`, `--observed-at`, `--state-path` |
| `ghdag quota status` | Print quota/deferred snapshot JSON | `--state-path` |
| `ghdag tools list` | List tool definitions | `--path`, `--json` |

## Public API

`ghdag.__all__` exports these top-level symbols:

| Symbol | Source module |
|---|---|
| `GhdagError` | `ghdag.exceptions` |
| `QueueTask` | `ghdag.pipeline.result` |
| `QueueTaskStore` | `ghdag.pipeline.result` |
| `LLMPipelineAPI` | `ghdag.pipeline.llm_pipeline` |
| `PipelineState` | `ghdag.pipeline.state` |
| `DagEngine` | `ghdag.dag.engine` |
| `WorkflowDispatcher` | `ghdag.workflow.dispatcher` |
| `QuotaGate` | `ghdag.quota` |

Full `__all__` exports by module:

| Module | Exported symbols (`__all__`) |
|---|---|
| `ghdag` | `GhdagError`, `QueueTask`, `QueueTaskStore`, `LLMPipelineAPI`, `PipelineState`, `DagEngine`, `WorkflowDispatcher`, `QuotaGate` |
| `ghdag.cleanup` | `cleanup_queue`, `CleanupResult`, `file_timestamp`, `QUEUE_FILE_RE` |
| `ghdag.cli` | `main` |
| `ghdag.core` | `command` |
| `ghdag.core.command` | `AdapterNotFoundError`, `EngineAdapter`, `_CAPABILITY_FLAG_BUILDERS`, `_GenericAdapter`, `_build_claude_flags`, `_build_codex_flags`, `_build_cursor_flags`, `_dedupe_extra_args`, `build_llm_cmd`, `get_adapter`, `register_adapter`, `render_exec_command` |
| `ghdag.dag` | `DagConfig`, `DagEngine`, `DagHooks`, `DefaultHooks`, `RunningTask`, `Task`, `check_pipeline_status`, `extract_tee_target`, `parse_jsonl` |
| `ghdag.dag.hooks` | `DagHooks`, `DefaultHooks` |
| `ghdag.dag.models` | `Task`, `DagConfig`, `RunningTask` |
| `ghdag.dag.parser` | `parse_jsonl`, `validate_dependencies` |
| `ghdag.dag.state` | `is_done`, `mark_done`, `load_done_from_dir`, `load_succeeded_from_dir` |
| `ghdag.exceptions` | `GhdagError`, `GitHubApiError`, `AuthError`, `RateLimitError`, `PermissionDeniedError`, `NetworkError` |
| `ghdag.files` | `AppendResult`, `AppendStatus`, `MdFile`, `PathTraversalError`, `PromoteResult`, `PromoteStatus`, `WriteResult`, `md_append`, `md_promote`, `md_read`, `md_write` |
| `ghdag.files._rotate` | `_MAX_AUDIT_BYTES`, `_do_rotate`, `_maybe_rotate` |
| `ghdag.files.links` | `job_footer`, `rewrite_links`, `summary_footer` |
| `ghdag.files.models` | `PathTraversalError`, `MdFile`, `AppendStatus`, `AppendResult`, `WriteResult`, `PromoteStatus`, `PromoteResult` |
| `ghdag.github_cli` | `GitHubClient`, `DEFAULT_REPO`, `API_BASE`, `GRAPHQL_URL` |
| `ghdag.io` | `audit`, `audit_query`, `done`, `exec_jsonl`, `queue`, `sessions` |
| `ghdag.io.audit` | `AuditContext`, `append_audit_record`, `compute_prompt_hash`, `write_audit_log`, `write_llm_audit_log`, `write_llm_inference_audit`, `write_rate_limit_audit`, `write_task_exit_audit`, `_MAX_AUDIT_BYTES`, `_do_rotate`, `_maybe_rotate` |
| `ghdag.io.audit_query` | `read_task_exit_events`, `get_latest_status`, `detect_correlation_bursts`, `get_correlation_top_n` |
| `ghdag.io.exec_jsonl` | `read`, `parse`, `parse_as_dict`, `check_idempotency`, `append`, `remove_by_predicate`, `remove_by_uuids`, `prune`, `load_uuids`, `validate`, `repair`, `extract_uuid` |
| `ghdag.llm` | `_config`, `DEFAULT_ENGINE_MODELS`, `ENGINE_DEFAULTS`, `ENGINE_SPECS`, `EngineModelError`, `EngineSpec`, `LLMCapabilities`, `LLMParseError`, `LLMResult`, `TextResult`, `TEXT_ONLY`, `JSON_ONLY`, `WEB_RESEARCH`, `DANGEROUS_FULL_ACCESS`, `build_llm_cmd`, `call`, `call_text`, `get_engine_models`, `list_engines`, `list_models`, `validate_engine_model` |
| `ghdag.llm.adapters` | `EngineOutputAdapter`, `get_output_adapter` |
| `ghdag.llm.capabilities` | `LLMParseError`, `LLMCapabilities`, `TEXT_ONLY`, `JSON_ONLY`, `WEB_RESEARCH`, `DANGEROUS_FULL_ACCESS`, `READONLY_OBSERVE`, `PRESETS` |
| `ghdag.llm.engines` | `EngineModelError`, `LLMResult`, `TextResult`, `build_llm_cmd`, `call`, `call_text`, `get_engine_models`, `list_engines`, `list_models`, `validate_engine_model`, `ENGINE_CLI`, `ENGINE_DEFAULTS`, `_CAPABILITY_FLAG_BUILDERS`, `_build_claude_flags`, `_build_codex_flags`, `_build_cursor_flags`, `_IGNORED_CAPABILITIES`, `_UNSUPPORTED_CAPABILITIES` |
| `ghdag.llm.spec` | `InputMode`, `DangerFlagPosition`, `EngineSpec`, `ENGINE_SPECS`, `render_exec_command`, `_dedupe_extra_args` |
| `ghdag.metrics` | `MetricsRecorder`, `TaskMetrics` |
| `ghdag.metrics.models` | `FailureClass`, `TokenUsage`, `TaskMetrics` |
| `ghdag.metrics.parsers` | `parse_engine_model`, `parse_token_usage_json`, `parse_token_count` |
| `ghdag.pipeline` | `AuditHooks`, `ModelValidationError`, `PipelineConfig`, `PipelineState`, `OrderBuilder`, `TemplateOrderBuilder`, `InlineOrderBuilder`, `resolve_models`, `build_agent_cmd`, `status_rank`, `parse_frontmatter`, `LLMPipelineAPI`, `SubmittedStep`, `task_status`, `wait_for_result`, `read_task_exit_events`, `get_latest_status`, `STATE_EMPTY`, `STATE_DEFERRED`, `STATE_FAIL`, `STATE_OK`, `STATE_PENDING_DEPS`, `STATE_PENDING_RUN`, `STATE_REJECTED`, `STATE_RUNNING`, `STATE_UNKNOWN_DONE`, `make_order_record`, `submit_order` |
| `ghdag.pipeline.audit` | `AuditContext`, `append_audit_record`, `compute_prompt_hash`, `write_audit_log`, `write_llm_audit_log`, `write_llm_inference_audit`, `write_rate_limit_audit`, `write_task_exit_audit`, `_MAX_AUDIT_BYTES`, `_do_rotate`, `_maybe_rotate` |
| `ghdag.pipeline.audit_query` | `read_task_exit_events`, `get_latest_status`, `detect_correlation_bursts`, `get_correlation_top_n` |
| `ghdag.pipeline.hooks` | `AuditHooks` |
| `ghdag.pipeline.order` | `OrderBuilder`, `InlineOrderBuilder`, `TemplateOrderBuilder`, `TemplateVariableError` |
| `ghdag.pipeline.result` | `QueueTask`, `QueueTaskStore` |
| `ghdag.quota` | `QuotaGate`, `AdmissionDecision`, `QuotaSnapshot`, `QuotaReportResult` |
| `ghdag.tool` | `FallbackEntry`, `TOOL_EXIT_CODES`, `ToolDef`, `ToolRegistry`, `write_tool_fallback_audit` |
| `ghdag.ui.dashboard` | `aggregate_task_status`, `aggregate_token_usage`, `aggregate_cb_firing`, `resolve_audit_path` |
| `ghdag.ui.monitor` | `STATE_PENDING_DEPS`, `STATE_PENDING_RUN`, `STATE_RUNNING`, `STATE_DEFERRED`, `STATE_OK`, `STATE_FAIL`, `STATE_REJECTED`, `STATE_EMPTY`, `STATE_UNKNOWN_DONE`, `read_done_content`, `interpret_done`, `dep_succeeded`, `label_for_done`, `task_status`, `task_state`, `Row`, `MonitorTask`, `build_rows`, `filter_rows`, `relayout_tree_for_visible_rows`, `apply_default_monitor_filters` |
| `ghdag.workflow` | `WorkflowConfig`, `TriggerConfig`, `HandlerConfig`, `StepConfig`, `OnTriggerConfig`, `DispatchResult`, `load_workflows`, `WorkflowDispatcher`, `GitHubIssueClient`, `create_github_client` |
| `ghdag.workflow.engine` | `EngineAdapter`, `_GenericAdapter`, `_CUSTOM_ADAPTERS`, `AdapterNotFoundError`, `register_adapter`, `get_adapter` |
| `ghdag.workflow.gates` | `Violation`, `GateRule`, `GATE_REGISTRY` |
| `ghdag.workflow.schema` | `StepConfig`, `OnTriggerConfig`, `HandlerConfig`, `TriggerConfig`, `DispatchResult`, `WorkflowConfig` |

## Architecture

Module inventory under `src/ghdag`:

| Module | Responsibility |
|---|---|
| `ghdag` | Package initializer and re-exports |
| `ghdag.__main__` | Module entry point for `python -m ghdag` |
| `ghdag.cleanup` | Package initializer and re-exports |
| `ghdag.cleanup.archiver` | Queue cleanup and archive logic |
| `ghdag.cleanup.link_rewriter` | Queue cleanup and archive logic |
| `ghdag.cleanup.orchestrator` | Queue cleanup and archive logic |
| `ghdag.cleanup.orphan_detector` | Queue cleanup and archive logic |
| `ghdag.cleanup.pruner` | Queue cleanup and archive logic |
| `ghdag.cli` | Package initializer and re-exports |
| `ghdag.cli.commands` | Package initializer and re-exports |
| `ghdag.cli.commands.audit_query` | CLI command implementation |
| `ghdag.cli.commands.cleanup` | CLI command implementation |
| `ghdag.cli.commands.llm` | CLI command implementation |
| `ghdag.cli.commands.quota` | CLI command implementation |
| `ghdag.cli.commands.run` | CLI command implementation |
| `ghdag.cli.commands.trigger` | CLI command implementation |
| `ghdag.cli.commands.ui` | CLI command implementation |
| `ghdag.cli.commands.watch` | CLI command implementation |
| `ghdag.cli.main` | CLI command implementation |
| `ghdag.core` | Package initializer and re-exports |
| `ghdag.core.capabilities` | Core models, protocols, and shared primitives |
| `ghdag.core.command` | Core models, protocols, and shared primitives |
| `ghdag.core.engine_spec` | Core models, protocols, and shared primitives |
| `ghdag.core.exceptions` | Core models, protocols, and shared primitives |
| `ghdag.core.models` | Package initializer and re-exports |
| `ghdag.core.models.dag` | Core models, protocols, and shared primitives |
| `ghdag.core.models.files` | Core models, protocols, and shared primitives |
| `ghdag.core.models.metrics` | Core models, protocols, and shared primitives |
| `ghdag.core.models.workflow` | Core models, protocols, and shared primitives |
| `ghdag.core.parsers` | Core models, protocols, and shared primitives |
| `ghdag.core.ports` | Package initializer and re-exports |
| `ghdag.core.ports.dag_hooks` | Core models, protocols, and shared primitives |
| `ghdag.core.ports.gate` | Core models, protocols, and shared primitives |
| `ghdag.core.ports.github` | Core models, protocols, and shared primitives |
| `ghdag.core.ports.order` | Core models, protocols, and shared primitives |
| `ghdag.core.ports.output` | Core models, protocols, and shared primitives |
| `ghdag.core.vocabulary` | Core models, protocols, and shared primitives |
| `ghdag.dag` | Package initializer and re-exports |
| `ghdag.dag._util` | DAG runtime execution logic |
| `ghdag.dag.audit_hooks` | DAG runtime execution logic |
| `ghdag.dag.circuit_breaker` | DAG runtime execution logic |
| `ghdag.dag.engine` | DAG runtime execution logic |
| `ghdag.dag.fanout` | DAG runtime execution logic |
| `ghdag.dag.fanout_manager` | DAG runtime execution logic |
| `ghdag.dag.hooks` | DAG runtime execution logic |
| `ghdag.dag.models` | DAG runtime execution logic |
| `ghdag.dag.parser` | DAG runtime execution logic |
| `ghdag.dag.state` | DAG runtime execution logic |
| `ghdag.dag.task_launcher` | DAG runtime execution logic |
| `ghdag.dag.watcher` | DAG runtime execution logic |
| `ghdag.exceptions` | Shared exception exports |
| `ghdag.files` | Package initializer and re-exports |
| `ghdag.files._rotate` | Markdown file operation utilities |
| `ghdag.files.append` | Markdown file operation utilities |
| `ghdag.files.links` | Package initializer and re-exports |
| `ghdag.files.links.obsidian` | Markdown file operation utilities |
| `ghdag.files.models` | Markdown file operation utilities |
| `ghdag.files.promote` | Markdown file operation utilities |
| `ghdag.files.reader` | Markdown file operation utilities |
| `ghdag.files.writer` | Markdown file operation utilities |
| `ghdag.github_cli` | GitHub API client layer and CLI wrapper |
| `ghdag.github_client` | GitHub API client layer and CLI wrapper |
| `ghdag.io` | Package initializer and re-exports |
| `ghdag.io._rotate` | Filesystem I/O helpers for queue, done, and audit files |
| `ghdag.io.audit` | Filesystem I/O helpers for queue, done, and audit files |
| `ghdag.io.audit_query` | Filesystem I/O helpers for queue, done, and audit files |
| `ghdag.io.done` | Filesystem I/O helpers for queue, done, and audit files |
| `ghdag.io.exec_jsonl` | Filesystem I/O helpers for queue, done, and audit files |
| `ghdag.io.queue` | Filesystem I/O helpers for queue, done, and audit files |
| `ghdag.io.sessions` | Filesystem I/O helpers for queue, done, and audit files |
| `ghdag.llm` | Package initializer and re-exports |
| `ghdag.llm._config` | LLM adapters, engine selection, and capability handling |
| `ghdag.llm._constants` | LLM adapters, engine selection, and capability handling |
| `ghdag.llm.adapters` | Package initializer and re-exports |
| `ghdag.llm.adapters.claude_json` | LLM adapters, engine selection, and capability handling |
| `ghdag.llm.adapters.claude_text` | LLM adapters, engine selection, and capability handling |
| `ghdag.llm.adapters.codex` | LLM adapters, engine selection, and capability handling |
| `ghdag.llm.adapters.cursor` | LLM adapters, engine selection, and capability handling |
| `ghdag.llm.capabilities` | LLM adapters, engine selection, and capability handling |
| `ghdag.llm.engines` | LLM adapters, engine selection, and capability handling |
| `ghdag.llm.spec` | LLM adapters, engine selection, and capability handling |
| `ghdag.maintenance` | Queue maintenance and repair helpers |
| `ghdag.markdown` | Package initializer and re-exports |
| `ghdag.markdown.body_editor` | Markdown body editing utilities |
| `ghdag.metrics` | Package initializer and re-exports |
| `ghdag.metrics.models` | Metrics models, parsing, and persistence |
| `ghdag.metrics.parsers` | Metrics models, parsing, and persistence |
| `ghdag.metrics.recorder` | Metrics models, parsing, and persistence |
| `ghdag.pipeline` | Package initializer and re-exports |
| `ghdag.pipeline.audit` | Pipeline orchestration and task submission |
| `ghdag.pipeline.audit_query` | Pipeline orchestration and task submission |
| `ghdag.pipeline.config` | Pipeline orchestration and task submission |
| `ghdag.pipeline.hooks` | Pipeline orchestration and task submission |
| `ghdag.pipeline.llm_pipeline` | Pipeline orchestration and task submission |
| `ghdag.pipeline.order` | Pipeline orchestration and task submission |
| `ghdag.pipeline.result` | Pipeline orchestration and task submission |
| `ghdag.pipeline.state` | Pipeline orchestration and task submission |
| `ghdag.pipeline.status` | Pipeline orchestration and task submission |
| `ghdag.pipeline.submit` | Pipeline orchestration and task submission |
| `ghdag.pipeline.wait` | Pipeline orchestration and task submission |
| `ghdag.tool` | Package initializer and re-exports |
| `ghdag.tool.audit` | Tool schema, registry, and CLI support |
| `ghdag.tool.cli` | Tool schema, registry, and CLI support |
| `ghdag.tool.exceptions` | Tool schema, registry, and CLI support |
| `ghdag.tool.registry` | Tool schema, registry, and CLI support |
| `ghdag.tool.schema` | Tool schema, registry, and CLI support |
| `ghdag.ui` | Package initializer and re-exports |
| `ghdag.ui.dashboard` | Dashboard monitoring and HTTP/SSE server code |
| `ghdag.ui.monitor` | Dashboard monitoring and HTTP/SSE server code |
| `ghdag.ui.server` | Dashboard monitoring and HTTP/SSE server code |
| `ghdag.workflow` | Package initializer and re-exports |
| `ghdag.workflow.conditional_step` | Workflow polling, routing, and schema handling |
| `ghdag.workflow.dispatcher` | Workflow polling, routing, and schema handling |
| `ghdag.workflow.engine` | Workflow polling, routing, and schema handling |
| `ghdag.workflow.gates` | Package initializer and re-exports |
| `ghdag.workflow.gates.__main__` | Module entry point for `python -m ghdag` |
| `ghdag.workflow.gates.common` | Workflow polling, routing, and schema handling |
| `ghdag.workflow.loader` | Workflow polling, routing, and schema handling |
| `ghdag.workflow.schema` | Workflow polling, routing, and schema handling |
| `ghdag.workflow.state_machine` | Workflow polling, routing, and schema handling |
| `ghdag.workflow.typecheck` | Workflow polling, routing, and schema handling |

## Configuration

### Environment variables

| Variable | Required | Default | Used in |
|---|---|---|---|
| `GITHUB_TOKEN` | One of `GITHUB_TOKEN`/`GH_TOKEN` is required for GitHub API calls | none | `ghdag.github_client` |
| `GH_TOKEN` | Fallback token variable | none | `ghdag.github_client` |
| `GITHUB_REPOSITORIES` | Required for multi-repo watch/list behaviors | none | `ghdag.github_client` |
| `GHDAG_AUDIT_PATH` | Optional | `jobs/audit.jsonl` | `ghdag.ui.dashboard`, `ghdag.cli.commands.llm` |
| `GHDAG_TOKEN_WARN_THRESHOLD` | Optional | `500000` | `ghdag.ui.dashboard` |
| `GHDAG_SAFE_DEFAULT_PERMISSION` | Optional | `text_only` | `ghdag.pipeline.llm_pipeline` |
| `GHDAG_LLM_MODELS` | Optional | `llm-models.yml` in current directory, then built-ins | `ghdag.llm._config` |

Example `llm-models.yml`:

```yaml
engines:
  claude:
    - claude-sonnet-4-6
  codex:
    - gpt-5
  cursor:
    - auto
```

## Error Reference

`GhdagError` base and GitHub API hierarchy:

| Exception | Module | Notes |
|---|---|---|
| `GhdagError` | `ghdag.core.exceptions` | Base exception type |
| `GitHubApiError` | `ghdag.core.exceptions` | GitHub API call failure |
| `AuthError` | `ghdag.core.exceptions` | Authentication/token failure |
| `RateLimitError` | `ghdag.core.exceptions` | Rate limit exhausted |
| `PermissionDeniedError` | `ghdag.core.exceptions` | Permission denied (403 without rate limit exhaustion) |
| `NetworkError` | `ghdag.core.exceptions` | Network/transport failure |

Custom `GhdagError` subclasses verified in `tests/test_exceptions.py::_CUSTOM_EXCEPTIONS`:

| Exception | Module |
|---|---|
| `ValidationError` | `ghdag.workflow.loader` |
| `AdapterNotFoundError` | `ghdag.workflow.engine` |
| `ContextHookError` | `ghdag.workflow.dispatcher` |
| `DependencyError` | `ghdag.pipeline.llm_pipeline` |
| `ModelValidationError` | `ghdag.pipeline.config` |
| `ConfigLoadError` | `ghdag.llm._config` |
| `LLMParseError` | `ghdag.llm.capabilities` |
| `EngineModelError` | `ghdag.llm.engines` |
| `FanoutError` | `ghdag.dag.fanout` |
| `PathTraversalError` | `ghdag.files.models` |
| `AppendRecoverError` | `ghdag.files.append` |

Additional public exception:

| Exception | Module |
|---|---|
| `ToolRegistryError` | `ghdag.tool.exceptions` |

## License

MIT License (SPDX: `MIT`).
