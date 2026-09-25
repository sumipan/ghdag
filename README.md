# ghdag

ghdag is a local, file-based orchestrator that turns GitHub Issue labels into LLM/shell task graphs and runs them with a dependency-aware DAG runner.
Unlike CI-hosted orchestrators (GitHub Actions, Dagger), ghdag runs on your machine as two independent processes — an intake tower (`ghdag watch`) and an execution tower (`ghdag run`) — that communicate only through `exec.jsonl` task records and `jobs/done/<uuid>` markers.

## Status

![stability](https://img.shields.io/badge/stability-pre--1.0-orange)
![version](https://img.shields.io/badge/version-v0.80.0-blue)
![python](https://img.shields.io/badge/python-%3E%3D3.10-blue)
![license](https://img.shields.io/badge/license-MIT-green)

| Item | Value |
|---|---|
| Current release | **v0.80.0** (`pyproject.toml` `version = "0.80.0"`) |
| Stability | pre-1.0 (`0.Y.Z`); public interfaces may change in any minor release before `1.0.0` |
| Python | `>=3.10` (classifiers: 3.10, 3.11, 3.12, 3.13) |
| License | MIT (SPDX: `MIT`) |

## Installation

Prerequisites: Python 3.10+ on a POSIX system (the runner uses `fcntl` locks and process groups). LLM steps additionally need the engine CLI on `PATH` (`claude`, `agent` for cursor, `codex`, or `gemini`).

Install a release tag from GitHub:

```bash
pip install "git+https://github.com/sumipan/ghdag.git@v0.80.0"
```

Install from a local checkout (with development tools):

```bash
git clone https://github.com/sumipan/ghdag.git
cd ghdag
pip install -e ".[dev]"
```

| Item | Value |
|---|---|
| Console script | `ghdag` → `ghdag.cli:main` (also `python -m ghdag`) |
| Runtime dependencies | `watchdog>=4.0.0`, `pyyaml>=6.0` |
| `dev` extra | `pytest>=7.0.0`, `pytest-cov>=4.0.0`, `mypy>=1.10.0`, `ruff>=0.4.0`, `import-linter>=2.0`, `types-PyYAML>=6.0` |
| Package data | `ghdag/py.typed`, `ghdag/ui/static/*` |

GitHub access uses the standard library (`urllib`); there is no `requests` or `gh` CLI dependency.

## Quick Start

### 1. Run a DAG from a queue file

Each line of `exec.jsonl` is one task. `depends` lists upstream task UUIDs.

```bash
mkdir -p jobs
cat > jobs/exec.jsonl <<'EOF'
{"uuid":"demo-0001","command":"echo hello from ghdag","depends":[]}
{"uuid":"demo-0002","command":"echo second","depends":["demo-0001"]}
EOF
ghdag run jobs/exec.jsonl   # long-running; stop with Ctrl-C (SIGINT) or SIGTERM
```

`demo-0002` starts only after `demo-0001` succeeds. Each successful task writes `jobs/done/<uuid>` containing `0`; lifecycle events go to `jobs/audit.jsonl`. The runner keeps watching `exec.jsonl`, so tasks appended later are picked up without a restart.

### 2. Dispatch tasks from GitHub Issue labels

`workflows/sample.yml`:

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

Each step's `template` must exist as `workflows/templates/<template>.md` (the order text sent to the engine). Poll once:

```bash
export GITHUB_TOKEN="<token>"            # or GH_TOKEN
export GITHUB_REPOSITORIES="owner/repo"
ghdag watch workflows --exec-md jobs/exec.jsonl --once
```

Issues labelled `pipeline:develop-ready` get their steps appended to `jobs/exec.jsonl`, where a running `ghdag run jobs/exec.jsonl` executes them.

### 3. Call an LLM from Python

```python
from ghdag.llm import TEXT_ONLY, call_text

result = call_text("Summarize this repo in one line.", engine="claude", capabilities=TEXT_ONLY)
print(result.body if result.success else result.stderr)
```

## CLI Reference

Entry point: `ghdag.cli.main:_build_parser()`. Global options: `--verbose` / `-v` (DEBUG logging), `--quiet` / `-q` (WARNING and above).

### Commands

| Command | Description |
|---|---|
| `ghdag run` | Run `exec.jsonl` via `DagEngine` (long-running) |
| `ghdag watch` | Poll GitHub Issues and dispatch workflow handlers via `WorkflowDispatcher` |
| `ghdag ui` | Launch the Web UI dashboard (HTTP + SSE) |
| `ghdag llm` | One-shot LLM call without a workflow |
| `ghdag version` | Print the installed ghdag version |
| `ghdag cleanup` | Archive completed/orphaned queue tasks |
| `ghdag trigger` | Dispatch one workflow handler for one Issue (one-shot) |
| `ghdag status` | Show Issue DAG status and/or running tasks |
| `ghdag dag` | DAG utilities (`recover`, `cancel`) |
| `ghdag dag recover` | Reset failed or pending steps of an existing handler run so they re-execute |
| `ghdag dag cancel` | Request cancellation of a running task via `jobs/cancel/<uuid>` |
| `ghdag audit-query` | Query `audit.jsonl` by correlation ID or detect correlation bursts |
| `ghdag tools` | Tool definition management (`list`) |
| `ghdag tools list` | List tool definitions from a directory |
| `ghdag quota` | Manage quota gate state (`report`, `clear`, `drain`, `resume`, `status`) |
| `ghdag quota report` | Report an engine as `available` or `paused` |
| `ghdag quota clear` | Clear an engine's quota state |
| `ghdag quota drain` | Stop new launches for an engine (drain mode) |
| `ghdag quota resume` | Release an engine from drain mode |
| `ghdag quota status` | Print an engine-level quota / drain / queue snapshot as JSON |

### Arguments and defaults

| Command | Arguments (defaults) |
|---|---|
| `run` | `exec_jsonl`; `--interval SEC` (`1.0`); `--hooks MODULE` (`DagHooks` implementation module; default `AuditHooks`); `--max-concurrency N` (unlimited) |
| `watch` | `workflows_dir`; `--interval SEC` (`30`); `--exec-md PATH` (`jobs/exec.jsonl`); `--once`; `--pause-file PATH` (disabled); `--state-dir PATH` (`<exec-md parent's parent>/.pipeline-state`) |
| `ui` | `--repo-root PATH` (`.`); `--host HOST` (`127.0.0.1`); `--port PORT` (`8080`); `--interval SEC` (`3.0`); `--max-visible N` (`30`) |
| `llm` | `[prompt]` (stdin if omitted); `--engine` / `-e` (`claude`); `--model` / `-m` (engine default); `--timeout SEC` (no limit); `--dangerously-skip-permissions`; `--permission-mode {default,plan,bypassPermissions}`; `--capabilities-preset {text_only,json_only,web_research,dangerous_full_access}`; `--stdin`; `--list-engines`; `--list-models`; `--audit-path` (`GHDAG_AUDIT_PATH`); `--correlation-id`; `--request-id` |
| `version` | — |
| `cleanup` | `repo_root`; `--dry-run`; `--cutoff-days` (`1`); `--orphan-days` (`7`); `--auto-repair` (fix orphan / dead entries; default is detect-only) |
| `trigger` | `issue_number`; `--handler` (required); `--workflows-dir PATH` (`workflows`); `--exec-md PATH` (`jobs/exec.jsonl`); `--workflow NAME` (auto-detected when only one workflow exists); `--redispatch`; `--reason REASON`; `--state-dir PATH` (derived from `--exec-md`) |
| `status` | `--issue N` and/or `--running` (at least one); `--handler` and `--workflow` (required with `--issue`); `--json`; `--exec-jsonl PATH` (`$GHDAG_EXEC_JSONL` or `jobs/exec.jsonl`); `--state-dir PATH` (`.pipeline-state`); `--done-dir PATH` (`<exec parent>/done`); `--running-dir PATH` (`<exec parent>/running`); `--audit-path PATH` (optional) |
| `dag recover` | `--issue` (required); `--handler` (required); `--from STEP_NAME`; `--dry-run`; `--workflows-dir PATH` (`workflows`); `--exec-md PATH` (`jobs/exec.jsonl`); `--workflow NAME` (auto); `--state-dir PATH` (`.pipeline-state`); `--keep-results` |
| `dag cancel` | `uuid`; `--queue-dir PATH` (`jobs`) |
| `audit-query` | `--correlation-id`; `--burst-detect` (exit `1` when a burst is found); `--since` (ISO 8601, correlation mode only); `--audit-path` (`jobs/audit.jsonl`); `--window-sec` (`600`); `--threshold` (`10`) |
| `tools list` | `--path PATH` (required); `--json` |
| `quota report` | `engine`; `--status {available,paused}` (required); `--observed-at` (required, ISO 8601 with timezone); `--resume-at`; `--reason`; `--state-path` (`jobs/quota-gate.json`) |
| `quota clear` | `engine`; `--observed-at` (required); `--state-path` (`jobs/quota-gate.json`) |
| `quota drain` | `engine`; `--reason`; `--state-path` (`jobs/quota-gate.json`) |
| `quota resume` | `engine`; `--state-path` (`jobs/quota-gate.json`) |
| `quota status` | `--state-path` (`jobs/quota-gate.json`); `--exec-path` (`jobs/exec.jsonl`); `--done-dir` (`jobs/done`) |

### Command behavior notes

| Topic | Behavior |
|---|---|
| `ghdag run` working directory | Tasks run with `cwd` = parent of the `exec.jsonl` directory (for `jobs/exec.jsonl`, the repository root) |
| Single runner | `DagEngine` holds `<queue>/.ghdag.lock`; a second runner on the same queue fails with "Another DagEngine is already running" |
| `ghdag dag cancel` | Creates `jobs/cancel/<uuid>` only when `jobs/running/<uuid>.json` exists; otherwise prints `error: not running: <uuid>` and exits `1`. The CLI never signals processes itself — the runner polls cancel markers, stops the task's process group, and writes `CANCELLED` |
| `ghdag dag recover` | Clears done markers of failed/pending steps so the runner re-executes them. Steps that are still running are left alone. Existing result files are moved to `<result>.prev-<timestamp>` first; `--keep-results` keeps them in place |
| `--redispatch` (`ghdag trigger`) | Increments the handler generation and starts a new run (use when recover is not possible); `--reason` is recorded in `audit.jsonl` |
| `ghdag watch` rate limiting | When GitHub returns a rate-limit error with a reset time, polls are skipped until the reset passes (log: `rate limited: skip poll until <ISO8601>`) |

### Module entry points

| Command | Purpose |
|---|---|
| `python -m ghdag.github_cli <command> ...` | `gh`-style client over `get_forge()`: `issue {view,edit,comment,close,create}`, `pr {list,view,edit,diff,create,merge,checks,ready}`, `run {view,rerun}`, `repo view`, `api` |
| `python -m ghdag.workflow.gates --gate NAME --body-file PATH [--labels-file PATH]` | Run a gate rule against an Issue body; prints violations as JSON |
| `python -m ghdag.workflow.state_machine transition --workflow YAML ISSUE TARGET_LABEL` | Validate a label transition against `transitions` / `reset_label` and apply it |
| `python -m ghdag.workflow.render TEMPLATE [key=value ...]` | Expand a `render: live` template at run time and execute it with `bash -o pipefail` (exit `2` on template errors) |
| `python -m ghdag.workflow.conditional_step [--engine {claude,cursor}] [--model M] TEMPLATE [KEY=VALUE ...]` | Substitute template variables and invoke claude or cursor |

## Public API

Import from the package paths below. Modules and attributes starting with `_` are private.

### Top-level (`ghdag.__all__`)

| Symbol | Kind | Defined in | Role |
|---|---|---|---|
| `GhdagError` | exception | `ghdag.core.exceptions` | Base class of ghdag domain errors |
| `QueueTask` | dataclass | `ghdag.io.queue` | One queue task (`uuid`, `timestamp`, `engine`, `order_path`, `result_path`, `stderr_path`, `is_done`) |
| `QueueTaskStore` | class | `ghdag.io.queue` | Scan a queue directory and its done markers: `QueueTaskStore(queue_dir, done_dir)` |
| `LLMPipelineAPI` | class | `ghdag.pipeline.llm_pipeline` | Intake API: turns workflow steps into order files and `exec.jsonl` records |
| `PipelineState` | class | `ghdag.pipeline.state` | Pipeline state directory, generations, and `exec.jsonl` append: `PipelineState(state_dir, exec_jsonl_path)` |
| `DagEngine` | class | `ghdag.dag.engine` | Local DAG runner: `DagEngine(config, hooks=None)`; `.run()`, `.append_task(...)`, `.mark_done(...)` |
| `WorkflowDispatcher` | class | `ghdag.workflow.dispatcher` | Label-driven dispatch: `.poll_once()`, `.run()`, `.dispatch(...)` |
| `QuotaGate` | class | `ghdag.quota` | Engine quota / drain admission: `QuotaGate(state_path, audit_path=None, brake_state_path=None)` |
| `IssueStatus` | dataclass | `ghdag.status` | Aggregated status of one Issue × handler run |
| `StepStatus` | dataclass | `ghdag.status` | Status of one step |
| `RunningTask` | dataclass | `ghdag.status` | Snapshot of `jobs/running/<uuid>.json` |
| `issue_status` | function | `ghdag.status` | Build `IssueStatus` from queue / state / done directories |
| `running_tasks` | function | `ghdag.status` | List `RunningTask` from a running directory |

`ghdag.__version__` holds the installed distribution version (`"unknown"` when not installed).

### Key signatures

| Callable | Signature |
|---|---|
| `DagEngine` | `(config: DagConfig, hooks: DagHooks \| None = None)` |
| `WorkflowDispatcher` | `(workflows, github_client, pipeline, queue_dir="queue", pause_file=None, workflows_dir=None)` |
| `LLMPipelineAPI` | `(pipeline_state, order_builder, queue_dir="queue", *, order_builders=None)`; `.submit(...) -> list[str]` |
| `QuotaGate` | `(state_path, audit_path=None, brake_state_path=None)`; methods `report`, `clear`, `drain`, `resume`, `admit`, `begin_run`, `finish_run`, `defer`, `release_ready`, `snapshot`, `wait_idle` |
| `issue_status` | `(issue_number, *, handler=None, workflow=None, exec_jsonl_path, state_dir, done_dir, running_uuids=None, audit_path=None, running_dir=None) -> IssueStatus` |
| `running_tasks` | `(running_dir) -> list[RunningTask]` |
| `ghdag.llm.call` | `(prompt, *, engine="claude", model=None, timeout=None, stdin_text=None, cwd=None, capabilities=TEXT_ONLY, dangerously_skip_permissions=False, resume_session_id=None, isolation=None) -> LLMResult` |
| `ghdag.llm.call_text` | same parameters as `call` `-> TextResult` |
| `ghdag.llm.call_managed` | `(prompt, *, engine="claude", model=None, timeout=None, stdin_text=None, cwd=None, capabilities=TEXT_ONLY, fallback_candidates=(), additional_tags=None, quota_gate=None) -> ManagedResult` |
| `ghdag.llm.build_llm_cmd` | `(engine, model, prompt, *, capabilities=TEXT_ONLY, dangerously_skip_permissions=False, resume_session_id=None, isolation=False) -> list[str]` |
| `ghdag.workflow.load_workflows` | `(directory) -> list[WorkflowConfig]` |
| `ghdag.workflow.get_forge` | `(repo=None) -> ForgePort` |
| `ghdag.pipeline.submit_order` | `(state, *, engine, content, audit_source, model=None, queue_dir="jobs", depends=None, annotations=None, idempotency_key=None, correlation_id=None) -> dict` |
| `ghdag.pipeline.wait_for_result` | `(exec_done_dir, uuid, *, timeout, poll_interval=0.5) -> tuple[str, str]` |
| `ghdag.dag.recover.plan_recover` | `(*, state_dir, exec_jsonl_path, workflow_name, handler_name, issue_number, queue_dir, done_dir, from_step=None, running_uuids=None) -> RecoverPlan` |
| `ghdag.dag.recover.execute_recover` | `(plan, *, queue_dir, done_dir, dry_run=False, running_uuids=None, keep_results=False, now=None) -> RecoverResult` |
| `ghdag.cleanup.cleanup_queue` | `(queue_dir, archive_dir, done_dir, exec_md, cutoff_days=1, orphan_days=7, dry_run=False, auto_repair=False) -> CleanupResult` |
| `ghdag.maintenance.validate_exec_jsonl` | `(exec_jsonl_path) -> list[tuple[int, str]]` |
| `ghdag.maintenance.repair_exec_jsonl` | `(exec_jsonl_path, *, dry_run=False) -> int` |
| `ghdag.maintenance.repair_jobs_done` | `(exec_jsonl_path, done_dir, *, dry_run=False) -> dict[str, int]` |
| `ghdag.audit.emit_span` | `(*, orchestration_id, name, route, start_ts, duration_ms, status, span_id, parent_span_id, attributes=None, log_path=None) -> None` |
| `ghdag.audit.make_span_id` | `() -> str` (8 hex characters) |

### Subpackage `__all__`

| Package | Public symbols |
|---|---|
| `ghdag.dag` | `DagConfig`, `DagEngine`, `DagHooks`, `DefaultHooks`, `RunningTask`, `Task`, `check_pipeline_status`, `extract_tee_target`, `parse_jsonl` |
| `ghdag.workflow` | `WorkflowConfig`, `TriggerConfig`, `HandlerConfig`, `StepConfig`, `OnTriggerConfig`, `DispatchResult`, `load_workflows`, `WorkflowDispatcher`, `GitHubIssueClient`, `create_github_client`, `ForgePort`, `get_forge` |
| `ghdag.workflow.gates` | `Violation`, `GateRule`, `GATE_REGISTRY`, `get_gate` |
| `ghdag.pipeline` | `AuditHooks`, `ModelValidationError`, `PipelineConfig`, `PipelineState`, `OrderBuilder`, `TemplateOrderBuilder`, `InlineOrderBuilder`, `resolve_models`, `status_rank`, `parse_frontmatter`, `LLMPipelineAPI`, `SubmittedStep`, `task_status`, `wait_for_result`, `read_task_exit_events`, `get_latest_status`, `STATE_EMPTY`, `STATE_DEFERRED`, `STATE_ENGINE_ERROR`, `STATE_FAIL`, `STATE_OK`, `STATE_PENDING_DEPS`, `STATE_PENDING_RUN`, `STATE_REJECTED`, `STATE_RUNNING`, `STATE_UNKNOWN_DONE`, `make_order_record`, `submit_order` |
| `ghdag.llm` | `_config`, `DEFAULT_ENGINE_MODELS`, `ENGINE_DEFAULTS`, `ENGINE_SPECS`, `EngineModelError`, `EngineSpec`, `InputMode`, `PromptFlag`, `LLMCapabilities`, `LLMParseError`, `LLMResult`, `ManagedResult`, `TextResult`, `SessionRecord`, `SessionStore`, `TEXT_ONLY`, `JSON_ONLY`, `WEB_RESEARCH`, `DANGEROUS_FULL_ACCESS`, `build_llm_cmd`, `call`, `call_managed`, `call_text`, `get_engine_models`, `list_engines`, `list_models`, `validate_engine_model` |
| `ghdag.llm.capabilities` | `LLMParseError`, `LLMCapabilities`, `TEXT_ONLY`, `JSON_ONLY`, `WEB_RESEARCH`, `DANGEROUS_FULL_ACCESS`, `READONLY_OBSERVE`, `PRESETS` |
| `ghdag.llm.adapters` | `EngineOutputAdapter`, `get_output_adapter` |
| `ghdag.files` | `AppendResult`, `AppendStatus`, `MdFile`, `PathTraversalError`, `PromoteResult`, `PromoteStatus`, `WriteResult`, `md_append`, `md_promote`, `md_read`, `md_write` |
| `ghdag.files.links` | `job_footer`, `rewrite_links`, `summary_footer` |
| `ghdag.io` | submodules `audit`, `audit_query`, `done`, `exec_jsonl`, `queue`, `sessions` |
| `ghdag.config` | `env` (accessors listed in [Environment variables](#environment-variables)) |
| `ghdag.core` | `command` |
| `ghdag.core.ports` | `ForgePort` |
| `ghdag.audit` | `emit_span`, `make_span_id` |
| `ghdag.audit.span` | `EVENT_TYPE_E2E_COMPLETED`, `EVENT_TYPE_E2E_FAILED`, `EVENT_TYPE_LATENCY_SPAN`, `LATENCY_SPAN_JSONL`, `emit_span`, `make_span_id` |
| `ghdag.metrics` | `MetricsRecorder`, `TaskMetrics` |
| `ghdag.tool` | `FallbackEntry`, `TOOL_EXIT_CODES`, `ToolDef`, `ToolRegistry`, `write_tool_fallback_audit` |
| `ghdag.cleanup` | `cleanup_queue`, `CleanupResult`, `file_timestamp`, `QUEUE_FILE_RE` |
| `ghdag.exceptions` | `GhdagError`, `GitHubApiError`, `AuthError`, `RateLimitError`, `PermissionDeniedError`, `NetworkError` |
| `ghdag.github_cli` | `GitHubClient`, `DEFAULT_REPO`, `API_BASE`, `GRAPHQL_URL`, `get_forge` |
| `ghdag.cli` | `main` |

Modules without `__all__` that are intended for direct import:

| Module | Public functions / classes |
|---|---|
| `ghdag.github_client` | `GitHubClient(token=None, repo=None, etag_cache_path=None, rate_limit_max_wait_sec=None)`, `create_github_client`, `create_github_clients`, `GitHubIssueClient` (alias of `GitHubClient`) |
| `ghdag.forge.local` | `LocalForge(root)` — file-backed `ForgePort` storing Issues/PRs under `<root>/.forge/` |
| `ghdag.maintenance` | `validate_exec_jsonl`, `repair_exec_jsonl`, `repair_jobs_done` |
| `ghdag.markdown.body_editor` | `split_h2_sections`, `count_heading`, `get_section`, `upsert_section`, `filter_section_by_paths`, `get_subsections` |
| `ghdag.llm.engines` | `supports_capability(engine, capability)` (not re-exported from `ghdag.llm`) |
| `ghdag.core.vocabulary` | Done-marker constants (`DONE_*`), `QUEUE_FILE_RE`, `PIPELINE_STATUS_RE`, fan-out constants |

### Capability presets

`LLMCapabilities` fields: `permission_mode`, `output_format`, `allowed_tools`, `disallowed_tools`, `stream`, `sandbox`, `resume`. Presets (`ghdag.core.capabilities.PRESETS`, used by `StepConfig.permission` and `GHDAG_SAFE_DEFAULT_PERMISSION`):

| Name | Constant | Settings |
|---|---|---|
| `text_only` | `TEXT_ONLY` | text output; `disallowed_tools=("Bash", "Edit", "Write", "NotebookEdit", "WebFetch", "WebSearch")` |
| `json_only` | `JSON_ONLY` | JSON output; same `disallowed_tools` as `text_only` |
| `web_research` | `WEB_RESEARCH` | `allowed_tools=("WebFetch", "WebSearch", "Read", "Grep", "Glob")`; `disallowed_tools=("Bash", "Edit", "Write", "NotebookEdit")` |
| `dangerous_full_access` | `DANGEROUS_FULL_ACCESS` | `permission_mode="bypassPermissions"` |
| `readonly_observe` | `READONLY_OBSERVE` | `sandbox="readonly"` (claude: `--permission-mode plan`; cursor: `--sandbox enabled`); `disallowed_tools=("Edit", "NotebookEdit")` |

`readonly_observe` is available to workflow steps and the Python API but not to `ghdag llm --capabilities-preset`. `disallowed_tools` is a no-op on codex and cursor (no equivalent CLI flag); `allowed_tools` is a no-op on codex.

### Engines

| Engine | Default model (`ENGINE_DEFAULTS`) | Built-in allowed models (`DEFAULT_ENGINE_MODELS`) |
|---|---|---|
| `claude` | `claude-sonnet-4-6` | `claude-opus-4-7`, `claude-opus-4-6`, `claude-sonnet-4-6`, `claude-haiku-4-5-20251001` |
| `codex` | `gpt-5.6-terra` | `gpt-5.6-terra`, `gpt-5.6-luna`, `gpt-5.5`, `gpt-5.4-mini` |
| `cursor` | `auto` | `auto`, `composer-2`, `composer-2-fast`, `gpt-5.2`, `gpt-5.3-codex`, `gpt-5.3-codex-fast`, `gpt-5.3-codex-high`, `gpt-5.3-codex-high-fast`, `gpt-5.4-medium-fast` |
| `gemini` | `gemini-2.5-flash` | `gemini-2.5-pro`, `gemini-2.5-flash` |
| `shell` | — | `bash` (runs the order file as a bash script; no LLM call) |

The allowed-model list can be replaced with `llm-models.yml` (see [Configuration](#configuration)).

### Protocols (extension points)

| Protocol | Module | Implement to |
|---|---|---|
| `DagHooks` | `ghdag.core.ports.dag_hooks` (re-exported by `ghdag.dag`) | React to task lifecycle events: `on_task_start`, `on_task_success`, `on_task_failure`, `on_task_rejected`, `on_task_dep_failed`, `on_task_empty_result`, `on_task_cancelled`, `on_task_progress`, `on_shutdown`, `check_rejected`, `check_pipeline_status`, `check_promote_target`. Subclass `DefaultHooks` and pass the module to `ghdag run --hooks` (resolved via a `HOOKS_CLASS` attribute or the first class defining `on_task_success`) |
| `ForgePort` | `ghdag.core.ports.forge` | Provide an Issue/PR backend (`GitHubClient` and `LocalForge` implement it) |
| `GitHubIssuePort` | `ghdag.core.ports.github` | Minimal Issue client used by `WorkflowDispatcher` (`get_issue`, `reopen_issue`, `list_issues`, `list_all_issues`, …) |
| `OrderBuilder` | `ghdag.core.ports.order` | Build order text: `build_order(step_id, context) -> str` |
| `EngineOutputAdapter` | `ghdag.core.ports.output` | Parse engine stdout/stderr: `extract_result_text`, `extract_token_usage`, `extract_session_id`, `extract_error`, `classify_failure` |
| `GateRule` | `ghdag.core.ports.gate` | Validate an Issue body: `check(body, labels) -> list[Violation]`. Register in `GATE_REGISTRY` or via the `ghdag.gates` entry-point group |

## Architecture

### Towers and file contracts

```
GitHub Issues / labels   (or LocalForge when GHDAG_FORGE=local)
        │
        ▼
 intake tower: workflow/ + pipeline/   (ghdag watch / ghdag trigger)
        │ appends task records
        ▼
 jobs/exec.jsonl
        │ watched by
        ▼
 execution tower: dag/ DagEngine       (ghdag run)
        │ writes
        ▼
 jobs/done/<uuid>, jobs/running/, jobs/events/, jobs/audit.jsonl
```

Import-linter contracts (`pyproject.toml`) enforce the separation: the intake tower does not import the execution tower and vice versa, `ghdag.core` depends on no other ghdag package, and `io` / `files` / `llm` do not import orchestration code.

| File | Written by | Read by | Contents |
|---|---|---|---|
| `jobs/exec.jsonl` | `LLMPipelineAPI.submit`, `submit_order`, `DagEngine.append_task` | `DagEngine` | One JSON task record per line (see [exec.jsonl task fields](#execjsonl-task-fields)) |
| `jobs/done/<uuid>` | `DagEngine` / `TaskLauncher` | dispatcher, `ghdag status`, `ghdag dag recover`, UI | Done marker (see [Done markers](#done-markers)) |
| `jobs/running/<uuid>.json` | `TaskLauncher` on launch | `ghdag dag cancel`, `ghdag status --running`, UI, restart adoption | `pgid`, `started_at`; optional `interrupted_at`, `interrupted_reruns`. Removed on exit |
| `jobs/cancel/<uuid>` | `ghdag dag cancel`, UI `/api/stop` | `DagEngine` | Empty marker requesting cancellation |
| `jobs/events/<uuid>.jsonl` | `TaskLauncher` stream drain | UI SSE `progress` | Raw engine stream events (claude / cursor / codex with `result_path`) |
| `jobs/audit.jsonl` | `AuditHooks`, `ghdag.io.audit` | `ghdag audit-query`, UI dashboard | Audit events |
| `jobs/quota-gate.json` | `QuotaGate`, `ghdag quota` | launch admission | `engines`, `deferred_tasks`, `draining_engines`, `running_tasks` |
| `jobs/latency_span.jsonl` | `ghdag.audit.emit_span` | external tracing tools | One latency span per line |

### DAG runner lifecycle

| Phase | Behavior |
|---|---|
| Launch | A task starts when all `depends` succeeded and `QuotaGate.admit` allows its engine/role. Each task runs in its own process group with `GHDAG_TASK_UUID` and `GHDAG_RESULT_PATH` in its environment |
| Dependency failure | Downstream tasks of a failed task get `DEP_FAILED` |
| Timeout / cancel | SIGTERM to the process group, SIGKILL after `DagConfig.kill_grace`; done marker `TIMEOUT` / `CANCELLED` |
| Deferral | A task printing `PIPELINE_STATUS: DEFERRED` gets `DEFERRED`; the marker is removed and the task relaunched when `QuotaGate.release_ready()` releases it |
| Circuit breaker | The runner stops after `max_consecutive_failures` consecutive failures; the counter resets when the gap since the previous failure exceeds `failure_window_sec` |
| First SIGTERM / SIGINT | Records `interrupted_at` in every `jobs/running/<uuid>.json`, stops launching new tasks, and drains running tasks until `task_timeout` elapses (no drain when `task_timeout` is `None`); remaining tasks are then terminated and the runner exits |
| Second SIGTERM / SIGINT | Exits immediately |
| Restart | After the first `exec.jsonl` load, `adopt_orphans` inspects `jobs/running/*.json`: live tasks are adopted and tracked to completion; dead tasks are closed with `ORPHANED_ON_RESTART`; interrupted tasks (`interrupted_at` set) are killed if still alive and relaunched once with the same UUID and `GHDAG_PREVIOUS_ATTEMPT=interrupted` (a second interruption closes them with `ORPHANED_ON_RESTART`) |

### Engine streaming and failure classification

| Topic | Behavior |
|---|---|
| Progress events | For claude (`--output-format stream-json`), cursor (`--output-format stream-json`), and codex (`--json`) tasks with `result_path`, stdout is drained line by line into `jobs/events/<uuid>.jsonl`. Without stream flags the runner reads stdout in bulk and sets `annotations.stream_fallback=true` |
| cursor result text | Assistant turns are reconstructed at `tool_call` boundaries and joined with a blank line |
| Interactive prompts | A non-zero exit whose last output line is a question (`?` or the full-width question mark U+FF1F) is classified `FailureClass.INTERACTIVE_PROMPT` (permanent, no retry); done marker `INTERACTIVE_PROMPT` |
| cursor `RetriableError:` | `[resource_exhausted]` → `EngineErrorKind.RATE_LIMIT`, other codes → `EngineErrorKind.CAPACITY`; both retryable and written as `ENGINE_ERROR` while retries remain |

### GitHub client

`GitHubClient` (`ghdag.github_client`) is a `urllib`-based REST/GraphQL client that implements `ForgePort` and `GitHubIssuePort`.

| Feature | Behavior |
|---|---|
| Authentication | `token` argument, else `GITHUB_TOKEN`, else `GH_TOKEN`; `AuthError` when none is set |
| Repository | `repo` argument, else the first entry of `GITHUB_REPOSITORIES`; `GhdagError` when neither is set |
| Pagination | List endpoints (`list_issues`, `list_all_issues`, `get_issue_comments`, `milestone_list`, `pr_list`, `pr_get` files, `pr_checks`, `run_logs_failed`, …) follow `Link: rel="next"` and return all pages. `pr_list(limit=None)` returns every matching PR; pass `limit` to cap it |
| ETag cache | Conditional requests (`If-None-Match` → 304) are cached in memory; with `etag_cache_path` or `GHDAG_ETAG_CACHE` the cache persists to one JSON file (max 2000 entries, LRU, atomic writes) shared across processes |
| Rate limits | On 403 with `X-RateLimit-Remaining: 0`, the client sleeps until the reset and retries once if the wait is ≤ `rate_limit_max_wait_sec` (argument > `GHDAG_RATE_LIMIT_MAX_WAIT_SEC` > `900`); otherwise raises `RateLimitError(reset_at=...)` immediately |

### Forge backends

| `GHDAG_FORGE` | Backend returned by `get_forge()` | Requirement |
|---|---|---|
| unset / `github` | `GitHubClient` | GitHub token |
| `local` | `ghdag.forge.local.LocalForge` | `GHDAG_FORGE_ROOT` (data under `<root>/.forge/`) |

Any other value raises `ValueError`.

### Module inventory

Every Python module under `src/ghdag/`. Package `__init__.py` files re-export the symbols listed under [Public API](#public-api); modules described as shims only re-export another module.

| Module | Role |
|---|---|
| `__init__.py` | Top-level public API and `__version__` |
| `__main__.py` | `python -m ghdag` entry point |
| `exceptions.py` | Re-export of `GhdagError` and GitHub API exceptions |
| `github_cli.py` | `gh`-style CLI (`python -m ghdag.github_cli`) over `get_forge()` |
| `github_client.py` | `GitHubClient` REST/GraphQL client (ETag cache, pagination, rate limits) |
| `maintenance.py` | Queue validation and repair (`exec.jsonl`, done markers) |
| `quota.py` | `QuotaGate`: engine quota, drain, role admission, deferral |
| `status.py` | `issue_status` / `running_tasks` and their dataclasses |
| `audit/__init__.py` | Audit envelope API package |
| `audit/span.py` | `emit_span` / `make_span_id` latency span writer |
| `cleanup/__init__.py` | `cleanup_queue`, `CleanupResult`, `file_timestamp` |
| `cleanup/archiver.py` | `QueueArchiver`: move order/result/stderr files to `<archive>/YYYY-MM/` |
| `cleanup/link_rewriter.py` | `LinkRewriter`: rewrite wiki links in queue `.md` files to archived paths |
| `cleanup/orchestrator.py` | `cleanup_queue` phases and catch-all sweep |
| `cleanup/orphan_detector.py` | `OrphanDetector`: find orphaned queue tasks |
| `cleanup/pruner.py` | `ExecJsonlPruner`: remove archived task lines from `exec.jsonl` |
| `cli/__init__.py` | Exports `main` |
| `cli/main.py` | argparse parser (`_build_parser`) and dispatch |
| `cli/commands/__init__.py` | Command package marker |
| `cli/commands/audit_query.py` | `ghdag audit-query` |
| `cli/commands/cancel.py` | `ghdag dag cancel` |
| `cli/commands/cleanup.py` | `ghdag cleanup` |
| `cli/commands/llm.py` | `ghdag llm` |
| `cli/commands/quota.py` | `ghdag quota *` |
| `cli/commands/recover.py` | `ghdag dag recover` |
| `cli/commands/run.py` | `ghdag run` and `--hooks` loading |
| `cli/commands/status.py` | `ghdag status` |
| `cli/commands/trigger.py` | `ghdag trigger` |
| `cli/commands/ui.py` | `ghdag ui` |
| `cli/commands/watch.py` | `ghdag watch` |
| `config/__init__.py` | Exports `env` |
| `config/env.py` | Central environment variable accessors |
| `core/__init__.py` | Shared cross-tower primitives |
| `core/capabilities.py` | `LLMCapabilities` and presets |
| `core/command.py` | Engine command-line construction and engine adapters (`AdapterNotFoundError`) |
| `core/engine_spec.py` | `EngineSpec`, `InputMode`, `PromptFlag`, `ENGINE_SPECS` |
| `core/exceptions.py` | `GhdagError` and GitHub API exception hierarchy |
| `core/parsers.py` | Token usage parsing helpers |
| `core/vocabulary.py` | Done markers, queue file naming, `PIPELINE_STATUS` and fan-out conventions |
| `core/models/__init__.py` | Shared dataclass package |
| `core/models/dag.py` | `Task`, `DagConfig` |
| `core/models/files.py` | Markdown file models and `PathTraversalError` |
| `core/models/metrics.py` | `TaskMetrics`, `FailureClass` |
| `core/models/workflow.py` | Workflow YAML dataclasses and `validate_workflow_roles` |
| `core/ports/__init__.py` | Exports `ForgePort` |
| `core/ports/dag_hooks.py` | `DagHooks` Protocol |
| `core/ports/forge.py` | `ForgePort` Protocol |
| `core/ports/gate.py` | `GateRule` Protocol, `Violation` |
| `core/ports/github.py` | `GitHubIssuePort` Protocol |
| `core/ports/order.py` | `OrderBuilder` Protocol |
| `core/ports/output.py` | `EngineOutputAdapter` Protocol, `EngineError`, `EngineErrorKind` |
| `dag/__init__.py` | DAG runner public API |
| `dag/_util.py` | Internal DAG helpers |
| `dag/audit_hooks.py` | `AuditHooks`: `DefaultHooks` plus `audit.jsonl` writes |
| `dag/circuit_breaker.py` | `CircuitBreakerPolicy` consecutive-failure breaker |
| `dag/engine.py` | `DagEngine` main loop, signal handling, dependency resolution |
| `dag/engine_quarantine.py` | `EngineQuarantine`: temporarily block launches for broken engines |
| `dag/fanout.py` | Fan-out spec parsing and child record building (`FanoutError`) |
| `dag/fanout_manager.py` | `FanOutManager`: child generation and join |
| `dag/hooks.py` | `DagHooks` re-export and `DefaultHooks` |
| `dag/models.py` | `Task` / `DagConfig` re-export |
| `dag/parser.py` | `parse_jsonl` for `exec.jsonl` |
| `dag/recover.py` | `plan_recover` / `execute_recover` (`RecoverError`) |
| `dag/state.py` | Done-directory state helpers |
| `dag/task_launcher.py` | `TaskLauncher`: subprocess launch, streaming, completion, orphan adoption |
| `files/__init__.py` | Markdown file operations API |
| `files/_rotate.py` | Shim for `ghdag.io._rotate` |
| `files/append.py` | `md_append` (idempotent section append, `AppendRecoverError`) |
| `files/models.py` | Shim for `ghdag.core.models.files` |
| `files/promote.py` | `md_promote` |
| `files/reader.py` | `md_read` |
| `files/writer.py` | `md_write` |
| `files/links/__init__.py` | Wiki-link helper exports |
| `files/links/obsidian.py` | Obsidian wiki-link helpers (pure functions) |
| `forge/__init__.py` | `get_forge` backend factory |
| `forge/local.py` | `LocalForge` file-backed forge |
| `io/__init__.py` | Filesystem I/O facade |
| `io/_rotate.py` | Size-based `audit.jsonl` rotation |
| `io/audit.py` | `audit.jsonl` writers |
| `io/audit_query.py` | Read-only `audit.jsonl` queries |
| `io/done.py` | Done-marker I/O |
| `io/exec_jsonl.py` | `exec.jsonl` read / parse / append / validate / repair |
| `io/queue.py` | `QueueTask`, `QueueTaskStore` queue directory scan |
| `io/sessions.py` | Per-task session ID store |
| `llm/__init__.py` | One-shot LLM API |
| `llm/_config.py` | `llm-models.yml` loading (`ConfigLoadError`) |
| `llm/_constants.py` | `DEFAULT_ENGINE_MODELS` |
| `llm/capabilities.py` | Capabilities re-export and `LLMParseError` |
| `llm/compaction.py` | Resume-session handoff summary compaction |
| `llm/engines.py` | Engine/model allow-list, `call` / `call_text`, `supports_capability` (`EngineModelError`) |
| `llm/managed.py` | `call_managed`: failure classification, quota report, fallback |
| `llm/session.py` | `SessionRecord`, `SessionStore` |
| `llm/spec.py` | Shim for `EngineSpec` / `render_exec_command` |
| `llm/adapters/__init__.py` | `get_output_adapter` |
| `llm/adapters/claude_json.py` | claude JSON / stream-json output adapter |
| `llm/adapters/codex.py` | codex `--json` output adapter |
| `llm/adapters/codex_jsonl.py` | codex JSONL stream adapter |
| `llm/adapters/cursor.py` | cursor JSON / text output adapter |
| `llm/adapters/cursor_stream.py` | cursor stream-json adapter and `RetriableError:` classification |
| `llm/adapters/failure_classification.py` | Shared failure classification (`INTERACTIVE_PROMPT`, quota pause) |
| `markdown/__init__.py` | Package marker |
| `markdown/body_editor.py` | Deterministic H2 section editing of Issue bodies |
| `metrics/__init__.py` | `MetricsRecorder`, `TaskMetrics` |
| `metrics/models.py` | Shim for `TaskMetrics` |
| `metrics/parsers.py` | Engine/model detection and token count parsing |
| `metrics/recorder.py` | `MetricsRecorder` locked JSONL append |
| `pipeline/__init__.py` | Intake pipeline public API |
| `pipeline/audit.py` | Shim for `ghdag.io.audit` |
| `pipeline/audit_query.py` | Shim for `ghdag.io.audit_query` |
| `pipeline/config.py` | `PipelineConfig`, `resolve_models` (`ModelValidationError`) |
| `pipeline/hooks.py` | Shim for `AuditHooks` |
| `pipeline/llm_pipeline.py` | `LLMPipelineAPI`, `SubmittedStep` (`DependencyError`) |
| `pipeline/order.py` | Template order builders (`TemplateVariableError`) |
| `pipeline/result.py` | Shim for `QueueTask` / `QueueTaskStore` |
| `pipeline/state.py` | `PipelineState` |
| `pipeline/status.py` | Human-readable task status (`task_status`, `STATE_*`) |
| `pipeline/submit.py` | `make_order_record`, `submit_order` |
| `pipeline/wait.py` | `wait_for_result` done-marker polling |
| `tool/__init__.py` | Tool definition API |
| `tool/audit.py` | `write_tool_fallback_audit` |
| `tool/cli.py` | `ghdag tools list` handler |
| `tool/exceptions.py` | `ToolRegistryError` |
| `tool/registry.py` | `ToolRegistry` discovery |
| `tool/schema.py` | `ToolDef`, `FallbackEntry`, `TOOL_EXIT_CODES` |
| `ui/__init__.py` | Web UI package |
| `ui/dashboard.py` | `audit.jsonl` aggregation for the dashboard |
| `ui/monitor.py` | Build display rows from queue files |
| `ui/server.py` | HTTP + SSE server (`/api/rows`, `/api/stream`, `/api/config`, `/api/dashboard/*`, `/api/correlation-bursts`, `/api/retry`, `/api/stop`) |
| `workflow/__init__.py` | Workflow public API |
| `workflow/conditional_step.py` | Conditional LLM step helper (`python -m`) |
| `workflow/dispatcher.py` | `WorkflowDispatcher` polling and dispatch (`ContextHookError`) |
| `workflow/engine.py` | Shim for engine adapters |
| `workflow/loader.py` | Workflow YAML loading and validation (`ValidationError`) |
| `workflow/render.py` | `render: live` runtime template runner (`python -m`) |
| `workflow/schema.py` | Shim for workflow dataclasses |
| `workflow/state_machine.py` | Label transition state machine (`python -m`) |
| `workflow/typecheck.py` | Static skill I/O type check across steps (`TypeCheckError`) |
| `workflow/gates/__init__.py` | `GATE_REGISTRY`, `get_gate` |
| `workflow/gates/__main__.py` | `python -m ghdag.workflow.gates` |
| `workflow/gates/common.py` | `strip_code_regions` helper |
| `workflow/gates/loader.py` | `ghdag.gates` entry-point loading |

## Configuration

### Workflow YAML (`ghdag.core.models.workflow`)

`load_workflows(directory)` loads every workflow YAML in a directory and validates it (`ValidationError` on errors).

| Type | Field | Type | Required | Default | Meaning |
|---|---|---|---|---|---|
| `WorkflowConfig` | `name` | `str` | yes | — | Workflow name |
| | `triggers` | `list[TriggerConfig]` | yes | — | Label → handler map; definition order is precedence |
| | `handlers` | `dict[str, HandlerConfig]` | yes | — | Handler definitions |
| | `polling_interval` | `int` | no | `30` | Seconds between polls |
| | `template_dir` | `str \| None` | no | `None` (`<workflow dir>/templates`) | Order template directory; relative to the workflow file |
| | `label_namespace` | `str \| None` | no | `None` | Label prefix |
| | `transitions` | `dict[str, list[str]] \| None` | no | `None` | Allowed label transitions |
| | `reset_label` | `str \| None` | no | `None` | Label reachable from any state |
| | `roles` | `dict[str, list[str]]` | no | `{}` | Role name → engine list for quota admission |
| | `nonterminal_closed` | `NonterminalClosedConfig \| None` | no | `None` | Handling of CLOSED Issues without a terminal label |
| `NonterminalClosedConfig` | `action` | `str` | yes | — | `"reopen"` or `"trigger"` |
| | `terminal_labels` | `list[str]` | yes | — | CLOSED Issues with any of these labels are ignored |
| | `trigger` | `str \| None` | when `action: trigger` | `None` | Trigger label to fire |
| `TriggerConfig` | `label` | `str` | yes | — | Issue label to match |
| | `handler` | `str` | yes | — | Key in `handlers` |
| `HandlerConfig` | `steps` | `list[StepConfig]` | yes | — | Steps to enqueue |
| | `on_trigger` | `OnTriggerConfig \| None` | no | `None` | Trigger-time side effects |
| | `type` | `str \| None` | no | `None` | Special handler type (e.g. `"reset"`) |
| | `context_hook` | `str \| None` | no | `None` | Command that generates extra context |
| `OnTriggerConfig` | `issue_context` | `bool` | no | `False` | Write Issue body and comments to `design.md` |
| `StepConfig` | `template` | `str` | yes | — | Template name without `.md`; the file must exist |
| | `model` | `str` | yes | — | Model ID |
| | `id` | `str \| None` | no | `None` | Step ID for `depends` / `resume_from` |
| | `engine` | `str` | no | `"claude"` | Engine name |
| | `depends` | `list[str]` | no | `[]` | Upstream step IDs; templates referencing `${<id>_result_filename}` / `${<id>_result_content}` must list `<id>` here |
| | `resume_from` | `str \| None` | no | `None` | Step ID whose session to resume (same engine required) |
| | `permission` | `str \| None` | no | `None` | Capability preset name; `None` uses `GHDAG_SAFE_DEFAULT_PERMISSION` or `text_only` |
| | `skill_name` | `str \| None` | no | `None` | Skill invoked by the step (used by `workflow.typecheck`) |
| | `render` | `str` | no | `"frozen"` | `"frozen"` (expand at enqueue) or `"live"` (expand at run time) |
| | `role` | `str \| None` | no | `None` | `QuotaGate` role; must be declared in `roles` |

### exec.jsonl task fields

Parsed into `Task` (`ghdag.core.models.dag`). When a UUID appears on several lines, the last line wins.

| Field | Type | Required | Default | Meaning |
|---|---|---|---|---|
| `uuid` | `str` | yes | — | Task ID |
| `command` | `str` | yes | — | Shell command |
| `depends` | `list[str]` | no | `[]` | Upstream task UUIDs |
| `retry` | `int` | no | `0` | Retry depth |
| `annotations` | `dict[str, str]` | no | `{}` | Metadata (`timeout_sec`, `step_name`, `role`, `role_engines`, `stream_fallback`, …) |
| `result_path` | `str \| None` | no | `None` | Result file written from engine output |
| `idempotency_key` | `str \| None` | no | `None` | Deduplication key across enqueues |
| `engine` | `str \| None` | no | `None` | Engine name (output adapter, quota) |
| `model` | `str \| None` | no | `None` | Model ID |
| `result_finalize` | `str \| None` | no | `None` | `"preserve_nonempty"` or `"stdout_only"` |

`annotations.timeout_sec` overrides `DagConfig.task_timeout` for one task; invalid or non-positive values fall back to the default.

### `DagConfig`

| Field | Type | Default | Meaning |
|---|---|---|---|
| `exec_jsonl_path` | `str \| Path` | required | Queue file |
| `exec_done_dir` | `str \| Path` | `"jobs/done"` | Done-marker directory |
| `poll_interval` | `float` | `1.0` | Poll interval (seconds) |
| `launch_stagger` | `float` | `0.5` | Delay between launches (seconds) |
| `max_retry` | `int` | `1` | Engine-error retry budget |
| `lock_file` | `str \| Path \| None` | `<queue dir>/.ghdag.lock` | Single-runner lock |
| `timezone` | `str` | `"UTC"` | Timezone for audit / metrics timestamps |
| `cwd` | `str \| Path \| None` | `None` | Task working directory |
| `task_timeout` | `float \| None` | `None` | Default task timeout and SIGTERM drain window (seconds) |
| `kill_grace` | `float` | `10.0` | SIGTERM → SIGKILL grace (seconds) |
| `max_concurrency` | `int \| None` | `None` | Concurrent task cap |
| `serialize_mutating` | `bool` | `False` | Run mutating tasks one at a time |
| `max_consecutive_failures` | `int` | `5` | Circuit breaker threshold |
| `failure_window_sec` | `float` | `60.0` | Circuit breaker window (seconds) |
| `quota_state_path` | `str \| Path \| None` | `<queue dir>/quota-gate.json` | Quota state file |
| `quota_audit_path` | `str \| Path \| None` | `<queue dir>/audit.jsonl` | Quota audit file |
| `audit_path` | `Path \| None` | `<queue dir>/audit.jsonl` | Task audit file |

### `llm-models.yml`

Replaces the built-in engine → allowed-model list. `ghdag.llm._config.load_engine_models` resolves it in this order:

1. explicit path argument
2. `GHDAG_LLM_MODELS`, if set
3. `./llm-models.yml` in the current directory
4. built-in `DEFAULT_ENGINE_MODELS`

```yaml
engines:
  claude:
    - claude-sonnet-4-6
  codex:
    - gpt-5.5
  cursor:
    - auto
```

The top-level `engines` key is required and each value must be a list of strings (`ConfigLoadError` otherwise). Read the effective list with `get_engine_models()`.

### Quota state (`jobs/quota-gate.json`)

Managed by `QuotaGate` and `ghdag quota`. Top-level keys: `engines` (per engine: `status`, `observed_at`, `resume_at`, `reason`, `override_until`), `deferred_tasks`, `draining_engines`, `running_tasks`. An engine is unavailable while it is draining, reported `paused`, or marked `paused` in the optional brake file (`QuotaGate(brake_state_path=...)`, same `engines.<name>.status` shape). `ghdag quota status` adds per-engine `queued` / `deferred` / `running` / `idle` counts.

### Environment variables

Every variable ghdag reads (`os.environ` / `os.getenv` in `src/ghdag/`). Most reads go through `ghdag.config.env`.

| Variable | Default | Read by | Meaning |
|---|---|---|---|
| `GITHUB_TOKEN` | none | `config.env.github_token` | GitHub token; required for the GitHub forge (`AuthError` when neither token is set) |
| `GH_TOKEN` | none | `config.env.github_token` | Fallback when `GITHUB_TOKEN` is unset |
| `GITHUB_REPOSITORIES` | `""` | `config.env.github_repositories_raw` | Comma-separated `owner/repo` list; the first entry is the default repository, `ghdag watch` polls all |
| `GHDAG_AUDIT_PATH` | `jobs/audit.jsonl` (UI) / unset (`ghdag llm` writes no audit) | `config.env.ghdag_audit_path` | Audit log path for the UI dashboard and `ghdag llm` |
| `GHDAG_TOKEN_WARN_THRESHOLD` | `500000` | `config.env.ghdag_token_warn_threshold` | UI dashboard token usage warning threshold |
| `GHDAG_SAFE_DEFAULT_PERMISSION` | `text_only` | `config.env.ghdag_safe_default_permission` | Capability preset for workflow steps without `permission`; unknown names raise `ValueError` |
| `GHDAG_LLM_MODELS` | unset | `config.env.ghdag_llm_models` | Path to `llm-models.yml` |
| `GHDAG_SESSION_COMPACTION` | off | `config.env.session_compaction_enabled` | `1` / `true` / `yes` / `on` enables resume-session compaction |
| `LATENCY_SPAN_PATH` | `jobs/latency_span.jsonl` | `config.env.latency_span_path` | Output path of `ghdag.audit.emit_span` when `log_path` is not given |
| `GHDAG_ENGINE_ISOLATION` | unset (off) | `llm.engines`, `pipeline.llm_pipeline` | Any non-empty value enables engine isolation: claude gets `--disable-slash-commands`; codex `call()` uses `CODEX_HOME=/var/tmp/ghdag-dag-codex/` when `auth.json` exists there (otherwise a warning is printed and isolation is skipped). An explicit `isolation=` argument overrides it |
| `GHDAG_QUOTA_DEFAULT_PAUSE_SECONDS` | `18000` | `llm.adapters.failure_classification` (claude / codex adapters) | Pause length (seconds) used when an engine reports quota exhaustion without a reset time; read once at import |
| `GHDAG_FORGE` | `github` | `forge.get_forge`, `github_cli.get_forge` | Forge backend: `github` or `local` |
| `GHDAG_FORGE_ROOT` | none | `forge.get_forge` | Data root for `LocalForge`; required when `GHDAG_FORGE=local` |
| `GHDAG_EXEC_JSONL` | `jobs/exec.jsonl` | `cli.commands.status` | Default `--exec-jsonl` for `ghdag status` |
| `GHDAG_ETAG_CACHE` | unset (in-memory only) | `github_client.GitHubClient` | Path of the persistent ETag cache file |
| `GHDAG_RATE_LIMIT_MAX_WAIT_SEC` | `900` | `github_client.GitHubClient` | Maximum rate-limit sleep before raising `RateLimitError`; non-integer values fall back to `900` |

Variables ghdag sets for child processes (not configuration):

| Variable | Set for | Value |
|---|---|---|
| `GHDAG_TASK_UUID` | every DAG task | Task UUID (e.g. for `QuotaGate.defer`) |
| `GHDAG_RESULT_PATH` | every DAG task | Task `result_path` or `""` |
| `GHDAG_PREVIOUS_ATTEMPT` | DAG tasks relaunched after an interrupted run | `interrupted` |
| `CODEX_HOME` | codex `call()` with isolation enabled | `/var/tmp/ghdag-dag-codex/` |

## Error Reference

All exception types defined in `src/ghdag/`.

### `GhdagError` hierarchy

`except GhdagError` catches every type in this table.

| Exception | Parent(s) | Defined in | Raised when |
|---|---|---|---|
| `GhdagError` | `Exception` | `ghdag.core.exceptions` (also `ghdag`, `ghdag.exceptions`) | Base class |
| `GitHubApiError` | `GhdagError` | `ghdag.core.exceptions` | GitHub API failure; has `status_code` |
| `AuthError` | `GitHubApiError` | `ghdag.core.exceptions` | 401 or no token configured |
| `RateLimitError` | `GitHubApiError` | `ghdag.core.exceptions` | 403 with `X-RateLimit-Remaining: 0`; has `reset_at` (epoch seconds) |
| `PermissionDeniedError` | `GitHubApiError` | `ghdag.core.exceptions` | 403 / 404 on a private resource |
| `NetworkError` | `GitHubApiError` | `ghdag.core.exceptions` | Timeout, DNS, or connection failure |
| `ModelValidationError` | `GhdagError` | `ghdag.pipeline.config` | Invalid model configuration |
| `EngineModelError` | `GhdagError` | `ghdag.llm.engines` | Unknown engine or model not in the allow-list |
| `LLMParseError` | `GhdagError` | `ghdag.llm.capabilities` | Output violates `output_format`; has `raw`, `reason` |
| `ToolRegistryError` | `GhdagError` | `ghdag.tool.exceptions` | Tool definition naming violation or duplicate |
| `DependencyError` | `GhdagError`, `ValueError` | `ghdag.pipeline.llm_pipeline` | Invalid step dependencies |
| `ConfigLoadError` | `GhdagError`, `ValueError` | `ghdag.llm._config` | Invalid `llm-models.yml` |
| `FanoutError` | `GhdagError`, `ValueError` | `ghdag.dag.fanout` | Invalid fan-out specification |
| `AdapterNotFoundError` | `GhdagError`, `ValueError` | `ghdag.core.command` | No engine adapter for an engine name |
| `PathTraversalError` | `GhdagError`, `ValueError` | `ghdag.core.models.files` (also `ghdag.files`) | Markdown path escapes the repository root |
| `ContextHookError` | `GhdagError`, `ValueError` | `ghdag.workflow.dispatcher` | Handler `context_hook` failed |
| `AppendRecoverError` | `GhdagError`, `ValueError` | `ghdag.files.append` | `md_append` cannot recover a partial append |
| `ValidationError` | `GhdagError`, `ValueError` | `ghdag.workflow.loader` | Invalid workflow YAML |

### Outside the `GhdagError` hierarchy

| Exception | Parent(s) | Defined in | Raised when |
|---|---|---|---|
| `RecoverError` | `Exception` | `ghdag.dag.recover` | Recover plan or execution is not possible |
| `TemplateVariableError` | `ValueError`, `KeyError` | `ghdag.pipeline.order` | A template variable is undefined |

### Related types that are not exceptions

| Type | Defined in | Meaning |
|---|---|---|
| `EngineErrorKind` | `ghdag.core.ports.output` | Enum of engine error kinds (`RATE_LIMIT`, `CAPACITY`, …) |
| `EngineError` | `ghdag.core.ports.output` | Dataclass returned by `EngineOutputAdapter.extract_error` (`kind`, `message`, `retryable`, `resume_at`) |
| `FailureClass` | `ghdag.core.models.metrics` | Enum of task failure classes (e.g. `INTERACTIVE_PROMPT`, `PROCESS_ERROR`) |
| `TypeCheckError` | `ghdag.workflow.typecheck` | Dataclass describing a skill I/O mismatch |

### Done markers

Contents of `jobs/done/<uuid>` (`ghdag.core.vocabulary`):

| Constant | Value | Meaning |
|---|---|---|
| `DONE_SUCCESS` | `0` | Success |
| `DONE_REJECTED` / `DONE_REJECTED_FINAL` | `REJECTED` / `REJECTED_FINAL` | Result rejected by `check_rejected` (retryable / final) |
| `DONE_ENGINE_ERROR` / `DONE_ENGINE_ERROR_FINAL` | `ENGINE_ERROR` / `ENGINE_ERROR_FINAL` | Retryable engine error (retries remain / exhausted) |
| `DONE_ENGINE_ENV_ERROR` | `ENGINE_ENVIRONMENT_ERROR` | Engine environment problem |
| `DONE_INTERACTIVE_PROMPT` | `INTERACTIVE_PROMPT` | Engine stopped to ask a question |
| `DONE_TIMEOUT` | `TIMEOUT` | Task timeout |
| `DONE_CANCELLED` | `CANCELLED` | Cancelled via `jobs/cancel/<uuid>` |
| `DONE_DEP_FAILED` | `DEP_FAILED` | An upstream task failed |
| `DONE_EMPTY_RESULT` | `EMPTY_RESULT` | Engine produced no result text |
| `DONE_PIPELINE_FAILED_PREFIX` | `PIPELINE_FAILED:` | Prefix for `PIPELINE_STATUS` failure markers |
| `DONE_FANOUT_CHILD_FAILED` / `DONE_FANOUT_PARSE_FAILED` | `FANOUT_CHILD_FAILED` / `FANOUT_PARSE_FAILED` | Fan-out failures |
| `DONE_SKIPPED_MISSING_INPUT` | `SKIPPED_MISSING_INPUT` | Required input missing |
| `DONE_UNKNOWN_FAILURE` | `UNKNOWN_FAILURE` | Unclassified failure |
| `DONE_ORPHAN_ARCHIVED` | `ORPHAN_ARCHIVED` | Archived by `ghdag cleanup` as an orphan |
| `DONE_ORPHANED_ON_RESTART` | `ORPHANED_ON_RESTART` | Task lost across a runner restart (recoverable with `ghdag dag recover`) |
| `DONE_DEFERRED` | `DEFERRED` | Deferred by the quota gate; relaunched on release |

Any other content is the task's non-zero exit code.

## Not in Scope

- Not a hosted CI service: ghdag runs where you start it and does not use GitHub Actions runners.
- Not an LLM SDK: engines are invoked through their CLIs (`claude`, `agent`, `codex`, `gemini`); ghdag does not call model HTTP APIs directly.
- Not a distributed scheduler: one runner per queue directory, coordinated through local files and `fcntl` locks.
- No host-specific agents, personas, or chat integrations; hosts compose ghdag's intake and DAG primitives.

## API Stability

ghdag is pre-1.0 (`0.Y.Z`). A minor version bump may change or remove public symbols, CLI options, and file formats. Pin an exact tag (`@v0.80.0`) in production and read `CHANGELOG.md` before upgrading.

## License

MIT License (SPDX: `MIT`), matching `pyproject.toml` `license = "MIT"`. See [`LICENSE`](LICENSE).
