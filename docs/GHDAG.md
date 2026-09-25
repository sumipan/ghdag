# ghdag — package notes

Implementation code under `src/ghdag/` is the source of truth. Nexus host docs live in the nexus repository (`docs/GHDAG.md`); this file tracks package-local reference material.

## Status API (nexus #3084)

公開モジュール `ghdag.status` は Issue × handler の現在世代 DAG 状態を返す。

| シンボル | 役割 |
|---|---|
| `issue_status(issue_number, *, handler, workflow, exec_jsonl_path, state_dir, done_dir, ...)` | 世代・各ステップの `status` / `orphan_uuids` / `running` |
| `running_tasks(running_dir)` | `jobs/running/*.json` から経過時間付き一覧 |
| `IssueStatus` / `StepStatus` / `RunningTask` | 上記の戻り値 dataclass |

ステップ `status` 語彙: `success | failed | pending | running | skipped | cancelled | dep_failed`。

判定コア `_step_status_core` は `pipeline.status.task_status`（UI `/api/rows`）と共有する。`dag.recover.plan_recover` は `_read_step_records` を共有し、Recover 向けに status を粗くする。

### CLI: `ghdag status`

```bash
ghdag status --issue N --handler H --workflow W --json \
  [--exec-jsonl PATH] [--state-dir PATH] [--done-dir PATH] [--running-dir PATH]

ghdag status --running --json [--exec-jsonl PATH] [--running-dir PATH]
```

`--exec-jsonl` 未指定時は環境変数 `GHDAG_EXEC_JSONL`、それも無ければ `jobs/exec.jsonl`。

## Environment variables

All reads go through `ghdag.config.env`. Other modules must not call `os.environ.get` for these keys.

| Variable | Accessor | Default | Purpose |
|---|---|---|---|
| `GITHUB_TOKEN` | `github_token()` | unset | GitHub REST/GraphQL auth (preferred) |
| `GH_TOKEN` | `github_token()` | unset | Fallback when `GITHUB_TOKEN` is unset |
| `GITHUB_REPOSITORIES` | `github_repositories_raw()` | `""` | Comma-separated `owner/repo` list |
| `GHDAG_LLM_MODELS` | `ghdag_llm_models()` | unset | Path to engine model allowlist YAML |
| `GHDAG_SAFE_DEFAULT_PERMISSION` | `ghdag_safe_default_permission()` | unset → `text_only` | Permission preset when step has none |
| `GHDAG_SESSION_COMPACTION` | `session_compaction_enabled()` | off | Opt-in session compaction (`1`/`true`/`yes`/`on`) |
| `GHDAG_AUDIT_PATH` | `ghdag_audit_path()` | unset | Override audit.jsonl path (`ghdag llm` / UI) |
| `LATENCY_SPAN_PATH` | `latency_span_path()` | unset → `jobs/latency_span.jsonl` | Override latency span JSONL path (`ghdag.audit.span`) |
| `GHDAG_TOKEN_WARN_THRESHOLD` | `ghdag_token_warn_threshold()` | `500000` | UI token-usage warning threshold |
| `GHDAG_FORGE` | `ghdag.forge.get_forge` | `github` | Forge backend: `github` (default) or `local` |
| `GHDAG_FORGE_ROOT` | `ghdag.forge.get_forge` | unset | Data directory for `GHDAG_FORGE=local` (required when local) |
| `GHDAG_EXEC_JSONL` | CLI `ghdag status` | `jobs/exec.jsonl` | Default exec.jsonl path for status CLI |
| `ENABLE_GIT` | `enable_git()` | off | Allow `ghdag.vcs.get_sink` to return a real `GitSink` (`1`/`true`/`yes`, case-insensitive) |
| `GHDAG_VCS_CONFIG` | `ghdag_vcs_config()` | unset | Path to the VCS sink YAML read by `get_sink` |
| `GHDAG_STATE_DIR` | `ghdag_state_dir()` / `state_dir(default)` | unset → current paths | Move runtime state (`done/` `running/` `events/` `.sessions/` `cancel/` `quota-gate.json` `.pipeline-state/`) out of the repo |

## GHDAG_STATE_DIR (nexus #3831)

`ghdag.config.env.state_dir(default)` returns `Path($GHDAG_STATE_DIR).expanduser()` when the variable is non-empty, otherwise `Path(default)`. Every caller passes its historical default, so behaviour is unchanged when the variable is unset.

| Path | Unset | Set (`<S>`) |
|---|---|---|
| `DagConfig.exec_done_dir` | `jobs/done` | `<S>/done` |
| `DagConfig.quota_state_path` | `<exec.jsonl parent>/quota-gate.json` | `<S>/quota-gate.json` |
| `TaskLauncher` `running/` `events/` `.sessions/` `cancel/` | parent of `exec_done_dir` | `<S>/...` |
| `PipelineState` quota state | `<exec.jsonl parent>/quota-gate.json` | `<S>/quota-gate.json` |
| `PipelineState.from_repo_root` | `<root>/.pipeline-state` | `<S>/.pipeline-state` |
| `ghdag watch` / `trigger` / `status` / `dag recover` state dir | `.pipeline-state` (derived) | `<S>/.pipeline-state` |
| `ghdag dag cancel --queue-dir` | `jobs` | `<S>` |
| `ghdag quota * --state-path` | `jobs/quota-gate.json` | `<S>/quota-gate.json` |

