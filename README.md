# ghdag

ghdag is a GitHub-driven DAG workflow engine: label-based issue dispatch and a local dependency-aware queue runner in one Python package and CLI. Unlike CI-centric orchestrators (GitHub Actions, Dagger), intake (`workflow` / `pipeline`) and execution (`dag`) stay separate processes that communicate only through `exec.jsonl` and `jobs/done/` markers.

## Status

![stability](https://img.shields.io/badge/stability-pre--1.0-orange)
![version](https://img.shields.io/badge/version-v0.39.0-blue)
![ci](https://github.com/sumipan/ghdag/actions/workflows/test.yml/badge.svg?branch=main)
![python](https://img.shields.io/badge/python-%3E%3D3.10-blue)
![license](https://img.shields.io/badge/license-MIT-green)

Current release is **v0.39.0** (`0.Y.Z`, pre-1.0). Public interfaces may change before `1.0.0`.

## Installation

Requires **Python >= 3.10**.

```bash
pip install ghdag
```

Install a specific tag:

```bash
pip install git+https://github.com/sumipan/ghdag.git@v0.39.0
```

| Item | Value |
|---|---|
| Python requirement | `>=3.10` (`pyproject.toml` `requires-python`) |
| Runtime dependencies | `watchdog>=4.0.0`, `pyyaml>=6.0`, `requests>=2.28.0` |
| Dev extras | `pip install "ghdag[dev]"` |

## Quick Start

Create a queue file and run it (shape used in `tests/test_ghdag_engine.py`):

```jsonl
{"uuid":"demo-0001","command":"echo hello from ghdag","depends":[]}
```

```bash
ghdag run jobs/exec.jsonl
```

Create a minimal workflow YAML (schema exercised in `tests/test_ghdag_workflow.py`) and poll once:

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

Re-dispatch a handler with a new generation:

```bash
ghdag trigger 123 --handler impl --redispatch --reason "manual retry"
```

Recover failed or pending steps from an existing handler run:

```bash
ghdag dag recover --issue 123 --handler impl --dry-run
ghdag dag recover --issue 123 --handler impl --from p2
```

## CLI Reference

Global options: `--verbose` / `-v`, `--quiet` / `-q`.

### Top-level commands

| Command | Description |
|---|---|
| `ghdag run` | Run `exec.jsonl` via `DagEngine` |
| `ghdag watch` | Poll GitHub issues and dispatch workflow handlers |
| `ghdag trigger` | Trigger one workflow handler for one issue |
| `ghdag dag` | DAG utilities (`recover`) |
| `ghdag llm` | Execute one LLM call without workflow dispatch |
| `ghdag cleanup` | Archive completed/orphaned queue tasks |
| `ghdag quota` | Manage quota gate state |
| `ghdag audit-query` | Query `audit.jsonl` or detect correlation bursts |
| `ghdag ui` | Launch the Web UI dashboard |
| `ghdag tools` | Manage tool definitions |
| `ghdag version` | Print installed package version |

### Subcommands

| Command | Description |
|---|---|
| `ghdag dag recover` | Reset failed/pending steps for re-execution |
| `ghdag tools list` | List tool definitions (`--path`, `--json`) |
| `ghdag quota report` | Report engine quota availability |
| `ghdag quota clear` | Clear engine quota pause state |
| `ghdag quota drain` | Pause new launches for one engine and wait for idle |
| `ghdag quota resume` | Release drain mode for one engine |
| `ghdag quota status` | Print engine-level quota/drain/queue snapshot JSON |

### Key options

| Command | Key options / arguments |
|---|---|
| `ghdag run` | `exec_jsonl`, `--interval`, `--hooks`, `--max-concurrency` |
| `ghdag watch` | `workflows_dir`, `--interval`, `--exec-md`, `--once`, `--pause-file` |
| `ghdag trigger` | `issue_number`, `--handler`, `--workflows-dir`, `--exec-md`, `--workflow`, `--redispatch`, `--reason` |
| `ghdag dag recover` | `--issue`, `--handler`, `--from`, `--dry-run`, `--workflows-dir`, `--exec-md`, `--workflow`, `--state-dir` |
| `ghdag llm` | `prompt`, `--engine`, `--model`, `--timeout`, `--dangerously-skip-permissions`, `--permission-mode`, `--capabilities-preset`, `--stdin`, `--list-engines`, `--list-models`, `--audit-path`, `--correlation-id`, `--request-id` |
| `ghdag cleanup` | `repo_root`, `--dry-run`, `--cutoff-days`, `--orphan-days`, `--auto-repair` |
| `ghdag audit-query` | `--correlation-id`, `--burst-detect`, `--since`, `--audit-path`, `--window-sec`, `--threshold` |
| `ghdag ui` | `--repo-root`, `--host`, `--port`, `--interval`, `--max-visible` |
| `ghdag quota report` | `engine`, `--status`, `--observed-at`, `--resume-at`, `--reason`, `--state-path` |
| `ghdag quota clear` | `engine`, `--observed-at`, `--state-path` |
| `ghdag quota drain` | `engine`, `--reason`, `--state-path` |
| `ghdag quota resume` | `engine`, `--state-path` |
| `ghdag quota status` | `--state-path`, `--exec-path`, `--done-dir` |
| `ghdag tools list` | `--path`, `--json` |

`ghdag trigger --redispatch` increments the handler generation and starts a new run; `--reason` is recorded in `audit.jsonl`.

`ghdag dag recover --dry-run` prints the recover plan without modifying `jobs/done/` markers. `--from STEP_NAME` limits recovery to that step and its downstream dependents.

`ghdag quota status` returns per-engine `quota_status`, `draining`, `queued`, `deferred`, `running`, and `idle`.

## Public API

`ghdag.__all__` exports these top-level symbols:

| Symbol | Source module |
|---|---|
| `GhdagError` | `ghdag.exceptions` (re-export of `ghdag.core.exceptions`) |
| `QueueTask` | `ghdag.pipeline.result` |
| `QueueTaskStore` | `ghdag.pipeline.result` |
| `LLMPipelineAPI` | `ghdag.pipeline.llm_pipeline` |
| `PipelineState` | `ghdag.pipeline.state` |
| `DagEngine` | `ghdag.dag.engine` |
| `WorkflowDispatcher` | `ghdag.workflow.dispatcher` |
| `QuotaGate` | `ghdag.quota` |

Package version is available as `ghdag.__version__`.

Frequently used non-top-level entry points (imported from their modules):

| Symbol | Module |
|---|---|
| `GitHubClient` | `ghdag.github_cli` |
| `build_llm_cmd`, `call`, `call_text`, `call_managed` | `ghdag.llm` |
| `md_read`, `md_write`, `md_append`, `md_promote` | `ghdag.files` |
| `cleanup_queue` | `ghdag.cleanup` |
| `plan_recover`, `execute_recover`, `RecoverPlan`, `RecoverResult`, `RecoverError` | `ghdag.dag.recover` |
| `validate_workflow_roles` | `ghdag.core.models.workflow` |
| `get_gate`, `GateRule`, `Violation` | `ghdag.workflow.gates` |
| `typecheck_dag`, `TypeCheckError` | `ghdag.workflow.typecheck` |

## Architecture

Two towers communicate only through `exec.jsonl` and `jobs/done/` markers: **intake** (`pipeline` / `workflow`) and **execution** (`dag`). Import-linter contracts enforce that boundary.

| Module / package | Role |
|---|---|
| `cleanup/` | Queue archival, orphan detection, link rewriting, pruning |
| `cli/` | CLI entry point (`main.py`) and subcommand handlers |
| `core/` | Foundations: exceptions, command adapters, models, ports, capabilities |
| `dag/` | Local DAG engine, fanout, circuit breaker, recover, session compaction |
| `files/` | Markdown file ops (read, write, append, promote, links) |
| `io/` | Queue, done, audit, `exec.jsonl`, session I/O |
| `llm/` | LLM engines, capabilities presets, adapters, compaction |
| `markdown/` | Issue body H2 section editor |
| `metrics/` | Task metrics, token parsing, `FailureClass` |
| `pipeline/` | Order submission, audit hooks, pipeline state, LLM pipeline API |
| `tool/` | Tool definitions, registry, CLI helpers |
| `ui/` | Web dashboard, SSE monitor, static assets |
| `workflow/` | YAML loader, dispatcher, gates, state machine, typecheck |

Top-level package files: `__init__.py` (public API), `__main__.py` (`python -m ghdag`), `exceptions.py` (re-export shim), `github_cli.py` / `github_client.py`, `maintenance.py`, `quota.py`, `py.typed`.

### Workflow gates (entry-points)

Gate rules resolve through `ghdag.workflow.gates.get_gate`:

1. `GATE_REGISTRY` (import-time registration)
2. `importlib.metadata` entry-points in group `ghdag.gates`

Run a gate against an issue body: `python -m ghdag.workflow.gates --gate NAME --body-file PATH`.

### Role-based quota admission

Workflow YAML may declare `roles` (role name → engine list). Steps reference a `role`; `QuotaGate.check_admission` evaluates all engines in that role. When an engine is paused, `override_until` (TTL pause override) prevents stale pause reports from overwriting a fresher override window.

`validate_workflow_roles(config)` raises `ValueError` when a step references an undeclared role.

### DAG recover

`ghdag.dag.recover` builds a `RecoverPlan` from pipeline state, `exec.jsonl` idempotency keys, and `jobs/done/` markers, then `execute_recover` clears done markers so `DagEngine` re-runs selected steps.

## Configuration

### Environment variables

| Variable | Required | Default | Used in |
|---|---|---|---|
| `GITHUB_TOKEN` | One of `GITHUB_TOKEN` / `GH_TOKEN` for GitHub API calls | none | `ghdag.github_client` |
| `GH_TOKEN` | Fallback token variable | none | `ghdag.github_client` |
| `GITHUB_REPOSITORIES` | Multi-repo watch / list behaviors | none | `ghdag.github_client` |
| `GHDAG_AUDIT_PATH` | Optional | `jobs/audit.jsonl` | `ghdag.ui.dashboard`, `ghdag.cli.commands.llm` |
| `GHDAG_TOKEN_WARN_THRESHOLD` | Optional | `500000` | `ghdag.ui.dashboard` |
| `GHDAG_LLM_MODELS` | Optional | `llm-models.yml` in cwd, then built-ins | `ghdag.llm._config` |
| `GHDAG_SAFE_DEFAULT_PERMISSION` | Optional | `text_only` | `ghdag.pipeline.llm_pipeline` |
| `GHDAG_SESSION_COMPACTION` | Optional (opt-in) | off | `ghdag.dag.task_launcher` (`1` / `true` / `yes` / `on`) |

### Optional `llm-models.yml`

```yaml
engines:
  claude:
    - claude-sonnet-4-6
  codex:
    - gpt-5
  cursor:
    - auto
```

### Quota state file

`ghdag quota` reads/writes JSON state (default path `jobs/quota-gate.json`). Deferred tasks may record `role` and `role_engines` for role-based admission.

## Error Reference

### Exception hierarchy

```
GhdagError (core/exceptions.py)
├── GitHubApiError (core/exceptions.py)
│   ├── AuthError
│   ├── RateLimitError
│   ├── PermissionDeniedError
│   └── NetworkError
├── ModelValidationError (pipeline/config.py)
├── DependencyError (pipeline/llm_pipeline.py) *
├── EngineModelError (llm/engines.py)
├── LLMParseError (llm/capabilities.py)
├── ConfigLoadError (llm/_config.py) *
├── AdapterNotFoundError (core/command.py) *
├── FanoutError (dag/fanout.py) *
├── ValidationError (workflow/loader.py) *
├── ContextHookError (workflow/dispatcher.py) *
├── AppendRecoverError (files/append.py) *
├── PathTraversalError (core/models/files.py) *
└── ToolRegistryError (tool/exceptions.py)
```

`*` = also inherits `ValueError` (catchable with `except ValueError`).

Outside the `GhdagError` tree:

| Type | Module | Notes |
|---|---|---|
| `RecoverError` | `dag/recover.py` | Recover plan/execution failure |
| `TemplateVariableError` | `pipeline/order.py` | Template variable missing (`ValueError` + `KeyError`) |
| `TypeCheckError` | `workflow/typecheck.py` | Static skill I/O mismatch (dataclass, not `Exception`) |

`ghdag.exceptions` re-exports `GhdagError`, `GitHubApiError`, `AuthError`, `RateLimitError`, `PermissionDeniedError`, and `NetworkError` for backward compatibility.

### Exception reference table

| Exception | Module | Notes |
|---|---|---|
| `GhdagError` | `ghdag.core.exceptions` | Base exception type |
| `GitHubApiError` | `ghdag.core.exceptions` | GitHub API call failure |
| `AuthError` | `ghdag.core.exceptions` | Authentication / token failure |
| `RateLimitError` | `ghdag.core.exceptions` | Rate limit exhausted |
| `PermissionDeniedError` | `ghdag.core.exceptions` | 403 without rate-limit exhaustion |
| `NetworkError` | `ghdag.core.exceptions` | Network / transport failure |
| `ModelValidationError` | `ghdag.pipeline.config` | LLM model validation failure |
| `DependencyError` | `ghdag.pipeline.llm_pipeline` | Pipeline dependency failure (`ValueError`) |
| `EngineModelError` | `ghdag.llm.engines` | Unknown engine or model |
| `LLMParseError` | `ghdag.llm.capabilities` | LLM response parse failure |
| `ConfigLoadError` | `ghdag.llm._config` | LLM config load failure (`ValueError`) |
| `AdapterNotFoundError` | `ghdag.core.command` | Engine adapter not registered (`ValueError`) |
| `FanoutError` | `ghdag.dag.fanout` | DAG fanout failure (`ValueError`) |
| `ValidationError` | `ghdag.workflow.loader` | Workflow schema validation failure (`ValueError`) |
| `ContextHookError` | `ghdag.workflow.dispatcher` | Context hook failure (`ValueError`) |
| `AppendRecoverError` | `ghdag.files.append` | Markdown append recovery failure (`ValueError`) |
| `PathTraversalError` | `ghdag.core.models.files` | Path traversal attempt detected (`ValueError`) |
| `ToolRegistryError` | `ghdag.tool.exceptions` | Tool registry error |
| `RecoverError` | `ghdag.dag.recover` | Recover cannot proceed |
| `TemplateVariableError` | `ghdag.pipeline.order` | Missing template variable |
| `TypeCheckError` | `ghdag.workflow.typecheck` | Skill I/O typecheck failure (dataclass) |

## License

MIT License (SPDX: `MIT`).
