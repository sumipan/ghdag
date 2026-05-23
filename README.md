# ghdag

A DAG-based workflow engine for GitHub issues and projects.

## Installation

```bash
pip install git+https://github.com/sumipan/ghdag.git
```

## Quick Start

Run pending jobs from an exec.jsonl file:

```bash
ghdag run jobs/exec.jsonl
```

Watch a workflows directory and dispatch handlers on GitHub events:

```bash
ghdag watch workflows/
```

The `watch` command polls GitHub at a configurable interval and writes new
execution entries to the path specified by `--exec-md` (default: `exec.md`).
Use `--exec-md jobs/exec.jsonl` to write JSONL format instead.

## CLI Reference

| Subcommand | Description | Key options |
|---|---|---|
| `run` | Run exec.jsonl (or exec.md) via DagEngine | `--interval SEC`, `--hooks MODULE` |
| `watch` | Watch workflows dir and dispatch on GitHub events | `--interval SEC`, `--exec-md PATH`, `--once` |
| `trigger` | One-shot handler dispatch for a specific issue | `--handler NAME` (required), `--workflows-dir PATH`, `--workflow NAME`, `--exec-md PATH` |
| `llm` | One-shot LLM call without a workflow | `--engine NAME`, `--model ID`, `--list-engines`, `--list-models`, `--audit-path PATH` |
| `ui` | Launch Web UI dashboard | `--host ADDR`, `--port N`, `--interval SEC`, `--max-visible N` |
| `cleanup` | Archive completed/orphaned queue tasks | `--dry-run`, `--cutoff-days N`, `--orphan-days N` |
| `version` | Show version and exit | — |

Global flags: `--verbose` / `-v` (DEBUG logging), `--quiet` / `-q` (WARNING+ only).

### `ghdag run`

```
ghdag run <exec-file> [--interval SEC] [--hooks MODULE]
```

- `exec-file`: path to `exec.jsonl` (JSONL) or `exec.md` (text format, legacy).
  The engine auto-detects format by file extension (`.jsonl` → JSONL parser).
- `--hooks`: Python module path of a `DagHooks` implementation (e.g. `scripts.diary_hooks`).

### `ghdag watch`

```
ghdag watch <workflows-dir> [--interval SEC] [--exec-md PATH] [--once]
```

- `--once`: poll once and exit (event-driven / one-shot mode).
- `--exec-md`: output path for the exec file written by the dispatcher (default: `exec.md`).

### `ghdag trigger`

```
ghdag trigger <issue-number> --handler NAME [--workflows-dir PATH] [--workflow NAME] [--exec-md PATH]
```

Fetches the issue from GitHub and immediately dispatches the named handler.

### `ghdag llm`

```
ghdag llm [prompt] [--engine NAME] [--model ID] [--timeout SEC] [--stdin]
```

Reads prompt from the positional argument or stdin. Use `--list-engines` /
`--list-models` to enumerate available engines and models.

### `ghdag cleanup`

```
ghdag cleanup <repo-root> [--dry-run] [--cutoff-days N] [--orphan-days N]
```

Archives files from `jobs/` based on age. Completed tasks older than
`--cutoff-days` (default: 1) are archived; orphaned tasks older than
`--orphan-days` (default: 7) are archived to `jobs/archive/`.

## Workflow YAML

### WorkflowConfig

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | `str` | — | Workflow name |
| `triggers` | `list[TriggerConfig]` | — | Ordered trigger rules |
| `handlers` | `dict[str, HandlerConfig]` | — | Handler name → config |
| `polling_interval` | `int` | `30` | GitHub polling interval (seconds) |
| `template_dir` | `str \| None` | `None` | Template directory (relative to workflow file) |

### TriggerConfig

| Field | Type | Description |
|---|---|---|
| `label` | `str` | Label to match (e.g. `"pipeline:draft-ready"`) |
| `handler` | `str` | Handler name to invoke |

### HandlerConfig

