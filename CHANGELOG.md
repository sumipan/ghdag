# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.14.5] - 2026-05-07

### Fixed

- `ui/monitor.py`: `build_rows` の `exec_done_dir` を `repo_root / "exec-done"` ハードコードから `_detect_exec_done_dir()` による自動検出に変更。`jobs/done/` が存在する場合はそちらを優先し、存在しない場合は `exec-done/` にフォールバック。`jobs/` ディレクトリ統合移行後に UI のタスクステータスが全て pending になっていた問題を修正

## [0.14.4] - 2026-05-07

### Fixed

- `ui/server.py`: `_STATIC_DIR` を `Path(__file__).parent` から `importlib.resources.files()` に変更。editable インストール時でも `static/index.html` が正しく解決されるように修正（再発防止）
- `ghdag/__init__.py`: `__version__` をハードコード値から `importlib.metadata.version("ghdag")` に変更。`pyproject.toml` と自動同期

### Added

- `tests/test_version.py`: `__version__` と `pyproject.toml` のバージョン一致を確認するテストを追加
- `tests/test_ghdag_ui.py`: static アセット（`_STATIC_DIR` / `index.html`）の存在確認テスト `TestStaticAssets` を追加

## [0.14.3] - 2026-05-07

### Fixed

- `ui/monitor.py`: `QUEUE_TS` 正規表現を `(?:queue|jobs)/` に変更し、`jobs/` パスのタスクで日時列が空になっていたバグを修正 (#57)

## [0.14.2] - 2026-05-07

### Added

- `ui`: Web UI が `jobs/exec.jsonl`（JSONL 形式）を自動検出して表示できるよう対応。`queue/exec.md` はフォールバック (#55)
- `ui`: `/api/rows` と `/api/stream` に `?max_visible=N` クエリパラメータを追加し、表示件数をリクエスト単位で変更可能に (#55)
- `ui`: ダッシュボードに表示件数プルダウン（20/40/60/80/100件）を追加 (#55)

### Fixed

- `ui/monitor.py`: `_ORDER_PATH_RE` / `_RESULT_PATH_RE` を `jobs/` パスにも対応 (#55)
- `ui/cli.py`: `queue/exec.md` が存在しない場合に `exit(1)` していた動作を撤廃 (#55)

## [0.14.1] - 2026-05-07

### Added

- `ghdag.dag`: JSONL 形式の exec ファイル向けパーサー `parse_jsonl` を追加し、公開 API へエクスポート (#762, #763)
- `ghdag.pipeline.audit`: LLM 呼び出しの監査ログを書き出す `write_llm_audit_log()` と `ghdag llm --audit-path` を追加 (#756)

### Changed

- `workflow/schema.py` / `workflow/loader.py` / `llm/engines.py`: `agent` エンジンの後方互換分岐を整理し、`depends` 検証と権限フラグの扱いを明確化 (#766)
- `workflow/engine.py`: JSONL タスク向けに `build_exec_record()` と stdout 直接書き込み経路を追加 (#764)

### Fixed

- `dag`: Layer 0 実行のロバストネスを改善し、依存解決・状態遷移時の異常系を安定化 (#760)
- `dag/parser.py`: `parse_exec_md` を `__all__` に公開し、import 時の F401 lint エラーを解消 (#763)

## [0.14.0] - 2026-05-06

### Added

- `ghdag.metrics` パッケージ（`TaskMetrics` データクラス・`MetricsRecorder`・`parse_engine_model` / `parse_token_count` パーサー）を追加 (#599)

### Changed (BREAKING)

- `DagHooks` Protocol の `on_task_success` / `on_task_failure` / `on_task_rejected` / `on_task_empty_result` に `metrics: TaskMetrics` 引数を追加 (#599)
- `DagEngine._check_completions()` で `TaskMetrics` を構築し、各フックに渡すよう変更 (#599)

#### Migration

既存の `DagHooks` 実装は各メソッドのシグネチャに `metrics: TaskMetrics` 引数を追加する必要があります（`on_task_dep_failed` は変更なし）。

```python
# Before
def on_task_success(self, uuid: str, task: Task) -> None: ...

# After
from ghdag.metrics import TaskMetrics
def on_task_success(self, uuid: str, task: Task, metrics: TaskMetrics) -> None: ...
```

## [0.13.1] - 2026-05-05

### Fixed

- `workflow/engine.py`: `CursorAdapter.build_exec_line()` の exec line を `cat ... | agent -p 'string'` 形式から `agent -p --force < order_path` 形式（stdin リダイレクト）に変更。`agent` CLI は `-p 'string'` に文字列を渡すと stdin を無視する仕様のため、order ファイルの内容がプロンプトとして読まれなかったバグを修正 (#43)

## [0.11.5] - 2026-05-03

### Fixed

- `workflow/dispatcher.py`: `WorkflowDispatcher.poll_once()` に per-trigger 例外隔離を追加。ある trigger の `list_issues` 失敗（過渡的な GitHub API エラー等）が他 trigger / 他 workflow の評価を巻き添えで停止させる SPOF を解消。失敗した trigger は warning ログを出してスキップし、後続 trigger の評価を続行する

## [0.11.4] - 2026-05-03

### Fixed

- `pipeline/llm_pipeline.py`: `LLMPipelineAPI` に per-workflow `order_builders` 辞書を追加。`cli.py:_cmd_watch` が複数ワークフローを持つとき、先頭ワークフローの `template_dir` だけが全ワークフローに使われていた設計バグを修正 (#37)

## [0.11.3] - 2026-04-30

### Added

- `workflow/engine.py`: `CursorAdapter` を追加。`engine: cursor, model: gemini-3-flash` の exec line を生成できるように。`agent -p '<prompt>' --model '<model>' --force` 形式で呼び出す (#35)

## [0.11.2] - 2026-04-29

### Added

- `dag.py`: `PIPELINE_STATUS` パーサを追加。exit code に依存しない failure detection を実装 (#33)

## [0.11.1] - 2026-04-27

### Fixed

- `engines.py`: cursor engine の `_validate_capabilities_for_engine` から `disallowed_tools` 過剰検証を削除 (#438)

## [0.11.0] - 2026-04-27

### Changed (BREAKING)

- `ghdag.llm.call()` の API を能力ベースに作り直し。`dangerously_skip_permissions` / `action` 引数を廃止し、`capabilities: LLMCapabilities` 引数に置換 (#432)
- プリセット定数 `TEXT_ONLY` / `JSON_ONLY` / `WEB_RESEARCH` / `DANGEROUS_FULL_ACCESS` を追加
- `output_format="json"` 指定時、JSON parse 失敗で `LLMParseError` を送出
- gemini/cursor engine で非対応の capabilities を要求した場合 `NotImplementedError` を送出（サイレント無視を禁止）

### Fixed

- `_config.py`: `GHDAG_LLM_MODELS` 環境変数のファイルが存在しない場合にデフォルトへフォールバックするよう修正
- `dispatcher`: サイレント失敗をエラーコメントと詳細な例外メッセージで可視化
- 重複メソッド `post_comment` を削除し既存の `add_comment` を使用

## [0.10.3] - 2026-04-25

### Added

- `claude-opus-4-7` を `DEFAULT_ENGINE_MODELS` の claude エンジン許可リストに追加。

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

- Layer 2: CLI (`ghdag run`, `ghdag watch`) and watcher module migrated from diary repo
- Extended workflow schema: multi-step DAG, `--model` flag, issue context injection, backward guard, reset handler
- Trigger entry validation for `label` and `handler` fields in the loader
- `--hooks` option for `ghdag run` to inject DagHooks from external modules

### Changed

- `requires-python` set to `>=3.11`

### Fixed

- `TemplateOrderBuilder` now uses `safe_substitute()` to avoid `KeyError` on template variables intended as AI instructions (e.g. `$base_branch`)
- `_load_hooks()` inserts `cwd` into `sys.path` so `--hooks scripts.diary_hooks` resolves correctly

## [0.2.0] - 2026-03-01

### Added

- Layer 1: GitHub adapter (pipeline module) for reading issues and projects via the GitHub API
- State mapping between GitHub issue/project data and Layer 0 engine state

## [0.1.0] - 2026-02-01

### Added

- Initial package setup with DAG engine extracted from diary repo
- Layer 0: Core DAG engine, state machine, and workflow schema parser
- `pyproject.toml` with `setuptools` build backend and `pytest` dev dependency
