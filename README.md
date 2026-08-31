# ghdag

Generic DAG execution engine extracted from graph-watcher.

ghdag polls GitHub labels, dispatches workflow handlers, and runs `exec.jsonl` task queues with dependency-aware concurrency. It couples workflow YAML, LLM engine adapters, and audit logging in one package — unlike generic CI runners that leave those concerns to external tooling.

## Not

ghdag is **not**:

- A CI/CD product or GitHub Actions replacement
- A general-purpose workflow DSL for arbitrary orchestration
- An application framework for hosting long-running services

It is a library + CLI for GitHub Issue–driven LLM pipelines and local `exec.jsonl` DAG execution.

## Status

![version](https://img.shields.io/badge/version-v0.32.0-blue)
![stability](https://img.shields.io/badge/stability-pre--1.0-orange)
![python](https://img.shields.io/badge/python-%3E%3D3.10-blue)
![license](https://img.shields.io/badge/license-MIT-green)

**v0.32.0** — pre-1.0 (`0.Y.Z`). Public API may change until `1.0.0`.

### Public API Stability

Until `1.0.0`, SemVer for this package means:

| Bump | When |
|---|---|
| **Y** (minor) | Breaking change to public API (`ghdag.__all__`), CLI contract, or entry points |
| **Z** (patch) | Backward-compatible fixes and additions |

Treat symbols outside `ghdag.__all__` as internal unless documented here.

## Installation

```bash
pip install ghdag
```

From source (pinned release):

```bash
pip install git+https://github.com/sumipan/ghdag.git@v0.32.0
```

**Requirements**

| Item | Value |
|---|---|
| Python | `>=3.10` (`requires-python` in `pyproject.toml`) |
| Runtime deps | `watchdog>=4.0.0`, `pyyaml>=6.0`, `requests>=2.28.0` |
| Optional LLM CLIs | `claude`, `gemini`, `agent` (Cursor), `codex`, `bash` — used by `ghdag llm` and workflow steps |

Dev extras: `pip install "ghdag[dev]"` (pytest, mypy, ruff, import-linter, …).

## Quick Start

Minimal queue from the fixture shape used in `tests/ui/test_dashboard.py`:

Create `jobs/exec.jsonl`:

```jsonl
{"uuid":"aaaa-bbbb-cccc-0001","command":"echo hello","depends":[]}
```

Run the DAG engine:

```bash
ghdag run jobs/exec.jsonl
```

Watch GitHub workflows (requires token + repo list):

```bash
export GITHUB_TOKEN="ghp_..."          # or GH_TOKEN
export GITHUB_REPOSITORIES="owner/repo"
ghdag watch workflows/ --exec-md jobs/exec.jsonl
```

`watch` polls GitHub every `--interval` seconds (default: `30`), matches workflow trigger labels, and appends entries to `jobs/exec.jsonl`. Use `--once` for a single poll cycle.

## CLI Reference / Public API

Global flags (all subcommands): `--verbose` / `-v` (DEBUG), `--quiet` / `-q` (WARNING and above only).

| Subcommand | Description | Key arguments |
|---|---|---|
| `run` | Run `exec.jsonl` via `DagEngine` | `exec_jsonl`, `--interval SEC`, `--hooks MODULE`, `--max-concurrency N` |
| `watch` | Watch workflows directory and dispatch on GitHub events | `workflows_dir`, `--interval SEC`, `--exec-md PATH`, `--once` |
| `ui` | Launch Web UI dashboard | `--repo-root PATH`, `--host ADDR`, `--port N`, `--interval SEC`, `--max-visible N` |
| `llm` | One-shot LLM call without a workflow | `prompt`, `--engine`, `--model`, `--timeout SEC`, `--stdin`, `--list-engines`, `--list-models`, `--audit-path PATH`, `--permission-mode MODE`, `--capabilities-preset NAME` |
| `version` | Print package version | — |
| `cleanup` | Archive completed/orphaned queue tasks | `repo_root`, `--dry-run`, `--cutoff-days N`, `--orphan-days N`, `--auto-repair` |
| `trigger` | One-shot handler dispatch for a specific issue | `issue_number`, `--handler NAME` (required), `--workflow NAME`, `--workflows-dir PATH`, `--exec-md PATH` |
| `audit-query` | Query `audit.jsonl` for correlation events or burst detection | `--correlation-id ID`, `--burst-detect`, `--since ISO8601`, `--audit-path PATH`, `--window-sec SEC`, `--threshold N` |
| `tools list` | List tool definitions from a directory | `--path DIR` (required), `--json` |

### `ghdag run`

```
ghdag run <exec_jsonl> [--interval SEC] [--hooks MODULE] [--max-concurrency N]
```

| Option | Default | Description |
|---|---|---|
| `exec_jsonl` | — | Path to `exec.jsonl` (JSONL task queue) |
| `--interval SEC` | `1.0` | Poll interval in seconds |
| `--hooks MODULE` | — | Python module path for a `DagHooks` implementation (e.g. `scripts.diary_hooks`) |
| `--max-concurrency N` | unlimited | Maximum concurrent tasks |

### `ghdag watch`

```
ghdag watch <workflows_dir> [--interval SEC] [--exec-md PATH] [--once]
```

Requires `GITHUB_REPOSITORIES` and `GITHUB_TOKEN` or `GH_TOKEN`.

| Option | Default | Description |
|---|---|---|
| `workflows_dir` | — | Path to workflow YAML directory |
| `--interval SEC` | `30.0` | GitHub polling interval in seconds |
| `--exec-md PATH` | `jobs/exec.jsonl` | Output path for dispatched exec entries |
| `--once` | — | Poll once and exit |

### `ghdag ui`

```
ghdag ui [--repo-root PATH] [--host ADDR] [--port N] [--interval SEC] [--max-visible N]
```

| Option | Default | Description |
|---|---|---|
| `--repo-root PATH` | `.` | Repository root containing `jobs/exec.jsonl` |
| `--host ADDR` | `127.0.0.1` | Bind address |
| `--port N` | `8080` | Bind port |
| `--interval SEC` | `3.0` | SSE poll interval in seconds |
| `--max-visible N` | `30` | Maximum tasks displayed |

### `ghdag llm`

```
ghdag llm [prompt] [--engine NAME] [--model ID] [--timeout SEC] [--stdin]
          [--dangerously-skip-permissions] [--permission-mode MODE]
          [--capabilities-preset NAME] [--list-engines] [--list-models]
          [--audit-path PATH] [--correlation-id ID] [--request-id ID]
```

| Option | Default | Description |
|---|---|---|
| `prompt` | stdin | Prompt text (reads from stdin when omitted) |
| `--engine`, `-e` | `claude` | LLM engine name |
| `--model`, `-m` | engine default | Model ID |
| `--timeout SEC` | no limit | Subprocess timeout |
| `--stdin` | — | Also pipe stdin content to the LLM process |
| `--dangerously-skip-permissions` | — | Pass bypass flag to Claude CLI |
| `--permission-mode MODE` | — | Claude permission mode: `default`, `plan`, `bypassPermissions` |
| `--capabilities-preset NAME` | — | Preset: `text_only`, `json_only`, `web_research`, `dangerous_full_access` |
| `--list-engines` | — | List available engines and exit |
| `--list-models` | — | List models for `--engine` and exit |
| `--audit-path PATH` | `GHDAG_AUDIT_PATH` | Audit log path |
| `--correlation-id ID` | — | Correlation ID for audit log |
| `--request-id ID` | — | Request ID for audit log |

### `ghdag version`

```
ghdag version
```

Prints `ghdag.__version__` and exits.

### `ghdag cleanup`

```
ghdag cleanup <repo_root> [--dry-run] [--cutoff-days N] [--orphan-days N] [--auto-repair]
```

| Option | Default | Description |
|---|---|---|
| `repo_root` | — | Repository root path |
| `--dry-run` | — | Show targets without making changes |
| `--cutoff-days N` | `1` | Days before archiving completed tasks |
| `--orphan-days N` | `7` | Days before archiving orphaned tasks |
| `--auto-repair` | — | Auto-fix orphan and dead-entry issues (default: detect-only) |

### `ghdag trigger`

```
ghdag trigger <issue_number> --handler NAME [--workflows-dir PATH] [--workflow NAME] [--exec-md PATH]
```

| Option | Default | Description |
|---|---|---|
| `issue_number` | — | GitHub Issue number |
| `--handler NAME` | — | Handler name to execute (required) |
| `--workflows-dir PATH` | `workflows` | Workflow YAML directory |
| `--workflow NAME` | auto | Workflow name (required when multiple workflows exist) |
| `--exec-md PATH` | `jobs/exec.jsonl` | Output path for exec entries |

### `ghdag audit-query`

```
ghdag audit-query [--correlation-id ID | --burst-detect] [--since ISO8601]
                  [--audit-path PATH] [--window-sec SEC] [--threshold N]
```

`--correlation-id` and `--burst-detect` are mutually exclusive; one is required.

| Option | Default | Description |
|---|---|---|
| `--correlation-id ID` | — | Filter task-exit events by correlation ID |
| `--burst-detect` | — | Detect correlation-ID bursts (exit code 1 if found) |
| `--since ISO8601` | — | Datetime filter (`--correlation-id` mode only) |
| `--audit-path PATH` | `jobs/audit.jsonl` | Path to audit log |
| `--window-sec SEC` | `600.0` | Burst detection window in seconds |
| `--threshold N` | `10` | Burst detection event count threshold |

### `ghdag tools list`

```
ghdag tools list --path DIR [--json]
```

| Option | Default | Description |
|---|---|---|
| `--path DIR` | — | Tool definition directory (required) |
| `--json` | — | Output as JSON instead of text |

### Public API

`ghdag.__init__.__all__` exports:

| Symbol | Module | Description |
|---|---|---|
| `GhdagError` | `ghdag.exceptions` | Base exception for all ghdag errors |
| `QueueTask` | `ghdag.pipeline.result` | One queue task (UUID → order / result / stderr) |
| `QueueTaskStore` | `ghdag.pipeline.result` | Read/write access to queue task files |
| `LLMPipelineAPI` | `ghdag.pipeline.llm_pipeline` | Submit and track LLM pipeline orders |
| `PipelineState` | `ghdag.pipeline.state` | Persistent pipeline state (`.pipeline-state/`) |
| `DagEngine` | `ghdag.dag.engine` | DAG execution loop over `exec.jsonl` |
| `WorkflowDispatcher` | `ghdag.workflow.dispatcher` | GitHub polling and handler dispatch |

```python
from ghdag import DagEngine, WorkflowDispatcher, LLMPipelineAPI, GhdagError
```

## Architecture

```
src/ghdag/
├── __init__.py          # Package public API (__all__)
├── __main__.py          # python -m ghdag entry
├── exceptions.py        # Re-export shim for core.exceptions
├── cleanup/             # Archive completed/orphaned queue tasks
├── cli/                 # CLI subcommand definitions
│   ├── main.py          # argparse entry
│   └── commands/        # Per-subcommand implementations
├── core/                # Shared foundation (exceptions, command, models, ports)
├── dag/                 # DAG engine and fanout (execution tower)
├── files/               # File I/O and append
├── github_cli.py        # gh CLI wrapper
├── github_client.py     # GitHub REST API client
├── io/                  # I/O utilities (exec.jsonl, audit)
├── llm/                 # LLM engine adapters and config
├── maintenance.py       # Repository maintenance utilities
├── markdown/            # Markdown body editing
├── metrics/             # Metrics and FailureClass
├── pipeline/            # Pipeline state, config, audit, hooks (intake tower)
├── tool/                # Tool definition registry
├── ui/                  # Web UI dashboard
└── workflow/            # Workflow dispatcher, loader, engine (intake tower)
```

| Module / package | Responsibility |
|---|---|
| `cli/` | CLI entry — `main`, `commands/` (`run`, `watch`, `ui`, `llm`, `cleanup`, `trigger`, `audit_query`) |
| `core/` | Shared foundation — `exceptions`, `command`, `models`, `ports`, `capabilities`, `engine_spec` |
| `dag/` | Execution tower — `engine`, `parser`, `state`, `fanout`, `hooks`, `watcher`, `models` |
| `workflow/` | Intake tower — GitHub polling / dispatch (`dispatcher`, `engine`, `loader`, `schema`, `state_machine`, `gates`) |
| `pipeline/` | Intake tower — LLM pipeline (`llm_pipeline`, `order`, `result`, `state`, `audit`, `config`, `hooks`) |
| `llm/` | LLM engine integration — `engines`, `capabilities`, `spec`, `adapters`, `_config` |
| `files/` | Repository-scoped `.md` I/O — `reader`, `writer`, `append`, `promote` |
| `cleanup/` | Archive completed and orphaned queue tasks under `jobs/` |
| `markdown/` | Markdown body editing (`body_editor`) |
| `metrics/` | Task execution metrics — `recorder`, `parsers`, `models` |
| `tool/` | Tool definition management — `registry`, `schema`, `cli`, `exceptions` |
| `ui/` | Web UI dashboard — `dashboard`, `monitor`, `server`, `static` |
| `io/` | Filesystem I/O facade — `exec_jsonl`, `audit`, `audit_query` |
| `github_cli.py` | Thin wrapper around `gh` CLI for issue operations |
| `github_client.py` | GitHub REST API client (token auth) |
| `maintenance.py` | Repository maintenance utilities |
| `exceptions.py` | Re-export shim for `ghdag.core.exceptions` |
| `__main__.py` | `python -m ghdag` entry |

### Layer dependency constraints (import-linter)

Enforced by `[tool.importlinter.contracts]` in `pyproject.toml`:

| Layer | Rule |
|---|---|
| **core** | Must not import other ghdag packages |
| **infra** (`io`, `files`, `llm`, `metrics`, `github_client`) | Must not import orchestration (`pipeline`, `workflow`, `dag`, `cli`, `ui`) |
| **intake** (`pipeline`, `workflow`) | Must not import **execution** (`dag`); communication is via `exec.jsonl` / `done` only |
| **execution** (`dag`) | Must not import **intake** (`pipeline`, `workflow`) |
| **ops** (`cleanup`, `maintenance`, `tool`, `markdown`) | Must not depend on execution/orchestration towers |

## Configuration

### Environment variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `GITHUB_TOKEN` | Either `GITHUB_TOKEN` or `GH_TOKEN` for `watch` / `trigger` | — | GitHub API authentication (`github_client.py`) |
| `GH_TOKEN` | Alternate of `GITHUB_TOKEN` | — | Same as `GITHUB_TOKEN` (checked second) |
| `GITHUB_REPOSITORIES` | Required for `watch` | — | Comma-separated `owner/repo` list to poll (`github_client.py`) |
| `GHDAG_SAFE_DEFAULT_PERMISSION` | Optional | `text_only` | Safe-default capabilities preset for LLM pipeline when permission is unset (`pipeline/llm_pipeline.py`) |
| `GHDAG_AUDIT_PATH` | Optional | `jobs/audit.jsonl` | Audit log file path (`ghdag llm`, `ghdag ui`) |
| `GHDAG_TOKEN_WARN_THRESHOLD` | Optional | `500000` | Token-usage warning threshold for the Web UI dashboard (`ui/dashboard.py`) |
| `GHDAG_LLM_MODELS` | Optional | `llm-models.yml` in cwd → built-in defaults | Path to LLM engine/model allowlist YAML (`llm/_config.py`) |

### `llm-models.yml`

Override the per-engine model allowlist. Resolution order (see `ghdag.llm._config.load_engine_models`):

1. Path in `GHDAG_LLM_MODELS`
2. `llm-models.yml` in the current working directory
3. Built-in `DEFAULT_ENGINE_MODELS` (`llm/_constants.py`)

Example:

```yaml
engines:
  claude:
    - claude-sonnet-4-6
    - claude-haiku-4-5-20251001
  gemini:
    - gemini-2.5-flash
  cursor:
    - auto
    - composer-2
```

## Error Reference

### `GhdagError` hierarchy

Canonical definitions live under `ghdag.core.exceptions` and related modules. `ghdag.exceptions` re-exports the GitHub API exception family. External code can use `except GhdagError` to catch all rows below except where noted.

```
GhdagError (core.exceptions)
├── GitHubApiError (core.exceptions)
│   ├── AuthError (core.exceptions)
│   ├── RateLimitError (core.exceptions)
│   ├── PermissionDeniedError (core.exceptions)
│   └── NetworkError (core.exceptions)
├── ModelValidationError (pipeline.config)
├── DependencyError (pipeline.llm_pipeline) + ValueError
├── EngineModelError (llm.engines)
├── LLMParseError (llm.capabilities)
├── ConfigLoadError (llm._config) + ValueError
├── FanoutError (dag.fanout) + ValueError
├── ValidationError (workflow.loader) + ValueError
├── AdapterNotFoundError (core.command) + ValueError
├── ContextHookError (workflow.dispatcher) + ValueError
├── PathTraversalError (core.models.files) + ValueError
├── AppendRecoverError (files.append) + ValueError
└── ToolRegistryError (tool.exceptions)
```

| Exception | Module | Also inherits | Raised when |
|---|---|---|---|
| `GhdagError` | `ghdag.core.exceptions` | — | Base class for all ghdag custom exceptions |
| `GitHubApiError` | `ghdag.core.exceptions` | — | GitHub API operation failure (carries `status_code`, `message`) |
| `AuthError` | `ghdag.core.exceptions` | `GitHubApiError` | Authentication failure (401, missing token) |
| `RateLimitError` | `ghdag.core.exceptions` | `GitHubApiError` | Rate limit exceeded (403 with `X-RateLimit-Remaining: 0`) |
| `PermissionDeniedError` | `ghdag.core.exceptions` | `GitHubApiError` | Insufficient permissions (403, 404 on private repos) |
| `NetworkError` | `ghdag.core.exceptions` | `GitHubApiError` | Connection timeout, DNS failure, or other network errors |
| `ModelValidationError` | `ghdag.pipeline.config` | — | Unauthorized model ID |
| `DependencyError` | `ghdag.pipeline.llm_pipeline` | `ValueError` | Invalid or circular step dependency |
| `EngineModelError` | `ghdag.llm.engines` | — | Unknown engine or unauthorized model |
| `LLMParseError` | `ghdag.llm.capabilities` | — | LLM response violates output format |
| `ConfigLoadError` | `ghdag.llm._config` | `ValueError` | Engine config file structure invalid |
| `FanoutError` | `ghdag.dag.fanout` | `ValueError` | Invalid fan-out spec |
| `ValidationError` | `ghdag.workflow.loader` | `ValueError` | Workflow YAML validation failure |
| `AdapterNotFoundError` | `ghdag.core.command` | `ValueError` | Unknown or unregistered engine adapter |
| `ContextHookError` | `ghdag.workflow.dispatcher` | `ValueError` | `context_hook` output is not valid JSON |
| `PathTraversalError` | `ghdag.core.models.files` | `ValueError` | File path escapes repository root |
| `AppendRecoverError` | `ghdag.files.append` | `ValueError` | Partial write detected during append |
| `ToolRegistryError` | `ghdag.tool.exceptions` | — | Tool definition registry error |

### Outside `GhdagError` hierarchy

These exceptions are **not** caught by `except GhdagError`:

| Exception | Module | Base class | Notes |
|---|---|---|---|
| `TemplateVariableError` | `ghdag.pipeline.order` | `ValueError`, `KeyError` | Missing template variable in order rendering (not under `GhdagError`) |

## License

MIT License — SPDX: `MIT` (see `pyproject.toml` and `LICENSE`).