| Field | Type | Default | Description |
|---|---|---|---|
| `steps` | `list[StepConfig]` | — | Ordered step list |
| `on_trigger` | `OnTriggerConfig \| None` | `None` | Context fetch on trigger |
| `type` | `str \| None` | `None` | Special handler type (e.g. `"reset"`) |
| `context_hook` | `str \| None` | `None` | Custom context-generation script |

### OnTriggerConfig

| Field | Type | Default | Description |
|---|---|---|---|
| `issue_context` | `bool` | `False` | Write Issue body + comments to `design.md` |

### StepConfig

| Field | Type | Default | Description |
|---|---|---|---|
| `template` | `str` | — | Order template filename (no extension) |
| `model` | `str` | — | Model ID to use |
| `id` | `str \| None` | `None` | Step ID (for `depends` references) |
| `engine` | `str` | `"claude"` | LLM engine name |
| `depends` | `list[str]` | `[]` | Dependency step IDs |

### DispatchResult

| Field | Type | Default | Description |
|---|---|---|---|
| `status` | `str` | — | `"dispatched"` / `"skipped"` / `"reset"` |
| `reason` | `str` | `""` | Human-readable reason |
| `exec_lines` | `list[str]` | `[]` | Written exec entries |

## Architecture

ghdag is organized into the following modules under `src/ghdag/`:

### `dag/`
Core DAG execution engine. No GitHub dependency.

| Module | Description |
|---|---|
| `engine.py` | `DagEngine` — main loop, task launch, completion handling |
| `parser.py` | `parse_jsonl()` (JSONL), `parse_exec_md()` (text, legacy), `validate_dependencies()` |
| `models.py` | `Task`, `RunningTask`, `DagConfig` |
| `state.py` | `jobs/done/` flag management |
| `fanout.py` | Fan-out task expansion |
| `hooks.py` | `DagHooks` interface, `DefaultHooks` |
| `watcher.py` | File-system watcher integration |

### `workflow/`
GitHub polling, YAML schema, and dispatching.

| Module | Description |
|---|---|
| `dispatcher.py` | `WorkflowDispatcher` — polling loop and handler dispatch |
| `schema.py` | `WorkflowConfig`, `HandlerConfig`, `TriggerConfig`, `DispatchResult` |
| `loader.py` | YAML → `WorkflowConfig` loading |
| `github.py` | `GitHubIssueClient` — GitHub API access |
| `engine.py` | `_GenericAdapter` — unified engine invocation |

### `pipeline/`
LLM pipeline state and order management.

| Module | Description |
|---|---|
| `llm_pipeline.py` | `LLMPipelineAPI` — order submission |
| `order.py` | `TemplateOrderBuilder` |
| `result.py` | Result parsing |
| `state.py` | `PipelineState` |
| `audit.py` | Audit log writing |
| `audit_query.py` | Audit log querying |
| `config.py` | Pipeline configuration |
| `status.py` | Task status helpers |
| `wait.py` | Completion waiting |

### `llm/`
Engine specs and model configuration.

| Module | Description |
|---|---|
| `engines.py` | `call()`, `list_engines()`, `list_models()` |
| `spec.py` | `EngineSpec`, `ENGINE_SPECS` |
| `capabilities.py` | Per-engine capability queries |
| `_config.py` | `load_engine_models()` — YAML config loader |
| `_constants.py` | `DEFAULT_ENGINE_MODELS` |

