# ghdag

ghdag is a generic DAG execution engine extracted from graph-watcher.
Unlike CI-centric orchestrators, ghdag focuses on label-driven GitHub workflow dispatch and local dependency-aware queue execution in one Python package and CLI.

## Status

![stability](https://img.shields.io/badge/stability-pre--1.0-orange)
![version](https://img.shields.io/badge/version-v0.35.0-blue)
![ci](https://github.com/sumipan/ghdag/actions/workflows/test.yml/badge.svg?branch=main)
![python](https://img.shields.io/badge/python-%3E%3D3.10-blue)
![license](https://img.shields.io/badge/license-MIT-green)

Current release is **v0.35.0** (pre-1.0). Interfaces may evolve before `1.0.0`.

## Installation

```bash
pip install ghdag
```

Install from a specific tag:

```bash
pip install git+https://github.com/sumipan/ghdag.git@v0.35.0
```

| Item | Value |
|---|---|
| Python requirement | `>=3.10` |
| Runtime dependencies | `watchdog>=4.0.0`, `pyyaml>=6.0`, `requests>=2.28.0` |
| Dev dependencies | `pip install "ghdag[dev]"` |

## Quick Start

Create an execution queue (`jobs/exec.jsonl`) and run it:

```jsonl
{"uuid":"demo-0001","command":"echo hello from ghdag","depends":[]}
```

```bash
ghdag run jobs/exec.jsonl
```

Create a minimal workflow file and run one dispatch poll:

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

### Top-level commands

| Command | Description |
|---|---|
| `ghdag run` | Run `exec.jsonl` via `DagEngine` |
| `ghdag watch` | Poll GitHub issues and dispatch workflow handlers |
| `ghdag ui` | Launch the Web UI dashboard |
| `ghdag llm` | Execute one LLM call without workflow dispatch |
| `ghdag version` | Print installed package version |
| `ghdag cleanup` | Archive completed/orphaned queue tasks |
| `ghdag trigger` | Trigger one workflow handler for one issue |
| `ghdag audit-query` | Query `audit.jsonl` or detect bursts |
| `ghdag tools` | Manage tool definitions |
| `ghdag quota` | Manage quota gate state |

### Subcommands

| Command | Description |
|---|---|
| `ghdag tools list` | List tool definitions (`--path`, `--json`) |
| `ghdag quota report` | Report engine quota availability |
| `ghdag quota clear` | Clear engine quota pause state |
| `ghdag quota drain` | Pause new launches for one engine and wait for idle |
| `ghdag quota resume` | Release drain mode for one engine |
| `ghdag quota status` | Print engine-level quota/drain/queue snapshot JSON |

`ghdag quota status` returns per-engine `quota_status`, `draining`, `queued`, `deferred`, `running`, and `idle`.

### Key options

| Command | Key options/arguments |
|---|---|
| `ghdag run` | `exec_jsonl`, `--interval`, `--hooks`, `--max-concurrency` |
| `ghdag watch` | `workflows_dir`, `--interval`, `--exec-md`, `--once`, `--pause-file` |
| `ghdag ui` | `--repo-root`, `--host`, `--port`, `--interval`, `--max-visible` |
| `ghdag llm` | `prompt`, `--engine`, `--model`, `--timeout`, `--dangerously-skip-permissions`, `--permission-mode`, `--capabilities-preset`, `--stdin`, `--list-engines`, `--list-models`, `--audit-path`, `--correlation-id`, `--request-id` |
| `ghdag cleanup` | `repo_root`, `--dry-run`, `--cutoff-days`, `--orphan-days`, `--auto-repair` |
| `ghdag trigger` | `issue_number`, `--handler`, `--workflows-dir`, `--exec-md`, `--workflow` |
| `ghdag audit-query` | `--correlation-id`, `--burst-detect`, `--since`, `--audit-path`, `--window-sec`, `--threshold` |

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

## Architecture

### Module layout

```
src/ghdag/
├── cli/             — CLI entry point (commands/ subpackage)
├── core/            — Foundations (exceptions, command, models, ports)
├── dag/             — DAG engine and fanout
├── files/           — File operations (append, state)
├── io/              — I/O utilities (queue, done, audit, exec_jsonl)
├── llm/             — LLM engines and capabilities
├── markdown/        — Markdown parser and body_editor
├── metrics/         — Metrics and FailureClass
├── pipeline/        — Pipeline (config, order, audit)
├── tool/            — Tool extensions and ToolRegistry
├── ui/              — UI utilities (dashboard, server)
├── workflow/        — Workflow (dispatcher, loader)
├── cleanup/         — Cleanup utilities
├── __init__.py      — Top-level public API
├── __main__.py      — `python -m ghdag` entry point
├── exceptions.py    — Re-export shim for core/exceptions.py (backward compat)
├── github_cli.py    — CLI-compatible wrapper (GitHubClient)
├── github_client.py — GitHub API client
├── maintenance.py   — Maintenance utilities
├── quota.py         — Quota gate (QuotaGate)
└── py.typed         — PEP 561 type marker
```

13 packages + standalone modules: `github_cli.py`, `github_client.py`, `maintenance.py`, `quota.py`, `exceptions.py`.

## Configuration

### Environment variables

| Variable | Required | Default | Used in |
|---|---|---|---|
| `GITHUB_TOKEN` | One of `GITHUB_TOKEN`/`GH_TOKEN` required for GitHub API calls | none | `ghdag.github_client` |
| `GH_TOKEN` | Fallback token variable | none | `ghdag.github_client` |
| `GITHUB_REPOSITORIES` | Required for multi-repo watch/list behaviors | none | `ghdag.github_client` |
| `GHDAG_AUDIT_PATH` | Optional | `jobs/audit.jsonl` | `ghdag.ui.dashboard`, `ghdag.cli.commands.llm` |
| `GHDAG_TOKEN_WARN_THRESHOLD` | Optional | `500000` | `ghdag.ui.dashboard` |
| `GHDAG_LLM_MODELS` | Optional | `llm-models.yml` in current directory, then built-ins | `ghdag.llm._config` |
| `GHDAG_SAFE_DEFAULT_PERMISSION` | Optional | `text_only` | `ghdag.pipeline.llm_pipeline` |

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

`*` = multiple inheritance from both `GhdagError` and `ValueError` (catchable via `except ValueError`).

Hierarchy-external exception: `TemplateVariableError` (`pipeline/order.py` — inherits `ValueError` + `KeyError`, not integrated into `GhdagError`).

Re-exports: `src/ghdag/exceptions.py` re-exports the primary exceptions from `core/exceptions.py` for backward compatibility.

### Exception reference table

| Exception | Module | Notes |
|---|---|---|
| `GhdagError` | `ghdag.core.exceptions` | Base exception type |
| `GitHubApiError` | `ghdag.core.exceptions` | GitHub API call failure |
| `AuthError` | `ghdag.core.exceptions` | Authentication/token failure |
| `RateLimitError` | `ghdag.core.exceptions` | Rate limit exhausted |
| `PermissionDeniedError` | `ghdag.core.exceptions` | 403 without rate limit exhaustion |
| `NetworkError` | `ghdag.core.exceptions` | Network/transport failure |
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

## License

MIT License (SPDX: `MIT`).
