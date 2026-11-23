# ghdag

ghdag is a local workflow foundation for agent work. It does three things, and only these three:

- **Intake** — turn a request into queued tasks. `ghdag watch` polls labelled Issues (GitHub, or a file-backed `LocalForge`), `ghdag trigger` dispatches one Issue, and the MCP tool `ghdag_enqueue` accepts tasks from an agent. All of them append lines to `exec.jsonl`.
- **Execution** — `ghdag run` reads `exec.jsonl` and starts each task on your machine once its dependencies have succeeded. Tasks are local engine CLIs (`claude`, `cursor-agent`, `codex`, `gemini`) or `bash`.
- **Recording** — every task leaves a `done/<uuid>` marker, lifecycle events in `audit.jsonl`, and a result file, so a run can be inspected and recovered after the fact.

Intake and execution are separate processes that share nothing but files on disk. There is no server, no hosted runner and no database: coordination is the queue file, a few runtime directories and `fcntl` locks.

## Status

![stability](https://img.shields.io/badge/stability-pre--1.0-orange)
![version](https://img.shields.io/badge/version-v0.153.0-blue)
![python](https://img.shields.io/badge/python-%3E%3D3.10-blue)
![license](https://img.shields.io/badge/license-MIT-green)

| Item | Value |
|---|---|
| Current release | **v0.153.0** (`pyproject.toml` `version = "0.153.0"`) |
| Stability | pre-1.0 (`0.Y.Z`): public symbols, CLI options and file formats may change in any minor release |
| Python | `>=3.10` (classifiers: 3.10, 3.11, 3.12, 3.13) |
| Platform | POSIX (`fcntl` locks, process groups) |
| License | MIT (SPDX: `MIT`) |

[llms.txt](./llms.txt) is a short overview for LLM readers. [TESTING.md](./TESTING.md) describes what the tests pin. [CHANGELOG.md](./CHANGELOG.md) lists every release.

## Installation

Prerequisites:

- Python 3.10 or newer on a POSIX system.
- For LLM steps, the engine CLI on `PATH`: `claude`, `cursor-agent`, `codex` or `gemini`. Shell steps need only `bash`.
- `git` for `ghdag.vcs` sinks and for `LocalForge`. `docker` only when a permission uses `sandbox="container"`.

Install a release tag from GitHub:

```bash
pip install "git+https://github.com/sumipan/ghdag.git@v0.153.0"
ghdag version
```

Install from a checkout with the development tools:

```bash
git clone https://github.com/sumipan/ghdag.git
cd ghdag
pip install -e ".[dev]"
```

| Item | Value |
|---|---|
| Console script | `ghdag` -> `ghdag.cli:main` (also `python -m ghdag`) |
| Runtime dependencies | `watchdog>=4.0.0`, `pyyaml>=6.0` |
| `dev` extra | `pytest>=7.0.0`, `pytest-cov>=4.0.0`, `mypy>=1.10.0`, `ruff>=0.4.0`, `import-linter>=2.0`, `types-PyYAML>=6.0` |
| Package data | `ghdag/py.typed`, `ghdag/ui/static/*` |

GitHub access uses the standard library (`urllib`). ghdag needs neither `requests` nor the `gh` CLI.

## Quick Start

### End to end without GitHub

This script needs no token. It uses `GHDAG_FORGE=local` with a throwaway git checkout as the forge, defines a one-step shell workflow, creates a labelled Issue, enqueues it with `ghdag watch --once`, and lets a background `ghdag run` execute it.

<!-- ghdag-localforge-quick-start -->
```bash
set -euo pipefail
unset GITHUB_TOKEN GH_TOKEN GITHUB_REPOSITORIES
ROOT="${GHDAG_QUICKSTART_WORKDIR:-$(mktemp -d)}"
cd "$ROOT"
export GHDAG_FORGE=local
export GHDAG_FORGE_ROOT="$ROOT"

git init -q
git config user.email "quickstart@example.com"
git config user.name "ghdag quickstart"
printf 'init\n' > README.md
git add README.md
git commit -q -m init
git branch -M main

mkdir -p workflows/templates jobs
cat > workflows/sample.yml <<'YAML'
name: local-quickstart
template_dir: templates
triggers:
  - label: "pipeline:develop-ready"
    handler: run
handlers:
  run:
    steps:
      - id: write-out
        template: write-out
        engine: shell
YAML
cat > workflows/templates/write-out.md <<'MD'
printf 'quickstart-ok\n' > quickstart.out
printf 'quickstart-ok\n' > "jobs/${result_filename}"
MD
touch jobs/exec.jsonl

ISSUE="$(python -m ghdag.github_cli issue create --title quickstart --body body)"
python -m ghdag.github_cli issue edit "$ISSUE" --add-label pipeline:develop-ready

python -m ghdag run jobs/exec.jsonl &
RUNNER_PID=$!
python -m ghdag watch workflows --exec-jsonl jobs/exec.jsonl --once

for _ in $(seq 1 300); do
  if compgen -G "jobs/done/*" > /dev/null && [[ "$(cat jobs/done/*)" == "0" ]]; then
    break
  fi
  sleep 0.05
done
kill "$RUNNER_PID" 2>/dev/null || true
wait "$RUNNER_PID" 2>/dev/null || true

test -f quickstart.out
test "$(grep -cve '^[[:space:]]*$' jobs/exec.jsonl || true)" -eq 1
```

Afterwards `jobs/exec.jsonl` holds one task line, `jobs/done/<uuid>` contains `0`, and `jobs/audit.jsonl` holds the lifecycle events.

### Run a queue file directly

Each line of `exec.jsonl` is one task; `depends` lists the UUIDs of upstream tasks.

```bash
mkdir -p jobs
cat > jobs/exec.jsonl <<'EOF'
{"uuid":"demo-0001","command":"echo hello from ghdag","depends":[]}
{"uuid":"demo-0002","command":"echo second","depends":["demo-0001"]}
EOF
ghdag run jobs/exec.jsonl   # long-running; stop with Ctrl-C (SIGINT) or SIGTERM
```

`demo-0002` starts only after `demo-0001` succeeds. The runner keeps watching the file, so lines appended later run without a restart.

### Dispatch from GitHub Issue labels

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

Each step `template` must exist as `workflows/templates/<template>.md`; that file is the order text handed to the engine.

```bash
export GITHUB_TOKEN="<token>"            # or GH_TOKEN
export GITHUB_REPOSITORIES="owner/repo"
ghdag validate workflows
ghdag watch workflows --exec-jsonl jobs/exec.jsonl --once
ghdag status --issue 12 --handler impl --workflow sample-pipeline
```

`watch` appends the steps of every Issue labelled `pipeline:develop-ready` to `jobs/exec.jsonl`; a running `ghdag run jobs/exec.jsonl` executes them.

### Call an engine from Python

`validate_engine_model` checks an engine / model pair without launching anything (the same check `ghdag llm` uses):

```python
from ghdag.llm import validate_engine_model

model = validate_engine_model("claude", None)
assert model
```

`ghdag.llm.call(prompt, route=[("claude", None)])` runs the prompt and returns an `LLMResult`. The same call from the shell:

```bash
ghdag llm --engine claude --permission text_only "Summarize this repo in one line."
```

### Commit owned paths through a git sink

`LocalGitSink.create` builds a throwaway bare remote plus a clone, which is handy for trying the sink:

```python
from pathlib import Path
from tempfile import mkdtemp

from ghdag.vcs import LocalGitSink

sink = LocalGitSink.create(Path(mkdtemp()), owner="host", allow_prefixes=["notes/"])
(sink.repo_root / "notes").mkdir()
(sink.repo_root / "notes" / "entry.md").write_text("entry\n", encoding="utf-8")

result = sink.commit(["notes/entry.md"], "host(notes): add entry")
print(result.committed, result.pushed, result.sha)
```

In production, use `ghdag.vcs.get_sink(name)` with a [VCS sink config](#vcs-sink-config-ghdag_vcs_config).

## CLI Reference

Global options: `--verbose` / `-v` (DEBUG logging) and `--quiet` / `-q` (WARNING and above).

Every leaf command accepts `--json`. With `--json`, success is one JSON object or array on stdout, and a `GhdagError` is one line on stdout: `{"error": {"code", "message", "hint", "exit_code"}}`. Without it, errors go to stderr as `error[<CODE>]: <message>`, followed by `  hint: <hint>` when there is one. Either way the process exits with the exception's `exit_code` (see [Error Reference](#error-reference)).

### Commands

| Command | Description |
|---|---|
| `ghdag run` | Execute `exec.jsonl` with `DagEngine` (long-running) |
| `ghdag watch` | Poll Issues and enqueue the handlers their labels trigger |
| `ghdag trigger` | Enqueue one handler for one Issue (one-shot) |
| `ghdag status` | Show the step states of an Issue run and / or the running tasks |
| `ghdag validate` | Validate every workflow YAML file in a directory |
| `ghdag llm` | One-shot engine call without a workflow |
| `ghdag mcp` | Run the stdio MCP server |
| `ghdag ui` | Serve the Web UI dashboard (HTTP + SSE) |
| `ghdag cleanup` | Archive completed and orphaned queue tasks |
| `ghdag audit-query` | Query `audit.jsonl` by correlation ID, or detect correlation-ID bursts |
| `ghdag version` | Print the installed version |
| `ghdag dag` | Group: `recover`, `cancel` |
| `ghdag dag recover` | Reset the failed and pending steps of a handler run so they run again |
| `ghdag dag cancel` | Request cancellation of a running task |
| `ghdag quota` | Group: `report`, `clear`, `drain`, `resume`, `status` |
| `ghdag quota report` | Report an engine as `available` or `paused` |
| `ghdag quota clear` | Clear the quota state of an engine |
| `ghdag quota drain` | Stop new launches for an engine |
| `ghdag quota resume` | Take an engine out of drain mode |
| `ghdag quota status` | Print a per-engine quota / drain / queue snapshot as JSON |
| `ghdag schema` | Group: `list`, `show` |
| `ghdag schema list` | List the bundled JSON Schema names |
| `ghdag schema show` | Print one bundled JSON Schema |
| `ghdag tools` | Group: `list` |
| `ghdag tools list` | List the tool definitions found in a directory |

### Arguments and defaults

`$GHDAG_STATE_DIR/x or y` means "`x` under `GHDAG_STATE_DIR` when that variable is set, otherwise `y`". `--json` is omitted from the rows.

| Command | Arguments (defaults) |
|---|---|
| `run` | `exec_jsonl`; `--interval-sec SEC` (`1.0`); `--task-timeout SEC` (no limit); `--max-concurrency N` (unlimited); `--quota-state-path PATH` (`$GHDAG_STATE_DIR/quota-gate.json` or `<exec.jsonl parent>/quota-gate.json`); `--hooks MODULE` (repeatable; `AuditHooks` when omitted); `--nodes PATH` (YAML file listing remote SSH execution nodes) |
| `watch` | `workflows_dir`; `--interval-sec SEC` (`30`); `--exec-jsonl PATH` (`jobs/exec.jsonl`); `--once`; `--pause-file PATH` (disabled); `--state-dir PATH` (`$GHDAG_STATE_DIR/.pipeline-state` or `<exec.jsonl parent's parent>/.pipeline-state`) |
| `trigger` | `issue_number`; `--handler NAME` (required); `--workflows-dir PATH` (`workflows`); `--exec-jsonl PATH` (`jobs/exec.jsonl`); `--workflow NAME` (auto-detected when the directory has one workflow); `--redispatch`; `--reason TEXT`; `--state-dir PATH` (as `watch`) |
| `status` | `--issue N` and / or `--running` (at least one); `--handler NAME` and `--workflow NAME` (both required with `--issue`); `--exec-jsonl PATH` (`$GHDAG_EXEC_JSONL` or `jobs/exec.jsonl`); `--state-dir PATH` (`$GHDAG_STATE_DIR/.pipeline-state` or `.pipeline-state`); `--done-dir PATH` / `--running-dir PATH` (`$GHDAG_STATE_DIR/done` / `running` or `<exec.jsonl parent>/done` / `running`); `--audit-path PATH` (optional; adds `started_at` / `elapsed_sec` to completed steps) |
| `validate` | `workflows_dir` |
| `llm` | `[prompt]` (stdin when omitted); `--engine` / `-e` (`claude`); `--model` / `-m` (engine default); `--timeout-sec SEC` (no limit); `--permission {text_only,json_only,web_research,dangerous_full_access,readonly_observe}`; `--stdin` (also pipe stdin to the engine); `--list-engines`; `--list-models`; `--audit-path PATH` (`GHDAG_AUDIT_PATH`); `--correlation-id ID`; `--request-id ID` |
| `mcp` | `--repo-root PATH` (`.`); `--exec-jsonl PATH` (`jobs/exec.jsonl` under the repo root); `--state-dir PATH` (derived as for `watch`); `--read-path PATH` (repeatable; repo-relative allowlist for `ghdag_read`) |
| `ui` | `--repo-root PATH` (`.`); `--host HOST` (`127.0.0.1`); `--port PORT` (`8080`); `--interval-sec SEC` (SSE poll, `3.0`); `--max-visible N` (`30`) |
| `cleanup` | `repo_root`; `--dry-run`; `--cutoff-days N` (`1`); `--orphan-days N` (`7`); `--auto-repair` (fix orphan and dead entries; detect-only by default) |
| `audit-query` | `--correlation-id ID`; `--burst-detect` (exit `1` when a burst is found); `--since ISO8601` (correlation-ID mode only); `--audit-path PATH` (`$GHDAG_AUDIT_PATH` or `jobs/audit.jsonl`); `--window-sec SEC` (`600`); `--threshold N` (`10`) |
| `version` | none |
| `dag recover` | `--issue N` (required); `--handler NAME` (required); `--from STEP` (only this step and its downstream steps); `--dry-run`; `--keep-results`; `--workflows-dir PATH` (`workflows`); `--exec-jsonl PATH` (`jobs/exec.jsonl`); `--workflow NAME` (auto-detected); `--state-dir PATH` (`$GHDAG_STATE_DIR/.pipeline-state` or `.pipeline-state`) |
| `dag cancel` | `uuid`; `--state-dir DIR` (`$GHDAG_STATE_DIR` or `jobs`) |
| `quota report` | `engine`; `--status {available,paused}` (required); `--observed-at ISO8601` (required, with timezone); `--resume-at ISO8601`; `--reason TEXT`; `--state-dir DIR` (`$GHDAG_STATE_DIR` or `jobs`; contains `quota-gate.json`) |
| `quota clear` | `engine`; `--observed-at ISO8601` (required); `--state-dir DIR` (as above) |
| `quota drain` | `engine`; `--reason TEXT`; `--state-dir DIR` (as above) |
| `quota resume` | `engine`; `--state-dir DIR` (as above) |
| `quota status` | `--state-dir DIR` (as above); `--exec-jsonl PATH` (`jobs/exec.jsonl`); `--done-dir PATH` (`$GHDAG_STATE_DIR/done` or `<exec.jsonl parent>/done`) |
| `schema list` | none |
| `schema show` | `name` (logical name without `.schema.json`) |
| `tools list` | `--path PATH` (required) |

### Behavior notes

| Topic | Behavior |
|---|---|
| Working directory of tasks | `ghdag run` starts tasks with `cwd` set to the parent of the directory that holds `exec.jsonl` (for `jobs/exec.jsonl`, the repository root) |
| One runner per queue | `DagEngine` holds `<queue dir>/.ghdag.lock`; a second runner on the same queue logs "Another DagEngine is already running" and stops |
| `run --hooks` | Each module is imported with the current directory on `sys.path` and supplies `HOOKS_CLASS`, or else its first public class with `on_task_success`. Several modules are chained in order: lifecycle methods call every hook, `check_rejected` is true as soon as one hook returns true, and `check_pipeline_status` / `check_promote_target` use the first non-`None` return. A failed import raises `HOOKS_IMPORT_FAILED`; a missing hook class raises `HOOKS_CLASS_NOT_FOUND`. A missing `exec.jsonl` raises `EXEC_JSONL_NOT_FOUND` before the runner starts |
| `watch` and rate limits | `watch` never sleeps on a rate limit. After a `RateLimitError` it skips polls until the reset time (log: `rate limited: skip poll until <ISO8601>`), and it doubles the polling interval while the remaining quota is low. Pause-file checks and workflow hot reload keep running |
| `trigger --redispatch` | Increments the handler generation and starts a new run, for when `dag recover` is not possible; `--reason` is written to `audit.jsonl` |
| `dag recover` | Clears the done markers of failed and pending steps so the runner runs them again; running steps are left alone. Existing result files are first moved to `<result>.prev-<timestamp>` unless `--keep-results` is given |
| `dag cancel` | Creates `cancel/<uuid>` only when `running/<uuid>.json` exists (or the task is a waiting exclusive gate); otherwise raises `GhdagError` with `code="TASK_NOT_RUNNING"` (`error[TASK_NOT_RUNNING]: ...` on stderr, or JSON with `--json`). The CLI never signals a process: the runner sees the marker, stops the task's process group and writes `CANCELLED` |
| `llm` exit codes | Exits with the engine's exit code; `124` on timeout (partial output is printed), `127` when the engine binary is missing, `126` when it is not executable |
| `validate` | Exits `3` when one or more workflow files fail validation, `2` when the directory does not exist |
| `ui` start-up | Loads the language pack before binding, so an invalid `GHDAG_LANGUAGE_PACK` fails the command |
| Failed `shell` tasks | A non-zero `shell` task writes its stdout (stderr merged) plus a final `EXIT_CODE: <n>` line to its result file; the done marker keeps the exit code |

### Module entry points

| Command | Purpose |
|---|---|
| `python -m ghdag.github_cli <command> ...` | `gh`-style client over `get_forge()`: `issue {view,edit,comment,close,create}`, `pr {list,view,edit,diff,create,merge,checks,ready}`, `run {view,rerun}`, `repo view`, `api`. Unknown arguments and unknown or missing subcommands exit `2` with `error[CLI_USAGE]:` and a `hint:` line. `issue create`, `issue edit` and `pr create` accept `--body-file` |
| `python -m ghdag.workflow.state_machine transition --workflow YAML ISSUE TARGET_LABEL` | Check a label transition against the workflow's `transitions` / `reset_label` and apply it |
| `python -m ghdag.workflow.render TEMPLATE [key=value ...]` | Expand a `render: live` template at run time and run it with `bash -o pipefail` (exit `2` on template errors) |
| `python -m ghdag.workflow.conditional_step [--engine {claude,cursor}] [--model M] TEMPLATE [KEY=VALUE ...]` | Substitute template variables and invoke claude or cursor |

## Public API

Supported import paths are `ghdag` and one-segment `ghdag.<package>`. Deeper paths (`ghdag.<package>.<module>`) are implementation detail and may change without notice; the tables below name a few of them because some types have no package-level export yet. Names starting with `_` are private.

The top-level package exports one name, `GhdagError` (the base class of every ghdag domain error), plus `ghdag.__version__` (`"unknown"` when the distribution is not installed).

### Package `__all__`

| Package | Public symbols |
|---|---|
| `ghdag.cli` | `main` |
| `ghdag.core.ports` | `ForgePort` |
| `ghdag.dag` | `AuditHooks`, `DagConfig`, `DagEngine`, `DagHooks`, `DONE_SUCCESS`, `FanOutSpec`, `PIPELINE_WAIT_QUOTA`, `Task`, `WaitQuotaSignal`, `build_child_jsonl_record`, `check_pipeline_status`, `default_check_rejected`, `execute_recover`, `extract_tee_target`, `parse_fanout_spec`, `parse_wait_quota_signal`, `plan_recover`, `running_uuids_from_queue_dir` |
| `ghdag.files` | `md_read`, `md_write` |
| `ghdag.files.links` | `job_footer`, `rewrite_links`, `summary_footer` |
| `ghdag.forge` | `ForgePort`, `GitHubApiError`, `GitHubClient`, `LocalForge`, `get_forge` |
| `ghdag.io` | `done`, `exec_jsonl`, `queue`, `redact`, `sessions` |
| `ghdag.llm` | `ENGINE_DEFAULTS`, `ENGINE_SPECS`, `EngineError`, `EngineModelError`, `PERMISSIONS`, `classify_common_failure`, `container_prefix`, `supports_capability`, `Permission`, `LLMResult`, `SessionRecord`, `TEXT_ONLY`, `DANGEROUS_FULL_ACCESS`, `call`, `get_engine_models`, `list_engines`, `validate_engine_model` |
| `ghdag.llm.adapters` | `EngineOutputAdapter`, `get_output_adapter` |
| `ghdag.metrics` | `FailureClass`, `MetricsRecorder`, `TaskMetrics`, `TokenUsage` |
| `ghdag.pipeline` | `AuditContext`, `PipelineConfig`, `TaskQueue`, `OrderBuilder`, `TemplateOrderBuilder`, `InlineOrderBuilder`, `TemplateVariableError`, `build_idempotency_key`, `get_command_adapter`, `resolve_models`, `wait_for_result`, `write_task_exit_audit` |
| `ghdag.quota` | `QuotaGate`, `QuotaSnapshot`, `EngineQuotaState`, `EngineUsage`, `AdmissionDecision` |
| `ghdag.status` | `EXCLUSIVE_STATE_FILE`, `StepStatus`, `issue_status`, `read_exclusive_gate` |
| `ghdag.tool` | `ToolDef` |
| `ghdag.vcs` | `CommitResult`, `ConflictError`, `LocalGitSink`, `NullSink`, `OwnershipError`, `get_sink`, `git_enabled` |
| `ghdag.workflow` | `ConsumeEntry`, `HandlerConfig`, `SkillIOSpec`, `StepConfig`, `TriggerConfig`, `WorkflowConfig`, `load_workflows`, `WorkflowDispatcher`, `transition`, `typecheck_dag` |

`ghdag.io` exports submodules: use `ghdag.io.queue.QueueTaskStore`, `ghdag.io.done.done_failure_class`, and so on.

### Key signatures

Intake:

| Callable | Signature |
|---|---|
| `ghdag.workflow.load_workflows` | `(directory) -> list[WorkflowConfig]` |
| `ghdag.workflow.WorkflowDispatcher` | `(workflows, github_client, task_queue, queue_dir="queue", pause_file=None, workflows_dir=None)`; `github_client` is one `GitHubIssuePort` or a list of them |
| `ghdag.pipeline.TaskQueue` | `(state_dir, exec_jsonl_path, *, queue_dir="queue", order_builder=None, order_builders=None, timezone=None)`; `timezone` is the IANA name used for order / result file name timestamps (`None` = machine local time). Also `TaskQueue.from_repo_root(repo_root, **kwargs)` |
| `TaskQueue.enqueue` | `(steps, base_context, *, idempotency_key=None, audit_context, metadata=None, order_builder=None) -> list[str]`; returns the task UUIDs |
| `TaskQueue.enqueue_records` | `(records, audit_context=None) -> None`; appends pre-built exec records |
| `TaskQueue.write_order` | `(ts, order_uuid, content, queue_dir, engine="claude", order_footer_fn=None) -> str` |
| `TaskQueue.set_order_builders` | `(mapping) -> None`; replaces the per-workflow builders |
| `ghdag.pipeline.wait_for_result` | `(exec_done_dir, uuid, *, timeout, poll_interval=0.5) -> tuple[str, str]` |
| `ghdag.forge.get_forge` | `(repo=None) -> ForgePort` |
| `ghdag.forge.GitHubClient` | `(token=None, repo=None, etag_cache_path=None, rate_limit_max_wait_sec=None)` |
| `ghdag.forge.LocalForge` | `(root, *, repo="local/forge", checks_command=None)` |

Execution and inspection:

| Callable | Signature |
|---|---|
| `ghdag.dag.DagEngine` | `(config: DagConfig, hooks: DagHooks \| None = None)`; `.run()` blocks until shutdown |
| `ghdag.dag.plan_recover` | `(*, state_dir, exec_jsonl_path, workflow_name, handler_name, issue_number, queue_dir, done_dir, from_step=None, running_uuids=None) -> RecoverPlan` |
| `ghdag.dag.execute_recover` | `(plan, *, queue_dir, done_dir, dry_run=False, running_uuids=None, keep_results=False, now=None) -> RecoverResult` |
| `ghdag.status.issue_status` | `(issue_number, *, handler=None, workflow=None, exec_jsonl_path, state_dir, done_dir, running_uuids=None, audit_path=None, running_dir=None) -> IssueStatus` |
| `ghdag.status.running_tasks` | `(running_dir) -> list[RunningTask]` |
| `ghdag.status.read_exclusive_gate` | `(queue_dir) -> tuple[str, str] \| None`; the `(uuid, state)` of the exclusive gate, `None` when `exclusive.json` is absent or invalid |
| `ghdag.quota.QuotaGate` | `(state_path, audit_path=None, pause_ttl_seconds=3600, limits=None)`; methods `report`, `clear`, `drain`, `resume`, `admit`, `begin_run`, `finish_run`, `refresh`, `snapshot`, `observe` |
| `ghdag.io.queue.QueueTaskStore` | `(queue_dir, done_dir)`; `.list_tasks()` |

Engines:

| Callable | Signature |
|---|---|
| `ghdag.llm.call` | `(prompt, *, route, timeout=None, stdin_text=None, cwd=None, permission=TEXT_ONLY, resume_session_id=None, isolation=None, permission_overrides=None, quota_gate=None, additional_tags=None) -> LLMResult`; `route` is an ordered list of `(engine, model)` pairs, and `model=None` selects the engine default |
| `ghdag.llm.validate_engine_model` | `(engine, model) -> str`; returns the resolved model or raises `EngineModelError` |
| `ghdag.llm.supports_capability` | `(engine, capability) -> bool` |
| `ghdag.llm.container_prefix` | `(permission, *, workdir='"$PWD"') -> list[str]` |

Files and git:

| Callable | Signature |
|---|---|
| `ghdag.files.md_read` | `(path, *, repo_root=None) -> MdFile` |
| `ghdag.files.md_write` | `(path, content, *, repo_root=None, source=None, correlation_id=None) -> WriteResult` |
| `ghdag.vcs.get_sink` | `(name) -> GitSink \| NullSink` |
| `ghdag.vcs.git_enabled` | `() -> bool` |
| `ghdag.vcs.LocalGitSink.create` | `(tmp_dir, *, owner, allow_prefixes, branch="main", push="immediate", audit_path=None) -> GitSink` |
| `GitSink.commit` | `(paths, message, *, trailers=None) -> CommitResult`; `GitSink.flush() -> CommitResult`. `NullSink` has the same two methods and always returns `skipped=True` |

Helpers that have no package-level export and are imported from their module:

| Module | Names |
|---|---|
| `ghdag.maintenance` | `validate_exec_jsonl(exec_jsonl_path)`, `repair_exec_jsonl(exec_jsonl_path, *, dry_run=False)`, `repair_jobs_done(exec_jsonl_path, done_dir, *, dry_run=False)` |
| `ghdag.github_client` | `create_github_client`, `create_github_clients(*, token=None, rate_limit_max_wait_sec=None)` |
| `ghdag.cleanup.orchestrator` | `cleanup_queue(queue_dir, archive_dir, done_dir, exec_jsonl, cutoff_days=1, orphan_days=7, dry_run=False, auto_repair=False) -> CleanupResult` |
| `ghdag.core.command` | `render_exec_command`, `build_llm_cmd`, `inject_resume`, `register_command_adapter` |
| `ghdag.core.vocabulary` | Done-marker constants (`DONE_*`), `QUEUE_FILE_RE`, reply-block vocabulary, fan-out separator |
| `ghdag.pipeline.status` | `task_status`, `state_label(state_id, pack=None)` and the `STATE_*` identifiers |
| `ghdag.pipeline.llm_pipeline` | `build_order_builders(workflows) -> dict[str, OrderBuilder]` |
| `ghdag.files.append` / `ghdag.files.promote` | `md_append(path, section, body, *, idempotency_key=None, repo_root=None, allow_recover=False)`, `md_promote(source_path, target_path, *, section="Promoted", idempotency_key=None, repo_root=None)` |
| `ghdag.audit.span` | `emit_span(*, orchestration_id, name, route, start_ts, duration_ms, status, span_id, parent_span_id, attributes=None, log_path=None)`, `make_span_id()` |
| `ghdag.config.language` | `load_language_pack(path)`, `get_language_pack()` (cached per process) |

The `STATE_*` identifiers are language-neutral: `pending_deps`, `pending_run`, `running`, `deferred`, `ok`, `fail`, `rejected`, `empty`, `engine_error`, `unknown`. Use `state_label()` for display text.

### `LLMResult`

Return type of `ghdag.llm.call`. `call` does not raise on timeouts or missing binaries; it reports them here.

| Field / member | Type | Meaning |
|---|---|---|
| `body` | `str` | Result text extracted by the output adapter (or the validated JSON text); empty when `error` is set |
| `stdout` | `str` | Process stdout after normalization (for codex streams, the raw JSONL) |
| `stderr` | `str` | Process stderr; on timeout it ends with `TIMEOUT: <cli> timed out after <n>s` |
| `returncode` | `int` | Process exit code; `124` on timeout, `127` when the binary is missing, `126` when it is not executable |
| `raw_stdout` | `str \| None` | For claude and cursor stream calls, the JSONL before normalization |
| `latency_ms` | `float` | Wall-clock latency |
| `session_id` | `str \| None` | Engine session ID when the adapter can extract one |
| `failure_class` | `FailureClass \| None` | Set by `call` itself (for example `TIMEOUT`); `None` when the caller has to classify the output |
| `error` | `EngineError \| None` | Structured adapter error |
| `usage` | `TokenUsage \| None` | Token usage when the adapter can extract it |
| `engine_used` / `model_used` | `str` | The route entry launched last |
| `attempts` | `int` | Number of route entries launched |
| `quota_reported` | `bool` | Whether this call reported a pause to the quota gate |
| `additional_tags` | `dict[str, str]` | The tags passed to `call` |
| `ok` | property | `returncode == 0 and error is None` |
| `timed_out` | property | `failure_class is FailureClass.TIMEOUT` |

With a `quota_gate`, `call` walks `route` front to back. Engines the gate marks paused are skipped without launching. After `QUOTA_EXHAUSTED` or `AUTH` the gate may record a pause and the next entry is tried; after `ENGINE_ENVIRONMENT_ERROR` the next entry is tried without a pause; any other failure ends the walk. `resume_session_id` is used only for the first route entry.

### Permissions

`Permission` (a frozen dataclass) says what an engine may do. Fields and defaults: `permission_mode="default"`, `output_format="text"` (or `"json"`), `allowed_tools=()`, `disallowed_tools=()`, `stream=False`, `sandbox="off"` (or `"readonly"` / `"container"`), `resume=False`, `web_search=False`, and the container fields below.

Presets live in `PERMISSIONS`. Workflow YAML names a preset in `permission`; steps without one use `GHDAG_SAFE_DEFAULT_PERMISSION`. Code that builds `StepConfig` in Python may pass a `Permission` value instead of a name, for example to set a per-job tool allow-list.

| Name | Settings |
|---|---|
| `text_only` | Text output; `disallowed_tools=("Bash", "Edit", "Write", "NotebookEdit", "WebFetch", "WebSearch")` |
| `json_only` | As `text_only`, with `output_format="json"` |
| `web_research` | `web_search=True`; `disallowed_tools=("Bash", "Edit", "Write", "NotebookEdit")` |
| `dangerous_full_access` | `permission_mode="bypassPermissions"` |
| `readonly_observe` | `sandbox="readonly"` (claude: `--permission-mode plan`; cursor: `--sandbox enabled`); `disallowed_tools=("Edit", "NotebookEdit")` |

Engines do not all honour every field; `supports_capability(engine, capability)` reports what an engine supports. gemini supports none of the tool, sandbox, resume, stream and Web search fields, and `call` raises `NotImplementedError` for a non-default value an engine cannot honour.

`web_search=True` asks for Web search in an engine-neutral way: claude gets the Web tools on its allow-list, codex gets `--search`, and cursor uses its built-in Web tools with no extra flag.

With `sandbox="container"`, the commands written to `exec.jsonl` wrap the claude / cursor / codex CLI in a `docker run` prefix built by `container_prefix`. Only the worktree (`"$PWD"`, mounted at `/work`), `container_mounts` and the variables named in `container_env` reach the container. `ghdag.llm.call` and the `shell` engine reject the container sandbox with `ValueError`. No preset uses it; derive one with `dataclasses.replace`.

| Field | Default | Meaning |
|---|---|---|
| `container_image` | `""` | Image to run; empty raises `ValueError` |
| `container_readonly` | `False` | Adds `--read-only` and mounts the worktree `:ro` |
| `container_mounts` | `()` | Extra `host:container[:ro]` mounts (`~` is expanded) |
| `container_env` | `()` | Variable **names** passed as `-e NAME`; values never go on argv, and `NAME=value` raises `ValueError` |
| `docker_bin` | `"docker"` | docker executable; when it is a path, its directory is prepended to `PATH` |

### Engines

`ENGINE_SPECS` is the single source of engine command lines.

| Engine | CLI | Default model | Built-in allowed models |
|---|---|---|---|
| `claude` | `claude` | `claude-sonnet-4-6` | `claude-opus-4-7`, `claude-opus-4-6`, `claude-sonnet-4-6`, `claude-haiku-4-5-20251001` |
| `codex` | `codex exec -` | `gpt-5.6-terra` | `gpt-5.6-terra`, `gpt-5.6-luna`, `gpt-5.5`, `gpt-5.4-mini` |
| `cursor` | `cursor-agent` | `auto` | `auto`, `composer-2`, `composer-2-fast`, `gpt-5.2`, `gpt-5.3-codex`, `gpt-5.3-codex-fast`, `gpt-5.3-codex-high`, `gpt-5.3-codex-high-fast`, `gpt-5.4-medium-fast` |
| `gemini` | `gemini` | `gemini-2.5-flash` | `gemini-2.5-pro`, `gemini-2.5-flash` |
| `shell` | `bash -o pipefail` | none | `bash` (runs the order file as a script; no LLM call) |

claude, cursor, codex and gemini receive the prompt on stdin, which avoids the per-argument size limit; `shell` gets the order path on argv. Replace the allowed-model lists with [`llm-models.yml`](#llm-modelsyml).

The gemini integration is defined but not exercised in day-to-day maintenance (`EngineSpec.verified` is `False`), so it may lag the upstream CLI. Launching gemini logs a one-time warning.

### Protocols

These are the extension points. All are `typing.Protocol` classes.

| Protocol | Defined in | Implement to |
|---|---|---|
| `DagHooks` | `ghdag.core.ports.dag_hooks` (re-exported by `ghdag.dag`) | React to task lifecycle events: `on_task_start`, `on_task_success`, `on_task_failure`, `on_task_rejected`, `on_task_dep_failed`, `on_task_empty_result`, `on_task_cancelled`, `on_task_progress`, `on_shutdown`, and decide `check_rejected`, `check_pipeline_status`, `check_promote_target`. Subclass `ghdag.dag.hooks.DefaultHooks` and load the module with `ghdag run --hooks` |
| `ForgePort` | `ghdag.core.ports.forge` | Provide an Issue / PR backend (`GitHubClient` and `LocalForge` implement it) |
| `GitHubIssuePort` | `ghdag.core.ports.github` | The smaller Issue client that `WorkflowDispatcher` needs (`get_issue`, `list_issues`, `update_label`, `add_comment`, ...) |
| `OrderBuilder` | `ghdag.core.ports.order` | Build order text: `build_order(step_id, context) -> str` |
| `EngineOutputAdapter` | `ghdag.core.ports.output` | Parse engine output: `extract_result_text`, `extract_token_usage`, `extract_quota_windows`, `extract_session_id`, `extract_error`, `classify_failure` |
| `EngineAdapter` | `ghdag.core.command` | Build the command line of a custom engine; register it with `register_command_adapter` |

## Architecture

### Processes and files

```
Issues and labels (GitHub, or LocalForge)        agent (MCP client)
        |                                               |
        v                                               v
 intake: ghdag watch / ghdag trigger               ghdag mcp
        |            (workflow/ + pipeline/)            |
        +----------------- TaskQueue.enqueue -----------+
                                |
                                v
                         jobs/exec.jsonl
                                |
                                v
 execution: ghdag run  (dag/ DagEngine)  ---> engine CLIs / bash
                                |
                                v
 recording: done/<uuid>, jobs/audit.jsonl, result files
```

The intake side and the execution side never import each other; `import-linter` contracts in `pyproject.toml` enforce this, along with `ghdag.core` depending on no other ghdag package and `io` / `files` / `llm` / `quota` not importing orchestration code.

| File | Written by | Read by | Contents |
|---|---|---|---|
| `jobs/exec.jsonl` | `TaskQueue.enqueue`, `DagEngine.append_task` | `DagEngine` | One task record per line ([fields](#execjsonl-task-fields)) |
| `jobs/audit.jsonl` | `AuditHooks`, `ghdag.io.audit`, git sinks | `ghdag audit-query`, Web UI | Lifecycle events (rotated by size) |
| `done/<uuid>` | the runner | dispatcher, `ghdag status`, `ghdag dag recover`, Web UI | [Done marker](#done-markers) |
| `running/<uuid>.json` | the runner at launch | `ghdag dag cancel`, `ghdag status --running`, restart adoption | `pgid`, `started_at`; optional `interrupted_at`, `interrupted_reruns`. Removed when the task exits |
| `cancel/<uuid>` | `ghdag dag cancel`, Web UI | the runner | Empty marker requesting cancellation |
| `events/<uuid>.jsonl` | the runner | Web UI progress | Engine stream events of one task |
| `stderr/<uuid>/` | the runner | people | Stderr of failed tasks ([details](#reviewing-task-runs)) |
| `exclusive.json` | the runner | `ghdag status`, MCP | The current [exclusive](#exclusive-tasks) gate |
| `fanout-pending/<uuid>.json` | the runner | the runner on restart | Children a fan-out parent is waiting for |
| `quota-waiting/<uuid>.json` | the runner | the runner on restart | Engines a task is waiting on after `WAIT_QUOTA` |
| `.sessions/` | the runner | `resume_from` steps | Engine session IDs per task |
| `quota-gate.json` | `QuotaGate`, `ghdag quota` | launch admission | [Quota state](#quota-state-quota-gatejson) |
| `.pipeline-state/` | `TaskQueue` | dispatcher, `ghdag status`, `ghdag dag recover` | Which tasks belong to which Issue / handler run |
| `jobs/latency_span.jsonl` | `ghdag.audit.span.emit_span` | external tracing tools | One latency span per line |

`exec.jsonl` and `audit.jsonl` always live in the queue directory (`jobs/`). The runtime state (`done/`, `running/`, `cancel/`, `events/`, `.sessions/`, `quota-gate.json`, `.pipeline-state/`) moves to `$GHDAG_STATE_DIR` when that variable is set.

### Runner lifecycle

| Phase | Behavior |
|---|---|
| Launch | A task starts when every task in `depends` has succeeded and `QuotaGate.admit` admits its engine. Each task runs in its own process group, with the environment described under [Task environment](#task-environment) |
| Dependency failure | Tasks downstream of a failed task get `DEP_FAILED` |
| Timeout / cancel | SIGTERM to the process group, SIGKILL after `DagConfig.kill_grace`; done marker `TIMEOUT` / `CANCELLED` |
| Reply block | A result may end with a fenced YAML block whose info string is `ghdag` (schema `reply-block`). It carries the task status, and `status: ok` with `children` declares fan-out: the runner enqueues the children and the parent completes when they do. |
| Repair turn | When the reply block or fan-out is invalid and the task has a stored session, the runner resumes that session once with a repair order (`DagConfig.max_repair_turns`, audit event `llm.repair_turn`). The relaunch goes through quota admission like any launch; if it is refused, the original failure stands |
| Quota wait | A task whose reply block reports `status: wait_quota` stays pending without a done marker. The named engines are paused in the quota gate and the task is relaunched when one of them is available again |
| Retry | Retryable engine errors are relaunched up to `DagConfig.max_retry` times |
| Engine quarantine | An engine whose environment is broken (missing binary, for example) is temporarily blocked from launching |
| Circuit breaker | The runner stops after `max_consecutive_failures` failures in a row (audit event `circuit_breaker_tripped`); the count resets when failures are more than `failure_window_sec` apart |
| Stall guard | For cursor tasks, a `cat` child that sits at zero CPU for `GHDAG_ENGINE_STALL_SEC` is sent SIGTERM (audit event `stall_guard`). Disable with `GHDAG_ENGINE_STALL_GUARD=0` |
| First SIGTERM / SIGINT | Stops launching, records `interrupted_at` for the running tasks, and waits for them up to `task_timeout` (no wait when it is `None`); whatever remains is terminated |
| Second SIGTERM / SIGINT | Exits immediately |
| Restart | Tasks found in `running/` are adopted if their process is alive and closed with `ORPHANED_ON_RESTART` if not. Tasks interrupted by a shutdown are relaunched once with the same UUID and `GHDAG_PREVIOUS_ATTEMPT=interrupted` |

### Exclusive tasks

Some work, such as replacing the runner's own code, must not overlap with any other task. Mark such a task exclusive with `annotations._exclusive: "true"` on its `exec.jsonl` line, `"exclusive": true` on a `ghdag_enqueue` step, or `StepConfig(exclusive=True)` in Python. Workflow YAML has no key for it.

- The first eligible exclusive task in queue order becomes the **gate**. While a gate exists, no other task is launched.
- The gate starts when nothing else is running, and nothing else starts until it finishes. Several exclusive tasks run one after another.
- The runner writes `exclusive.json` as `{"uuid": "<gate>", "state": "waiting" | "running"}`. `ghdag status` and `ghdag_status` report `blocked_by` on the tasks the gate is holding back.
- `ghdag dag cancel <uuid>` works on a gate that is still waiting.
- On shutdown, a running exclusive task is not signalled; it keeps running and the next runner adopts it. An exclusive task is never rerun after an interruption: if its process is gone, it is closed with `ORPHANED_ON_RESTART`.

### Engine output and failure classification

| Topic | Behavior |
|---|---|
| Progress events | For claude, cursor and codex tasks with a result path, stdout is read line by line into `events/<uuid>.jsonl`, with secrets redacted |
| Interactive prompts | A failed run whose last non-empty output line ends with `?` (or a suffix from the language pack) is `INTERACTIVE_PROMPT`: permanent, never retried |
| Missing engine binary | `command not found`, `No such file or directory` or `permission denied` for the engine's CLI, or exit `127`, is `ENGINE_ENVIRONMENT_ERROR` |
| Authentication | Auth wording in the output (`unauthorized`, `not logged in`, `invalid api key`, `authentication_error`, ...) is classified `AUTH` |
| Quota exhaustion | `QUOTA_EXHAUSTED` pauses the engine in the quota gate until the reported reset time, or for `GHDAG_QUOTA_DEFAULT_PAUSE_SECONDS` when the engine gives none |
| cursor `RetriableError:` | `[resource_exhausted]` is a rate limit, other codes are capacity errors; both are retried |

### Reviewing task runs

What each artifact or interface tells you about a handler run (Issue + workflow + handler) or about one task:

| Artifact or interface | Look up by | What it shows |
|---|---|---|
| `exec.jsonl` | task UUID (field `uuid`) | The command, the `depends` edges and the intake metadata |
| `done/<uuid>` | task UUID | The outcome: `0`, or one of the [done markers](#done-markers) |
| `audit.jsonl` | task UUID and event type (`task_started`, `task_exit`, ...) | The timeline, durations, correlation IDs, and `stderr_path` for failures |
| result file | `result_path` on the task line | The text the engine or shell step produced |
| `stderr/<uuid>/...` | task UUID | The redacted stderr of a failed or empty-result task |
| `ghdag status` | Issue, handler and workflow, or `--running` | Step states, done values and failure classes; `--json` output follows the `issue-status` schema |
| `ghdag_status` (MCP) | task UUIDs, or Issue + handler + workflow | The same snapshot for agents without a shell |

Failure stderr is kept under the queue directory as `stderr/<uuid>/<UTC-timestamp>-r<retry>.log`, and that path is recorded as `stderr_path` in `audit.jsonl`. The file is redacted like audit records, capped at 1 MiB (the tail is kept), and never overwritten. Successful, cancelled and dependency-failed tasks leave no file.

### Done markers

`done/<uuid>` holds one of these values (constants in `ghdag.core.vocabulary`, schema `done-marker`). Any other content is the task's non-zero exit code.

| Value | Meaning | `FailureClass` |
|---|---|---|
| `0` | Success | none |
| `REJECTED` / `REJECTED_FINAL` | Result rejected by `check_rejected` (retryable / final) | `REJECTED` |
| `ENGINE_ERROR` / `ENGINE_ERROR_FINAL` | Retryable engine error (retries remain / exhausted) | `ENGINE_ERROR` |
| `ENGINE_ENVIRONMENT_ERROR` | Engine environment problem, such as a missing binary | same name |
| `INTERACTIVE_PROMPT` | The engine stopped to ask a question | same name |
| `TIMEOUT` | Task timeout | same name |
| `EMPTY_RESULT` | The engine produced no result text | same name |
| `DEP_FAILED` | An upstream task failed | same name |
| `PIPELINE_FAILED:<reason>` | The task reported a failure status | `PIPELINE_FAILED` |
| `FANOUT_CHILD_FAILED` / `FANOUT_PARSE_FAILED` | Fan-out failures | same name |
| `UNKNOWN_FAILURE` | Unclassified failure | same name |
| non-zero integer | Process exit code | `PROCESS_ERROR` |
| `CANCELLED` | Cancelled through `cancel/<uuid>` | none (terminal, not a failure class) |
| `SKIPPED_MISSING_INPUT` | A required input is missing | none |
| `ORPHAN_ARCHIVED` | Archived by `ghdag cleanup` as an orphan | none |
| `ORPHANED_ON_RESTART` | Lost across a runner restart; recover with `ghdag dag recover` | none |
| anything else | | `UNKNOWN_FAILURE` |

`ghdag.io.done.done_failure_class(raw)` implements the third column. `issue_status` exposes it as `StepStatus.failure_class`, together with the `cause` and `retry_policy` of that class.

### MCP server

`ghdag mcp` serves newline-delimited JSON-RPC 2.0 on stdio (protocol version `2025-06-18`), so an agent can enqueue and inspect tasks on the same queue. Enqueues go through `TaskQueue.enqueue`, the same path `watch` uses.

| Tool | Input | Output (`structuredContent`) |
|---|---|---|
| `ghdag_enqueue` | `steps` (1 to 20; each needs `engine` and `command` or `prompt`; optional `id`, `model`, `depends`, `permission`, `exclusive`) | `{"uuids": {<step id>: <uuid>}}` |
| `ghdag_status` | `uuids` (1 to 100, optional `include_result`) **or** `issue` + `handler` + `workflow` | `{"tasks": [...]}` with `pending` / `running` / `success` / `failed`, or the Issue run snapshot |
| `ghdag_read` | `path` (repo-relative; must be allowed by `--read-path`) | `{"path", "content", "truncated", "reason"}`; read-only, capped at 64 KiB |

Domain failures return `isError: true` with `structuredContent` shaped like `GhdagError.to_dict()`. Unknown tool names return JSON-RPC error `-32602`. The accepted engines and permissions are listed as enums in the `tools/list` schemas.

```json
{
  "mcpServers": {
    "ghdag": {
      "command": "ghdag",
      "args": ["mcp", "--repo-root", "/path/to/repo"]
    }
  }
}
```

### GitHub client and forges

`get_forge()` picks the Issue / PR backend from `GHDAG_FORGE`:

| `GHDAG_FORGE` | Backend | Requirement |
|---|---|---|
| unset / `github` | `GitHubClient` | A token and `GITHUB_REPOSITORIES` |
| `local` | `LocalForge` | `GHDAG_FORGE_ROOT`, a git checkout; Issues and PRs are JSON files under `<root>/.forge/` |

Any other value, or `local` without `GHDAG_FORGE_ROOT`, raises `ConfigError`.

`GitHubClient` is a `urllib`-based REST / GraphQL client:

| Feature | Behavior |
|---|---|
| Authentication | `token` argument, else `GITHUB_TOKEN`, else `GH_TOKEN`; `AuthError` when none is set |
| Repository | `repo` argument, else the first entry of `GITHUB_REPOSITORIES`; `GhdagError` when neither is set |
| Pagination | List calls follow `Link: rel="next"` to the end |
| ETag cache | Conditional requests (`If-None-Match`, 304) are cached in memory. With `etag_cache_path` or `GHDAG_ETAG_CACHE`, entries are also stored one file per entry in that directory, shared across processes and pruned oldest-first above 256 MiB |
| Rate limits | On a rate-limit 403 the client sleeps until the reset and retries once, if the wait is at most `rate_limit_max_wait_sec` (argument, then `GHDAG_RATE_LIMIT_MAX_WAIT_SEC`, then `900`); otherwise it raises `RateLimitError(reset_at=...)`. `0` means never sleep |

### Git sinks

A sink commits a fixed set of path prefixes on behalf of one owner. `GitSink.commit(paths, message)` commits only the given paths, then runs `fetch`, `rebase --autostash` and `push`, retrying a rejected push up to twice. All sinks on one repository take turns through a lock file in the git directory.

| Topic | Behavior |
|---|---|
| Ownership | Every path must be relative, contain no `..` and start with one of `allow_prefixes`; the commit subject must start with `<owner>(`. Violations raise `OwnershipError` before git is touched |
| Trailers | Each commit gets `Layer: <layer or owner>` and `Host: <hostname>`, plus any `trailers` passed in |
| Push policy | `immediate` (default), `debounce:<sec>` (push at most once per interval) or `manual`; `flush()` pushes what was left behind |
| No changes | Returns `CommitResult(skipped=True, reason="no changes")` |
| Conflicts | A rebase conflict saves the local versions under `<repo_root>/inbox/`, resets to the remote, and raises `ConflictError` (`inbox_paths`) |
| Audit | With an audit path, writes `vcs_commit`, `vcs_skipped` or `vcs_conflict` events |
| `get_sink(name)` | Returns a `NullSink` unless `ENABLE_GIT` is on and `GHDAG_VCS_CONFIG` is set; `ValueError` when the config has no `sinks.<name>` |

### Web UI

`ghdag ui` serves one page plus a JSON / SSE API over `<repo-root>/jobs/`. The page takes its strings and state labels from the active language pack.

| Endpoint | Response |
|---|---|
| `GET /` | The dashboard page |
| `GET /api/rows` | Task rows, newest first (`?max_visible=N`, default 30). Each row has `uuid`, `state_id`, `state` (label), `cmd_preview`, `tree_ts`, `engine_model`, `order_path`, `result_path`, and `progress` when events exist. `?state=<name>` filters by state (repeatable or comma-separated) |
| `GET /api/stream` | SSE stream of the same rows, sent when they change |
| `GET /api/config` | `{"github_base_url", "i18n": {"ui", "state_labels"}}` |
| `GET /api/dashboard/status` | Task status counts from `audit.jsonl` (`?since=<sec>`, default `86400`) |
| `GET /api/dashboard/tokens` | Token usage (`?since=<sec>`, `?threshold=N`; default `GHDAG_TOKEN_WARN_THRESHOLD`) |
| `GET /api/dashboard/cb-firing` | Circuit breaker firings (`?since=<sec>`, `?window=<minutes>`) |
| `GET /api/correlation-bursts` | The busiest correlation IDs (`?hours=1`, `?top_n=20`) |
| `POST /api/retry` | Body `{"uuid": "..."}`; deletes the done marker so the runner runs the task again (`404` when there is none) |
| `POST /api/stop` | Body `{"uuid": "..."}`; writes the cancel marker of a running task (`404` otherwise) |

The two `POST` endpoints require `Content-Type: application/json` and reject cross-site requests (`Origin` must match the `Host`; `Sec-Fetch-Site` is checked when `Origin` is absent). No endpoint sends CORS headers.

### Module inventory

Every Python module under `src/ghdag/`.

| Module | Role |
|---|---|
| `__init__.py` | `GhdagError` and `__version__` |
| `__main__.py` | `python -m ghdag` entry point |
| `github_cli.py` | `gh`-style CLI over `get_forge()` |
| `github_client.py` | `GitHubClient`: REST / GraphQL client with ETag cache, pagination and rate-limit handling |
| `maintenance.py` | Validation and repair of `exec.jsonl` and done markers |
| `quota.py` | `QuotaGate`: engine pauses, drain mode, per-engine limits, launch admission |
| `status.py` | `issue_status`, `running_tasks`, `read_exclusive_gate` and their dataclasses |
| `audit/__init__.py` | Package marker |
| `audit/span.py` | `emit_span` / `make_span_id` latency span writer |
| `cleanup/__init__.py` | `CleanupResult`, `file_timestamp` |
| `cleanup/archiver.py` | `QueueArchiver`: moves order / result / stderr files to `<archive>/YYYY-MM/` |
| `cleanup/link_rewriter.py` | `LinkRewriter`: rewrites wiki links in queue files to archived paths |
| `cleanup/orchestrator.py` | `cleanup_queue`: the cleanup phases and the catch-all sweep |
| `cleanup/orphan_detector.py` | `OrphanDetector`: finds orphaned queue tasks |
| `cleanup/pruner.py` | `ExecJsonlPruner`: removes archived task lines from `exec.jsonl` |
| `cli/__init__.py` | Exports `main` |
| `cli/main.py` | Top-level argparse tree, `--json` on leaf commands and `GhdagError` reporting |
| `cli/commands/__init__.py` | Package marker |
| `cli/commands/audit_query.py` | `ghdag audit-query` |
| `cli/commands/cancel.py` | `ghdag dag cancel` |
| `cli/commands/cleanup.py` | `ghdag cleanup` |
| `cli/commands/llm.py` | `ghdag llm` |
| `cli/commands/mcp.py` | `ghdag mcp` |
| `cli/commands/quota.py` | `ghdag quota` subcommands |
| `cli/commands/recover.py` | `ghdag dag recover` |
| `cli/commands/run.py` | `ghdag run` and `--hooks` loading |
| `cli/commands/schema_show.py` | `ghdag schema list` / `ghdag schema show` |
| `cli/commands/status.py` | `ghdag status` |
| `cli/commands/trigger.py` | `ghdag trigger` |
| `cli/commands/ui.py` | `ghdag ui` |
| `cli/commands/validate.py` | `ghdag validate` |
| `cli/commands/watch.py` | `ghdag watch` |
| `config/__init__.py` | Exports `env` |
| `config/env.py` | Environment variable accessors and runtime path resolution |
| `config/language.py` | Language packs: state labels, UI strings, question suffixes |
| `core/__init__.py` | Primitives shared by intake and execution |
| `core/capabilities.py` | `Permission` and the `PERMISSIONS` presets |
| `core/command.py` | Engine command lines (`render_exec_command`, `build_llm_cmd`) and command adapters (`AdapterNotFoundError`) |
| `core/container.py` | `container_prefix`: the `docker run` prefix of the container sandbox |
| `core/engine_spec.py` | `EngineSpec`, `InputMode`, `PromptFlag`, `ENGINE_SPECS` |
| `core/exceptions.py` | `GhdagError` and the GitHub API exception hierarchy |
| `core/parsers.py` | Token usage parsing helpers |
| `core/reply_block.py` | Reply-block parsing (`ReplyBlockError`) |
| `core/stall_guard.py` | Stall detection for cursor tasks (`StallTracker`) |
| `core/vocabulary.py` | Done markers, queue file naming, reply-block and fan-out conventions |
| `core/models/__init__.py` | Package marker |
| `core/models/dag.py` | `Task`, `DagConfig` |
| `core/models/files.py` | Markdown file models and `PathTraversalError` |
| `core/models/metrics.py` | `TaskMetrics`, `TokenUsage`, `FailureClass`, `QuotaWindow` |
| `core/models/workflow.py` | Workflow YAML dataclasses |
| `core/ports/__init__.py` | Exports `ForgePort` |
| `core/ports/dag_hooks.py` | `DagHooks` Protocol |
| `core/ports/forge.py` | `ForgePort` Protocol |
| `core/ports/order.py` | `OrderBuilder` Protocol |
| `core/ports/output.py` | `EngineOutputAdapter` Protocol, `EngineError`, `EngineErrorKind` |
| `core/ports/quota.py` | `QuotaAdmissionPort` and `QuotaCallPort` Protocols |
| `dag/__init__.py` | Runner public API |
| `dag/_nodes.py` | `NodeSpec`, `load_nodes`, `pick_node`: remote SSH execution node definitions from YAML |
| `dag/_process_host.py` | `LocalProcessHost`: local subprocess launch and process-group control |
| `dag/_util.py` | Internal runner helpers |
| `dag/audit_hooks.py` | `AuditHooks`: `DefaultHooks` plus `audit.jsonl` writes |
| `dag/circuit_breaker.py` | `CircuitBreakerPolicy`: consecutive-failure breaker |
| `dag/completion.py` | `decide_completion`: the pure decision of how a finished task is closed |
| `dag/engine.py` | `DagEngine`: main loop, signal handling, dependency resolution |
| `dag/engine_quarantine.py` | `EngineQuarantine`: temporary launch block for broken engines |
| `dag/fanout.py` | Fan-out spec parsing and child record building (`FanoutError`) |
| `dag/fanout_manager.py` | `FanOutManager`: child generation and join |
| `dag/hooks.py` | `DefaultHooks` and the `DagHooks` re-export |
| `dag/models.py` | `Task` / `DagConfig` re-export and the internal process record |
| `dag/parser.py` | `parse_jsonl` for `exec.jsonl` |
| `dag/recover.py` | `plan_recover` / `execute_recover` (`RecoverError`) |
| `dag/task_launcher.py` | `TaskLauncher`: process launch, streaming, completion, orphan adoption |
| `files/__init__.py` | Markdown file operations API |
| `files/append.py` | `md_append`: idempotent section append (`AppendRecoverError`) |
| `files/promote.py` | `md_promote` |
| `files/reader.py` | `md_read` |
| `files/writer.py` | `md_write` |
| `files/links/__init__.py` | Wiki-link helper exports |
| `files/links/obsidian.py` | Obsidian wiki-link helpers |
| `forge/__init__.py` | `get_forge` backend factory |
| `forge/local.py` | `LocalForge`: file-backed forge |
| `io/__init__.py` | Filesystem I/O facade |
| `io/_rotate.py` | Size-based `audit.jsonl` rotation |
| `io/audit.py` | `audit.jsonl` writers |
| `io/audit_query.py` | Read-only `audit.jsonl` queries |
| `io/done.py` | Done-marker I/O and `done_failure_class` |
| `io/exec_jsonl.py` | `exec.jsonl` read / append / validate / repair (`DuplicateTaskError`) |
| `io/queue.py` | `QueueTask`, `QueueTaskStore`: queue directory scan |
| `io/redact.py` | Secret redaction for audit records and task events |
| `io/sessions.py` | Per-task session ID store |
| `llm/__init__.py` | One-shot engine call API |
| `llm/_config.py` | `llm-models.yml` loading (`ConfigLoadError`) |
| `llm/_constants.py` | `DEFAULT_ENGINE_MODELS` |
| `llm/compaction.py` | Resume-session handoff summary compaction (JSON output mode) |
| `llm/engines.py` | `call`, `LLMResult`, the model allow-list, `supports_capability` (`EngineModelError`) |
| `llm/json_extract.py` | JSON extraction from engine stdout |
| `llm/session.py` | `SessionRecord`, `SessionStore` |
| `llm/adapters/__init__.py` | `get_output_adapter` |
| `llm/adapters/claude_json.py` | claude JSON / stream-json output adapter |
| `llm/adapters/codex.py` | codex `--json` output adapter |
| `llm/adapters/codex_jsonl.py` | codex JSONL stream helpers |
| `llm/adapters/cursor_stream.py` | cursor stream-json adapter and `RetriableError:` classification |
| `llm/adapters/failure_classification.py` | Shared failure classification (`classify_common_failure`, `looks_like_question`) |
| `mcp/__init__.py` | Package marker |
| `mcp/server.py` | JSON-RPC 2.0 stdio server |
| `mcp/tools.py` | `ghdag_enqueue` / `ghdag_status` / `ghdag_read` handlers |
| `metrics/__init__.py` | Metrics exports |
| `metrics/parsers.py` | Engine / model detection and token count parsing |
| `metrics/recorder.py` | `MetricsRecorder`: locked JSONL append |
| `pipeline/__init__.py` | Intake public API |
| `pipeline/config.py` | `PipelineConfig`, `resolve_models` (`ModelValidationError`) |
| `pipeline/llm_pipeline.py` | `build_order_builders`, `validate_depends` (`DependencyError`) |
| `pipeline/order.py` | Order builders (`TemplateVariableError`) |
| `pipeline/state.py` | Idempotency key and front matter helpers |
| `pipeline/status.py` | Task state identifiers (`task_status`, `STATE_*`) and `state_label` |
| `pipeline/task_queue.py` | `TaskQueue` |
| `pipeline/wait.py` | `wait_for_result`: done-marker polling |
| `schemas/__init__.py` | Bundled JSON Schema loader |
| `tool/__init__.py` | Tool definition API |
| `tool/cli.py` | `ghdag tools list` handler |
| `tool/exceptions.py` | `ToolRegistryError` |
| `tool/registry.py` | `ToolRegistry`: tool definition discovery |
| `tool/schema.py` | `ToolDef`, `FallbackEntry`, `TOOL_EXIT_CODES` |
| `ui/__init__.py` | Package marker |
| `ui/dashboard.py` | `audit.jsonl` aggregation for the dashboard |
| `ui/monitor.py` | Display rows built from queue files |
| `ui/server.py` | HTTP + SSE server (`run_server`) |
| `vcs/__init__.py` | Git sink public API |
| `vcs/factory.py` | `get_sink` / `git_enabled` |
| `vcs/local.py` | `LocalGitSink.create`: throwaway remote plus clone |
| `vcs/sink.py` | `GitSink`, `NullSink`, `CommitResult` (`OwnershipError`, `ConflictError`) |
| `workflow/__init__.py` | Workflow public API |
| `workflow/conditional_step.py` | Conditional engine step helper (`python -m`) |
| `workflow/dispatcher.py` | `WorkflowDispatcher`: polling and dispatch (`ContextHookError`) |
| `workflow/loader.py` | Workflow YAML loading and validation (`ValidationError`) |
| `workflow/render.py` | `render: live` template runner (`python -m`) |
| `workflow/state_machine.py` | Label transition state machine (`python -m`) |
| `workflow/typecheck.py` | Static skill I/O type check across steps |

## Configuration

### Environment variables

Every variable ghdag reads. Explicit CLI options and API arguments win over these.

| Variable | Default | Read by | Meaning |
|---|---|---|---|
| `GITHUB_TOKEN` | none | `config.env.github_token` | GitHub token; required by the GitHub forge |
| `GH_TOKEN` | none | `config.env.github_token` | Fallback when `GITHUB_TOKEN` is unset |
| `GITHUB_REPOSITORIES` | `""` | `config.env.github_repositories_raw` | Comma-separated `owner/repo` list; the first entry is the default repository, and `ghdag watch` polls all of them |
| `GHDAG_FORGE` | `github` | `forge.get_forge` | Forge backend: `github` or `local` |
| `GHDAG_FORGE_ROOT` | none | `forge.get_forge` | Data root of `LocalForge`; required when `GHDAG_FORGE=local` |
| `GHDAG_ETAG_CACHE` | unset (in-memory only) | `github_client.GitHubClient` | Directory of the persistent ETag cache |
| `GHDAG_RATE_LIMIT_MAX_WAIT_SEC` | `900` | `github_client.GitHubClient` | Longest rate-limit sleep before `RateLimitError`; non-integer values fall back to `900`. `ghdag watch` always uses `0` |
| `GHDAG_STATE_DIR` | unset | `config.env.ghdag_state_dir` | Directory for runtime state (`done/`, `running/`, `cancel/`, `events/`, `.sessions/`, `quota-gate.json`, `.pipeline-state/`); `exec.jsonl` and `audit.jsonl` stay in the queue directory |
| `GHDAG_EXEC_JSONL` | `jobs/exec.jsonl` | `cli.commands.status` | Default `--exec-jsonl` of `ghdag status` |
| `GHDAG_AUDIT_PATH` | Web UI and `ghdag audit-query`: `jobs/audit.jsonl`; `ghdag llm`: unset (no audit) | `config.env.ghdag_audit_path` | Audit log for the Web UI, `ghdag audit-query`, `ghdag llm` and git sinks |
| `GHDAG_SAFE_DEFAULT_PERMISSION` | `text_only` | `config.env.ghdag_safe_default_permission` | Permission preset for workflow steps without `permission`; unknown names raise `ValueError` |
| `GHDAG_LLM_MODELS` | unset | `config.env.ghdag_llm_models` | Path to [`llm-models.yml`](#llm-modelsyml) |
| `GHDAG_ENGINE_ISOLATION` | off | `config.env.engine_isolation_enabled` | `1` / `true` / `yes` / `on` keeps engines from loading user-level extensions: claude gets `--disable-slash-commands`, codex gets `-c skills.include_instructions=false`, and codex calls made through `ghdag.llm.call` use `CODEX_HOME=/var/tmp/ghdag-dag-codex/` when that directory has an `auth.json`. cursor has no such flag. An explicit `isolation=` argument wins |
| `GHDAG_SESSION_COMPACTION` | off | `config.env.session_compaction_enabled` | `1` / `true` / `yes` / `on` compacts a long session into a handoff summary before it is resumed |
| `GHDAG_ENGINE_STALL_GUARD` | `1` (on) | `config.env.stall_guard_enabled` | `0` disables the cursor stall guard |
| `GHDAG_ENGINE_STALL_SEC` | `180` | `config.env.stall_guard_stall_sec` | Seconds a stalled child must persist before SIGTERM |
| `GHDAG_ENGINE_STALL_INTERVAL_SEC` | `30` | `config.env.stall_guard_interval_sec` | Minimum seconds between stall scans |
| `GHDAG_QUOTA_DEFAULT_PAUSE_SECONDS` | `18000` | `llm.adapters.failure_classification` | Pause length when an engine reports quota exhaustion without a reset time; read once at import |
| `GHDAG_TASK_ENV_ALLOW` | unset | `config.env.ghdag_task_env_allow` | Comma-separated `fnmatch` patterns; when set, only matching host variables reach tasks |
| `GHDAG_TASK_ENV_DENY` | unset | `config.env.ghdag_task_env_deny` | Comma-separated `fnmatch` patterns; matching host variables are removed from the task environment |
| `GHDAG_REDACT_PATTERNS` | unset (built-in patterns) | `config.env.ghdag_redact_patterns_path` | File of extra secret patterns: `name<TAB>regex`, or one regex per line |
| `GHDAG_LANGUAGE_PACK` | unset (bundled English) | `config.env.ghdag_language_pack` | Path to a [language pack](#language-pack-ghdag_language_pack) YAML; an invalid pack raises `GhdagError` |
| `GHDAG_TOKEN_WARN_THRESHOLD` | `500000` | `config.env.ghdag_token_warn_threshold` | Token usage warning threshold on the Web UI dashboard |
| `ENABLE_GIT` | off | `config.env.enable_git` | `1` / `true` / `yes` lets `get_sink` return a real `GitSink` |
| `GHDAG_VCS_CONFIG` | none | `config.env.ghdag_vcs_config` | Path to the [VCS sink config](#vcs-sink-config-ghdag_vcs_config) |
| `LATENCY_SPAN_PATH` | unset (`jobs/latency_span.jsonl`) | `config.env.latency_span_path` | Output file of `emit_span` when `log_path` is omitted |

### Task environment

The runner builds each task's environment from its own, then filters it: with `GHDAG_TASK_ENV_ALLOW` only matching variables are kept, otherwise variables matching `GHDAG_TASK_ENV_DENY` are dropped (allow wins when both are set). A workflow step can exempt names from the deny list with `env_passthrough`; `TaskQueue.enqueue` copies the list to `annotations.env_passthrough` on the task line. Every `GHDAG_*` variable is always kept, and the number of dropped variables is recorded on the `task_started` audit event.

The runner adds these variables:

| Variable | Set for | Value |
|---|---|---|
| `GHDAG_TASK_UUID` | Every task | The task UUID |
| `GHDAG_RESULT_PATH` | Every task | The task's `result_path`, or `""` |
| `GHDAG_PREVIOUS_ATTEMPT` | Tasks relaunched after an interrupted run | `interrupted` |

Secrets are redacted before anything is written to `audit.jsonl`, `events/<uuid>.jsonl` or the stderr artifacts. The built-in patterns cover GitHub tokens, Slack tokens and common API key shapes; `GHDAG_REDACT_PATTERNS` adds more. Matches become `[REDACTED:<name>]`.

### Workflow YAML

One file per workflow. `ghdag validate <dir>` and `load_workflows(dir)` check every file and raise `ValidationError` on the first problem. The bundled schema `workflow-config` describes the document.

| Object | Key | Type | Required | Default | Meaning |
|---|---|---|---|---|---|
| workflow | `name` | string | yes | | Workflow name |
| | `triggers` | list | yes | | Label -> handler pairs; earlier entries take precedence |
| | `handlers` | map | yes | | Handler definitions by name |
| | `polling_interval` | int | no | `30` | Seconds between polls |
| | `template_dir` | string | no | `templates` | Order template directory, relative to the workflow file |
| | `label_namespace` | string | no | none | Label prefix |
| | `transitions` | map of lists | no | none | Allowed label transitions |
| | `reset_label` | string | no | none | Label reachable from any state |
| | `nonterminal_closed` | map | no | none | What to do with closed Issues that have no terminal label |
| `nonterminal_closed` | `action` | string | yes | | `reopen` or `trigger` |
| | `terminal_labels` | list | yes | | Closed Issues with any of these labels are ignored |
| | `trigger` | string | with `action: trigger` | none | Trigger label to fire |
| trigger | `label` | string | yes | | Issue label to match |
| | `handler` | string | yes | | Key in `handlers` |
| handler | `steps` | list | yes | | Steps to enqueue |
| | `on_trigger` | map | no | none | `issue_context: true` writes the Issue body and comments to `design.md` |
| | `type` | string | no | none | Special handler type (for example `reset`) |
| | `context_hook` | string | no | none | Shell command run before enqueue. Its stdout must be a JSON object (schema `context-hook-output`) and is merged into the template variables; empty stdout means no extra context. A non-zero exit raises `ContextHookError` and nothing is enqueued |
| step | `template` | string | yes | | Template name without `.md`; the file must exist |
| | `model` | string | except for `shell` | none | Model ID |
| | `id` | string | no | none | Step ID, referenced by `depends` and `resume_from` |
| | `engine` | string | no | `claude` | Engine name |
| | `depends` | list | no | `[]` | IDs of the steps this one waits for |
| | `resume_from` | string | no | none | Step whose engine session to resume (same engine required) |
| | `permission` | string | no | `GHDAG_SAFE_DEFAULT_PERMISSION` | Permission preset name |
| | `skill_name` | string | no | none | Skill the step invokes (used by the static I/O type check) |
| | `render` | string | no | `frozen` | `frozen` expands the template at enqueue; `live` expands it when the task runs |
| | `env_passthrough` | list | no | `[]` | Host variables exempt from `GHDAG_TASK_ENV_DENY` |

Templates use `${name}` variables. `${result_filename}` is the step's own result file name, and `${<id>_result_filename}` is that of a step listed in `depends`; read the upstream result from `jobs/${<id>_result_filename}` at run time. An undefined variable raises `TemplateVariableError`.

Step validation errors carry these codes:

| Code | Raised when |
|---|---|
| `STEP_ENGINE_UNKNOWN` | `engine` is not a known engine |
| `STEP_MODEL_REQUIRED` | `model` is missing on a non-`shell` step |
| `STEP_PERMISSION_UNKNOWN` | `permission` is not a preset name |
| `STEP_DEPENDENCY_UNKNOWN` | `depends` names an unknown step |
| `STEP_DEPENDENCY_CYCLE` | `depends` has a cycle |
| `STEP_RESUME_FROM_INVALID` | `resume_from` is unknown or uses a different engine |

### exec.jsonl task fields

One JSON object per line (schema `exec-record`). When a UUID appears on several lines, the last line wins.

| Field | Type | Required | Default | Meaning |
|---|---|---|---|---|
| `uuid` | string | yes | | Task ID |
| `command` | string | yes | | Shell command |
| `depends` | list | no | `[]` | Upstream task UUIDs |
| `retry` | int | no | `0` | Retry depth |
| `annotations` | map of strings | no | `{}` | Metadata: `timeout_sec` (overrides the default task timeout), `step_name`, `env_passthrough`, `_exclusive` (`"true"`), ... |
| `result_path` | string | no | none | Result file |
| `idempotency_key` | string | no | none | De-duplication key across enqueues |
| `engine` | string | no | none | Engine name (selects the output adapter and the quota bucket) |
| `model` | string | no | none | Model ID |
| `result_finalize` | string | no | none | `preserve_nonempty` or `stdout_only` |

### `DagConfig`

The configuration of `DagEngine`. `<queue dir>` is the directory that contains `exec_jsonl_path`.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `exec_jsonl_path` | `str \| Path` | required | Queue file |
| `exec_done_dir` | `str \| Path` | `$GHDAG_STATE_DIR/done` or `<queue dir>/done` | Done-marker directory; its parent also holds the other runtime directories |
| `poll_interval` | `float` | `1.0` | Poll interval (seconds) |
| `launch_stagger` | `float` | `0.5` | Delay between launches (seconds) |
| `max_retry` | `int` | `1` | Engine-error retry budget |
| `max_repair_turns` | `int` | `1` | Repair turns per task; `0` disables them |
| `max_concurrency` | `int \| None` | `None` | Concurrent task cap |
| `engine_limits` | `dict[str, int]` | `{}` | Concurrent task cap per engine |
| `serialize_mutating` | `bool` | `False` | Run mutating tasks one at a time |
| `task_timeout` | `float \| None` | `None` | Default task timeout, and how long a shutdown waits for running tasks |
| `kill_grace` | `float` | `10.0` | SIGTERM -> SIGKILL grace (seconds) |
| `max_consecutive_failures` | `int` | `5` | Circuit breaker threshold |
| `failure_window_sec` | `float` | `60.0` | Circuit breaker window (seconds) |
| `cwd` | `str \| Path \| None` | `None` | Task working directory |
| `timezone` | `str \| None` | `None` | IANA time zone of file name and audit timestamps; `None` = machine local time |
| `lock_file` | `str \| Path \| None` | `<queue dir>/.ghdag.lock` | Single-runner lock |
| `quota_state_path` | `str \| Path \| None` | `$GHDAG_STATE_DIR/quota-gate.json` or `<queue dir>/quota-gate.json` | Quota state file |
| `quota_pause_ttl_seconds` | `int \| None` | `3600` | How long a pause without a resume time lasts |
| `quota_audit_path` | `str \| Path \| None` | `<queue dir>/audit.jsonl` | Quota audit file |
| `audit_path` | `Path \| None` | `<queue dir>/audit.jsonl` | Task audit file |

### `llm-models.yml`

Replaces the built-in engine -> allowed-model lists. The file is looked up in this order: an explicit path argument, `GHDAG_LLM_MODELS`, `./llm-models.yml`, then the built-in lists.

```yaml
engines:
  claude:
    - claude-sonnet-4-6
  codex:
    - gpt-5.5
  cursor:
    - auto
```

The top-level `engines` key is required and every value must be a list of strings (`ConfigLoadError` otherwise). `ghdag llm --list-models` and `get_engine_models()` show the effective lists.

### VCS sink config (`GHDAG_VCS_CONFIG`)

Read by `get_sink(name)` when `ENABLE_GIT` is on. Each `sinks.<name>` mapping becomes the arguments of one `GitSink`.

```yaml
audit_path: jobs/audit.jsonl        # optional; defaults to GHDAG_AUDIT_PATH
sinks:
  notes:
    repo_root: /path/to/repo
    branch: main
    owner: host
    allow_prefixes: ["notes/"]
    push: "debounce:60"             # immediate | debounce:<sec> | manual
```

| Key | Required | Default | Meaning |
|---|---|---|---|
| `repo_root` | yes | | Repository working tree |
| `branch` | yes | | Branch to rebase onto and push |
| `owner` | yes | | Required commit subject prefix `<owner>(` |
| `allow_prefixes` | yes | | Path prefixes the sink may commit |
| `remote` | no | `origin` | Remote name |
| `push` | no | `immediate` | Push policy |
| `layer` | no | `owner` | Value of the `Layer:` trailer |

### Quota state (`quota-gate.json`)

Written by `QuotaGate` and by `ghdag quota`. Top-level keys: `engines`, `draining_engines`, `running_tasks`, and optionally `limits` and `usage`.

An engine is unavailable while it is draining or while any active pause source says so. Each `engines.<name>` entry keeps a `sources` list; a source has a `kind` (`budget`, `failure`, `manual` or `drain`), `reason`, `until`, `set_by`, `observed_at` and `available`. `QuotaGate.report(kind=...)` adds or updates the source of that kind. `ghdag quota report` always records a `failure` source.

`usage.<engine>` holds the latest utilization windows an engine reported (`window_minutes`, `used_percent`, `resets_at`); today only claude reports them. They are informational and never pause an engine. `ghdag quota status` prints them together with per-engine `queued` / `running` / `idle` counts.

### Language pack (`GHDAG_LANGUAGE_PACK`)

State labels, Web UI strings and extra question suffixes come from a language pack. ghdag ships English only; point `GHDAG_LANGUAGE_PACK` at a YAML file to replace it. CLI, log and exception messages are not part of the pack.

```yaml
state_labels:
  pending_deps: "Pending (waiting on deps)"
  pending_run: "Pending (ready)"
  running: "Running"
  deferred: "Deferred"
  ok: "Done (success)"
  fail: "Done (failed)"
  rejected: "Done (REJECTED)"
  empty: "Done (EMPTY_RESULT)"
  engine_error: "Done (ENGINE_ERROR)"
  unknown: "Done (other)"
ui:
  page_size_option: "{n} rows"
  tab_tasks: "Tasks"
  tab_bursts: "Bursts"
  col_time_tree: "Time / Tree"
  col_state: "State"
  col_engine_model: "Engine / Model"
  col_label: "Label"
  empty_tasks: "No tasks"
  col_count: "Count"
  col_last_seen: "Last seen"
  empty_bursts: "No burst data"
  confirm_stop: "Force-stop the agent process?"
question_suffixes:          # optional
  extra: []
```

`state_labels` and `ui` must contain exactly the keys shown, with non-empty string values. `question_suffixes.extra` lists line endings that, like `?`, mark engine output as a question. Any violation, or an unreadable file, raises `GhdagError`.

### Schemas

Bundled JSON Schemas describe the file formats. `ghdag schema list` prints the names and `ghdag schema show <name>` prints one schema.

| Name | Describes |
|---|---|
| `workflow-config` | A workflow YAML document |
| `exec-record` | One `exec.jsonl` task line |
| `done-marker` | The body of `done/<uuid>` |
| `reply-block` | The reply block at the end of an engine result |
| `issue-status` | The output of `ghdag status --issue ... --json` |
| `context-hook-output` | The stdout of a handler `context_hook` |

## Error Reference

Every exception type defined in ghdag. `except GhdagError` catches all of the first table. Each type has a default `code` and `exit_code`; `GhdagError.to_dict()` returns `code`, `message`, `hint` and `exit_code`.

| Exception | Parent(s) | Code | Exit | Defined in | Raised when |
|---|---|---|---|---|---|
| `GhdagError` | `Exception` | `GHDAG_ERROR` | 1 | `ghdag.core.exceptions` (also `ghdag`) | Base class; also raised directly, for example for an invalid language pack or a missing default repository |
| `ConfigError` | `GhdagError`, `ValueError` | `CONFIG_INVALID` | 3 | `ghdag.core.exceptions` | Invalid configuration or input |
| `CliUsageError` | `GhdagError` | `CLI_USAGE` | 2 | `ghdag.core.exceptions` | Invalid CLI argument combination |
| `GitHubApiError` | `GhdagError` | `GITHUB_API_ERROR` | 4 | `ghdag.core.exceptions` (also `ghdag.forge`) | A GitHub API call failed; has `status_code` |
| `AuthError` | `GitHubApiError` | `GITHUB_AUTH` | 4 | `ghdag.core.exceptions` | 401, or no token configured |
| `RateLimitError` | `GitHubApiError` | `GITHUB_RATE_LIMIT` | 4 | `ghdag.core.exceptions` | Rate limit exhausted; has `reset_at` (epoch seconds) |
| `PermissionDeniedError` | `GitHubApiError` | `GITHUB_PERMISSION_DENIED` | 4 | `ghdag.core.exceptions` | 403 / 404 on a private resource |
| `NetworkError` | `GitHubApiError` | `GITHUB_NETWORK` | 4 | `ghdag.core.exceptions` | Timeout, DNS or connection failure |
| `LLMParseError` | `GhdagError` | `LLM_OUTPUT_NOT_JSON` | 3 | `ghdag.core.exceptions` | Engine output does not match `output_format`; has `raw`, `reason` |
| `ValidationError` | `ConfigError` | `WORKFLOW_INVALID` | 3 | `ghdag.workflow.loader` | Invalid workflow YAML |
| `ContextHookError` | `ConfigError` | `CONTEXT_HOOK_FAILED` | 3 | `ghdag.workflow.dispatcher` | A `context_hook` exited non-zero or printed invalid JSON |
| `TemplateVariableError` | `ConfigError`, `KeyError` | `TEMPLATE_UNDEFINED_VARIABLE` | 3 | `ghdag.pipeline.order` (also `ghdag.pipeline`) | A template variable is undefined |
| `DependencyError` | `ConfigError` | `STEP_DEPENDENCY_INVALID` | 3 | `ghdag.pipeline.llm_pipeline` | Invalid step dependencies |
| `ModelValidationError` | `GhdagError` | `STEP_MODEL_INVALID` | 3 | `ghdag.pipeline.config` | Invalid model configuration |
| `DuplicateTaskError` | `GhdagError`, `ValueError` | `EXEC_DUPLICATE_TASK` | 3 | `ghdag.io.exec_jsonl` | An append would duplicate an active task UUID or idempotency key |
| `EngineModelError` | `ConfigError` | `ENGINE_MODEL_INVALID` | 3 | `ghdag.llm.engines` (also `ghdag.llm`) | Unknown engine, or model not in the allow-list |
| `ConfigLoadError` | `GhdagError`, `ValueError` | `LLM_MODELS_CONFIG_INVALID` | 3 | `ghdag.llm._config` | Invalid `llm-models.yml` |
| `AdapterNotFoundError` | `GhdagError`, `ValueError` | `ADAPTER_NOT_FOUND` | 3 | `ghdag.core.command` | No command adapter for an engine name |
| `ReplyBlockError` | `ConfigError` | `REPLY_BLOCK_INVALID` | 3 | `ghdag.core.reply_block` | Invalid reply block in an engine result |
| `FanoutError` | `ConfigError` | `FANOUT_INVALID` | 3 | `ghdag.dag.fanout` | Invalid fan-out specification |
| `RecoverError` | `GhdagError` | `RECOVER_NOT_POSSIBLE` | 1 | `ghdag.dag.recover` | A recover plan, or its execution, is not possible |
| `ToolRegistryError` | `GhdagError` | `TOOL_REGISTRY_INVALID` | 3 | `ghdag.tool.exceptions` | Tool definition naming violation or duplicate |
| `PathTraversalError` | `GhdagError`, `ValueError` | `PATH_TRAVERSAL` | 3 | `ghdag.core.models.files` | A Markdown path escapes the repository root |
| `AppendRecoverError` | `GhdagError`, `ValueError` | `APPEND_RECOVER_FAILED` | 1 | `ghdag.files.append` | `md_append` cannot recover a partial append |

Two exceptions of the git sinks are outside the `GhdagError` hierarchy:

| Exception | Parent(s) | Defined in | Raised when |
|---|---|---|---|
| `OwnershipError` | `ValueError` | `ghdag.vcs.sink` (also `ghdag.vcs`) | A sink path is absolute, contains `..` or is outside `allow_prefixes`, no paths were given, or the commit subject does not start with `<owner>(` |
| `ConflictError` | `RuntimeError` | `ghdag.vcs.sink` (also `ghdag.vcs`) | The rebase onto the remote conflicted; the local versions were saved under `inbox/` (`inbox_paths`) |

The public API also raises standard exceptions: `ValueError` (unknown permission preset, invalid container settings, undefined VCS sink, invalid push policy), `NotImplementedError` (a capability the engine does not support) and `RuntimeError` (a git command inside a sink failed).

### CLI exit codes

| Exit code | Meaning |
|---|---|
| 0 | Success |
| 1 | Operational failure (for example `RecoverError`, a trigger that dispatched nothing, a burst found by `audit-query --burst-detect`) |
| 2 | CLI usage (`CliUsageError`, argparse errors) |
| 3 | Configuration or input validation (`ConfigError` and its subclasses) |
| 4 | GitHub API errors (`GitHubApiError` and its subclasses) |

`ghdag llm` is the exception: it exits with the engine's own code (see [Behavior notes](#behavior-notes)).

Some errors use a base exception type with a more specific `code`:

| Code | Exception | Exit | Raised when |
|---|---|---|---|
| `FORGE_UNKNOWN` | `ConfigError` | 3 | `GHDAG_FORGE` is neither `github` nor `local` |
| `FORGE_ROOT_MISSING` | `ConfigError` | 3 | `GHDAG_FORGE=local` without `GHDAG_FORGE_ROOT` |
| `SCHEMA_UNKNOWN` | `ConfigError` | 3 | `ghdag schema show` with an unknown name |
| `MCP_ARGUMENT_INVALID` | `ConfigError` | 3 | An MCP `tools/call` with unknown keys, invalid types or conflicting shapes; a `ghdag_read` path that `--read-path` does not allow |
| `WORKFLOWS_DIR_NOT_FOUND` | `CliUsageError` | 2 | `ghdag validate`, `ghdag watch` or `ghdag trigger` when the workflows directory does not exist |
| `WORKFLOW_NONE_FOUND` | `ConfigError` | 3 | `ghdag trigger` when the workflows directory has no workflow YAML files |
| `WORKFLOW_NOT_FOUND` | `CliUsageError` | 2 | `ghdag trigger --workflow` names a workflow that is not present |
| `WORKFLOW_AMBIGUOUS` | `CliUsageError` | 2 | `ghdag trigger` when more than one workflow matches and `--workflow` was omitted |
| `HANDLER_NOT_FOUND` | `CliUsageError` | 2 | `ghdag trigger --handler` names a handler that the workflow does not define |
| `STATUS_TARGET_REQUIRED` | `CliUsageError` | 2 | `ghdag status` without `--issue` or `--running` |
| `STATUS_HANDLER_WORKFLOW_REQUIRED` | `CliUsageError` | 2 | `ghdag status --issue` without both `--handler` and `--workflow` |
| `EXEC_JSONL_NOT_FOUND` | `GhdagError` | 1 | `ghdag run` when `exec.jsonl` does not exist |
| `HOOKS_IMPORT_FAILED` | `GhdagError` | 1 | `ghdag run --hooks` when a hook module cannot be imported |
| `HOOKS_CLASS_NOT_FOUND` | `GhdagError` | 1 | `ghdag run --hooks` when no suitable hook class is found in a module |
| `TASK_NOT_RUNNING` | `GhdagError` | 1 | `ghdag dag cancel` when the UUID is not in `running/` and not a waiting exclusive gate |
| `QUOTA_INVALID_INPUT` | `GhdagError` | 1 | `ghdag quota *` when a datetime or engine name argument is invalid |
| `LLM_PROMPT_REQUIRED` | `CliUsageError` | 2 | `ghdag llm` with no prompt argument, and stdin is a terminal or empty |
| `AUDIT_QUERY_MODE_REQUIRED` | `CliUsageError` | 2 | `ghdag audit-query` without `--correlation-id` or `--burst-detect` |
| `AUDIT_QUERY_MODE_CONFLICT` | `CliUsageError` | 2 | `ghdag audit-query` with both `--correlation-id` and `--burst-detect` |
| `AUDIT_QUERY_INVALID_SINCE` | `CliUsageError` | 2 | `ghdag audit-query --correlation-id` with an unparseable `--since` value |

The `STEP_*` codes of workflow validation are listed under [Workflow YAML](#workflow-yaml).

### Related types that are not exceptions

| Type | Defined in | Meaning |
|---|---|---|
| `FailureClass` | `ghdag.core.models.metrics` (also `ghdag.metrics`) | Enum of task failure classes: `TIMEOUT`, `REJECTED`, `ENGINE_ERROR`, `QUOTA_EXHAUSTED`, `AUTH`, `ENGINE_ENVIRONMENT_ERROR`, `INTERACTIVE_PROMPT`, `PROCESS_ERROR`, `PIPELINE_FAILED`, `EMPTY_RESULT`, `FANOUT_CHILD_FAILED`, `FANOUT_PARSE_FAILED`, `DEP_FAILED`, `UNKNOWN_FAILURE` |
| `EngineError` | `ghdag.core.ports.output` (also `ghdag.llm`) | Dataclass returned by `EngineOutputAdapter.extract_error` (`kind`, `message`, `retryable`, `resume_at`) |
| `EngineErrorKind` | `ghdag.core.ports.output` | Enum: `CAPACITY`, `RATE_LIMIT`, `AUTH`, `QUOTA_EXHAUSTED`, `UNKNOWN` |
| `QuotaWindow` | `ghdag.core.models.metrics` | One utilization window an engine reported (`window_minutes`, `used_percent`, `resets_at`) |
| `TypeCheckError` | `ghdag.workflow.typecheck` | Dataclass describing a skill I/O mismatch between steps |
| `WaitQuotaParseError` | `ghdag.dag._util` | Dataclass describing a `status: wait_quota` reply block that could not be parsed (or a missing one); the task is then treated as failed |
| `CommitResult` | `ghdag.vcs.sink` (also `ghdag.vcs`) | Outcome of a sink call (`committed`, `pushed`, `skipped`, `reason`, `sha`, `paths`) |

## Not in Scope

- Not a hosted CI service: ghdag runs where you start it and uses no hosted runners.
- Not an LLM SDK: engines are invoked through their own CLIs, and ghdag never calls a model HTTP API.
- Not a distributed scheduler: one runner per queue directory, coordinated through local files and `fcntl` locks.
- Not a general git client: `ghdag.vcs` commits only owned path prefixes and never resolves a conflict for you.
- No agents, personas or chat integrations: applications build those on top of the intake and execution primitives.

## API Stability

ghdag is pre-1.0 (`0.Y.Z`) and follows SemVer's rule for major version zero: anything may change.

- A `Y` release adds, changes or removes public symbols, CLI options or file formats. Removals and breaking changes are listed under "Removed", "Breaking" or "Changed" in the changelog.
- A `Z` release contains fixes, internal changes and documentation only.
- The public surface is what this README documents: the `__all__` of `ghdag` and of each `ghdag.<package>`, the CLI, the file formats and their bundled schemas, and the environment variables.

Pin an exact tag in production and read [CHANGELOG.md](./CHANGELOG.md) before upgrading.

## License

MIT License (SPDX: `MIT`), matching `pyproject.toml` `license = "MIT"`. See [`LICENSE`](LICENSE).