### `files/`
Repository `.md` file operations (see [Files API](#files-api)).

| Module | Description |
|---|---|
| `reader.py` | `md_read()` |
| `writer.py` | `md_write()` |
| `append.py` | `md_append()` |
| `promote.py` | `md_promote()` |
| `models.py` | `MdFile`, `AppendResult`, `WriteResult`, `PromoteResult` |

### `metrics/`
Task execution metrics.

| Module | Description |
|---|---|
| `models.py` | `FailureClass`, `TaskMetrics` |
| `parsers.py` | `parse_engine_model()`, `parse_token_count()` |

### `ui/`
Web UI dashboard.

| Module | Description |
|---|---|
| `server.py` | `run_server()` — HTTP + SSE server |
| `monitor.py` | Task monitoring logic |

### `cleanup.py`
Queue archiving logic (`cleanup_queue()`).

## Engine Adapters

Engines are defined as `EngineSpec` instances in `src/ghdag/llm/spec.py`.

| engine | cli | input_mode | default_model | extra_args | danger_flag |
|---|---|---|---|---|---|
| `claude` | `claude` | `cat_pipe` | `claude-sonnet-4-6` | — | `--dangerously-skip-permissions` (trailing) |
| `gemini` | `gemini` | `cat_pipe` | `gemini-2.5-flash` | `--approval-mode yolo` | — |
| `cursor` | `agent` | `stdin_redirect` | `auto` | — | `--force` (after_prompt) |
| `shell` | `bash` | `argv` | — | `-o pipefail` | — |

**`EngineSpec` fields:**

| Field | Type | Description |
|---|---|---|
| `name` | `str` | Engine identifier |
| `cli` | `str` | CLI executable name |
| `input_mode` | `"cat_pipe" \| "stdin_redirect" \| "argv"` | How the prompt is passed to the process |
| `prompt_flag` | `str \| None` | Flag preceding the prompt string |
| `model_flag` | `str \| None` | Flag preceding the model name |
| `default_model` | `str \| None` | Default model ID |
| `danger_flag` | `str \| None` | Permissions-bypass flag |
| `danger_flag_position` | `"trailing" \| "after_prompt" \| "none"` | Where `danger_flag` is inserted |
| `extra_args` | `tuple[str, ...]` | Additional CLI arguments prepended before the model/prompt |

## Files API

`ghdag.files` provides atomic, path-traversal-safe operations on `.md` files
within the repository root. Agents must use this API instead of direct `open()`
calls.

```python
from ghdag.files import md_read, md_write, md_append, md_promote
```

| Function | Signature | Returns | Description |
|---|---|---|---|
| `md_read` | `(path: str, *, repo_root=None)` | `MdFile` | Read `.md` with frontmatter parsing. Supports `[[wikilink]]` paths. |
| `md_write` | `(path: str, content: str, *, repo_root=None)` | `WriteResult` | Atomically overwrite a file (creates if absent). Writes an audit entry. |
| `md_append` | `(path, section, body, *, idempotency_key=None, repo_root=None)` | `AppendResult` | Append `body` under `section` heading with idempotency. Raises `FileNotFoundError` if the file does not exist. |
| `md_promote` | `(source_path, target_path, *, section="Promoted", idempotency_key=None, repo_root=None)` | `PromoteResult` | Copy content of `source_path` into `section` of `target_path` via `md_append`. |

### Data models

| Class | Fields | Description |
|---|---|---|
| `MdFile` | `path`, `frontmatter: dict`, `content: str` | Parsed `.md` file |
| `AppendResult` | `status: AppendStatus`, `path`, `section`, `body_hash` | Result of `md_append` |
| `AppendStatus` | `APPENDED \| NOOP \| RECOVERED` | Append outcome |
| `WriteResult` | `path`, `bytes_written: int` | Result of `md_write` |
| `PromoteResult` | `status: PromoteStatus`, `source_path`, `target_path`, `section` | Result of `md_promote` |
| `PromoteStatus` | `PROMOTED \| NOOP` | Promote outcome |

`md_append` is idempotent: if the same content (identified by SHA-256 hash or
`idempotency_key`) is already present in `section`, it returns `AppendStatus.NOOP`.
A partially-written block (start marker present, end marker absent) is recovered
automatically.

## Configuration

### `llm-models.yml`

Override the default allowed-model list per engine. Search order:

1. Path in `GHDAG_LLM_MODELS` environment variable
2. `llm-models.yml` in the current working directory
3. Built-in defaults (`DEFAULT_ENGINE_MODELS`)

Example:

```yaml
engines:
  claude:
    - claude-sonnet-4-6
    - claude-haiku-4-5-20251001
  gemini:
    - gemini-2.5-flash
```

### Environment variables

| Variable | Description |
|---|---|
| `GHDAG_LLM_MODELS` | Path to a custom `llm-models.yml` file |
| `GHDAG_AUDIT_PATH` | Path to the audit log file (used by `ghdag llm --audit-path`) |

## License

MIT
