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
| `GHDAG_TOKEN_WARN_THRESHOLD` | `ghdag_token_warn_threshold()` | `500000` | UI token-usage warning threshold |
| `GHDAG_FORGE` | `ghdag.forge.get_forge` | `github` | Forge backend: `github` (default) or `local` |
| `GHDAG_FORGE_ROOT` | `ghdag.forge.get_forge` | unset | Data directory for `GHDAG_FORGE=local` (required when local) |
| `GHDAG_EXEC_JSONL` | CLI `ghdag status` | `jobs/exec.jsonl` | Default exec.jsonl path for status CLI |

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
