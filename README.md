# ghdag

Generic DAG execution engine for GitHub Issue–driven LLM pipelines.
Polls GitHub labels, dispatches workflow handlers, and runs `exec.jsonl` task queues with dependency-aware concurrency — unlike generic CI runners, ghdag couples workflow YAML, LLM engine adapters, and audit logging in one package.

## Status

![version](https://img.shields.io/badge/version-v0.29.1-blue)
![stability](https://img.shields.io/badge/stability-pre--1.0-orange)

**v0.29.1** — pre-1.0; public API may change until `1.0.0`.

## Installation

```bash
pip install git+https://github.com/sumipan/ghdag.git@v0.29.1
```

**Requirements**

| Item | Value |
|---|---|
| Python | `>=3.10` (`requires-python` in `pyproject.toml`) |
| Runtime deps | `watchdog`, `pyyaml`, `requests` |
| Optional LLM CLIs | `claude`, `gemini`, `agent` (Cursor), `bash` — used by `ghdag llm` and workflow steps |

## Quick Start

### Run a task queue (`ghdag run`)

Create `jobs/exec.jsonl`:

```jsonl
{"uuid":"a1b2c3d4-e5f6-7890-abcd-ef1234567890","command":"echo hello","depends":[]}
```

Execute:

```bash
ghdag run jobs/exec.jsonl
```

### Watch GitHub workflows (`ghdag watch`)

```bash
export GITHUB_TOKEN="ghp_..."          # or GH_TOKEN
export GITHUB_REPOSITORIES="owner/repo"
ghdag watch workflows/ --exec-md jobs/exec.jsonl
```

`watch` polls GitHub at `--interval` seconds (default: 30), matches workflow trigger labels, and appends new entries to `jobs/exec.jsonl`. Use `--once` for a single poll cycle.

## CLI Reference / Public API

### Subcommands

Global flags (all subcommands): `--verbose` / `-v` (DEBUG), `--quiet` / `-q` (WARNING+ only).

| Subcommand | Description | Key arguments |
|---|---|---|
| `run` | Run `exec.jsonl` via `DagEngine` | `exec_jsonl` (positional), `--interval SEC`, `--hooks MODULE`, `--max-concurrency N` |
| `watch` | Watch workflows directory and dispatch on GitHub events | `workflows_dir` (positional), `--interval SEC`, `--exec-md PATH`, `--once` |
| `ui` | Launch Web UI dashboard | `--repo-root PATH`, `--host ADDR`, `--port N`, `--interval SEC`, `--max-visible N` |
| `llm` | One-shot LLM call without a workflow | `prompt` (optional positional), `--engine`, `--model`, `--timeout SEC`, `--stdin`, `--list-engines`, `--list-models`, `--audit-path PATH` |
| `version` | Print package version | — |
| `cleanup` | Archive completed/orphaned queue tasks | `repo_root` (positional), `--dry-run`, `--cutoff-days N`, `--orphan-days N`, `--auto-repair` |
| `trigger` | One-shot handler dispatch for a specific issue | `issue_number` (positional), `--handler NAME` (required), `--workflow NAME`, `--workflows-dir PATH`, `--exec-md PATH` |
| `audit-query` | Query `audit.jsonl` for correlation events or burst detection | `--correlation-id ID`, `--burst-detect`, `--since ISO8601`, `--audit-path PATH` |
| `tools list` | List tool definitions from a directory | `--path DIR` (required), `--json` |

### Public API (`ghdag.__all__`)

| Symbol | Module | Description |
|---|---|---|
| `GhdagError` | `ghdag.exceptions` | Base exception for all ghdag errors |
| `QueueTask` | `ghdag.pipeline.result` | Parsed queue task record |
| `QueueTaskStore` | `ghdag.pipeline.result` | Read/write access to queue task files |
| `LLMPipelineAPI` | `ghdag.pipeline.llm_pipeline` | Submit and track LLM pipeline orders |
| `PipelineState` | `ghdag.pipeline.state` | Persistent pipeline state (`.pipeline-state/`) |
| `DagEngine` | `ghdag.dag.engine` | DAG execution loop over `exec.jsonl` |
| `WorkflowDispatcher` | `ghdag.workflow.dispatcher` | GitHub polling and handler dispatch |

## Architecture

Top-level modules and packages under `src/ghdag/`:

| Module / package | Responsibility |
|---|---|
| `cli.py` | CLI entry point (`argparse` subcommands) |
| `exceptions.py` | Shared exception hierarchy (`GhdagError` and GitHub API errors) |
| `github_cli.py` | Thin wrapper around `gh` CLI for issue operations |
| `github_client.py` | GitHub REST API client (token auth) |
| `cleanup.py` | Archive completed and orphaned queue tasks under `jobs/` |
| `maintenance.py` | Repository maintenance utilities |
| `dag/` | DAG execution engine — `engine`, `parser`, `state`, `fanout`, `hooks`, `watcher`, `models` |
| `files/` | Repository-scoped `.md` I/O — `reader`, `writer`, `append`, `promote`, `_rotate`, `models` |
| `llm/` | LLM engine integration — `engines`, `capabilities`, `spec`, `_config`, `_constants` |
| `markdown/` | Markdown body editing (`body_editor`) |
| `metrics/` | Task execution metrics — `recorder`, `parsers`, `models` |
| `pipeline/` | Workflow pipeline — `llm_pipeline`, `order`, `result`, `state`, `status`, `audit`, `audit_query`, `config`, `hooks` |
| `tool/` | Tool definition management — `registry`, `schema`, `cli`, `audit`, `exceptions` |
| `ui/` | Web UI dashboard — `dashboard`, `monitor`, `server` |
| `workflow/` | Workflow orchestration — `dispatcher`, `engine`, `loader`, `schema`, `state_machine`, `label_state_machine`, `conditional_step`, `github`, `typecheck`, `gates` |

## Configuration

### Environment variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `GITHUB_TOKEN` / `GH_TOKEN` | Either one required for `watch` / `trigger` | — | GitHub API authentication |
| `GITHUB_REPOSITORIES` | Required for `watch` | — | Comma-separated `owner/repo` list to poll |
| `GHDAG_LLM_MODELS` | Optional | `llm-models.yml` in cwd → built-in defaults | Path to LLM engine/model allowlist YAML |
| `GHDAG_AUDIT_PATH` | Optional | `jobs/audit.jsonl` | Audit log file path (`ghdag llm`, `ghdag ui`, `ghdag audit-query`) |
| `GHDAG_TOKEN_WARN_THRESHOLD` | Optional | `500000` | Token usage warning threshold in Web UI dashboard |
| `GHDAG_SAFE_DEFAULT_PERMISSION` | Optional | `text_only` (hardcoded when unset) | Default capabilities preset for Claude engine pipeline steps |

### `llm-models.yml`

Override the per-engine model allowlist. Resolution order (see `ghdag.llm._config.load_engine_models`):

1. Path in `GHDAG_LLM_MODELS`
2. `llm-models.yml` in the current working directory
3. Built-in `DEFAULT_ENGINE_MODELS`

Example:

```yaml
engines:
  claude:
    - claude-sonnet-4-6
    - claude-haiku-4-5-20251001
  gemini:
    - gemini-2.5-flash
```

## Error Reference

All symbols below are defined in `src/ghdag/exceptions.py` and exported from the `ghdag.exceptions` module.

| Exception | Parent | When raised |
|---|---|---|
| `GhdagError` | `Exception` | Base class for all ghdag custom exceptions |
| `GitHubApiError` | `GhdagError` | GitHub API operation failure (carries `status_code`, `message`) |
| `AuthError` | `GitHubApiError` | Authentication failure (401, missing token) |
| `RateLimitError` | `GitHubApiError` | Rate limit exceeded (403 with `X-RateLimit-Remaining: 0`) |
| `PermissionDeniedError` | `GitHubApiError` | Insufficient permissions (403, 404 on private repos) |
| `NetworkError` | `GitHubApiError` | Connection timeout, DNS failure, or other network errors |

## License

MIT License — SPDX: `MIT` (see `pyproject.toml` and `LICENSE`).
