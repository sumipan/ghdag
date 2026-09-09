# ghdag

ghdag turns GitHub Issues into a local workflow/pipeline intake tower and a separate dependency-aware DAG runner. Intake writes task records to `exec.jsonl`; the runner consumes those records and writes completion markers under `jobs/done/`. Unlike CI-centric orchestrators (GitHub Actions, Dagger), the two towers stay separate processes and exchange only queue files and done markers.

## Status

![stability](https://img.shields.io/badge/stability-pre--1.0-orange)
![version](https://img.shields.io/badge/version-v0.43.0-blue)
![ci](https://github.com/sumipan/ghdag/actions/workflows/test.yml/badge.svg?branch=main)
![python](https://img.shields.io/badge/python-%3E%3D3.10-blue)
![license](https://img.shields.io/badge/license-MIT-green)

Current release is **v0.43.0** (`0.Y.Z`, pre-1.0). Public interfaces may change before `1.0.0`. Requires **Python >= 3.10**. License is **MIT** (SPDX: `MIT`).

## Installation

```bash
pip install ghdag
```

Pin a release tag:

```bash
pip install git+https://github.com/sumipan/ghdag.git@v0.43.0
```

| Item | Value |
|---|---|
| Python requirement | `>=3.10` (`pyproject.toml` `requires-python`) |
| Runtime dependencies | `watchdog>=4.0.0`, `pyyaml>=6.0`, `requests>=2.28.0` |
| Dev extras | `pip install "ghdag[dev]"` (`pytest`, `pytest-cov`, `mypy`, `ruff`, `import-linter`, `types-PyYAML`) |

## Quick Start

Minimal queue (shape from `tests/test_ghdag_engine.py`):

```jsonl
{"uuid":"demo-0001","command":"echo hello from ghdag","depends":[]}
```

```bash
mkdir -p jobs
printf '%s\n' '{"uuid":"demo-0001","command":"echo hello from ghdag","depends":[]}' > jobs/exec.jsonl
ghdag run jobs/exec.jsonl
# success writes jobs/done/demo-0001 containing exit code 0
```

Minimal workflow YAML (schema exercised in `tests/test_ghdag_workflow.py`):

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

Place the YAML under `workflows/` with a matching `workflows/templates/impl.md`, then poll once:

```bash
export GITHUB_TOKEN="<token>"           # or GH_TOKEN
export GITHUB_REPOSITORIES="owner/repo"
ghdag watch workflows --exec-md jobs/exec.jsonl --once
```

Secrets stay in the environment — never commit real tokens. `--once` starts without argparse/schema errors when the YAML and env placeholders are valid.

Related one-shots:

```bash
ghdag trigger 123 --handler impl --redispatch --reason "manual retry"
ghdag dag recover --issue 123 --handler impl --dry-run
ghdag dag cancel <uuid>                 # only when jobs/running/<uuid>.json exists
```

## CLI Reference

Global options: `--verbose` / `-v` (DEBUG), `--quiet` / `-q` (WARNING+). Entry point: `ghdag.cli.main::_build_parser()`.

### Top-level commands (11)

| Command | Description |
|---|---|
| `ghdag run` | Run `exec.jsonl` via `DagEngine` |
| `ghdag watch` | Poll GitHub issues and dispatch workflow handlers |
| `ghdag ui` | Launch the Web UI dashboard |
| `ghdag llm` | One-shot LLM call without workflow dispatch |
| `ghdag version` | Print installed package version |
| `ghdag cleanup` | Archive completed/orphaned queue tasks |
| `ghdag trigger` | Trigger one workflow handler for one issue |
| `ghdag dag` | DAG utilities (`recover`, `cancel`) |
| `ghdag audit-query` | Query `audit.jsonl` or detect correlation bursts |
| `ghdag tools` | Tool definition management |
| `ghdag quota` | Manage quota gate state |

### Subcommands

| Command | Description |
|---|---|
| `ghdag dag recover` | Reset failed/pending steps for re-execution |
| `ghdag dag cancel` | Request cancel via `jobs/cancel/<uuid>` when running |
| `ghdag tools list` | List tool definitions (`--path` required, `--json`) |
| `ghdag quota report` | Report engine quota availability |
| `ghdag quota clear` | Clear engine quota pause state |
| `ghdag quota drain` | Pause new launches for one engine |
| `ghdag quota resume` | Release drain mode for one engine |
| `ghdag quota status` | Print engine-level quota/drain/queue snapshot JSON |

### Arguments and defaults

| Command | Arguments / options (defaults) |
|---|---|
| `run` | `exec_jsonl`; `--interval` `1.0`; `--hooks` none; `--max-concurrency` unlimited |
| `watch` | `workflows_dir`; `--interval` `30`; `--exec-md` `jobs/exec.jsonl`; `--once`; `--pause-file` disabled |
| `ui` | `--repo-root` `.`; `--host` `127.0.0.1`; `--port` `8080`; `--interval` `3.0`; `--max-visible` `30` |
| `llm` | `[prompt]`; `--engine`/`-e` `claude`; `--model`/`-m` engine default; `--timeout` none; `--dangerously-skip-permissions`; `--permission-mode` `{default,plan,bypassPermissions}`; `--capabilities-preset` `{text_only,json_only,web_research,dangerous_full_access}`; `--stdin`; `--list-engines`; `--list-models`; `--audit-path` (`GHDAG_AUDIT_PATH`); `--correlation-id`; `--request-id` |
| `cleanup` | `repo_root`; `--dry-run`; `--cutoff-days` `1`; `--orphan-days` `7`; `--auto-repair` |
| `trigger` | `issue_number`; `--handler` (required); `--workflows-dir` `workflows`; `--exec-md` `jobs/exec.jsonl`; `--workflow` auto; `--redispatch`; `--reason` |
| `dag recover` | `--issue` (required); `--handler` (required); `--from` STEP; `--dry-run`; `--workflows-dir` `workflows`; `--exec-md` `jobs/exec.jsonl`; `--workflow` auto; `--state-dir` `.pipeline-state` |
| `dag cancel` | `uuid`; `--queue-dir` `jobs` |
| `audit-query` | `--correlation-id`; `--burst-detect`; `--since`; `--audit-path` `jobs/audit.jsonl`; `--window-sec` `600`; `--threshold` `10` |
| `tools list` | `--path` (required); `--json` |
| `quota report` | `engine`; `--status` `{available,paused}` (required); `--observed-at` (required); `--resume-at`; `--reason`; `--state-path` `jobs/quota-gate.json` |
| `quota clear` | `engine`; `--observed-at` (required); `--state-path` `jobs/quota-gate.json` |
| `quota drain` | `engine`; `--reason`; `--state-path` `jobs/quota-gate.json` |
| `quota resume` | `engine`; `--state-path` `jobs/quota-gate.json` |
| `quota status` | `--state-path` `jobs/quota-gate.json`; `--exec-path` `jobs/exec.jsonl`; `--done-dir` `jobs/done` |

### Cancel behavior

`ghdag dag cancel <uuid>` creates `jobs/cancel/<uuid>` only when `jobs/running/<uuid>.json` exists. If the UUID is not running, the command exits with code `1`, prints `error: not running: …` on stderr, and does **not** create a cancel marker. The CLI never signals the process itself; `DagEngine` polls cancel markers and the launcher writes `DONE_CANCELLED` plus `on_task_cancelled`. UI `/api/stop` uses the same control-file path.

### Progress streaming (claude / cursor / codex)

While a DAG task with `result_path` runs for `claude`, `cursor`, or `codex`, stdout is drained line-by-line into `jobs/events/<uuid>.jsonl` when stream flags are present (`claude`: stream-json path; `cursor`: `-p` + `--output-format stream-json`; `codex`: `--json`). UI SSE snapshots may include `progress` (`tool`, `path`, `assistant_text`). Missing stream flags for a stream-capable engine fall back to bulk stdout reads and set `annotations.stream_fallback=true`. gemini / shell do not create events files. Session IDs and token usage are extracted by engine adapters (cursor: `session_id` / legacy `chat_id`; codex: `thread.started.thread_id` / legacy `session_id`; usage from each adapter’s JSON shape).

## Public API

Import from the public package paths below. Do not treat private modules (`_*`) or internal shims as the recommended surface.

### Top-level (`ghdag.__all__`)

| Symbol | Import | Role |
|---|---|---|
| `GhdagError` | `from ghdag import GhdagError` | Base exception (re-export of `ghdag.core.exceptions`) |
| `QueueTask` | `from ghdag import QueueTask` | Queue task dataclass |
| `QueueTaskStore` | `from ghdag import QueueTaskStore` | Queue task store |
| `LLMPipelineAPI` | `from ghdag import LLMPipelineAPI` | Intake API: submit steps → `exec.jsonl` |
| `PipelineState` | `from ghdag import PipelineState` | Pipeline state / order / exec append |
| `DagEngine` | `from ghdag import DagEngine` | Local DAG runner |
| `WorkflowDispatcher` | `from ghdag import WorkflowDispatcher` | Label-driven GitHub dispatch |
| `QuotaGate` | `from ghdag import QuotaGate` | Engine quota / drain admission |

Also: `ghdag.__version__` (installed distribution version string).

### Core callables (signatures from source)

| Callable | Signature (abridged) | Returns |
|---|---|---|
| `DagEngine` | `(config: DagConfig, hooks: DagHooks \| None = None)` | engine instance; `.run() -> None`, `.append_task(line, audit_context=None)`, `.mark_done(uuid, status)` |
| `WorkflowDispatcher` | `(workflows, github_client, pipeline, queue_dir="queue", pause_file=None)` | dispatcher; `.poll_once() -> list[dict]`, `.run(max_iterations=None)`, `.dispatch(...) -> DispatchResult` |
| `LLMPipelineAPI` | `(pipeline_state, order_builder, queue_dir="queue", *, order_builders=None)` | API; `.submit(steps, base_context, *, idempotency_key=None, audit_context, metadata=None) -> list[str]` (exec lines) |
| `QuotaGate` | `(state_path, audit_path=None)` | gate; `.report(...) -> QuotaReportResult`, `.admit(...) -> AdmissionDecision`, `.drain` / `.resume` / `.snapshot` / `.wait_idle` |
| `call` | `(prompt, *, engine="claude", model=None, timeout=None, …) -> LLMResult` | structured LLM result + optional `session_id` |
| `call_text` | `(prompt, *, …) -> TextResult` | text-oriented result |
| `call_managed` | `(prompt, *, fallback_candidates=(), quota_gate=None, …) -> ManagedResult` | managed call with quota/fallback |
| `build_llm_cmd` | `(engine, model, prompt, *, capabilities=…, …) -> list[str]` | argv for an engine CLI |
| `md_read` / `md_write` / `md_append` / `md_promote` | see `ghdag.files` | markdown file ops under repo root |
| `load_workflows` | `(directory: str \| Path) -> list[WorkflowConfig]` | load YAML workflows |
| `plan_recover` / `execute_recover` | see `ghdag.dag.recover` | recover plan / apply done-marker resets |
| `cleanup_queue` | `(queue_dir, archive_dir, done_dir, exec_md, …) -> CleanupResult` | archive/prune queue |

### Package `__all__` surfaces (recommended imports)

| Package | Notable public symbols |
|---|---|
| `ghdag.dag` | `DagConfig`, `DagEngine`, `DagHooks`, `DefaultHooks`, `RunningTask`, `Task`, `check_pipeline_status`, `extract_tee_target`, `parse_jsonl` |
| `ghdag.workflow` | `WorkflowConfig`, `TriggerConfig`, `HandlerConfig`, `StepConfig`, `OnTriggerConfig`, `DispatchResult`, `load_workflows`, `WorkflowDispatcher`, `GitHubIssueClient`, `create_github_client` |
| `ghdag.pipeline` | `AuditHooks`, `ModelValidationError`, `PipelineConfig`, `PipelineState`, `OrderBuilder`, `TemplateOrderBuilder`, `InlineOrderBuilder`, `resolve_models`, `build_agent_cmd`, `status_rank`, `parse_frontmatter`, `LLMPipelineAPI`, `SubmittedStep`, `task_status`, `wait_for_result`, `read_task_exit_events`, `get_latest_status`, `STATE_EMPTY`, `STATE_DEFERRED`, `STATE_ENGINE_ERROR`, `STATE_FAIL`, `STATE_OK`, `STATE_PENDING_DEPS`, `STATE_PENDING_RUN`, `STATE_REJECTED`, `STATE_RUNNING`, `STATE_UNKNOWN_DONE`, `make_order_record`, `submit_order` |
| `ghdag.llm` | `_config`, `DEFAULT_ENGINE_MODELS`, `ENGINE_DEFAULTS`, `ENGINE_SPECS`, `EngineModelError`, `EngineSpec`, `InputMode`, `PromptFlag`, `LLMCapabilities`, `LLMParseError`, `LLMResult`, `ManagedResult`, `TextResult`, `SessionRecord`, `SessionStore`, `TEXT_ONLY`, `JSON_ONLY`, `WEB_RESEARCH`, `DANGEROUS_FULL_ACCESS`, `build_llm_cmd`, `call`, `call_managed`, `call_text`, `get_engine_models`, `list_engines`, `list_models`, `validate_engine_model` |
| `ghdag.files` | `AppendResult`, `AppendStatus`, `MdFile`, `PathTraversalError`, `PromoteResult`, `PromoteStatus`, `WriteResult`, `md_append`, `md_promote`, `md_read`, `md_write` |
| `ghdag.io` | submodules `audit`, `audit_query`, `done`, `exec_jsonl`, `queue`, `sessions` |
| `ghdag.github_cli` | `GitHubClient`, `DEFAULT_REPO`, `API_BASE`, `GRAPHQL_URL` |
| `ghdag.exceptions` | `GhdagError`, `GitHubApiError`, `AuthError`, `RateLimitError`, `PermissionDeniedError`, `NetworkError` |
| `ghdag.workflow.gates` | `Violation`, `GateRule`, `GATE_REGISTRY`, `get_gate` |
| `ghdag.metrics` | `MetricsRecorder`, `TaskMetrics` |
| `ghdag.tool` | `ToolDef`, `ToolRegistry`, `FallbackEntry`, `TOOL_EXIT_CODES`, `write_tool_fallback_audit` |
| `ghdag.cleanup` | `cleanup_queue`, `CleanupResult`, `file_timestamp`, `QUEUE_FILE_RE` |

## Architecture

### Towers and contracts

```
GitHub Issues / labels
        │
        ▼
 workflow/ + pipeline/     ──writes──►  exec.jsonl  (task records)
        │                                    │
        │                                    ▼
        │                              dag/ DagEngine
        │                                    │
        │                                    ▼
        └──────────────reads──────────  jobs/done/<uuid>
```

| Boundary | Producer | Consumer | Contract |
|---|---|---|---|
| `exec.jsonl` task record | `workflow` / `pipeline` (`LLMPipelineAPI.submit`, `submit_order`) | `dag` (`parse_jsonl` → `Task`) | JSONL object with at least `uuid`, `command`; optional `depends`, `retry`, `annotations`, `result_path`, `idempotency_key`, `engine`, `model`, `result_finalize` |
| `jobs/done/<uuid>` | `DagEngine` / launcher | dispatcher wait helpers, recover, UI | file whose contents encode exit/status (`0`, non-zero, `DONE_CANCELLED`, engine-error markers, …) |
| `jobs/running/<uuid>.json` | launcher on start | `ghdag dag cancel`, UI stop | presence = running; removed on exit |
| `jobs/cancel/<uuid>` | CLI / UI | `DagEngine` cancel poll | empty marker file requesting cancel |
| `jobs/events/<uuid>.jsonl` | launcher stream drain | UI SSE `progress` | line-oriented engine stream events |
| `jobs/quota-gate.json` | `QuotaGate` / `ghdag quota` | launch admission | engines / deferred / draining / running |

### Module inventory (`src/ghdag/**/*.py`)

Every Python module under `src/ghdag/` (134 files), grouped by package. Import-linter contracts keep infrastructure (`files`, `llm`, …) from importing orchestration.

**Package root:** `__init__.py`, `__main__.py`, `exceptions.py`, `github_cli.py`, `github_client.py`, `maintenance.py`, `quota.py`

**`cleanup/`:** `__init__.py`, `archiver.py`, `link_rewriter.py`, `orchestrator.py`, `orphan_detector.py`, `pruner.py`

**`cli/`:** `__init__.py`, `main.py`; `commands/__init__.py`, `commands/audit_query.py`, `commands/cancel.py`, `commands/cleanup.py`, `commands/llm.py`, `commands/quota.py`, `commands/recover.py`, `commands/run.py`, `commands/trigger.py`, `commands/ui.py`, `commands/watch.py`

**`core/`:** `__init__.py`, `capabilities.py`, `command.py`, `engine_spec.py`, `exceptions.py`, `parsers.py`, `vocabulary.py`; `models/__init__.py`, `models/dag.py`, `models/files.py`, `models/metrics.py`, `models/workflow.py`; `ports/__init__.py`, `ports/dag_hooks.py`, `ports/gate.py`, `ports/github.py`, `ports/order.py`, `ports/output.py`

**`dag/`:** `__init__.py`, `_util.py`, `audit_hooks.py`, `circuit_breaker.py`, `engine.py`, `engine_quarantine.py`, `fanout.py`, `fanout_manager.py`, `hooks.py`, `models.py`, `parser.py`, `recover.py`, `state.py`, `task_launcher.py`, `watcher.py`

**`files/`:** `__init__.py`, `_rotate.py`, `append.py`, `models.py`, `promote.py`, `reader.py`, `writer.py`; `links/__init__.py`, `links/obsidian.py`

**`io/`:** `__init__.py`, `_rotate.py`, `audit.py`, `audit_query.py`, `done.py`, `exec_jsonl.py`, `queue.py`, `sessions.py`

**`llm/`:** `__init__.py`, `_config.py`, `_constants.py`, `capabilities.py`, `compaction.py`, `engines.py`, `managed.py`, `session.py`, `spec.py`; `adapters/__init__.py`, `adapters/claude_json.py`, `adapters/claude_text.py`, `adapters/codex.py`, `adapters/codex_jsonl.py`, `adapters/cursor.py`, `adapters/cursor_stream.py`, `adapters/failure_classification.py`

**`markdown/`:** `__init__.py`, `body_editor.py`

**`metrics/`:** `__init__.py`, `models.py`, `parsers.py`, `recorder.py`

**`pipeline/`:** `__init__.py`, `audit.py`, `audit_query.py`, `config.py`, `hooks.py`, `llm_pipeline.py`, `order.py`, `result.py`, `state.py`, `status.py`, `submit.py`, `wait.py`

**`tool/`:** `__init__.py`, `audit.py`, `cli.py`, `exceptions.py`, `registry.py`, `schema.py`

**`ui/`:** `__init__.py`, `dashboard.py`, `monitor.py`, `server.py`

**`workflow/`:** `__init__.py`, `conditional_step.py`, `dispatcher.py`, `engine.py`, `loader.py`, `render.py`, `schema.py`, `state_machine.py`, `typecheck.py`; `gates/__init__.py`, `gates/__main__.py`, `gates/common.py`, `gates/loader.py`

Package data: `py.typed`, `ui/static/*`.

### Workflow gates

`ghdag.workflow.gates.get_gate` resolves rules from `GATE_REGISTRY` first, then `importlib.metadata` entry-points group `ghdag.gates`. CLI: `python -m ghdag.workflow.gates --gate NAME --body-file PATH`.

### Label transitions

`python -m ghdag.workflow.state_machine transition --workflow <YAML> <issue_number> <target_label>` validates `transitions` / `reset_label` then updates issue labels via `GitHubClient`.

## Configuration

### Workflow YAML → dataclasses (`ghdag.core.models.workflow`)

| Type | Field | Type | Required | Default | Meaning |
|---|---|---|---|---|---|
| `WorkflowConfig` | `name` | `str` | yes | — | Workflow name |
| | `triggers` | `list[TriggerConfig]` | yes | — | Ordered label → handler map |
| | `handlers` | `dict[str, HandlerConfig]` | yes | — | Handler definitions |
| | `polling_interval` | `int` | no | `30` | Seconds between polls |
| | `template_dir` | `str \| None` | no | `None` (resolved vs workflow dir / `templates`) | Order template directory |
| | `label_namespace` | `str \| None` | no | `None` | Label prefix (state machine / dispatch) |
| | `transitions` | `dict[str, list[str]] \| None` | no | `None` | Allowed phase label graph |
| | `reset_label` | `str \| None` | no | `None` | Label allowed from any phase |
| | `roles` | `dict[str, list[str]]` | no | `{}` | Role name → engine list (quota admission) |
| | `nonterminal_closed` | `NonterminalClosedConfig \| None` | no | `None` | CLOSED non-terminal issue handling |
| `NonterminalClosedConfig` | `action` | `str` | yes | — | `"reopen"` or `"trigger"` |
| | `terminal_labels` | `list[str]` | yes | — | CLOSED issues with any of these are ignored |
| | `trigger` | `str \| None` | if `action=trigger` | `None` | Handler trigger label to fire |
| `TriggerConfig` | `label` | `str` | yes | — | Matching GitHub label |
| | `handler` | `str` | yes | — | Handler key |
| `HandlerConfig` | `steps` | `list[StepConfig]` | yes | — | Ordered steps (`type: reset` uses `[]`) |
| | `on_trigger` | `OnTriggerConfig \| None` | no | `None` | Trigger-time side effects |
| | `type` | `str \| None` | no | `None` | e.g. `"reset"` |
| | `context_hook` | `str \| None` | no | `None` | Custom context command |
| `OnTriggerConfig` | `issue_context` | `bool` | no | `False` | Write issue body/comments to design.md |
| `StepConfig` | `template` | `str` | yes | — | Template basename (no `.md`) |
| | `model` | `str` | yes | — | Model id |
| | `id` | `str \| None` | no | `None` | Step id for `depends` / resume |
| | `engine` | `str` | no | `"claude"` | LLM engine |
| | `depends` | `list[str]` | no | `[]` | Upstream step ids |
| | `resume_from` | `str \| None` | no | `None` | Parent step id for session resume |
| | `permission` | `str \| None` | no | `None` | Capabilities preset name |
| | `skill_name` | `str \| None` | no | `None` | Declared skill name |
| | `render` | `str` | no | `"frozen"` | `"frozen"` or `"live"` (trampoline re-render) |
| | `role` | `str \| None` | no | `None` | QuotaGate role name |

When `nonterminal_closed` is set, `WorkflowDispatcher.poll_once` also scans CLOSED issues that lack any `terminal_labels` entry and either reopens them or triggers the configured handler label.

### `exec.jsonl` task fields (`Task` / `ghdag.io.exec_jsonl.parse`)

| Field | Type | Required | Default | Meaning |
|---|---|---|---|---|
| `uuid` | `str` | yes | — | Task id (last line wins on duplicates) |
| `command` | `str` | yes | — | Shell command to run |
| `depends` | `list[str]` | no | `[]` | Upstream UUIDs |
| `retry` | `int` | no | `0` | Per-task retry hint |
| `annotations` | `dict[str, str]` | no | `{}` | Metadata (`timeout_sec`, `role`, `stream_fallback`, step names, …) |
| `result_path` | `str \| None` | no | `None` | Result file path |
| `idempotency_key` | `str \| None` | no | `None` | Dedup key across enqueue |
| `engine` | `str \| None` | no | `None` | Engine name for adapters/quota |
| `model` | `str \| None` | no | `None` | Model id |
| `result_finalize` | `str \| None` | no | `None` | `"preserve_nonempty"` \| `"stdout_only"` |

Per-task timeout override: set `annotations.timeout_sec` to a positive number (string or numeric). Invalid / non-positive values fall back to `DagConfig.task_timeout`.

### `DagConfig`

| Field | Type | Default | Meaning |
|---|---|---|---|
| `exec_jsonl_path` | `str \| Path` | (required) | Queue file |
| `exec_done_dir` | `str \| Path` | `jobs/done` | Done marker directory |
| `poll_interval` | `float` | `1.0` | Poll seconds |
| `launch_stagger` | `float` | `0.5` | Stagger between launches |
| `max_retry` | `int` | `1` | Engine retry budget |
| `lock_file` | `str \| Path \| None` | `<queue>/.ghdag.lock` | Process lock |
| `timezone` | `str` | `UTC` | Timestamp timezone name |
| `cwd` | `str \| Path \| None` | `None` | Subprocess cwd |
| `task_timeout` | `float \| None` | `None` | Default timeout seconds |
| `kill_grace` | `float` | `10.0` | SIGTERM→SIGKILL grace |
| `max_concurrency` | `int \| None` | `None` | Parallel task cap |
| `serialize_mutating` | `bool` | `False` | Serialize mutating tasks |
| `max_consecutive_failures` | `int` | `5` | Circuit breaker threshold |
| `failure_window_sec` | `float` | `60.0` | Circuit breaker window |
| `quota_state_path` | `str \| Path \| None` | `<queue>/quota-gate.json` | Quota state file |
| `quota_audit_path` | `str \| Path \| None` | `<queue>/audit.jsonl` | Quota audit file |

### `llm-models.yml`

Optional YAML loaded by `ghdag.llm._config.load_engine_models`:

1. explicit path argument
2. `GHDAG_LLM_MODELS` if set and file exists
3. `./llm-models.yml` in cwd
4. built-in `DEFAULT_ENGINE_MODELS`

```yaml
engines:
  claude:
    - claude-sonnet-4-6
  codex:
    - gpt-5
  cursor:
    - auto
```

Top-level key `engines` is required; each value is `list[str]`.

### Quota state (`jobs/quota-gate.json`)

Managed by `QuotaGate`. Snapshot includes `engines` (`status`, `observed_at`, `resume_at`, `reason`, `override_until`), `deferred_tasks` (may include `role` / `role_engines`), `draining_engines`, and `running_tasks`. `ghdag quota status` also reports per-engine `queued` / `deferred` / `running` / `idle`.

### Environment variables (8)

| Variable | Required | Default | Meaning |
|---|---|---|---|
| `GITHUB_TOKEN` | one of token vars for GitHub API | none | Primary auth token (`AuthError` if neither token set) |
| `GH_TOKEN` | fallback token | none | Used if `GITHUB_TOKEN` unset |
| `GITHUB_REPOSITORIES` | for multi-repo / default repo resolution | none | Comma-separated `owner/repo` list |
| `GHDAG_AUDIT_PATH` | no | `jobs/audit.jsonl` | Audit log path (UI dashboard, `ghdag llm`) |
| `GHDAG_TOKEN_WARN_THRESHOLD` | no | `500000` | UI token usage warning threshold |
| `GHDAG_LLM_MODELS` | no | cwd `llm-models.yml` then built-ins | Engine→model whitelist YAML path |
| `GHDAG_SAFE_DEFAULT_PERMISSION` | no | `text_only` | Safe default capabilities preset for pipeline steps |
| `GHDAG_SESSION_COMPACTION` | no (opt-in) | off | Enable session compaction when `1`/`true`/`yes`/`on` |

## Error Reference

### `GhdagError` hierarchy

```
GhdagError (core/exceptions.py)
├── GitHubApiError
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
├── PathTraversalError (core/models/files.py; also files/models.py re-export) *
└── ToolRegistryError (tool/exceptions.py)
```

`*` also subclasses `ValueError` (catchable with `except ValueError`).

### Outside the hierarchy

| Type | Module | Notes |
|---|---|---|
| `RecoverError` | `dag/recover.py` | Recover plan/execution failure (`Exception`, not `GhdagError`) |
| `TemplateVariableError` | `pipeline/order.py` | Missing template variable (`ValueError` + `KeyError`) |

### Not an exception

| Type | Module | Notes |
|---|---|---|
| `TypeCheckError` | `workflow/typecheck.py` | Dataclass describing skill I/O mismatch (not `BaseException`) |
| `EngineError` | `core/ports/output.py` | Dataclass for adapter-extracted engine failures (`kind`, `message`, `retryable`, `resume_at`) |

`ghdag.exceptions` re-exports the GitHub/`GhdagError` core set for compatibility.

## Not / API Stability / Deprecated API

**Not in scope:** host-specific personas, Slack bots, secretary agents, or diary-repo workflows. ghdag provides the generic intake + DAG primitives those hosts compose.

**Stability:** pre-1.0 (`0.Y.Z`). Minor versions may change public surfaces; pin `@v0.43.0` for production hosts.

**Deprecated / removed in v0.43.0:** none in v0.43.0. Do not reinstate older private shims or deleted CLI flags from prior majors as recommended API.

## License

MIT License (SPDX: `MIT`), matching `pyproject.toml` `license = "MIT"`.
