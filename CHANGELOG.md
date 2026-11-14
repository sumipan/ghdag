# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).


## [Unreleased]

### Removed

- **Breaking:** `ghdag.llm.call` no longer accepts `engine`, `model`, `fallback_candidates`, or `fallback_on_timeout`. Pass an ordered `route: Sequence[tuple[str, str | None]]` instead (sumipan/nexus#6679).

### Changed

- **Breaking:** `call()` walks `route` from front to back, skipping paused engines when `quota_gate` is set, advancing on `QUOTA_EXHAUSTED`, `AUTH`, and `ENGINE_ENVIRONMENT_ERROR`, and stopping on other failure classes including `TIMEOUT` (sumipan/nexus#6679).
- README identity messaging, release badge alignment, LocalForge GitHub-free quick start, and execution review artifacts are unchanged since 0.133.4 (sumipan/nexus#6610).

## 0.134.4 - 2026-11-14

### Changed

- **Breaking:** Unknown arguments and unknown or missing subcommands on `python -m ghdag.github_cli` now fail with `CLI_USAGE` (exit code 2); the prior warning-and-continue path is removed.
- README module-entry messaging for strict `github_cli` usage; pending notes still cover LocalForge GitHub-free quick start and execution review artifacts (unchanged since 0.133.4).

### Added

- `pr create --body-file` (same file-read semantics as `issue create` / `issue edit`).

## 0.134.3 - 2026-11-14

### Fixed

- Defer relaunching a task that returned `WAIT_QUOTA` while every engine named in its `WAIT_QUOTA:` line remains paused, instead of restarting it on every poll by its own engine (sumipan/nexus#6632).

## 0.134.1 - 2026-11-14

### Changed

- Remove downstream host vocabulary from comments and docstrings across `src/ghdag`; apply the same convention test to every Python module without xfail or known-violation exceptions.

## 0.134.0 - 2026-11-14

### Removed

- **Breaking:** Remove `DEFAULT_REPO` from `ghdag.github_client` and `ghdag.forge`. Repository selection is unchanged: pass `repo="owner/repo"` to `GitHubClient` / `get_forge`, or set `GITHUB_REPOSITORIES` (comma-separated `owner/repo` list). When both are absent, `_resolve_repo()` and `GitHubClient` without `repo` still raise `GhdagError`.

| Before | After |
| --- | --- |
| `from ghdag.github_client import DEFAULT_REPO` | Set `GITHUB_REPOSITORIES` or pass `repo=` |
| `from ghdag.forge import DEFAULT_REPO` | Same |

## 0.133.4 - 2026-11-14

### Changed

- Align project messaging in `README.md`, `llms.txt`, and `[project].description` on work intake, dependency-ordered local execution, and file-based execution recording; remove legacy `graph-watcher` wording (sumipan/nexus#6610).
- Sync README release badge, Status table, and git-tag install example with `[project].version` without bumping the package version (sumipan/nexus#6610).
- Add a GitHub-free LocalForge Quick Start shell walkthrough and an execution review table mapping queue artifacts to `ghdag status` / MCP `ghdag_status` (sumipan/nexus#6610).

## 0.133.3 - 2026-11-14

### Fixed

- Reuse `extract_error` capacity/overloaded classification in `classify_failure` for codex, claude, and cursor stream adapters so non-zero exits surface `FailureClass.ENGINE_ERROR` (sumipan/nexus#6602).

## 0.133.1 - 2026-11-13

### Fixed

- `ghdag watch` and `ghdag trigger` honor `GHDAG_FORGE=local` via `get_forge()` instead of always requiring GitHub client factories (sumipan/nexus#6606).

### Added

- Persist task failure diagnostics to `stderr/<uuid>/…` under the queue directory (1 MiB tail, redaction, non-destructive re-runs) and record the queue-relative path as `stderr_path` on task exit audit events (`task_failed`, `task_empty_result`, and related lifecycle lines). See README *Failure stderr artifacts*.

## 0.133.0 - 2026-11-13

### Removed

- Trim unused names from package-level `__all__` lists across audit, cleanup, config, core, dag, files, `github_cli`, io, llm, mcp, pipeline, schemas, status, tool, vcs, and workflow. Runtime behavior is unchanged; import the symbol from its defining module instead of the package re-export. See README Package `__all__` for the curated surface (sumipan/nexus#6587).
- `ghdag.audit`: `emit_span`, `make_span_id`
- `ghdag.cleanup`: `cleanup_queue`, `QUEUE_FILE_RE` (package re-exports; `CleanupResult` / `file_timestamp` remain on the package but are not in `__all__`)
- `ghdag.dag`: `DefaultHooks`, `WAIT_QUOTA_COMPANION_RE`, `parse_jsonl`
- `ghdag.files`: `AppendResult`, `AppendStatus`, `MdFile`, `PathTraversalError`, `PromoteResult`, `PromoteStatus`, `WriteResult`, `md_append`, `md_promote`
- `ghdag.github_cli`: `API_BASE`, `DEFAULT_REPO`, `GRAPHQL_URL`, `GitHubClient` (package `__all__` is now empty; `get_forge` and `cli_main` remain importable)
- `ghdag.llm`: `DEFAULT_ENGINE_MODELS`, `EngineSpec`, `InputMode`, `JSON_ONLY`, `LLMParseError`, `PromptFlag`, `SessionStore`, `WEB_RESEARCH`, `build_llm_cmd`, `list_models`
- `ghdag.mcp`: `McpServer`, `McpToolRegistry`, `TOOL_NAMES`
- `ghdag.pipeline`: `ModelValidationError`, `SubmittedStep`, `get_latest_status`, `parse_frontmatter`, `read_task_exit_events`
- `ghdag.tool`: `FallbackEntry`, `TOOL_EXIT_CODES`, `ToolRegistry`, `write_tool_fallback_audit`
- `ghdag.vcs`: `GitSink`
- `ghdag.workflow`: `DispatchResult`, `OnTriggerConfig`

### Added

- `ghdag.quota.__all__` listing `QuotaGate`, `QuotaSnapshot`, `EngineQuotaState`, `AdmissionDecision`, and `Missing` (sumipan/nexus#6587).

## 0.132.1 - 2026-11-12

### Fixed

- `ghdag.llm.call` accepts `permission_mode="bypassPermissions"` (`DANGEROUS_FULL_ACCESS`) for codex and cursor and passes the engine's own bypass flag (`--dangerously-bypass-approvals-and-sandbox` / `--force`). 0.132.0 removed `dangerously_skip_permissions` from `call()` but still rejected the preset for these two engines, so they could not be run with full access at all.

## 0.132.0 - 2026-11-12

### Changed

- **Breaking:** Unify one-shot LLM invocation on `ghdag.llm.call` / `LLMResult`. `call_text`, `call_managed`, `TextResult`, and `ManagedResult` are removed; `LLMResult` always carries `body`, and `ok` is true only when `returncode == 0` and `error is None`. Quota / auth fallback kwargs (`fallback_candidates`, `quota_gate`, `fallback_on_timeout`, …) live on `call()`. Use permission preset `DANGEROUS_FULL_ACCESS` instead of `dangerously_skip_permissions` on `call()` (sumipan/nexus#6529).

| Before | After |
| --- | --- |
| `from ghdag.llm import call_text, TextResult` | `from ghdag.llm import call, LLMResult` |
| `call_text(...)` → `TextResult(body, success, raw)` | `call(...)` → `LLMResult(body, ok, …)` |
| `call_managed(...)` | `call(..., fallback_candidates=..., quota_gate=...)` |
| `result.success` | `result.ok` |
| `ghdag llm --json` envelope `stdout` field | `body` plus unified metadata fields |

### Removed

- `ghdag.llm.managed`, `call_text`, `call_managed`, `TextResult`, `ManagedResult`, and `LLMResult.validate()` (sumipan/nexus#6529).

## 0.131.0 - 2026-11-12

### Changed

- **Breaking:** Rename command adapter registry APIs: `get_adapter` → `get_command_adapter`, `register_adapter` → `register_command_adapter`. Import from `ghdag.core.command` or `get_command_adapter` from `ghdag.pipeline` (sumipan/nexus#6535).

| Before | After |
| --- | --- |
| `from ghdag.core.command import get_adapter, register_adapter` | `from ghdag.core.command import get_command_adapter, register_command_adapter` |
| `from ghdag.pipeline import get_adapter` | `from ghdag.pipeline import get_command_adapter` |

## 0.130.0 - 2026-11-12

### Changed

- Rename the LLM constraint value object to `Permission` and the preset registry to `PERMISSIONS`. Public call paths (`ghdag.llm.call`, `call_text`, `call_managed`, `build_llm_cmd`, `ghdag.core.command.render_exec_command`, `container_prefix`, and `EngineAdapter.build_exec_record`) take keyword argument `permission` instead of `capabilities`. Preset keys and workflow step field `permission` are unchanged (sumipan/nexus#6501).

### Removed

- `LLMCapabilities`, `PRESETS`, and the `capabilities` keyword on public LLM / command APIs; use `Permission`, `PERMISSIONS`, and `permission` (sumipan/nexus#6501).

| Before | After |
| --- | --- |
| `from ghdag.llm import LLMCapabilities, PRESETS` | `from ghdag.llm import Permission, PERMISSIONS` |
| `ghdag.llm.call(..., capabilities=TEXT_ONLY)` | `ghdag.llm.call(..., permission=TEXT_ONLY)` |

### Host consumer migration (sumipan/nexus#6501)

1. Ship this rename in ghdag **v0.130.0**; do not restore `LLMCapabilities` / `PRESETS` / `capabilities=` compatibility shims.
2. Pinned host consumers (sumipan/nexus#6493): file one tracking Issue per consumer repository (nexus host, issuesmith, mltgnt, slack-project). Each follow-up PR updates `Permission` / `PERMISSIONS` imports and the `permission` keyword together with the ghdag pin to **v0.130.0**.
3. Record each per-consumer tracking Issue number on sumipan/nexus#6501 before any consumer pin exceeds v0.130.0.

### Regression test checklist (sumipan/nexus#6501)

| Area | Test |
| --- | --- |
| workflow step `permission` key / exec record shape | `tests/workflow/test_engine.py::test_exec_record_permission_argument_preserves_schema` |
| MCP tool `permission` enum vs `PERMISSIONS` | `tests/conventions/test_schemas_in_sync.py::test_permission_registry_matches_workflow_and_mcp_schema` |
| exec.jsonl record keys vs schema | `tests/conventions/test_schemas_in_sync.py::test_exec_record_keys_subset_of_schema` |
| Public LLM API keyword `permission` | `tests/llm/test_call_text.py::test_public_signatures_use_permission` |
| `Permission` / `PERMISSIONS` exports | `tests/conventions/test_public_package_exports_runtime.py::test_permission_names_are_public` |
| Preset registry identity | `tests/llm/test_capabilities.py::test_permission_registry_identity` |

## 0.129.0 - 2026-11-12

### Changed

- CLI flags: `--exec-jsonl` (replaces `--exec-md` / `quota status` `--exec-path`), `--interval-sec` (replaces `--interval`), `--timeout-sec` (replaces `--timeout`), and `ghdag llm --permission PRESET` (replaces `--capabilities-preset`, `--permission-mode`, `--dangerously-skip-permissions`). Handler `argparse` attribute names are unchanged (`exec_jsonl`, `interval`, `timeout`, and related fields) (sumipan/nexus#6475).
- `ghdag.__all__` exports only `GhdagError`; import `QueueTask` / `QueueTaskStore`, pipeline, DAG, workflow, quota, and status symbols from their definition packages (sumipan/nexus#6475).
- `ghdag.pipeline` no longer re-exports `STATE_*`, `task_status`, `state_label`, or `status_rank`; import from `ghdag.pipeline.status` / `ghdag.pipeline.state` (sumipan/nexus#6475).
- Internal DAG process record renamed to `_RunningProcess` in `ghdag.dag.models`; it is not exported from `ghdag.dag`. Snapshot type `ghdag.status.RunningTask` is unchanged (sumipan/nexus#6475).

### Removed

- `ForgePort`, `get_forge`, `GitHubIssueClient`, and `create_github_client` from `ghdag.workflow.__all__`; import from `ghdag.core.ports.forge`, `ghdag.forge`, and `ghdag.github_client` (sumipan/nexus#6475).

| Before | After |
| --- | --- |
| `ghdag watch … --exec-md PATH` | `ghdag watch … --exec-jsonl PATH` |
| `ghdag trigger … --exec-md PATH` | `ghdag trigger … --exec-jsonl PATH` |
| `ghdag dag recover … --exec-md PATH` | `ghdag dag recover … --exec-jsonl PATH` |
| `ghdag quota status --exec-path PATH` | `ghdag quota status --exec-jsonl PATH` |
| `ghdag run|watch|ui … --interval SEC` | `… --interval-sec SEC` |
| `ghdag llm … --timeout SEC` | `ghdag llm … --timeout-sec SEC` |
| `ghdag llm --capabilities-preset P` | `ghdag llm --permission P` |
| `from ghdag import DagEngine` | `from ghdag.dag import DagEngine` |
| `from ghdag.dag import RunningTask` | internal `_RunningProcess` (not public) |
| `from ghdag.workflow import get_forge` | `from ghdag.forge import get_forge` |

## 0.127.0 - 2026-11-11

### Removed

- `ghdag.pipeline.submit` (`make_order_record`, `submit_order`) and their exports from `ghdag.pipeline`. Use `LLMPipelineAPI.submit` instead; unspecified step `permission` defaults to `text_only` (sumipan/nexus#6440).

## 0.126.1 - 2026-11-11

### Removed

- Compatibility re-export modules (no new aliases; import from the one-segment package or canonical module instead): `ghdag.exceptions` → `ghdag.core.exceptions`; `ghdag.dag.stall_guard` → `ghdag.core.stall_guard` (and `read_ps` from `ghdag.llm.engines` where needed); `ghdag.dag.state` (internal done-directory helpers); `ghdag.files._rotate` (internal); `ghdag.files.models` → `ghdag.files`; `ghdag.llm.capabilities` → `ghdag.core.capabilities` / `ghdag.llm`; `ghdag.llm.spec` → `ghdag.core.engine_spec` / `ghdag.core.command`; `ghdag.metrics.models` → `ghdag.metrics`; `ghdag.pipeline.audit` → `ghdag.io.audit`; `ghdag.pipeline.audit_query` → `ghdag.io.audit_query`; `ghdag.pipeline.hooks` → `ghdag.dag` (`AuditHooks`); `ghdag.pipeline.result` → `ghdag` / `ghdag.io.queue`; `ghdag.workflow.engine` → `ghdag.core.command`; `ghdag.workflow.schema` → `ghdag.workflow` (sumipan/nexus#6439 / #6450 / #6451).

### Changed

- Public imports: `AuditHooks` is exported from `ghdag.dag` only; `ghdag.pipeline` no longer re-exports it. README drops the module-level `__all__` inventory and documents that deep import paths are internal (sumipan/nexus#6451).

## 0.125.8 - 2026-11-11

### Changed

- Tests only: remove wall-clock dependencies from eight test modules (`tests/test_result_finalize.py`, `tests/test_session_lineage.py`, `tests/test_status.py`, `tests/test_task_cancel.py`, `tests/test_task_events.py`, `tests/test_task_events_multi_engine.py`, `tests/workflow/test_dispatcher.py`, `tests/workflow/test_dispatcher_reload.py`) per docs/TEST.md T13 (sumipan/nexus#6194).

## 0.125.7 - 2026-11-11

### Changed

- Tests only: remove wall-clock dependencies from nine test modules (`tests/io`, `tests/llm`, `tests/metrics`, `tests/pipeline`) per docs/TEST.md T13 (sumipan/nexus#6193).

## 0.125.6 - 2026-11-11

### Changed

- Internal import paths: `mcp`, `pipeline` (except `pipeline.__init__`), `schemas`, `tool`, `workflow`, and related tests import canonical modules directly instead of re-export shims (public behavior unchanged) (sumipan/nexus#6405).

## 0.125.3 - 2026-11-11

### Changed

- Tests only: remove wall-clock dependencies from thirteen root-level test modules per docs/TEST.md T13 (sumipan/nexus#6192).

## 0.125.2 - 2026-11-11

### Changed

- Tests only: remove wall-clock dependencies from `tests/dag` and `tests/files` per docs/TEST.md T13 (sumipan/nexus#6191).

## 0.125.0 - 2026-11-11

### Removed

- ``ghdag.workflow.gates`` (including ``python -m ghdag.workflow.gates``), ``ghdag.markdown.body_editor``, ``ghdag.core.ports.gate`` (``GateRule`` / ``Violation``), and entry-point group ``ghdag.gates`` loading. Migrate gate rules and Issue body section editing to [issuesmith](https://github.com/sumipan/issuesmith) (sumipan/nexus#6317).

## 0.124.0 - 2026-11-11

### Added

- ``ghdag.core.stall_guard``: detect recursive ``grep`` children under cursor shell state snapshots and SIGTERM them when CPU time stops advancing for ``stall_guard_stall_sec()`` (sumipan/nexus#6350).

## 0.123.1 - 2026-11-10

### Added

- README and `llms.txt` document `ghdag mcp`, its two tools, and Claude Code registration (sumipan/nexus#6318).

### Changed

- MCP public tools are `ghdag_enqueue` and `ghdag_status` only; multi-step enqueue shares one `LLMPipelineAPI.submit` call; optional `steps[].id` defaults to `step-<n>`; engine and permission choices are JSON Schema enums on `tools/list` (sumipan/nexus#6318).

### Removed

- MCP tools `ghdag_enqueue_task`, `ghdag_enqueue_dag`, `ghdag_get_status`, and `ghdag_describe`, plus `DEPENDENCY_UUID_UNKNOWN` validation for exec UUID dependencies (sumipan/nexus#6318).

## 0.123.0 - 2026-11-10

### Added

- Package-level exports for names used by downstream repos: ``ghdag.metrics`` (``FailureClass``, ``TokenUsage``), ``ghdag.pipeline`` (``AuditContext``, ``write_task_exit_audit``, ``TemplateVariableError``, ``build_idempotency_key``, ``get_adapter``), ``ghdag.workflow`` (``transition``, ``ConsumeEntry``, ``SkillIOSpec``, ``typecheck_dag``). Completes the seven-package one-segment import surface together with ``ghdag.dag``, ``ghdag.forge``, ``ghdag.io``, and ``ghdag.llm`` (sumipan/nexus#6319 / #6331).

## 0.120.2 - 2026-11-10

### Changed

- ``tests/cleanup``: replace wall-clock ``time.time()`` with a fixed clock (sumipan/nexus#6190).

## 0.120.1 - 2026-11-10

### Fixed

- ``ghdag.llm.managed``: when callers request JSON output but a trial runs with effective text (Codex / Cursor), ``ManagedResult.body`` now uses the adapter-extracted review text instead of raw JSONL stdout so standalone status markers survive (sumipan/nexus#6279).

## 0.120.0 - 2026-11-09

### Added

- Reply-block fan-out: ``children`` with flat ``prompt`` / ``command`` items in the `` ```ghdag `` fence (schema ``reply-block``).
- ``DagConfig.max_repair_turns`` (default ``1``): one resumable repair turn on ``REPLY_BLOCK_FAILED`` / ``FANOUT_INVALID`` when a session id exists.
- ``write_repair_turn_audit`` (``event_type: llm.repair_turn``) and fan-out ``prompt`` children that enqueue full exec records with order files.

### Changed

- ``parse_fanout_spec`` prefers reply-block ``children``; legacy ``---`` + ``ghdag_fanout:`` remains for command-only children.
- ``FanOutManager.spawn`` builds LLM child tasks from ``prompt`` (parent engine/model inheritance rules, sibling ``depends`` as uuids).

## 0.117.1 - 2026-11-09

### Fixed

- ``ghdag.core.stall_guard``: recognize cursor shell snap markers when GNU ``ps`` truncates the ``args`` column on Linux (sumipan/nexus#6181).

### Added

- ``tests/conventions/test_no_wallclock_in_tests.py``: T13 wall-clock ratchet and ``known_wallclock_tests.txt`` ledger (sumipan/nexus#6181).
- ``tests/core/test_stall_guard_ps.py``: Linux/macOS ``ps`` snapshot contract tests for stall detection (sumipan/nexus#6181).

### Changed

- Socketpair stall-guard integration test uses synthetic clock and thread sync instead of per-test timeouts (sumipan/nexus#6181).
- ``pytest-timeout`` dev dependency with suite-wide ``timeout = 300`` in ``pyproject.toml`` (sumipan/nexus#6181).

## 0.117.0 - 2026-11-08

### Added

- Workflow load-time validation codes: `STEP_ENGINE_UNKNOWN`, `STEP_PERMISSION_UNKNOWN`, and depends validation at load time (`validate_depends`).
- `ghdag.schemas` (`load_schema`, `SCHEMA_NAMES`) and five bundled JSON Schemas under `src/ghdag/schemas/`.
- `ghdag validate` — validate workflow YAML files in a directory (text or `--json` output).
- `ghdag schema list` / `ghdag schema show` — list or print bundled JSON Schemas.
- `llms.txt` — LLM-oriented project overview (llmstxt.org shape).

### Changed

- `shell` workflow steps may omit `model`; `StepConfig.model` is `str | None` (required for non-shell engines).
- `_validate_depends` renamed for clarity (load-time depends checks).

## 0.114.0 - 2026-11-08

### Added

- `ghdag.io.done.done_failure_class()` maps done-marker bodies to `FailureClass` using vocabulary constants (sumipan/nexus#6155).
- `ghdag.status.StepStatus` adds `done_value`, `failure_class`, `cause`, and `retry_policy`; `issue_status` fills them from done markers (`status --json` includes them in `steps[]`).

### Changed

- README: CLI `--json` / structured CLI errors, exit-code table, `done_failure_class` mapping, and `StepStatus` failure fields (sumipan/nexus#6155).

## 0.113.0 - 2026-11-08

### Fixed

- `ghdag watch` hot reload: include `.yaml` workflow files in mtime checks and refresh per-workflow order builders after a successful reload (sumipan/nexus#6092).

## 0.112.3 - 2026-11-08

### Added

- `LocalForge` implements `GitHubIssuePort` (`list_all_issues`, `get_issue`, `add_comment`, sub-issue APIs, `get_last_rate_limit`); `issue_get` includes `id` equal to `number` (sumipan/nexus#6093).

## 0.112.1 - 2026-11-08

### Fixed

- `ghdag trigger`: step count in dispatch output uses `len(exec_lines)` (JSONL exec records only; drops legacy `-1` for a comment line) (sumipan/nexus#6094).

## 0.112.0 - 2026-11-07

### Changed

- `resolve_runtime_paths` in `config/env.py` centralizes queue vs `GHDAG_STATE_DIR` paths; `DagConfig` defaults, `ghdag ui`, and `ghdag cleanup` use it. `TaskLauncher` audit events use `DagConfig.audit_path` (queue dir) so they stay aligned with `AuditHooks` when state dir is split (sumipan/nexus#6089).

## 0.111.2 - 2026-11-07

### Changed

- `parse_fanout_spec`: when a `ghdag_fanout:` anchor is present, invalid YAML, a missing `---` separator, malformed `children`, or invalid child entries raise `FanoutError` instead of returning None (sumipan/nexus#6088).

## 0.111.1 - 2026-11-07

### Fixed

- `compact_resume_session` passes `JSON_ONLY` capabilities to both LLM calls so claude JSON stdout yields a parseable `session_id` for compaction (sumipan/nexus#6090).

## 0.111.0 - 2026-11-07

### Changed

- `ghdag watch`: build GitHub clients with `rate_limit_max_wait_sec=0` so rate limits do not block the dispatcher loop; dispatch `RateLimitError` with a reset time skips remaining matches for that iteration and polls until reset (sumipan/nexus#6087).
- `create_github_clients`: accept `rate_limit_max_wait_sec` and pass it to each `GitHubClient` (sumipan/nexus#6087).

## 0.110.2 - 2026-11-07

### Fixed

- `ghdag.workflow.conditional_step`: build LLM argv via `build_llm_cmd` and `ENGINE_SPECS` (cursor uses `cursor-agent`; `--engine` adds codex with engine-default models) (sumipan/nexus#6086).

## 0.110.1 - 2026-11-07

### Fixed

- `LLMPipelineAPI._submit_jsonl` resolves `depends` and `resume_from` to UUIDs even when the referenced step appears later in the `steps` list (sumipan/nexus#6083).

## 0.110.0 - 2026-11-07

### Added

- `ghdag.io.exec_jsonl.parse_idempotency_key` — inverse of `build_idempotency_key` for structured idempotency key parsing.

### Fixed

- `PipelineState.remove_idempotency_matching` no longer deletes another issue's generation-suffixed keys (e.g. `wf:h:12:3` when resetting issue 3).
- `PipelineState.remove_idempotency_for_handler` now removes all generations for the same workflow × handler × issue triple.

## 0.109.0 - 2026-11-07

### Added

- `ghdag.core.command.inject_resume` — pure helper to insert resume flags into a `render_exec_command` string when only the command text is available (e.g. `TaskLauncher` resume wiring).

### Fixed

- `TaskLauncher._inject_resume_command` now uses `inject_resume`, so codex `web_search` / `--search` commands get `exec resume` injected correctly (sumipan/nexus#6084).

## 0.108.0 - 2026-11-07

### Fixed

- `GHDAG_ENGINE_ISOLATION=0` (and `false` / `no` / `off`) now correctly disables isolation. Previously any non-empty string was truthy. Access unified via `config.env.engine_isolation_enabled()`.

## 0.107.2 - 2026-11-07

### Fixed

- `TaskLauncher.launch()` now injects `--resume` only after quarantine, stdin input checks, and `QuotaGate.begin_run` admit the task, so paused / quarantined / missing-input polls no longer repeat `SessionStore.lookup`, compaction, or `session_compaction` audit; a Popen failure restores the pre-resume command and annotations (sumipan/nexus#6081).

## 0.107.1 - 2026-11-07

### Fixed

- `LLMPipelineAPI.submit()` now copies workflow `StepConfig.env_passthrough` into exec record `annotations.env_passthrough` so `TaskLauncher` honors YAML passthrough under deny lists (sumipan/nexus#6080).

## 0.107.0 - 2026-11-07

### Removed

- **Breaking**: `DagConfig.brake_state_path`, `QuotaGate(brake_state_path=)`, and `ghdag run --brake-state`. Since v0.106.0 these had no effect on quota admission; use `QuotaGate.report(kind="budget")` to pause engines via quota state instead.

## 0.106.5 - 2026-11-05

### Added

- `LLMResult.raw_stdout` preserves claude/cursor stream subprocess JSONL before `stdout` extraction (sumipan/nexus#6026).

## 0.106.4 - 2026-11-04

### Changed

- Split oversized test modules under `tests/` per TESTING.md T8 (pipeline, cleanup, CLI, DAG engine, LLM, workflow); removed sixteen duplicate contract tests listed in issue #5967.

## 0.106.3 - 2026-11-04

### Changed

- Removed or rewrote tests that violated TESTING.md rules T1, T3, T4, and T5 (implementation pinning, permanent absence checks, AC-style names, and excessive mock interaction assertions).

## 0.106.2 - 2026-10-31

### Added

- TESTING.md: testing policy (what tests protect, rules T1-T12, how to remove tests).

## 0.106.1 - 2026-10-28

### Changed

- `GitHubClient` sends conditional requests (`If-None-Match`) on every GET by default, not only `list_all_issues`. A 304 does not count against the PAT rate limit, so repeated reads of unchanged issues, comments, PRs and sub-issues (issuesmith tick, watchdog) stop draining the 5000/hour budget. Pass `etag=False` to opt out; non-GET and `raw=True` requests are never cached.
- The `GHDAG_ETAG_CACHE` file is written at most every 5 seconds plus once at process exit, and each write merges into the current file contents so long-lived processes no longer drop entries other processes added.
- Cached bodies are returned as copies, and the in-memory ETag cache is capped at 2000 entries.

## 0.106.0 - 2026-10-27

### Changed

- **Breaking:** Quota waiting is engine-owned. Tasks signal `PIPELINE_STATUS: WAIT_QUOTA` with a `WAIT_QUOTA:` companion line; the launcher records engine-level pause, deletes the attempt result, and does not write `done/`. `PIPELINE_STATUS: DEFERRED` is rejected as `PIPELINE_FAILED:LEGACY_DEFERRED_UNSUPPORTED`.
- **Breaking:** Removed `QuotaGate.defer`, `release_ready`, and persistent `deferred_tasks`. Use `QuotaGate.refresh()` to expire pause sources. Legacy `done/<uuid>` contents `DEFERRED` are migrated on first exec load (`task_deferred_migrated` audit).
- `ghdag quota status` no longer returns `deferred_tasks` or per-engine `deferred` counts.

## 0.105.0 - 2026-10-26

### Added

- `QuotaGate` engine state now stores multiple pause `sources` (`budget`, `failure`, `manual`, `drain`) with derived `status` / `resume_at` / `reason` compatibility fields. `report` / `clear` accept `kind`; `clear(ttl_seconds=…)` maps to a temporary `manual` override. Legacy schema v1 payloads without `sources` are read as a single `failure` source (`set_by=legacy`).
- `call_managed()` tracks per-engine AUTH strikes in quota state: the first AUTH on a primary engine runs fallback without pausing; a second consecutive AUTH on the same primary engine reports `kind=failure`. Primary success resets strikes; fallback success does not.

## 0.104.5 - 2026-10-24

### Changed

- Engine isolation: when `isolation=True`, codex `build_llm_cmd` and `render_exec_command` add `-c skills.include_instructions=false` so global skills are not injected into the CLI (#5541).
- `call_managed(..., isolation=None)` forwards `isolation` to `call()` on the primary attempt and on the single quota/auth fallback.

## 0.104.4 - 2026-10-20

### Changed

- Workflow loader: reject `${<id>_result_content}` when `<id>` is a prior step in the same handler (enqueue-time render cannot supply that body); use `${<id>_result_filename}` with `depends` instead.

## 0.104.3 - 2026-10-18

### Fixed

- `WorkflowDispatcher` now raises `ContextHookError` when a handler `context_hook` exits non-zero, instead of continuing dispatch with missing context. Failed runs are recorded in `audit.jsonl` as `dispatcher_dispatch_failed` and do not enqueue steps or transition ready labels.

## 0.103.5 - 2026-10-17

### Changed

- Cursor engine CLI: invoke `cursor-agent` instead of `agent` so PATH collisions with xAI Grok's `agent` binary no longer hijack cursor jobs. Legacy `agent` remains a matching alias for metrics and failure classification on old job logs (sumipan/nexus#5339).

## 0.103.4 - 2026-10-17

### Fixed

- `TaskLauncher.check_completions`: after a task exits 0 and `state_mark_done(..., 0)` succeeds, exceptions in `on_task_success`, `circuit_breaker.reset`, or `promote_fn` no longer overwrite the done marker with `UNKNOWN_FAILURE` or invoke failure hooks; each failed postprocess step is logged to audit as `task_success_postprocess_failed` (sumipan/nexus#5326).

## 0.103.3 - 2026-10-15

### Fixed

- `CursorStreamAdapter.extract_error` classifies cursor stderr `Cannot use this model: <model>. Available models:` with an empty model list as `EngineError(kind=CAPACITY, retryable=True)` so transient cursor-agent failures retry via the existing task launcher path (sumipan/nexus#5303).

## 0.103.2 - 2026-10-14

### Added

- `LLMCapabilities.web_search` and `EngineSpec.pre_subcommand_args`: engine-neutral Web search requests. `WEB_RESEARCH` now sets `web_search=True` instead of claude-only `allowed_tools`. codex emits top-level `--search` before `exec`; claude keeps the same Web tool allow-list via the flag builder; cursor accepts the preset without extra argv. gemini and shell reject `web_search=True` in `call()`.

## 0.103.0 - 2026-10-10

### Added

- `ghdag.core.stall_guard`: the cursor stall-guard logic (`ProcInfo`, `StallEvent`, `StallTracker`, `parse_ps_output`, `scan_process_tree`, `find_stalled_state_readers`) now lives in core. `ghdag.dag.stall_guard` re-exports the same objects. Because core must not run subprocesses, `read_ps` moved to `ghdag.llm.engines` (still re-exported by `ghdag.dag.stall_guard`) and `StallTracker` now takes `ps=` as a required keyword argument (sumipan/nexus#5141).

### Changed

- `ghdag.llm.call()` with `engine="cursor"` now runs the same stall guard as DAG cursor tasks: the engine is driven by `Popen` + a `communicate` loop, a `cat` stuck reading the shell-state snapshot for `GHDAG_ENGINE_STALL_SEC` (default 180s) gets SIGTERM, and a `[stall-guard] killed cat pid=<pid> after <n>s` line is appended to stderr. `GHDAG_ENGINE_STALL_GUARD=0` keeps the previous single `subprocess.run`; other engines are unchanged (sumipan/nexus#5141).

## 0.102.1 - 2026-10-10

### Added

- `QuotaGate` engine concurrency limits: optional `limits` mapping (`engine` → max simultaneous `running_tasks` for that engine). `begin_run` denies with `reason="engine_limit"` without writing `deferred_tasks` or audit `task_deferred` (sumipan/nexus#5032).
- `DagConfig.engine_limits` (default `{}` = unlimited) and `DagConfig.quota_pause_ttl_seconds` (default `3600`) wire limits and pause TTL into `DagEngine`'s `QuotaGate` (sumipan/nexus#5032).
- `QuotaSnapshot.limits` and `ghdag quota status` `engines.<name>.limit` (`int` or `null` when unlimited) (sumipan/nexus#5032).

### Changed

- `QuotaGate(..., pause_ttl_seconds=3600)`: `report(status="paused")` without `resume_at` now sets `resume_at` to `observed_at + pause_ttl_seconds` (default 1 hour, also for bare `QuotaGate(path)` and the `ghdag quota report` CLI), so pauses expire via `release_ready()`. Pass `pause_ttl_seconds=None` for the previous permanent pause; use `ghdag quota drain` for a manual indefinite stop. `role` tasks record the admitted engine (and `role`) in `running_tasks` instead of an empty `engine` (`"unknown"` when no engine can be resolved) (sumipan/nexus#5032).

## 0.102.0 - 2026-10-10

### Added

- `load_workflow_file(path)` loads and validates a single workflow YAML file (sumipan/nexus#5030). Breaking: workflow loading rejects unknown YAML keys at the workflow, trigger, handler, `on_trigger`, step, and `nonterminal_closed` levels instead of silently ignoring them (sumipan/nexus#5030).

### Fixed

- fix(repair): expect ValidationError for unknown workflow keys in legacy tests (#5030)
- fix(repair): restore legacy test names and assert count for unknown-key reject (#5030)

## 0.101.4 - 2026-10-10

### Fixed

- fix(tests): add graphql to ForgePort compat baseline

## 0.101.3 - 2026-10-10

### Fixed

- fix(cli): remove downstream vocabulary from help and comments

## 0.101.2 - 2026-10-09

### Added

- `QuotaGate.read_state()` / `QuotaGate.modify(fn)`: public access to the raw quota state dict. `read_state()` reads under a shared lock (missing file → default state, no file created; invalid JSON / unknown schema → `ValueError`). `modify(fn)` runs `fn(state)` under an exclusive lock and writes the in-place-mutated state (when `fn` returns `None`) or the returned dict, then returns it; if `fn` raises, nothing is written. Unknown top-level keys (e.g. `resources`) are preserved. Replaces direct use of `_lock` / `_load_state_unlocked` / `_write_state_unlocked` (sumipan/nexus#5033). `GitHubClient.graphql(query, variables=None)`: POSTs to `GRAPHQL_URL` via `_request`, so auth headers, transient retries, rate-limit waits / `RateLimitError` and `AuthError` are shared with REST calls. Returns the response `data` (`{}` when absent); a non-empty `errors` raises `GitHubApiError` (sumipan/nexus#5033). `LocalForge.graphql()`: raises `NotImplementedError` (sumipan/nexus#5033). `DagEngine.get_task(uuid)`: the loaded `Task` for `uuid`, or `None` before load / for an unknown uuid (sumipan/nexus#5033). `ghdag.dag.default_check_rejected` is exported (in `ghdag.dag.__all__`) (sumipan/nexus#5033). `ForgePort.graphql`: new Protocol method `graphql(query, variables=None) -> dict`. Implementers of `ForgePort` must provide it (`GitHubClient` and `LocalForge` do) (sumipan/nexus#5033). `ghdag.forge.get_forge()`: `LocalForge` is imported directly instead of via `importlib` with a `ModuleNotFoundError` fallback; behaviour is unchanged (sumipan/nexus#5033).

## 0.101.1 - 2026-10-09

### Changed

- docs(readme): align with v0.101.0 release inventory

## 0.101.0 - 2026-10-08

### Added

- feat(dag): add cursor engine stall guard for stuck cat readers

## 0.100.8 - 2026-10-08

### Changed

- test(cli): add ghdag run --brake-state wiring tests

## 0.100.7 - 2026-10-08

### Added

- feat(cli): pass brake_state_path from run into DagConfig

## 0.100.6 - 2026-10-08

### Changed

- test(dag): cover brake_state_path and pending requeue in engine quota tests (#4928)

## 0.100.5 - 2026-10-08

### Added

- feat(cli): add --brake-state to ghdag run parser (#4923)

## 0.100.4 - 2026-10-08

### Added

- feat(dag): add DagConfig.brake_state_path for budget-brake integration (#4920)

## 0.100.3 - 2026-10-08

### Fixed

- fix(dag): wire brake_state_path and deferred done requeue follow-up

## 0.100.2 - 2026-10-04

### Fixed

- fix(cursor_stream): detect complete assistant text by event structure (#4593)

## 0.100.1 - 2026-10-04

### Changed

- docs(workflow): translate workflow and top-level modules to English and gate CJK in src/tests (#4522, #4468)
- test(no-cjk): drop #4522 pending exclusions now that it has landed (#4468)
- docs(readme): rewrite README for v0.100.0

### Fixed

- fix(repair): build CJK ranges via chr() to avoid escape-decoded CJK in test_no_cjk (#4468)
- fix(repair): revert out-of-scope pyproject.toml version bump (#4468)

## 0.100.0 - 2026-10-03

### Added

- feat(language): add optional question_suffixes pack section for looks_like_question (sumipan/nexus#4522)

### Changed

- test(ui): pin GHDAG_AUDIT_PATH to tmp audit in dashboard endpoint tests (#4547)

## 0.99.0 - 2026-10-03

### Changed

- docs(llm): translate llm/adapters and files/links/obsidian to English
- chore(llm): restore #4522-owned files to main and drop stale CJK test comments (#4466, #4513)

## 0.98.0 - 2026-10-03

### Added

- feat(ui): localise index.html via /api/config i18n and judge state by state_id (#4457)

## 0.97.0 - 2026-10-03

### Changed

- docs(io): translate audit, audit_query, done, queue and sessions docstrings to English (#4467, #4348)

## 0.96.0 - 2026-10-03

### BREAKING

- `ghdag.config.language`: language packs for state labels and Web UI strings (sumipan/nexus#4454). `LanguagePack(state_labels, ui)`, the bundled English pack `EN` (the only pack shipped), `STATE_IDS` (10 keys) / `UI_KEYS` (12 keys), `load_language_pack(path)` and `get_language_pack()` (process-cached). `GHDAG_LANGUAGE_PACK` (`config.env.ghdag_language_pack()`) points at a YAML pack that replaces every field; missing / unknown keys, empty values, invalid YAML or an unreadable file raise `GhdagError`. See README "Language pack". `ghdag.pipeline.state_label(state_id, pack=None)`: display label for a state identifier from `pack` (default: the active language pack); unknown identifiers are returned unchanged (sumipan/nexus#4456). `ghdag.ui.monitor.Row.state_id`: the state identifier of a row; `Row.state` is now `state_label(state_id)`. `/api/rows` returns both, and accepts `?state=<name>` (repeatable or comma-separated identifiers / `STATE_ALIASES` names) to filter rows (sumipan/nexus#4456). `/api/config` gains `i18n` (`ui` 12 keys, `state_labels` 10 keys) from the active language pack. `run_server()` loads the pack once at startup, so an invalid `GHDAG_LANGUAGE_PACK` fails `ghdag ui` with `GhdagError` before the server binds (sumipan/nexus#4456). Task state values are language-neutral identifiers (sumipan/nexus#4456): `STATE_PENDING_DEPS` / `STATE_PENDING_RUN` / `STATE_RUNNING` / `STATE_DEFERRED` / `STATE_OK` / `STATE_FAIL` / `STATE_REJECTED` / `STATE_EMPTY` / `STATE_ENGINE_ERROR` / `STATE_UNKNOWN_DONE` are now `pending_deps` / `pending_run` / `running` / `deferred` / `ok` / `fail` / `rejected` / `empty` / `engine_error` / `unknown` (previously Japanese display strings), and `pipeline.task_status()`, `ui.monitor.task_state()` and `label_for_done()` return these identifiers. Use `state_label()` for the display string. `ui.monitor.STATE_ALIASES` maps filter names to identifiers and `filter_rows` matches `Row.state_id`. `/api/rows` keeps `state` as the display label, so a host that sets `GHDAG_LANGUAGE_PACK` to a pack with its previous labels sees the same values as before; without a pack the labels are English.

### Changed

- docs(llm): translate llm/ and markdown/body_editor comments and handoff prompt to English

### Fixed

- fix(repair): replace CJK regex escapes with ord-range check in test_compaction (#4465)

## 0.95.0 - 2026-10-03

### Changed

- docs(dag): translate CJK comments and docstrings to English

## 0.94.0 - 2026-10-03

### Changed

- docs(cli): translate ui, watch and main docstrings to English

## 0.93.0 - 2026-10-03

### Changed

- docs(cli): translate cli/commands docstrings to English (sumipan/nexus#4462)

## 0.92.0 - 2026-10-03

### Changed

- docs(cleanup): translate CJK comments, docstrings and CLI message to English (#4461)

## 0.91.0 - 2026-10-03

### Changed

- docs(core): translate core and core/models comments and docstrings to English (#4459, #4348)

## 0.90.0 - 2026-10-03

### Changed

- docs(pipeline): translate comments, docstrings and messages to English (#4458)
- docs(ports,tool): translate comments and docstrings to English (#4460, #4348)

## 0.89.0 - 2026-10-03

### Added

- feat(config): add language pack loader and GHDAG_LANGUAGE_PACK accessor (#4454)

## 0.88.0 - 2026-10-03

### Changed

- docs(readme): update to v0.87.0 and document LLM timeout API

## 0.87.0 - 2026-10-02

### Fixed

- `engines.call()` no longer raises `subprocess.TimeoutExpired`. When the engine process outlives `timeout` it is killed (as before) and an `LLMResult(returncode=124, failure_class=FailureClass.TIMEOUT)` is returned with the partial stdout / stderr captured before the kill plus a trailing `TIMEOUT: <cli> timed out after <n>s` line on stderr. `LLMResult` gains `failure_class` (`None` unless `call()` itself classified the failure) and `timed_out`; `engines.TIMEOUT_RETURNCODE` (124) is exported. `call_managed` honours `LLMResult.failure_class` before asking the output adapter, so a timeout is reported as `failure_class="TIMEOUT"` instead of `None`, and the new `fallback_on_timeout=False` argument lets a caller opt the one-shot fallback in for timeouts (no quota report is made for a timeout). `ghdag llm` therefore exits 124 with the partial output instead of a traceback. Until now the exception unwound `call_managed` and every caller above it, so e.g. issuesmith's `FailureClass.TIMEOUT` branch was unreachable and a cursor `agent -p` that had already committed but never exited turned a finished implementation into an andon (sumipan/nexus#4304).

## 0.86.0 - 2026-09-27

### Changed

- docs(readme): rewrite README for v0.85.0 (sumipan/nexus#3974)

## 0.85.0 - 2026-09-26

### Added

- feat(core): add sandbox="container" that wraps exec commands in docker run

## 0.84.0 - 2026-09-26

### Changed

- test(llm): build fixture script-range check from code points
- style(llm): drop downstream name from failure classification comment

### Fixed

- Failure classification (`classify_common_failure`) now recognises a missing / non-executable engine binary by its real CLI name (`ENGINE_SPECS[engine].cli`, e.g. cursor → `agent`) as `ENGINE_ENVIRONMENT_ERROR`: the name must follow line start / whitespace / `:` / a quote and be followed by `: command not found` / `: no such file or directory` / `: permission denied` (bash / sh format), or appear in the Python errno format (`No such file or directory: 'agent'`); `returncode == 127` plus `command not found` also qualifies. A bare word such as "agent" next to "permission denied" no longer counts. New optional `returncode` argument (positional 3-argument calls unchanged; `binary=""` keeps the legacy check); the four adapters pass it. Auth detection now matches whole words (`auth` / `authentication` / `authorization` / `unauthenticated` / `unauthorized` / `forbidden`) plus `oauth session expired` / `invalid api key` / `not logged in`, so e.g. "author" is no longer an auth error. `engines.call()` returns an `LLMResult` instead of raising when the binary cannot be launched: `FileNotFoundError` → `returncode=127`, stderr `<cmd>: command not found`; `PermissionError` → `126`, `<cmd>: permission denied`. `ghdag llm` therefore exits 127 without a traceback and `call_managed` reports `ENGINE_ENVIRONMENT_ERROR` (sumipan/nexus#4053).
- fix(llm): keep claude authentication_error classified as auth

## 0.83.0 - 2026-09-26

### Fixed

- `TaskLauncher.check_completions()`: when a `shell` engine task exits non-zero, stdout (including stderr merged via `2>&1`) is now written to `result_path` with a trailing `EXIT_CODE: <n>` line. Previously the result file was not created on failure, so failure output such as `PIPELINE_STATUS: ...` was lost. The done marker, `FailureClass`, and retry decisions are unchanged; successful shell results are unchanged (sumipan/nexus#3957).

## 0.82.0 - 2026-09-26

### BREAKING

- Direct LLM call path (`call` / `call_text` / `call_managed` / `ghdag llm`): engines declaring `InputMode.STDIN` (claude / cursor / gemini / codex) now receive the prompt body on stdin instead of argv. `build_llm_cmd` emits only the `-p` token for `PromptFlag.FLAG_ONLY` engines (e.g. `["agent", "--model", M, "-p"]`); codex argv is unchanged, and unknown engines (`spec is None`) keep `-p <prompt>`. This avoids `E2BIG` from Linux's 128 KiB per-argument `MAX_ARG_STRLEN` for large prompts. When `stdin_text` is also given, stdin is the non-empty parts joined by `"\n\n"` (`prompt + "\n\n" + stdin_text`; `prompt=""` → `stdin_text` only). codex no longer drops `prompt` when `stdin_text` is set. The exec.jsonl path (`render_exec_command`) is unchanged (sumipan/nexus#3805).

### Added

- `ghdag.vcs`: single git sink for upper layers (sumipan/nexus#3831). `GitSink.commit(paths, message, trailers=...)` stages only the given paths (ownership-checked against `allow_prefixes` and the `<owner>(` subject prefix), commits with `Layer:` / `Host:` trailers, then `fetch` → `rebase` → `push` under an `flock` on `<git-common-dir>/ghdag-vcs.lock`. Push policies `immediate` / `debounce:<sec>` / `manual` (+ `flush()`). Rebase conflicts save the local version to `inbox/`, reset to the remote with `--keep`, and raise `ConflictError`. Audit events `vcs_commit` / `vcs_skipped` / `vcs_conflict`. `get_sink(name)` returns a `NullSink` unless `ENABLE_GIT` is on and `GHDAG_VCS_CONFIG` names the sink. `LocalGitSink.create()` builds a throwaway bare remote + clone for tests. `ghdag.config.env`: `enable_git()` (`ENABLE_GIT`), `ghdag_vcs_config()` (`GHDAG_VCS_CONFIG`), `ghdag_state_dir()` / `state_dir(default)` (`GHDAG_STATE_DIR`) (sumipan/nexus#3831). Runtime state paths honour `GHDAG_STATE_DIR` (sumipan/nexus#3831): `DagConfig.exec_done_dir` default is now `None` (resolved to `state_dir("jobs")/"done"`), `DagConfig.quota_state_path`, `TaskLauncher` `running/` `events/` `.sessions/` `cancel/`, `PipelineState` quota state and `from_repo_root` `.pipeline-state`, and the `watch` / `trigger` / `status` / `dag recover` / `dag cancel` / `quota` CLI defaults. `--state-dir` (status / recover), `--state-path` (quota) and `--queue-dir` (dag cancel) now default to `None` and resolve in the command. With the variable unset every resolved path is unchanged; explicit arguments always win. `TaskLauncher`'s fallback `QuotaGate` now uses `config.quota_state_path` / `quota_audit_path`.

### Changed

- style: keep added comments and docs ASCII (no CJK in public repo)
- style: keep added comments and docs ASCII (no CJK in public repo)

## 0.81.0 - 2026-09-26

### Changed

- docs(readme): rewrite README for v0.80.0 (sumipan/nexus#3138)
- style: keep added comments and docs ASCII (no CJK in public repo)

## 0.80.0 - 2026-09-25

### Changed

- test(engines): rewrite existing CJK docstrings in test_engines.py to ASCII (sumipan/nexus#3188)

### Fixed

- `READONLY_OBSERVE` preset: `disallowed_tools` is now `("Edit", "NotebookEdit")` (`Write` removed). On claude, `sandbox="readonly"` maps to `--permission-mode plan`, whose harness writes the plan to `~/.claude/plans/*.md`; denying `Write` made that fail and wasted a turn every run. Plan mode itself still rejects writes to any other path. cursor / codex argv is unchanged (`disallowed_tools` is a noop there), and `TEXT_ONLY` / `JSON_ONLY` / `WEB_RESEARCH` still deny `Write` (sumipan/nexus#3188).

## 0.79.0 - 2026-09-25

### Added

- `GitHubClient(rate_limit_max_wait_sec=...)` / `GHDAG_RATE_LIMIT_MAX_WAIT_SEC`: per-caller cap on the synchronous sleep until rate-limit reset (403 + `X-RateLimit-Remaining: 0`). When the reset is further away than the cap, `RateLimitError(reset_at=...)` is raised immediately instead of sleeping. Precedence: constructor argument > env var > default 900 s (unchanged behaviour); a non-integer env value falls back to the default (sumipan/nexus#3752). `WorkflowDispatcher` (`ghdag watch`): when `list_all_issues` raises `RateLimitError` with `reset_at`, the reset time is kept and polls are skipped (log line `rate limited: skip poll until <ISO8601>`) until it passes; the first iteration after reset polls normally. Previously the watcher logged a failure every cycle and could miss triggers for minutes after the reset (sumipan/nexus#3752).

## 0.78.0 - 2026-09-25

### Added

- `GitHubClient(etag_cache_path=...)` / `GHDAG_ETAG_CACHE`: disk-persistent ETag cache so conditional requests (`If-None-Match` → 304, no rate-limit consumption) work across processes (e.g. issuesmith queue ticks, `ghdag watch --once`). Single JSON file keyed by `{sha256(token)[:8]}:{url}`, capped at 2000 entries with LRU eviction by last access, written atomically via `<path>.tmp.<pid>` + `os.replace`. A corrupt cache file is ignored (falls back to normal 200 fetches). When neither the argument nor the env var is set, behaviour is unchanged (in-memory only, no file I/O) (sumipan/nexus#3761).

## 0.77.0 - 2026-09-25

### Changed

- test(conventions): add 7 structural convention tests (issue #3572)

### Fixed

- fix: CP2 review fixes (LocalForge ForgePort coverage, restrict CHANGELOG exemption to Changed/Removed)

## 0.76.0 - 2026-09-25

### Fixed

- fix(dag): replace polling-based adopt test with event-driven sync to eliminate flakiness

## 0.75.0 - 2026-09-25

### Added

- `TaskLauncher.mark_interrupted_all()`: atomically writes `interrupted_at` (ISO8601) to `jobs/running/<uuid>.json` for all currently running tasks. Called by the SIGTERM handler before entering drain mode, so a subsequent SIGKILL cannot lose the interrupt record. Returns a list of successfully recorded uuids; missing or corrupt running files are skipped with a warning (sumipan/nexus#3666). `TaskLauncher.terminate_all()`: sends SIGTERM to all running task process groups and registers them in `_interrupting`. Called when the drain deadline is exceeded so that the engine can exit after kill-grace (sumipan/nexus#3666). `GHDAG_PREVIOUS_ATTEMPT` environment variable: set to `"interrupted"` when a task is relaunched after a SIGTERM-interrupted run, allowing the task command to detect and resume from a prior partial execution (sumipan/nexus#3666). `DagEngine` SIGTERM handler is now two-stage: first SIGTERM enters drain mode (`_draining = True`, calls `mark_interrupted_all`, sets `_drain_deadline = now + task_timeout`); a second SIGTERM while draining sets `_shutdown = True` for immediate exit. `task_timeout is None` sets the deadline to now (no drain: running tasks are terminated immediately and rerun after restart) (sumipan/nexus#3666). `DagEngine.run()` drain loop: while `_draining`, new task launches and dependency resolution are skipped; only `check_completions` and `_apply_pending_cancels` run. Exits when `running_count == 0` or `_shutdown`. When `_drain_deadline` is exceeded, calls `terminate_all()` to force-stop remaining tasks (sumipan/nexus#3666). `TaskLauncher.adopt_orphans()`: running files with `interrupted_at` are now treated as interrupted restarts instead of plain orphans. Live processes are killed (SIGTERM + kill_grace + SIGKILL); the running file is consumed and the task is queued for same-uuid rerun via `_pending_reruns`, up to `_MAX_INTERRUPTED_RERUNS = 1`. Tasks at the rerun limit fall back to `DONE_ORPHANED_ON_RESTART` with result `ORPHANED_ON_RESTART: interrupted rerun limit reached` (sumipan/nexus#3666). `TaskLauncher.check_completions()`: tasks in `_interrupting` (stopped by `terminate_all`) skip done-marker writing and `on_task_failure`; the running file is preserved so the next restart can detect and rerun them (sumipan/nexus#3666). `jobs/running/<uuid>.json` gains two optional fields: `interrupted_at` (ISO8601, written by `mark_interrupted_all`) and `interrupted_reruns` (int, written by `launch` when relaunching an interrupted task) (sumipan/nexus#3666).

### Fixed

- `TaskLauncher.check_completions()`: tasks stopped by `terminate_all()` are excluded from the `--resume` fallback relaunch (sumipan/nexus#3666).

## 0.74.0 - 2026-09-25

### Changed

- test(cleanup): make section comment ASCII-only (external_leak CJK check)

### Fixed

- `cleanup_queue` Phase 3 (catch-all sweep) no longer archives state files such as `quota-gate-override.json`, `quota-gate.json.lock`, or `issuesmith-brake.json.lock`. Phase 3 now only sweeps files whose name contains a 14-digit timestamp or 8-character hex token (UUID first group, short hex, or YYYYMMDD), which identifies them as one-shot artifacts. Additionally, Phase 3 now uses `st_mtime` instead of `st_birthtime` for age comparison, so files that are created once but updated in-place are not swept while they are still being modified. Sweep log lines now include `(token=…, age=…d)`. Resolves sumipan/nexus#3677.

## 0.73.0 - 2026-09-24

### Changed

- test(dag/recover): add regression and CLI output tests for result archiving (#3627)

## 0.72.0 - 2026-09-24

### Added

- `GitHubClient.get_issue_comments`, `issue_get(fields=["comments"])`, `milestone_list`, `list_issues`, `pr_checks`, and `run_logs_failed` now fetch all pages via `_paginate` instead of stopping at the first page. Previously `get_issue_comments` was capped at 30 (no `per_page` set), and the others at 100. Resolves sumipan/nexus#3696. `GitHubClient.pr_list` and `ForgePort.pr_list` default `limit` changed from `30` to `None` (fetch all matching PRs). Callers that pass an explicit `limit` value are unaffected. `LocalForge.pr_list` now only truncates when `limit` is explicitly provided. `GitHubClient._paginate` gains two new optional keyword arguments: `items_key` (extracts a nested list from dict-wrapped responses such as check-runs and jobs) and `max_items` (stops pagination early once the cumulative count reaches the limit).

## 0.71.0 - 2026-09-24

### Added

- `TaskLauncher.adopt_orphans(tasks)`: on runner restart, walks `jobs/running/*.json` and either adopts still-alive tasks (registering them in `_adopted` so they are not re-launched) or immediately closes dead ones with `DONE_ORPHANED_ON_RESTART` done marker and a `ORPHANED_ON_RESTART: runner restarted…` result. `is_running` / `running_count` now include adopted tasks; `check_completions` closes adopted tasks once their process group disappears. `DagEngine.run()` calls `adopt_orphans` once after the first exec-file load (sumipan/nexus#3259)
- `DONE_ORPHANED_ON_RESTART = "ORPHANED_ON_RESTART"` added to `ghdag.core.vocabulary`; `status._done_kind` maps it to `"other"` → `failed`, so `ghdag status` / `plan_recover` treat it as a recoverable failure

### Changed

- `ghdag dag recover` now archives existing result files to `<result>.prev-<ts>` before clearing the done marker (default behaviour). Use `--keep-results` to preserve the old result. Background: nexus #3259 observed that 5 consecutive re-runs after a runner crash all read the same 20:10 result because the old file was never removed. `execute_recover` gains `keep_results: bool = False` and `now: datetime | None` parameters; `RecoverStepInfo` gains `result_path`; `RecoverResult` gains `archived: list[tuple[str, str]]`

## 0.70.0 - 2026-09-24

### Fixed

- `CursorStreamAdapter.extract_error` now classifies `RetriableError:` lines from cursor stderr as `EngineError(kind=RATE_LIMIT, retryable=True)` for `[resource_exhausted]` or `EngineError(kind=CAPACITY, retryable=True)` for other codes, enabling the task launcher to write `DONE_ENGINE_ERROR` (non-final) and retry via the existing engine-error path instead of falling through to `PROCESS_ERROR` (sumipan/nexus#3476)

## 0.69.0 - 2026-09-23

### Fixed

- `GitHubClient.pr_get` now fetches all PR files via paginated `GET /pulls/{n}/files?per_page=100` instead of the first 30; `additions`/`deletions`/`changedFiles` use the PR body values as the source of truth (sumipan/nexus#3401)

## 0.68.0 - 2026-09-22

### Added

- Launched tasks receive `GHDAG_TASK_UUID` and `GHDAG_RESULT_PATH` in their environment (`task_launcher._task_env`), so an external dispatcher can call `QuotaGate.defer(task_uuid, ...)` before emitting `PIPELINE_STATUS: DEFERRED` (sumipan/nexus#3515 follow-up)

## 0.67.0 - 2026-09-22

> Y bump: `QuotaGate.defer` and `DONE_DEFERRED` are new public API (merged in PR #292 without a version bump; this release publishes them).

### Added

- `QuotaGate.defer(task_uuid, *, engine, after=None, role_engines=None, reason=None)` public API, `brake_state_path` secondary gate, `DONE_DEFERRED` (`PIPELINE_STATUS: DEFERRED`) handling in the task launcher and DAG engine requeue on release (sumipan/nexus#3515)

- `ghdag.audit.span` public API (`emit_span` / `make_span_id`). Moves the one-span-per-line `jobs/latency_span.jsonl` format and `LATENCY_SPAN_PATH` into the audit envelope unchanged from the host implementation (nexus #3323 / #3301 sub 8)

### Changed

- cursor stream-json result / `call_text` body: changed from "only the final complete assistant text after the last tool_call" (v0.60.0, nexus #3255) to turn reconstruction that joins each assistant turn with `"\n\n"` (nexus #3260). The events JSONL stays raw

### Fixed

- DAG timeout / cancel now sends SIGTERM→SIGKILL to the per-task process group (`start_new_session=True`), preventing descendant processes from being orphaned after the shell leader exits (nexus #3257)

### Added

- `ghdag.status` public API (`issue_status` / `running_tasks` with `IssueStatus` / `StepStatus` / `RunningTask`) and CLI `ghdag status --issue N --json` / `ghdag status --running --json` (nexus #3084)
- Add `FailureClass.INTERACTIVE_PROMPT` (cause `permanent` / retry_policy `forbidden`). Detects output where claude / cursor / codex ended with a question; the DAG does not retry and records the done marker `INTERACTIVE_PROMPT` and the question text (first 200 characters) in audit (nexus #3046)

### Changed

- Limit `FailureClass.INTERACTIVE_PROMPT` detection to exit ≠ 0, so a trailing `?` (ASCII or full-width) on a successful response is no longer a permanent failure. `looks_like_question` also detects the full-width question mark. `TaskMetrics.failure_class_reason` records the trailing line that triggered the detection (nexus #3175)
- Engine isolation is opt-in via `GHDAG_ENGINE_ISOLATION=1` (nexus #3174): claude gets `--disable-slash-commands` only when isolation is on; codex `call()` sets `CODEX_HOME=/var/tmp/ghdag-dag-codex/` only when isolation is on **and** `auth.json` exists there (otherwise skips with a stderr warning). Default (env unset) restores pre-v0.50.0 behavior so host skills / `~/.codex` auth work. cursor still reports `supports_capability(..., "isolation") == False`
- Unify state resolution in `pipeline.status.task_status` / `dag.recover.plan_recover` on `ghdag.status._step_status_core` / `_read_step_records` (nexus #3084)
- Dead-code cleanup, mypy `ignore_errors` removal, env accessors consolidation, and CHANGELOG versioning for 0.35.0–0.48.0 (nexus #3039)

## 0.48.0 — 2026-09-10

### Added

- `GitHubClient.list_all_issues(state)` / `get_last_rate_limit()`, and the same methods on `GitHubIssuePort`. Used for dispatcher batch polling and header-based rate limit observation (nexus #3070)
- Add tests for ETag / rate-limit retry / batch poll / rate observe (nexus #3070)

### Changed

- `WorkflowDispatcher.poll_once` / `_poll_nonterminal_closed` now fetch in bulk via `list_all_issues` and filter labels locally. `_observe_rate_limit` uses `get_last_rate_limit()` from response headers and temporarily doubles `polling_interval` when remaining is low (nexus #3070)
- `GitHubClient._request` gains ETag conditional requests and wait-then-retry-once on rate limit 403. `RateLimitError` gains `reset_at` (nexus #3070)

## 0.47.0 — 2026-09-10

### Changed

- `workflow/loader.py` now reads `label_namespace` / `transitions` / `reset_label` / `roles` / `step.role`, and `load_workflows()` calls `validate_workflow_roles` (nexus #3029)
- Add public arguments `order_builder` / `workflow_roles` to `LLMPipelineAPI.submit()`. The dispatcher no longer swaps the builder temporarily and goes through `submit(order_builder=...)` (nexus #3029)
- Switch the `dag.audit_hooks` import to `ghdag.io.audit` (nexus #3029)

## 0.46.0 — 2026-09-10

### Fixed

- `ghdag dag recover` collects running uuids from `jobs/running/*.json` and passes them as `running_uuids`, so the done markers of running steps are not removed (nexus #3035)
- Resume fallback now restarts asynchronously via `Popen` and no longer blocks the main loop (nexus #3035)
- `PipelineState` creates `QuotaGate` once at construction and reuses it (nexus #3035)
- `md_read` gains the same path traversal check as write (nexus #3035)
- UI running detection is now based on `jobs/running/<uuid>.json` (nexus #3035)
- Remove the unreachable `default_permission_applied` branch in `llm_pipeline` (nexus #3035)

## 0.45.0 — 2026-09-10

### Changed

- **Breaking:** the default time zone for audit / metrics timestamps changes from JST to UTC. Consolidated into `now_ts()` / `epoch_ts()`, which read `DagConfig.timezone` (default `"UTC"`) (nexus #3028)

### Added

- Add `DagConfig.audit_path` and make `config.audit_path` the default audit path for `ghdag run` (nexus #3028)
- UI `aggregate_task_status` counts `task_dep_failed` / `task_cancelled` as terminal events (nexus #3028)

## 0.44.0 — 2026-09-10

### Added

- Publish `ghdag.llm.engines.supports_capability(engine, capability)` (nexus #3034)

### Fixed

- `GitHubClient._paginate` now follows `Link: rel="next"` (nexus #3034)
- `GitHubClient()` / `_resolve_repo(None)` raise `GhdagError` when `GITHUB_REPOSITORIES` is unset (nexus #3034)
- Classify the codex usage limit as `QUOTA_EXHAUSTED` and extract `resume_at` (nexus #2959 / #2961)

## 0.43.0 — 2026-09-09

### Added

- DAG cursor / codex progress events: enable the `stream` capability, `CursorStreamAdapter` / `CodexJsonlAdapter`, `stream_fallback` annotation (#2967)

## 0.42.0 — 2026-09-09

### Added

- DAG claude progress events: `--output-format stream-json --verbose`, `jobs/events/<uuid>.jsonl`, `on_task_progress`, UI SSE `progress` (#2966)

## 0.41.0 — 2026-09-09

### Fixed

- Fix cursor / codex resume session recording to match actual CLI output. Add `--output-format json` to cursor on the DAG path (#2968)

## 0.40.0 — 2026-09-09

### Added

- DAG task cancellation: `jobs/running/` / `jobs/cancel/`, `DONE_CANCELLED`, `on_task_cancelled`, `ghdag dag cancel`. UI `/api/stop` now goes through the control-file path
- Add `headRefName` to `pr_get`

## 0.39.1 — 2026-09-06

### Added

- `pr edit` / `pr_update`
- `nonterminal_closed` (`reopen_issue` Port)
- `annotations.timeout_sec`

## 0.39.0 — 2026-09-05

### Added

- recover / redispatch (`dag/recover.py`, `ghdag dag recover`, `trigger --redispatch`, generation-tagged idempotency keys)

## 0.38.0 — 2026-09-05

### Added

- role-based admission (`StepConfig.role`, `QuotaGate.admit/begin_run(role, role_engines)`, `clear(ttl_seconds)`)

## 0.37.0 — 2026-09-05

### Added

- Gate registration via the `ghdag.gates` entry point (`load_entry_point_gates` / `get_gate`)
- `StepConfig.render` (`"frozen"` | `"live"`) and `python -m ghdag.workflow.render`

## 0.36.0 — 2026-09-04

### Changed

- **EngineSpec normalization** (`InputMode.STDIN/ARGV`, `cat_pipe` removed, all STDIN engines use `< order`)

### Added

- Session compaction (`llm/compaction.py`, `GHDAG_SESSION_COMPACTION`)
- `issue edit --title`

## 0.35.0 — 2026-09-03

### Added

- `call_managed()` / `ManagedResult`
- `TaskMetrics.additional_tags`

## 0.34.6 — 2026-09-03

### Changed

- Consolidate failure classification logic in `llm/adapters/failure_classification.py`

## 0.34.5 — 2026-09-02

### Added

- stderr failure classification (`classify_failure`), `FailureClass.AUTH / ENGINE_ENVIRONMENT_ERROR`, `EngineQuarantine` (300 seconds)

## 0.34.4 — 2026-09-02

### Added

- quota drain / resume, running registry (`begin_run` / `finish_run` / `wait_idle`), `ghdag quota drain/resume`

## 0.34.3 — 2026-09-02

### Added

- Add `ghdag.quota.QuotaGate`, which persists per-engine paused/available state and deferred tasks to `jobs/quota-gate.json` with exclusive, atomic writes
- Add `ghdag quota report|clear|status` so the host can report and read quota state as JSON
- Add `tests/test_quota.py` / `tests/dag/test_engine_quota.py`, pinning stale-notification ignore, release, runtime defer, and enqueue defer
- Add `drain/resume/begin_run/finish_run/wait_idle` to `QuotaGate`, exposing draining and the running registry as public API

### Changed

- Wire quota admission into `dag/engine.py` / `dag/task_launcher.py` / `io/exec_jsonl.py`: paused engines are not launched, and runtime quota exhaustion is deferred (the failure hook does not fire)
- Extend `core/ports/output.py` / `llm/adapters/{claude_json,codex}.py` with `EngineErrorKind.QUOTA_EXHAUSTED` and timezone-aware `resume_at` extraction
- Add `DEFERRED` display to `pipeline/status.py` / `ui/monitor.py` and update state precedence to done → running → deferred → pending deps → runnable
- Replace launch admission in `TaskLauncher` with `begin_run()`, guaranteeing rollback of the running reservation on `Popen` failure and `finish_run()` right after the subprocess exits
- Extend `ghdag quota drain|resume|status` to aggregate per-engine `queued/deferred/running/idle` and `unresolved` as JSON


## 0.34.2 — 2026-09-02

### Added

- Add `ghdag.llm.session`, publishing `SessionStore` (record/lookup/invalidate/gc) and `SessionRecord`. Reads both the new `.sessions/{key}.json` format (with `created_at`) and the old format (no `created_at`, mtime fallback)
- Add `tests/llm/test_session.py`, pinning record/lookup, expiry checks, invalidate, gc, and old-format compatibility

### Changed

- `llm/engines.py`: add `resume_session_id` to `call()`/`call_text()` and inject the `--resume` equivalent for supported engines (claude/cursor/codex). On success, the `session_id` extracted by the adapter is kept as `LLMResult.session_id` and is available via `TextResult.session_id`
- `core/command.py`: add `resume_session_id` to `build_llm_cmd()`. codex switches from `exec -` to `exec resume <session_id>`
- `dag/task_launcher.py`: stop calling `ghdag.io.sessions` directly; `resume_from` resolution and session recording now go through `SessionStore`


## 0.34.1 — 2026-09-02

### Added

- Add `extract_error()` to `EngineOutputAdapter`, standardizing `EngineError` (`kind`/`message`/`retryable`). `CodexAdapter`/`ClaudeJsonAdapter` extract error events such as capacity and rate-limit; `CursorAdapter`/passthrough are no-ops for backward compatibility
- Add `tests/llm/adapters/test_error_extraction.py`, pinning error extraction for codex/claude/cursor/passthrough

### Changed

- `llm/engines.py`: add `TextResult.error`; `call_text()` returns immediately with `success=False` and `body=""` when `extract_error()` detects an error, so error JSONL no longer leaks into the body
- `dag/task_launcher.py`: integrate adapter error detection; `retryable=True` records `ENGINE_ERROR` in done, anything else records `ENGINE_ERROR_FINAL`. `result_path` now receives an `ENGINE_ERROR (...)` summary instead of raw JSONL
- `pipeline/status.py` / `pipeline/wait.py` / `ui/monitor.py`: add state interpretation and display labels for `ENGINE_ERROR` / `ENGINE_ERROR_FINAL`
- `core/models/metrics.py`: add `FailureClass.ENGINE_ERROR` to classify engine-error failures explicitly


## 0.34.0 — 2026-09-02

### Added

- Add `ghdag watch --pause-file PATH`. While the file exists, `WorkflowDispatcher` stops `poll_once` / `dispatch` and GitHub API calls, and resumes automatically from the next loop once the file is removed
- Record `dispatcher_pause` / `dispatcher_resume` events in `jobs/audit.jsonl` when a pause starts or ends; the pause reason (file contents) is truncated to 500 characters
- Add `tests/workflow/test_dispatcher_pause.py`, pinning skip while paused, resume after release, audit de-duplication, and the reason length limit


## 0.33.0 — 2026-09-01

### Added

- Add `StepConfig.resume_from` and `io/sessions.py`, so the session of the parent task given by `resume_from_uuid` is saved to and read from `.sessions/<uuid>.json`
- Add `EngineOutputAdapter.extract_session_id()`, so `ClaudeJsonAdapter`/`CursorAdapter`/`CodexAdapter` extract the engine-specific session ID (`session_id` / `chat_id`)
- Add `tests/workflow/test_resume_from.py` / `tests/io/test_sessions.py` / `tests/llm/adapters/test_session_id_extraction.py`, pinning `resume_from` validation, command generation, and session save/extraction

### Changed

- `pipeline/llm_pipeline.py`: validate that `resume_from` points to a transitive ancestor and record `annotations.resume_from_uuid` at submit
- `core/command.py`: add `render_exec_command(..., resume_session_id=...)`; `claude`/`cursor` get `--resume` injected and `codex` switches to `exec resume <session_id>` (`gemini`/`shell` are unchanged)
- `dag/task_launcher.py`: look up the session store at launch to inject resume, and fall back automatically to the non-resume command on a session error


## 0.32.1 — 2026-09-01

### Changed

- Centralize done marker / queue scanning I/O in `ghdag.io.done` / `ghdag.io.queue`. `dag.state` / `pipeline.result` are re-export shims; `pipeline.status` / `wait` / `ui.monitor` / `maintenance` delegate (public API stays compatible) (nexus #2675)

### Added

- `tests/io/test_done.py` / `tests/io/test_queue.py`: tests pinning the done/queue I/O centralization and shim compatibility


## 0.32.0 — 2026-08-31

### Changed

- Centralize exec.jsonl reads and writes in `ghdag.io.exec_jsonl`. `dag` / `pipeline` / `cleanup` / `maintenance` / `ui` become delegating facades (public API stays compatible, `parse_jsonl` is a shim) (nexus #2674)
- Add `fcntl.LOCK_EX` to the rewrite equivalent to `ExecJsonlPruner.prune` (previously no lock was taken)
- Extend the `FanOutManager.append_task_fn` signature to `(line, parent_uuid)` (**breaking** for direct FanOutManager constructors)

### Added

- When appending fan-out child tasks, write an enqueue record with `source="fanout"`, `correlation_id=<parent uuid>` to `audit.jsonl` (closes the audit gap so every enqueue path is recorded)
- `tests/io/test_exec_jsonl.py` / `tests/test_fanout_audit.py`: tests pinning the centralization, locking, and fan-out audit


## 0.31.1 — 2026-08-31

### Changed

- Centralize audit I/O in `ghdag.io.audit` / `ghdag.io.audit_query` / `ghdag.io._rotate`. `pipeline.audit`, `pipeline.audit_query`, and `files._rotate` stay compatible as re-export shims (nexus #2673)
- Route JSON append + rotate in `files/writer.py` / `files/promote.py` / `tool/audit.py` through `io.audit.append_audit_record`
- Move read API imports in `ui/dashboard.py` / `ui/server.py` / `pipeline/__init__.py` to `io.audit_query`

### Added

- `tests/io/test_audit.py` / `tests/io/test_audit_query.py`: tests pinning the centralization, compatible imports, and canonical rotate location


## 0.31.0 — 2026-08-31

### Changed

- New `core/command.py`: consolidates `render_exec_command` / flag builders / `build_llm_cmd` / `EngineAdapter` / `get_adapter`. Removes the `pipeline ⇄ workflow` and `llm.spec ⇄ llm.engines` cycles. Old paths stay compatible as re-export shims (nexus #2656)
- `dag/audit_hooks.py`: `AuditHooks` moves to its canonical location. `pipeline/hooks.py` is a shim (allowed by import-linter) (nexus #2656)
- `llm/engines.py`: remove module-level `ENGINE_MODELS = load_engine_models()` in favor of lazy initialization via `get_engine_models()` (**breaking**: the `ENGINE_MODELS` variable is no longer public) (nexus #2656)
- `tool/audit.py`: import `_maybe_rotate` from `files._rotate` (removes the `tool → pipeline` reverse dependency) (nexus #2656)
- `pyproject.toml`: add five import-linter contracts (core independence, two-tower separation, io/ops isolation) (nexus #2656)
- `maintenance.py`: replace the `dag.state` dependency with a local helper for the ops contract (no behavior change) (nexus #2656)

### Added

- `tests/core/test_purity.py` / `test_no_import_side_effects.py`: core purity guard and tests pinning the absence of import side effects (nexus #2656)
- `ghdag.io`: placeholder package for Step 2 (referenced by import-linter contracts) (nexus #2656)

## 0.30.16 — 2026-08-31

### Changed

- New `core/` package: consolidates types, Protocols, and engine declaration data (exceptions, models, ports, capabilities, engine_spec, parsers) and removes the `llm ⇄ metrics` circular dependency. Old import paths stay fully compatible as re-export shims (nexus #2655)
- `metrics/parsers.py`: move `parse_token_count` / `parse_token_usage_json` to `core/parsers.py` (the old path still works through a shim)
- `llm/adapters/*`: import `TokenUsage` / parser functions via `core`

## 0.30.15 — 2026-08-31

### Changed

- `core/vocabulary.py`: consolidates done markers, `QUEUE_FILE_RE`, `PIPELINE_STATUS_RE`, and fan-out conventions as shared vocabulary. Hard-coded strings in cleanup / pipeline / dag are replaced with constant references (no behavior change). Remove `ghdag.workflow.github` from mypy overrides (nexus #2654)

## 0.30.14 — 2026-08-30

### Added

- `llm/capabilities.py`: add the `LLMCapabilities.sandbox` field (`"off"` / `"readonly"`, default `"off"`) and the `READONLY_OBSERVE` preset (`sandbox="readonly"` + `disallowed_tools` for editing tools). Allows observational Bash while the sandbox guarantees non-destructiveness (nexus #2640)
- `llm/engines.py`: the claude / codex / cursor flag builders map `sandbox="readonly"` to `--permission-mode plan` / `-s read-only` / `--sandbox enabled` respectively. Combining it with a bypass mode raises `ValueError`. gemini / shell are unsupported and raise `NotImplementedError` (nexus #2640)

### Fixed

- `llm/engines.py`: declare cursor's `disallowed_tools` in `_IGNORED_CAPABILITIES`, turning a silent ignore (there is no CLI flag) into a documented noop (nexus #2640)

## 0.30.13 — 2026-08-30

### Fixed

- `llm/engines.py`: fix `_build_codex_flags` ignoring `capabilities.permission_mode`, so `permission="dangerous_full_access"` did not add `--dangerously-bypass-approvals-and-sandbox` to the codex exec.jsonl command. `render_exec_command` always calls the builder with `dangerously_skip_permissions=False`, so without checking permission_mode the flag was dropped and codex started in the workspace-write sandbox (writable = cwd + /tmp only, network restricted) and could not write outside cwd. Now uses the same check as `_build_cursor_flags` (nexus #2627, PR #223)
- `github_client.py`: add bounded retries to `_request` for transient failures (RemoteDisconnected / BadStatusLine / ConnectionReset family / URLError / 502-504): at most 3 attempts, exponential backoff + jitter, Retry-After honored. Permanent errors such as 401/403/404 are not retried (nexus #2563, PR #222)
- `github_client.py`: replace DELETE+POST label handling in `issue_update` with `_converge_labels`. When a response is lost, it re-fetches the current labels and redoes only the remaining work to converge on the target state. DELETE 404 (already absent) counts as success (nexus #2563, PR #222)

### Notes

- 0.30.13 includes a fix required to run host workflows on the codex engine. Hosts need to update their ghdag install to this version and restart long-running processes that import ghdag

## 0.30.12 — 2026-08-14

### Changed

- `CHANGELOG.md`: add a `### Notes` section to the `## 0.30.10` entry, stating that `call_text` / `TextResult` are the prerequisite API for sumipan/nexus#2459 (nexus #2464)

## 0.30.11 — 2026-08-14

### Added

- `tests/llm/`: add adapter-specific tests for `call_text()` / `TextResult` (Issue #2463, PR #219)

## 0.30.10 — 2026-08-13

### Added

- `llm/engines.py`: add the `TextResult` frozen dataclass with `body` (extracted text), `success` (returncode == 0), and `raw` (LLMResult) fields, plus delegating `stderr` / `returncode` properties (Issue #2462)
- `llm/engines.py`: add `call_text()`, a superset API with the same signature as `call()`. It extracts text through the engine adapter, falls back to `raw.stdout` when empty, and returns `TextResult` (Issue #2462)
- `llm/__init__.py`: export `call_text` and `TextResult`
- `tests/llm/test_call_text.py`: add 11 unit tests for `TextResult` and `call_text()`

### Notes

- `call_text` / `TextResult` are the prerequisite API for sumipan/nexus#2459. Hosts can start migrating to `call_text` from this release onward.

## 0.30.9 — 2026-08-11

### Added

- `llm/spec.py`: add the `_dedupe_extra_args()` helper, which drops entries of `EngineSpec.extra_args` that duplicate flags emitted by `_CAPABILITY_FLAG_BUILDERS`. Fixes the codex engine exiting with an error because `--json --skip-git-repo-check` was expanded twice in argv (PR #215)

### Fixed

- `llm/engines.py`: register the codex engine's `allowed_tools` / `disallowed_tools` capabilities in `_UNSUPPORTED_CAPABILITIES`, turning an immediate `NotImplementedError` into a noop (PR #215)

## 0.30.7 — 2026-06-24

### Fixed

- `dag/task_launcher.py`: restore the `get_output_adapter()` wiring. Reconnects the adapter call lost in the PR #2243 refactor, converting the claude engine's stdout JSON via `adapter.extract_result_text()` before writing it to `result_path`. Removes `parse_token_count` (stderr grep) in favor of adapter-based usage extraction (Issue #2287)
- `dag/task_launcher.py`: add `cost_usd` / `cache_read_tokens` / `cache_creation_tokens` to all eight `TaskMetrics` paths (TIMEOUT / REJECTED / PIPELINE_FAILED / EMPTY_RESULT / SUCCESS / FANOUT_PARSE_FAILED / PROCESS_ERROR / UNKNOWN_FAILURE) (Issue #2287)

### Added

- `tests/dag/test_task_launcher_adapter_wiring.py`: regression test for the adapter wiring. Pins as a contract that claude JSON stdout does not leak into `result_path` and that metrics are non-null (Issue #2287)

## 0.30.6 — 2026-06-23

### Fixed

- `dag/parser.py`, `pipeline/llm_pipeline.py`, `pipeline/state.py`: fix `exec.md` references in code comments and docstrings to `exec.jsonl` (Issue #2245)

## 0.30.5 — 2026-06-23

### Changed

- `cleanup/`: convert `cleanup.py` into the `cleanup/` package, split into modules for maintainability (Issue #2244)
- `dag/`: extract `TaskLauncher`, `FanOutManager`, and `CircuitBreakerPolicy` from `DagEngine` (Issue #2243)

### Fixed

- `dag/task_launcher.py`: fix the type annotation of `fanout_manager` to `FanOutManager` (mypy)
- `dag/task_launcher.py`: remove dead code `_persist_fail_stdout`
- Multiple modules: remove unused imports (ruff lint)

## 0.30.4 — 2026-06-23

### Added

- `llm/adapters/`: new `EngineOutputAdapter` Protocol and `get_output_adapter()` registry. Implements an adapter layer that extracts the `result` text and `TokenUsage` (`token_count` / `cost_usd` / `cache_read_tokens` / `cache_creation_tokens`) from the claude engine's `--output-format json` stdout (Issue #2266)
- `metrics/models.py`: add `cost_usd`, `cache_read_tokens`, and `cache_creation_tokens` fields to `TaskMetrics` (Issue #2266)

### Fixed

- `dag/_state.py`: fix a race (TOCTOU) in `_remove_by_predicate` caused by separate LOCK_SH/LOCK_EX sections by using a single LOCK_EX (Issue #2266)
- `metrics/parsers.py`: add `parse_token_usage_json` to read usage accurately from claude JSON stdout. The previous stderr path is kept as a fallback (Issue #2266)

## 0.30.3 — 2026-06-21

### Added

- `dispatcher.py`: the `_READY_LABEL_RE` regex accepts both hyphen and colon label separators (both `research:ready` and `research-ready` transition correctly to `*:running` / `*-running`) (Issue #2258)
- `state.py`: `_remove_by_predicate` helper and `remove_idempotency_for_handler(workflow, handler, issue)` — per-handler idempotency key removal (retry one handler while keeping the idempotency records of the others) (Issue #2258)
- `LLMPipelineAPI`: add a delegating method for `remove_idempotency_for_handler` (Issue #2258)

## 0.30.2 — 2026-06-14

### Changed

- `README.md`: full rewrite for the v0.30.0 implementation, following OSS_QUALITY §4.1 (adds state_machine / tool/ / dashboard, removes label_state_machine, Error Reference with 17+7 types) (Issue #2154)

## 0.30.1 — 2026-06-14

### Changed

- `README.md`: full rewrite using the nine required sections of OSS_QUALITY.md chapter 4 (CLI Reference, Public API, Architecture, Configuration, and Error Reference synced with the current implementation) (Issue #2144)

## 0.28.16 — 2026-06-13

### Added

- `TOOL_EXIT_CODES` constant and `ToolDef.exit_codes` field: validation of the Phase D exit_code vocabulary (`success` / `failure` / `retry` / `skip`) (Issue #2091)
- `write_tool_fallback_audit()`: writes to audit.jsonl when a fallback chain fires (`event: tool.fallback`) (Issue #2091)

## 0.28.15 — 2026-06-13

### Added

- `LLMCapabilities.stream`: with `stream=True`, the claude engine gets `--output-format stream-json --verbose` and the JSONL stream output is parsed by `_extract_stream_result()` (Issue #2084)

## 0.28.14 — 2026-06-13

### Added

- `DagConfig.serialize_mutating`: mutual exclusion that serializes tasks with `annotations._mutates == "true"`. Works independently of `max_concurrency` (issuesmith #2079)

## 0.28.13 — 2026-06-13

### Added

- `dashboard.py`: dashboard aggregation over audit.jsonl (task status, token usage per correlation_id, circuit breaker firing frequency) (issuesmith #2083)
- Add `/api/dashboard/status`, `/api/dashboard/tokens`, `/api/dashboard/cb-firing` endpoints to `server.py` (issuesmith #2083)

## 0.28.11 — 2026-06-13

### Added

- `DagEngine` circuit breaker: shuts the engine down when consecutive task failures reach `max_consecutive_failures`. Adds `max_consecutive_failures` / `failure_window_sec` to `DagConfig` (issuesmith #2082)
- `ToolDef` / `FallbackEntry` dataclasses and `ToolRegistry.discover()`: Tool definition discovery by directory walk, file naming convention lint, and duplicate definition detection (issuesmith #2087)

## 0.28.10 — 2026-06-13

### Added

- `conditional_step.run_with_template()`: write diagnostic logs for LLM CLI runs (start, completion, elapsed time) to stderr (issuesmith #2070)
- `engine._check_completions()`: persist stdout to `{result_path}.fail` on PROCESS_ERROR / TIMEOUT (issuesmith #2070)

## 0.28.7 — 2026-06-08

### Added

- `detect_correlation_bursts()` / `get_correlation_top_n()`: helpers for detecting correlation_id bursts in audit.jsonl (issuesmith #2007)
- `WorkflowDispatcher._observe_correlation_burst()`: detects correlation bursts inside the polling loop and logs a warning. A cooldown suppresses duplicates (issuesmith #2007)

## 0.28.2 — 2026-05-30

### Added

- `StepConfig.skill_name`: optional field declaring the skill a workflow step invokes (backward compatible, default `None`)
- `typecheck_dag()`: DAG type checking based on the skill I/O contract (`SkillIOSpec`). Detects missing skills, `content_type` mismatches, and unsatisfied `consumes` warnings (#1406)

## 0.28.0 — 2026-05-29

### BREAKING

- `watch` repository resolution moves from the singular `GITHUB_REPOSITORY` to the plural `GITHUB_REPOSITORIES` (comma-separated `owner/repo` list). `GITHUB_REPOSITORY` is removed (#1322)

### Added

- `create_github_clients()`: builds a list of token clients, one per repository in `GITHUB_REPOSITORIES` (all clients share the same PAT)
- `WorkflowDispatcher` accepts multiple clients, and `poll_once` collects Issues across all repositories. `dispatch`, label transitions, error comments, and rate limit observation are routed to the client the Issue came from
- `WorkflowDispatcher(github_client=...)` accepts both a single client and a list of clients (backward compatible)

### Notes

- No dependency on the `gh` CLI (token-based operation is kept). No `gh` fallback is added
- Workflow labels are in practice scoped to a single repository, so collisions between the same Issue number and the same workflow across repositories (design.md file name, idempotency key) are out of scope for this change

## 0.26.0 — 2026-05-28

### BREAKING

- Physically remove the deprecated engine adapter aliases (`ClaudeAdapter` etc.) (#1267)

### Added

- CI: add ruff lint, a 70% coverage threshold, and import-linter layer checks (#1266)
- Introduce the `GhdagError` exception base class and re-parent all custom exceptions. Add an Error Reference section to the README (#1271)
- Remove mypy `ignore_errors = true` from six modules (#1272)
- Add the `llm.inference` audit event (`prompt_hash` and `latency_ms` fields) (#1275)

## 0.25.4 — 2026-05-26

### Fixed

- `ui`: fix a TypeError crash in `_ISSUESMITH_KEY_RE.match(None)` when `idempotency_key` in `exec.jsonl` is null. Double protection: normalization at the entry with `or ""` in `_parse_exec_jsonl`, and a None guard in `cmd_preview` (#141)

## 0.25.3 — 2026-05-26

### Added

- `fanout`: detect the fanout anchor by `ghdag_fanout:` startswith instead of relying on the `---` separator. Files without a separator now parse correctly (#1214)
- `ui`: add `idempotency_key` support to `MonitorTask` and `cmd_preview` (#1203)
- `pipeline/order`: `_check_missing_vars` now lists every missing variable in its KeyError message (#1193)

### Fixed

- `order`: change the exception type of `_check_missing_vars` from `KeyError` → `ValueError` (#137)
- `test`: make the gemini model validation test independent of the `GHDAG_LLM_MODELS` environment variable (#138)

## 0.25.2 — 2026-05-25

### Added

- Add `result_finalize` policy branching and clear result_path on retry (#1140)

### Fixed

- `parser`: normalize an empty result_path string to `None` to prevent `IsADirectoryError`
- Remove unused variables and imports flagged by ruff

## 0.25.1 — 2026-05-25

### Added

- Add `ghdag.dag.check_pipeline_status` to `ghdag.dag.__all__`, publishing it as public API (#1126)

### Docs

- Update the README for v0.25.0: document `--permission-mode`, `--max-concurrency`, `AuditHooks`, and the `StepConfig.permission` field

## 0.24.0 — 2026-05-24

### BREAKING

- `cleanup_queue()` default behavior change: automatic repair of Case D (orphan) and Case F (dead entry) is removed. The default is a detection report only (stderr output + `detected_orphan` / `detected_dead` counts). Automatic repair requires `auto_repair=True` / the `--auto-repair` flag (#1057)
- `md_append()` default behavior change: raises `AppendRecoverError` on a partial write (start marker detected). The previous RECOVERED behavior requires `allow_recover=True` (#1057)

### Changed

- `pipeline/audit.py`: remove daily rotation (`_should_rotate_daily`). Only size-based rotation (over 64MB) remains, so `cat jobs/audit.jsonl` shows the full log (#1057)
- `cleanup.py`: add `detected_orphan` / `detected_dead` fields to `CleanupResult`
- `cli.py`: add the `--auto-repair` flag to `ghdag cleanup`. The output format depends on whether `auto_repair` is set

## [0.22.0] - 2026-05-24 — BREAKING

### Changed

- `workflow/engine.py`: merge the four Adapter classes (`ClaudeAdapter` / `GeminiAdapter` / `CursorAdapter` / `ShellAdapter`) into `_GenericAdapter(spec: EngineSpec)`. `get_adapter()` now builds adapters on demand from `ENGINE_SPECS` (#1056)
- `llm/engines.py`: replace `_validate_capabilities_for_engine()` with a lookup driven by the `_UNSUPPORTED_CAPABILITIES` dict, and split `build_llm_cmd()` into `EngineSpec` fields + the `_CAPABILITY_FLAG_BUILDERS` map (removes hard-coded engine string branches)
- `pipeline/llm_pipeline.py`: `_build_exec_record()` references `llm.spec` directly instead of importing `workflow.engine.get_adapter()`, removing the Layer 1 → Layer 2 reverse dependency

### Removed

- **Remove exec.md text format support entirely (breaking change)**
  - `dag/parser.py`: remove the `parse_exec_md()` function
  - `dag/__init__.py`: remove the `parse_exec_md` export
  - `pipeline/state.py`: remove the `append_exec()` method, the `_is_jsonl_mode` property, and the text format parser. The default path of `from_repo_root()` changes from `queue/exec.md` → `jobs/exec.jsonl`
  - `pipeline/llm_pipeline.py`: remove `_submit_text()` / `_build_exec_line()` / the `_jsonl_mode` property. `submit()` always writes exec.jsonl in JSONL format
  - `llm/spec.py`: remove the `render_exec_line()` function
  - `workflow/engine.py`: remove `build_exec_line()` from the `EngineAdapter` Protocol and all Adapters
  - `ui/monitor.py`: remove `parse_exec_md()` / the text format exec.md detection path
  - `cleanup.py`: remove the `EXEC_LINE_RE` regex and the text format UUID extraction branch
  - `dag/engine.py`: remove the exec.md text format reading branch (the JSONL parser is always used)

### Deprecated

- `ClaudeAdapter` / `GeminiAdapter` / `CursorAdapter` / `ShellAdapter`: now deprecated aliases (to be removed in 0.24.0). Use `get_adapter(name)` or `_GenericAdapter(ENGINE_SPECS[name])` instead

### Migration

Convert the exec.md text format to the exec.jsonl JSONL format:

```sh
# Example: uuid: command → {"uuid": "uuid", "command": "command", "depends": []}
python3 -c "
import json, sys, re
for line in sys.stdin:
    line = line.rstrip()
    if not line or line.startswith('#'): continue
    m = re.match(r'^([a-fA-F0-9\-]+)(?:\[depends:([^\]]+)\])?:\s*(.+)', line)
    if m:
        uuid, deps, cmd = m.groups()
        print(json.dumps({'uuid': uuid, 'command': cmd, 'depends': (deps.split(',') if deps else [])}))
" < jobs/exec.md > jobs/exec.jsonl
```

## [0.18.0] - 2026-05-23

### Removed
- `ghdag shr` subcommand and `ghdag.shr` package (self-hosted runner management).
  SHR functionality has been superseded by polling-based execution (#986).

## [0.17.1] - 2026-05-22

### Added

- `pipeline/audit.py`: add `write_task_exit_audit()`. Records task exit events (completion, failure, rejection, dependency failure, empty result) to audit.jsonl in JSONL format
- `dag/hooks.py`: add `DefaultHooks.__init__(audit_path)`. Each hook method (`on_task_success`, `on_task_failure`, `on_task_rejected`, `on_task_dep_failed`, `on_task_empty_result`) calls `write_task_exit_audit()`
- `dag/engine.py`: when `hooks is None`, pass `audit.jsonl` in the parent directory of `exec_md_path` to `DefaultHooks` as `audit_path`

### Fixed

- `tests/`: remove unused pytest imports (ruff F401)

## [0.16.0] - 2026-05-16

### Added

- `workflow/engine.py`: add `ShellAdapter`. With `engine: shell`, the order file is executed directly as a bash script. The `prompt` / `model` parameters are ignored and the command is fixed to `bash -o pipefail {order_path}`
- `llm/_constants.py`, `llm/engines.py`: add the `shell` engine to `DEFAULT_ENGINE_MODELS` / `ENGINE_CLI` / `ENGINE_DEFAULTS` (the only model is `bash`)

## [0.15.1] - 2026-05-11

### Added

- `pipeline/`: add `QueueTaskStore` and `QueueTask`, providing an access API for result files (#76)
- `pipeline/audit.py`: support UUID extraction from JSON-format exec lines (#78)

### Fixed

- `pipeline/maintenance.py`: support JSONL prune, add done markers for orphans, and fix the done deletion order (#77)
- `pipeline/maintenance.py`: make `QUEUE_FILE_RE` handle compound tool names and stderr (#75)

## [0.15.0] - 2026-05-09

### Changed (BREAKING)

- `pipeline/`: remove all exec.md support code. Only the JSONL exec file format (`jobs/exec.jsonl`) is supported. No backward compatibility (#825)
- `pipeline/maintenance.py`: remove text-mode idempotency compatibility from `remove_idempotency_matching`, leaving JSONL only

### Added

- `pipeline/maintenance.py`: add the queue inspection and repair API (`validate_exec_jsonl`, `list_queue_tasks`, `inspect_exec_jsonl`, `repair_exec_jsonl`, `check_idempotency_key`) (#829)

### Fixed

- `pipeline/maintenance.py`: partially restore text-mode idempotency compatibility in `remove_idempotency_matching` / `_submit_text` (JSONL first, md fallback)

## [0.14.9] - 2026-05-08

### Added

- `dag/engine.py`: when the stdin redirect source file is missing, log a `WARNING` and skip the task with `SKIPPED_MISSING_INPUT`. No bash process is started, which avoids a false exit code 1 failure

## [0.14.8] - 2026-05-08

### Added

- `pipeline/`: JSONL mode that writes JSON records to exec.jsonl
- `llm/cursor.py`: add the `--force` flag when `dangerously_skip_permissions=True`

## [0.14.7] - 2026-05-07

### Fixed

- `ui/monitor.py`: read `result_path` directly from the JSONL field. Fixes the UI not showing a link when the command string does not contain the result path, as with claude jobs

## [0.14.6] - 2026-05-07

### Changed (BREAKING)

- Change the default of `DagConfig.exec_done_dir` from `"exec-done"` to `"jobs/done"`
- Rename `cleanup_queue()` arguments `queue_done_dir` → `archive_dir` and `exec_done_dir` → `done_dir`
- Change the `ghdag cleanup` CLI default paths from `queue-done/` → `jobs/archive/` and `exec-done/` → `jobs/done/`
- `ui/monitor.py`: remove the `exec-done/` fallback from `_detect_exec_done_dir()`. It always returns `jobs/done/`
- `ui/server.py`: change the done file path in the retry handler from `exec-done/` → `jobs/done/`
- Replace `exec-done` / `queue-done` in comments and docstrings with `jobs/done/` / `jobs/archive/`

## [0.14.5] - 2026-05-07

### Fixed

- `ui/monitor.py`: `build_rows` now auto-detects `exec_done_dir` via `_detect_exec_done_dir()` instead of hard-coding `repo_root / "exec-done"`. `jobs/done/` is preferred when it exists, otherwise it falls back to `exec-done/`. Fixes every task showing as pending in the UI after the migration to the unified `jobs/` directory

## [0.14.4] - 2026-05-07

### Fixed

- `ui/server.py`: change `_STATIC_DIR` from `Path(__file__).parent` to `importlib.resources.files()`, so `static/index.html` resolves correctly even with an editable install (regression prevention)
- `ghdag/__init__.py`: change `__version__` from a hard-coded value to `importlib.metadata.version("ghdag")`, keeping it in sync with `pyproject.toml`

### Added

- `tests/test_version.py`: add a test that `__version__` matches the version in `pyproject.toml`
- `tests/test_ghdag_ui.py`: add `TestStaticAssets`, which checks that static assets (`_STATIC_DIR` / `index.html`) exist

## [0.14.3] - 2026-05-07

### Fixed

- `ui/monitor.py`: change the `QUEUE_TS` regex to `(?:queue|jobs)/`, fixing an empty date-time column for tasks under `jobs/` paths (#57)

## [0.14.2] - 2026-05-07

### Added

- `ui`: the Web UI auto-detects and displays `jobs/exec.jsonl` (JSONL format). `queue/exec.md` is the fallback (#55)
- `ui`: add the `?max_visible=N` query parameter to `/api/rows` and `/api/stream`, so the number of visible rows can be changed per request (#55)
- `ui`: add a row-count dropdown (20/40/60/80/100 rows) to the dashboard (#55)

### Fixed

- `ui/monitor.py`: make `_ORDER_PATH_RE` / `_RESULT_PATH_RE` also match `jobs/` paths (#55)
- `ui/cli.py`: no longer `exit(1)` when `queue/exec.md` does not exist (#55)

## [0.14.1] - 2026-05-07

### Added

- `ghdag.dag`: add `parse_jsonl`, a parser for JSONL-format exec files, and export it in the public API (#762, #763)
- `ghdag.pipeline.audit`: add `write_llm_audit_log()`, which writes an audit log of LLM calls, and `ghdag llm --audit-path` (#756)

### Changed

- `workflow/schema.py` / `workflow/loader.py` / `llm/engines.py`: clean up the backward-compatibility branches for the `agent` engine and clarify `depends` validation and permission flag handling (#766)
- `workflow/engine.py`: add `build_exec_record()` and a direct stdout write path for JSONL tasks (#764)

### Fixed

- `dag`: improve the robustness of Layer 0 execution, stabilizing error paths in dependency resolution and state transitions (#760)
- `dag/parser.py`: publish `parse_exec_md` in `__all__`, fixing the F401 lint error on import (#763)

## [0.14.0] - 2026-05-06

### Added

- Add the `ghdag.metrics` package (`TaskMetrics` dataclass, `MetricsRecorder`, and the `parse_engine_model` / `parse_token_count` parsers) (#599)

### Changed (BREAKING)

- Add a `metrics: TaskMetrics` argument to `on_task_success` / `on_task_failure` / `on_task_rejected` / `on_task_empty_result` of the `DagHooks` Protocol (#599)
- `DagEngine._check_completions()` now builds `TaskMetrics` and passes it to each hook (#599)

#### Migration

Existing `DagHooks` implementations must add a `metrics: TaskMetrics` argument to each method signature (`on_task_dep_failed` is unchanged).

```python
# Before
def on_task_success(self, uuid: str, task: Task) -> None: ...

# After
from ghdag.metrics import TaskMetrics
def on_task_success(self, uuid: str, task: Task, metrics: TaskMetrics) -> None: ...
```

## [0.13.1] - 2026-05-05

### Fixed

- `workflow/engine.py`: change the exec line of `CursorAdapter.build_exec_line()` from the `cat ... | agent -p 'string'` form to the `agent -p --force < order_path` form (stdin redirect). The `agent` CLI ignores stdin when a string is passed to `-p 'string'`, so the order file contents were not read as the prompt (#43)

## [0.11.5] - 2026-05-03

### Fixed

- `workflow/dispatcher.py`: add per-trigger exception isolation to `WorkflowDispatcher.poll_once()`. Removes the SPOF where a `list_issues` failure for one trigger (e.g. a transient GitHub API error) stopped evaluation of other triggers / workflows. A failed trigger logs a warning and is skipped, and evaluation continues with the following triggers

## [0.11.4] - 2026-05-03

### Fixed

- `pipeline/llm_pipeline.py`: add a per-workflow `order_builders` dict to `LLMPipelineAPI`. Fixes a design bug where, with multiple workflows in `cli.py:_cmd_watch`, only the first workflow's `template_dir` was used for all workflows (#37)

## [0.11.3] - 2026-04-30

### Added

- `workflow/engine.py`: add `CursorAdapter`, which generates the exec line for `engine: cursor, model: gemini-3-flash`. It is invoked as `agent -p '<prompt>' --model '<model>' --force` (#35)

## [0.11.2] - 2026-04-29

### Added

- `dag.py`: add a `PIPELINE_STATUS` parser, implementing failure detection that does not depend on the exit code (#33)

## [0.11.1] - 2026-04-27

### Fixed

- `engines.py`: remove the excessive `disallowed_tools` validation from `_validate_capabilities_for_engine` for the cursor engine (#438)

## [0.11.0] - 2026-04-27

### Changed (BREAKING)

- Rebuild the `ghdag.llm.call()` API around capabilities. The `dangerously_skip_permissions` / `action` arguments are removed and replaced by the `capabilities: LLMCapabilities` argument (#432)
- Add the preset constants `TEXT_ONLY` / `JSON_ONLY` / `WEB_RESEARCH` / `DANGEROUS_FULL_ACCESS`
- With `output_format="json"`, a JSON parse failure raises `LLMParseError`
- Requesting capabilities unsupported by the gemini/cursor engines raises `NotImplementedError` (silent ignore is no longer allowed)

### Fixed

- `_config.py`: fall back to the defaults when the file named by the `GHDAG_LLM_MODELS` environment variable does not exist
- `dispatcher`: surface silent failures through error comments and detailed exception messages
- Remove the duplicate `post_comment` method and use the existing `add_comment`

## [0.10.3] - 2026-04-25

### Added

- Add `claude-opus-4-7` to the claude engine allow list in `DEFAULT_ENGINE_MODELS`.

## [0.10.2] - 2026-04-22

### Fixed

- `ghdag shr init` wrote a fully-resolved absolute path (e.g. `/Users/alice/.ghdag/runner/run.sh`) into Procfile, breaking the entry whenever the repository was synced to a host with a different home directory. The Procfile entry now uses `$HOME/...` when the runner directory lives under `$HOME`.

## [0.10.1] - 2026-04-22

### Fixed

- Wheel was missing `ghdag/ui/static/index.html`, causing `ghdag ui` to crash with `FileNotFoundError` on first request. Added `[tool.setuptools.package-data]` so static assets are bundled.

## [0.8.0] - 2026-04-18

### Added

- `template_dir` setting in workflow YAML to configure the template directory per workflow (#14)
- Relative `template_dir` paths are resolved relative to the workflow definition file's directory
- Falls back to `"templates"` when `template_dir` is not specified (backward compatible)

## [0.7.2] - 2026-04-17

### Fixed

- `DagEngine._launch_task()` now passes `cwd` to `subprocess.Popen`, preventing task failures when the parent process's working directory differs from the repository root

### Added

- `DagConfig.cwd` field — explicit working directory for task subprocesses (defaults to `None`, inheriting parent cwd for backward compatibility)
- CLI `ghdag run` auto-derives `cwd` from `exec_md` path (parent of `queue/`)

## [0.5.0] - 2026-04-13

### Added

- `ghdag shr` subcommand for self-hosted runner management (`init`, `status`, `remove`)
- Daemon management via overmind integration (migrated from launchd)
- `ghdag watch --once` flag for single-shot event-driven execution via GitHub Actions webhooks

## [0.4.0] - 2026-04-12

### Added

- Template context now includes `result_filename` and `${dep_id}_result_filename` for referencing pipeline step outputs (#2, PR #4)
- `context_hook` support in workflow YAML for injecting custom context via external scripts (#3, PR #5)

## [0.3.0] - 2026-04-11

### Added

- Layer 2: CLI (`ghdag run`, `ghdag watch`) and watcher module migrated from the host repository
- Extended workflow schema: multi-step DAG, `--model` flag, issue context injection, backward guard, reset handler
- Trigger entry validation for `label` and `handler` fields in the loader
- `--hooks` option for `ghdag run` to inject DagHooks from external modules

### Changed

- `requires-python` set to `>=3.11`

### Fixed

- `TemplateOrderBuilder` now uses `safe_substitute()` to avoid `KeyError` on template variables intended as AI instructions (e.g. `$base_branch`)
- `_load_hooks()` inserts `cwd` into `sys.path` so a hooks module under the working directory (e.g. `--hooks scripts.my_hooks`) resolves correctly

## [0.2.0] - 2026-03-01

### Added

- Layer 1: GitHub adapter (pipeline module) for reading issues and projects via the GitHub API
- State mapping between GitHub issue/project data and Layer 0 engine state

## [0.1.0] - 2026-02-01

### Added

- Initial package setup with DAG engine extracted from the host repository
- Layer 0: Core DAG engine, state machine, and workflow schema parser
- `pyproject.toml` with `setuptools` build backend and `pytest` dev dependency
