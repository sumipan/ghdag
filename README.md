# ghdag

ghdag is a generic DAG execution engine extracted from graph-watcher.
It combines label-driven GitHub issue dispatch with a local dependency-aware queue runner in one Python package and CLI — unlike CI-centric orchestrators such as GitHub Actions or Dagger.

## Status

![stability](https://img.shields.io/badge/stability-pre--1.0-orange)
![version](https://img.shields.io/badge/version-v0.36.0-blue)
![ci](https://github.com/sumipan/ghdag/actions/workflows/test.yml/badge.svg?branch=main)
![python](https://img.shields.io/badge/python-%3E%3D3.10-blue)
![license](https://img.shields.io/badge/license-MIT-green)

Current release is **v0.36.0** (`0.Y.Z`, pre-1.0). Public interfaces may change before `1.0.0`.

## Not

ghdag is **not**:

- A replacement for GitHub Actions, Dagger, or other CI/CD runners
- A hosted orchestration SaaS
- An LLM provider SDK (it shells out to installed CLIs such as Claude / Codex / Cursor)
- A general-purpose workflow language outside its YAML dispatch + `exec.jsonl` queue model

## Installation

Requires **Python >= 3.10**.

```bash
pip install ghdag
```

Install a specific tag:

```bash
pip install git+https://github.com/sumipan/ghdag.git@v0.36.0
```

| Item | Value |
|---|---|
| Python requirement | `>=3.10` (`pyproject.toml` `requires-python`) |
| Runtime dependencies | `watchdog>=4.0.0`, `pyyaml>=6.0`, `requests>=2.28.0` |
| Dev extras | `pip install "ghdag[dev]"` |

## Quick Start

Create a queue file and run it (shape used in `tests/test_ghdag_pipeline.py` / `tests/test_ghdag_engine.py`):

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

## CLI Reference

Global options: `--verbose` / `-v`, `--quiet` / `-q`.

### Top-level commands

| Command | Description |
|---|---|
| `ghdag run` | Run `exec.jsonl` via `DagEngine` |
| `ghdag watch` | Poll GitHub issues and dispatch workflow handlers |
| `ghdag trigger` | Trigger one workflow handler for one issue |
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
| `ghdag tools list` | List tool definitions (`--path`, `--json`) |
| `ghdag quota report` | Report engine quota availability |
| `ghdag quota clear` | Clear engine quota pause state |
| `ghdag quota drain` | Pause new launches for one engine and wait for idle |
| `ghdag quota resume` | Release drain mode for one engine |
| `ghdag quota status` | Print engine-level quota/drain/queue snapshot JSON |

`ghdag quota status` returns per-engine `quota_status`, `draining`, `queued`, `deferred`, `running`, and `idle`.

### Key options

| Command | Key options / arguments |
|---|---|
| `ghdag run` | `exec_jsonl`, `--interval`, `--hooks`, `--max-concurrency` |
| `ghdag watch` | `workflows_dir`, `--interval`, `--exec-md`, `--once`, `--pause-file` |
| `ghdag trigger` | `issue_number`, `--handler`, `--workflows-dir`, `--exec-md`, `--workflow` |
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

## Architecture

```
src/ghdag/
├── cli/             — CLI entry point (`commands/` subpackage)
├── core/            — Foundations (exceptions, command adapters, models, ports)
├── dag/             — Local DAG engine, fanout, circuit breaker, session compaction
├── files/           — Markdown file ops (append, promote, links)
├── io/              — Queue / done / audit / exec.jsonl I/O
├── llm/             — LLM engines, capabilities presets, adapters, compaction
├── markdown/        — Issue body H2 section editor
├── metrics/         — Task metrics and FailureClass
├── pipeline/        — Order submission, audit hooks, pipeline state
├── tool/            — Tool definitions and ToolRegistry
├── ui/              — Web dashboard and monitor
├── workflow/        — YAML loader, dispatcher, gates, state machine
├── cleanup/         — Queue archival and orphan detection
├── __init__.py      — Top-level public API
├── __main__.py      — `python -m ghdag` entry point
├── exceptions.py    — Re-export shim for `core.exceptions` (backward compat)
├── github_cli.py    — CLI-compatible wrapper (`GitHubClient`)
├── github_client.py — GitHub REST/GraphQL client
├── maintenance.py   — Maintenance helpers
├── quota.py         — Quota gate (`QuotaGate`)
└── py.typed         — PEP 561 marker
```

Two towers communicate only through `exec.jsonl` and `jobs/done/` markers: **intake** (`pipeline` / `workflow`) and **execution** (`dag`). Import-linter contracts enforce that boundary.

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

`ghdag quota` reads/writes JSON state (default path `jobs/quota-gate.json`).

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

Outside the `GhdagError` tree: `TemplateVariableError` (`pipeline/order.py` — `ValueError` + `KeyError`).

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

## Public API Stability

ghdag follows SemVer while remaining **pre-1.0** (`0.Y.Z`):

| Kind of change | Version impact |
|---|---|
| Breaking change to public CLI flags, `ghdag.__all__` symbols, or documented env vars | Minor bump (`0.Y`) |
| Backward-compatible feature | Patch or minor, depending on scope |
| Bug fix without API change | Patch (`0.0.Z`) |

Treat symbols listed in **Public API** and commands in **CLI Reference** as the supported surface. Undocumented private modules (leading underscore) may change without notice. Prefer pinning to a release tag until `1.0.0`.

## License

MIT License (SPDX: `MIT`).
