# ghdag

Generic DAG execution engine extracted from graph-watcher.

ghdag combines GitHub Issue polling, workflow dispatching, `exec.jsonl` queue execution, and LLM task orchestration in one Python package + CLI.

## Not

ghdag is **not**:

- A CI/CD platform or GitHub Actions replacement
- A general-purpose workflow DSL for arbitrary business processes
- An app framework for hosting long-running web services

It is a focused runtime for label-driven automation pipelines and dependency-aware local task execution.

## Status

![version](https://img.shields.io/badge/version-v0.33.0-blue)
![stability](https://img.shields.io/badge/stability-pre--1.0-orange)
![python](https://img.shields.io/badge/python-%3E%3D3.10-blue)
![license](https://img.shields.io/badge/license-MIT-green)

**v0.33.0** — pre-1.0 (`0.Y.Z`). Public API can still evolve until `1.0.0`.

### Public API Stability

Until `1.0.0`, SemVer interpretation in this repository:

| Bump | Meaning |
|---|---|
| **Y** (minor) | Breaking change to exported API (`ghdag.__all__`), CLI contract, or entry points |
| **Z** (patch) | Backward-compatible fixes and additions |

Symbols outside `ghdag.__all__` are internal unless explicitly documented otherwise.

## Installation

```bash
pip install ghdag
```

Pinned source install:

```bash
pip install git+https://github.com/sumipan/ghdag.git@v0.33.0
```

| Item | Value |
|---|---|
| Python | `>=3.10` |
| Runtime dependencies | `watchdog>=4.0.0`, `pyyaml>=6.0`, `requests>=2.28.0` |
| Dev dependencies | `pip install "ghdag[dev]"` (`pytest`, `mypy`, `ruff`, `import-linter`, `types-PyYAML`) |

## Quick Start

Create `jobs/exec.jsonl`:

```jsonl
{"uuid":"aaaa-bbbb-cccc-0001","command":"echo hello","depends":[]}
```

Run the queue:

```bash
ghdag run jobs/exec.jsonl
```

Dispatch workflows from GitHub labels:

```bash
export GITHUB_TOKEN="ghp_..." # or GH_TOKEN
export GITHUB_REPOSITORIES="owner/repo"
ghdag watch workflows --exec-md jobs/exec.jsonl
```

## CLI Reference / Public API

Global flags: `--verbose` / `-v`, `--quiet` / `-q`.

| Subcommand | Purpose | Key arguments |
|---|---|---|
| `run` | Execute `exec.jsonl` through `DagEngine` | `exec_jsonl`, `--interval`, `--hooks`, `--max-concurrency` |
| `watch` | Poll GitHub and dispatch workflow handlers | `workflows_dir`, `--interval`, `--exec-md`, `--once` |
| `ui` | Launch monitoring dashboard | `--repo-root`, `--host`, `--port`, `--interval`, `--max-visible` |
| `llm` | One-shot LLM execution | `prompt`, `--engine`, `--model`, `--timeout`, `--stdin`, `--list-engines`, `--list-models`, `--audit-path` |
| `version` | Print package version | none |
| `cleanup` | Archive completed/orphaned queue tasks | `repo_root`, `--dry-run`, `--cutoff-days`, `--orphan-days`, `--auto-repair` |
| `trigger` | One-shot dispatch for one issue + handler | `issue_number`, `--handler`, `--workflow`, `--workflows-dir`, `--exec-md` |
| `audit-query` | Query or burst-detect `audit.jsonl` | `--correlation-id`, `--burst-detect`, `--since`, `--audit-path`, `--window-sec`, `--threshold` |
| `tools list` | List tool definitions from a directory | `--path`, `--json` |

### Public API

`ghdag.__all__` exports exactly these seven symbols:

| Symbol | Source module | Responsibility |
|---|---|---|
| `GhdagError` | `ghdag.exceptions` | Base exception family for ghdag |
| `QueueTask` | `ghdag.pipeline.result` | Queue task value object |
| `QueueTaskStore` | `ghdag.pipeline.result` | Queue task file store |
| `LLMPipelineAPI` | `ghdag.pipeline.llm_pipeline` | Pipeline submit / state access API |
| `PipelineState` | `ghdag.pipeline.state` | Persistent pipeline state manager |
| `DagEngine` | `ghdag.dag.engine` | DAG execution loop |
| `WorkflowDispatcher` | `ghdag.workflow.dispatcher` | GitHub polling and workflow dispatch |

## Architecture

```text
src/ghdag/
├── cli/                 # argparse entrypoint and subcommands
├── core/                # shared contracts/exceptions/models/ports
├── dag/                 # execution tower
├── workflow/            # intake tower (GitHub polling + routing)
├── pipeline/            # intake tower (LLM pipeline and state)
├── llm/                 # engine adapters and model config
├── io/                  # queue/done/sessions/audit filesystem I/O
├── files/               # repo-scoped markdown I/O helpers
├── cleanup/             # queue archive and cleanup logic
├── markdown/            # markdown body editor utilities
├── metrics/             # runtime metrics
├── tool/                # tool registry and schema
└── ui/                  # dashboard server/static assets
```

`io/` currently provides six modules:

- `exec_jsonl.py`
- `audit.py`
- `audit_query.py`
- `done.py`
- `queue.py`
- `sessions.py`

Dependency boundaries are enforced with `import-linter` contracts in `pyproject.toml` (`core`, `infra`, `intake`, `execution`, `ops`).

## Configuration

### Environment variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `GITHUB_TOKEN` | one of `GITHUB_TOKEN` / `GH_TOKEN` required for GitHub polling | — | GitHub API auth |
| `GH_TOKEN` | fallback token variable | — | Same auth role as `GITHUB_TOKEN` |
| `GITHUB_REPOSITORIES` | required for `watch` polling | — | Comma-separated repositories (`owner/repo`) |
| `GHDAG_SAFE_DEFAULT_PERMISSION` | optional | `text_only` | Safe default permission preset for LLM pipeline |
| `GHDAG_AUDIT_PATH` | optional | `jobs/audit.jsonl` | Audit log file path |
| `GHDAG_TOKEN_WARN_THRESHOLD` | optional | `500000` | UI warning threshold for token usage |
| `GHDAG_LLM_MODELS` | optional | `llm-models.yml` in CWD, then built-in defaults | Model allowlist config path |

### `llm-models.yml`

Lookup order in `ghdag.llm._config`:

1. `GHDAG_LLM_MODELS`
2. `llm-models.yml` in current working directory
3. Built-in defaults

Example:

```yaml
engines:
  claude:
    - claude-sonnet-4-6
  gemini:
    - gemini-2.5-flash
  cursor:
    - auto
```

## Error Reference

### `GhdagError` hierarchy

```text
GhdagError
├── GitHubApiError
│   ├── AuthError
│   ├── RateLimitError
│   ├── PermissionDeniedError
│   └── NetworkError
├── ModelValidationError
├── DependencyError
├── EngineModelError
├── LLMParseError
├── ConfigLoadError
├── FanoutError
├── ValidationError
├── AdapterNotFoundError
├── ContextHookError
├── PathTraversalError
├── AppendRecoverError
└── ToolRegistryError
```

| Exception | Module |
|---|---|
| `GhdagError` | `ghdag.core.exceptions` |
| `GitHubApiError` | `ghdag.core.exceptions` |
| `AuthError` | `ghdag.core.exceptions` |
| `RateLimitError` | `ghdag.core.exceptions` |
| `PermissionDeniedError` | `ghdag.core.exceptions` |
| `NetworkError` | `ghdag.core.exceptions` |
| `ModelValidationError` | `ghdag.pipeline.config` |
| `DependencyError` | `ghdag.pipeline.llm_pipeline` |
| `EngineModelError` | `ghdag.llm.engines` |
| `LLMParseError` | `ghdag.llm.capabilities` |
| `ConfigLoadError` | `ghdag.llm._config` |
| `FanoutError` | `ghdag.dag.fanout` |
| `ValidationError` | `ghdag.workflow.loader` |
| `AdapterNotFoundError` | `ghdag.core.command` |
| `ContextHookError` | `ghdag.workflow.dispatcher` |
| `PathTraversalError` | `ghdag.core.models.files` |
| `AppendRecoverError` | `ghdag.files.append` |
| `ToolRegistryError` | `ghdag.tool.exceptions` |

Outside the hierarchy:

| Exception | Module | Notes |
|---|---|---|
| `TemplateVariableError` | `ghdag.pipeline.order` | Inherits `ValueError` and `KeyError` (not `GhdagError`) |

## Protocols / Extension Points

Core extension surfaces live in `ghdag.core.ports` and adapter registration in `ghdag.core.command` (`register_adapter()`). Engine/model policy is controlled through `ghdag.llm.engines` and `llm-models.yml`.

## License

MIT License (SPDX: `MIT`).