`exec.jsonl`, `audit.jsonl`, `.ghdag.lock` and order/result `.md` files stay in `jobs/`. Explicit arguments (`DagConfig(exec_done_dir=...)`, `--state-dir`, `--state-path`, `--queue-dir`) always win. `ghdag ui` and `ghdag cleanup` do not follow `GHDAG_STATE_DIR` yet.

## ghdag.vcs (nexus #3831)

A single git sink for upper layers. `ghdag.vcs` imports only `ghdag.io` and `ghdag.config.env` (checked by import-linter).

```python
from ghdag.vcs import get_sink

sink = get_sink("notes")
result = sink.commit(["diary/2026-09-25.md"], "host(diary): 2026-09-25", trailers={"Execution-Id": "..."})
```

`get_sink(name)` resolution order:

1. `ENABLE_GIT` off → `NullSink(reason="ENABLE_GIT unset")`
2. `GHDAG_VCS_CONFIG` unset → warning + `NullSink(reason="GHDAG_VCS_CONFIG unset")`
3. `sinks.<name>` missing → `ValueError`
4. otherwise → `GitSink(**sinks[name], name=name, audit_path=<audit_path or GHDAG_AUDIT_PATH>)`

Constructing `GitSink` / `LocalGitSink` directly bypasses the gate.

```yaml
audit_path: /abs/path/jobs/audit.jsonl   # optional
sinks:
  notes:
    repo_root: /abs/path/notes
    branch: main
    remote: origin          # optional (default origin)
    owner: host
    layer: host             # optional (default owner)
    allow_prefixes: ["diary/", "weekly/"]
    push: immediate         # immediate | debounce:<sec> | manual
```

`GitSink.commit(paths, message, *, trailers=None)`:

| Step | Behaviour |
|---|---|
| Ownership | Every path must start with one of `allow_prefixes`, and the subject must start with `<owner>(`; otherwise `OwnershipError` before any git call |
| Lock | `flock` on `<git-common-dir>/ghdag-vcs.lock`, held until push finishes (shared across processes) |
| Stage | `git add -- <paths>` only; no diff → `skipped=True, reason="no changes"` |
| Commit | `git commit -- <paths>` with trailers `Layer:` / `Host:` / caller trailers (`Execution-Id`, `Correlation-Id`, ...) |
| Push policy | `immediate` syncs now; `debounce:<sec>` syncs only if the last push (`<git-common-dir>/ghdag-vcs-last-push`) is older than `<sec>` (else `reason="push deferred"`); `manual` never syncs. `flush()` pushes pending commits. Invalid policy → `ValueError` |
| Sync | `fetch` → `rebase --autostash <remote>/<branch>` → `push HEAD:<branch>`; non-fast-forward is retried up to 2 times, then `pushed=False, reason="push failed: ..."` |
| Conflict | `rebase --abort`, save the HEAD version of each unpushed file to `<repo_root>/inbox/<YYYYmmddTHHMMSS>-<path with / as __>`, `git reset --keep <remote>/<branch>`, raise `ConflictError(inbox_paths=...)` |
| Audit | When `audit_path` is set: one `vcs_commit` / `vcs_skipped` / `vcs_conflict` line with `sink` / `owner` / `layer` / `host` / `paths` / `sha` / `pushed` / `reason` / `execution_id` / `correlation_id` |

`LocalGitSink.create(tmp_dir, owner=..., allow_prefixes=...)` builds `<tmp_dir>/remote.git` (bare) and `<tmp_dir>/work` (clone with one commit) for tests.

## ForgePort (nexus #3099 / #3100 / #3101)

Issue / PR / label / milestone / Actions 操作は `ghdag.core.ports.forge.ForgePort` に抽象化されている。実装は 2 つ:

| 実装 | 選択 | 役割 |
|---|---|---|
| `GitHubClient` | `GHDAG_FORGE=github`（既定） | 本番 GitHub REST。挙動不変 |
| `LocalForge` | `GHDAG_FORGE=local` + `GHDAG_FORGE_ROOT` | ファイルベース（`<root>/.forge/`）。オフライン実行・契約テスト用 |

`ghdag.forge.get_forge(repo=None)` が実装を返す。`github_cli` と `workflow.state_machine` は factory 経由のみ（`GitHubClient(` 直生成しない）。

### LocalForge の制限

- `reviewDecision` は常に `APPROVED`
- `pr_checks` は未設定時常に success（`checks_command` で上書き可）
- Actions（`run_*`）はスタブ（不在 / 空）
- `api repos/...` のうち milestones / timeline / pulls / repo / issues は型付きメソッドへルーティング。それ以外は LocalForge では未対応

## Cleanup notes (nexus #3039)

Retained despite earlier “unused” candidates:

- `metrics.MetricsRecorder` — used by nexus `scripts/diary_hooks.py` and issuesmith
- `markdown.body_editor` — used by issuesmith
- `io.sessions` — used by nexus `tools/mltgnt_bridge/`
