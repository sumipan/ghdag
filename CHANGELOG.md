# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).


## 0.34.0 — 2026-09-02

### Added

- `ghdag watch --pause-file PATH` を追加。指定ファイルが存在する間は `WorkflowDispatcher` が `poll_once` / `dispatch` と GitHub API 呼び出しを停止し、ファイル削除後に次ループから自動再開する
- pause 開始/解除時に `jobs/audit.jsonl` へ `dispatcher_pause` / `dispatcher_resume` イベントを記録し、pause 理由（ファイル本文）は 500 文字で切り捨てる
- `tests/workflow/test_dispatcher_pause.py` を追加し、pause 中スキップ・解除後再開・audit の重複抑止・reason 文字数上限を固定化


## 0.33.0 — 2026-09-01

### Added

- `StepConfig.resume_from` と `io/sessions.py` を追加し、`resume_from_uuid` で指定した親タスクの session を `.sessions/<uuid>.json` に保存・参照できるようにした
- `EngineOutputAdapter.extract_session_id()` を追加し、`ClaudeJsonAdapter`/`CursorAdapter`/`CodexAdapter` が engine 固有の session ID（`session_id` / `chat_id`）を抽出できるようにした
- `tests/workflow/test_resume_from.py` / `tests/io/test_sessions.py` / `tests/llm/adapters/test_session_id_extraction.py` を追加し、`resume_from` 検証・コマンド生成・session 保存/抽出を固定化した

### Changed

- `pipeline/llm_pipeline.py`: `resume_from` が推移的祖先を指すことを検証し、submit 時に `annotations.resume_from_uuid` を記録するようにした
- `core/command.py`: `render_exec_command(..., resume_session_id=...)` を追加し、`claude`/`cursor` は `--resume` 注入、`codex` は `exec resume <session_id>` に切り替えるようにした（`gemini`/`shell` は従来どおり）
- `dag/task_launcher.py`: launch 時に session ストアを参照して resume 注入し、session エラー時は自動で非 resume コマンドへフォールバックするようにした


## 0.32.1 — 2026-09-01

### Changed

- done マーカー / queue 走査 I/O を `ghdag.io.done` / `ghdag.io.queue` に一元化。`dag.state` / `pipeline.result` は re-export shim、`pipeline.status` / `wait` / `ui.monitor` / `maintenance` は委譲（公開 API は互換維持）(nexus Issue #2675)

### Added

- `tests/io/test_done.py` / `tests/io/test_queue.py`: done/queue I/O 一元化・shim 互換の固定テスト


## 0.32.0 — 2026-08-31

### Changed

- exec.jsonl の読み書きを `ghdag.io.exec_jsonl` に一元化。`dag` / `pipeline` / `cleanup` / `maintenance` / `ui` は委譲ファサード化（公開 API は互換維持、`parse_jsonl` は shim）(nexus Issue #2674)
- `ExecJsonlPruner.prune` 相当の rewrite に `fcntl.LOCK_EX` を追加（従来はロック未取得）
- `FanOutManager.append_task_fn` シグネチャを `(line, parent_uuid)` に拡張（**breaking** for direct FanOutManager constructors）

### Added

- fan-out 子タスク追記時に `audit.jsonl` へ `source="fanout"`, `correlation_id=親uuid` の enqueue レコードを書く（全 enqueue 経路の監査漏れ是正）
- `tests/io/test_exec_jsonl.py` / `tests/test_fanout_audit.py`: 一元化・ロック・fan-out 監査の固定テスト


## 0.31.1 — 2026-08-31

### Changed

- audit I/O を `ghdag.io.audit` / `ghdag.io.audit_query` / `ghdag.io._rotate` に一元化。`pipeline.audit`・`pipeline.audit_query`・`files._rotate` は re-export shim で互換維持 (nexus Issue #2673)
- `files/writer.py` / `files/promote.py` / `tool/audit.py` の JSON append + rotate を `io.audit.append_audit_record` 経由に統一
- `ui/dashboard.py` / `ui/server.py` / `pipeline/__init__.py` の read API import を `io.audit_query` に移行

### Added

- `tests/io/test_audit.py` / `tests/io/test_audit_query.py`: 一元化・互換 import・rotate 正規配置の固定テスト


## 0.31.0 — 2026-08-31

### Changed

- `core/command.py` 新設: `render_exec_command` / flag builders / `build_llm_cmd` / `EngineAdapter` / `get_adapter` を集約。`pipeline ⇄ workflow` と `llm.spec ⇄ llm.engines` の循環を解消。旧パスは re-export shim で互換維持 (nexus Issue #2656)
- `dag/audit_hooks.py`: `AuditHooks` の正規配置先へ移動。`pipeline/hooks.py` は shim（import-linter で許容）(nexus Issue #2656)
- `llm/engines.py`: モジュールレベル `ENGINE_MODELS = load_engine_models()` を廃止し `get_engine_models()` 遅延初期化へ移行（**breaking**: `ENGINE_MODELS` 変数は公開しない）(nexus Issue #2656)
- `tool/audit.py`: `_maybe_rotate` の import 先を `files._rotate` に変更（`tool → pipeline` 逆流解消）(nexus Issue #2656)
- `pyproject.toml`: import-linter 契約 5 本を追加（core 独立・二塔分離・io/ops 隔離）(nexus Issue #2656)
- `maintenance.py`: ops 契約のため `dag.state` 依存をローカルヘルパーに置換（挙動不変）(nexus Issue #2656)

### Added

- `tests/core/test_purity.py` / `test_no_import_side_effects.py`: core 純度ガードと import 副作用固定テスト (nexus Issue #2656)
- `ghdag.io`: Step 2 用プレースホルダパッケージ（import-linter 契約参照用）(nexus Issue #2656)

## 0.30.16 — 2026-08-31

### Changed

- `core/` パッケージ新設: 型・Protocol・エンジン宣言データ（exceptions, models, ports, capabilities, engine_spec, parsers）を集約し、`llm ⇄ metrics` の循環依存を解消。旧 import パスは re-export shim で完全互換を維持 (nexus Issue #2655)
- `metrics/parsers.py`: `parse_token_count` / `parse_token_usage_json` を `core/parsers.py` に移動（shim 経由で旧パスも維持）
- `llm/adapters/*`: `TokenUsage` / parser 関数の import を `core` 経由に変更

## 0.30.15 — 2026-08-31

### Changed

- `core/vocabulary.py`: done マーカー・`QUEUE_FILE_RE`・`PIPELINE_STATUS_RE`・fan-out 規約を共有語彙として集約。cleanup / pipeline / dag の直書き文字列を定数参照に置換（挙動不変）。mypy overrides から `ghdag.workflow.github` を削除 (nexus Issue #2654)

## 0.30.14 — 2026-08-30

### Added

- `llm/capabilities.py`: `LLMCapabilities.sandbox` フィールド（`"off"` / `"readonly"`、既定 `"off"`）と `READONLY_OBSERVE` プリセット（`sandbox="readonly"` + 編集系 `disallowed_tools`）を追加。観測系 Bash を許可しつつ非破壊をサンドボックスで担保する (nexus Issue #2640)
- `llm/engines.py`: claude / codex / cursor のフラグビルダーが `sandbox="readonly"` をそれぞれ `--permission-mode plan` / `-s read-only` / `--sandbox enabled` にマッピング。bypass 系との同時指定は `ValueError`。gemini / shell は未対応として `NotImplementedError` (nexus Issue #2640)

### Fixed

- `llm/engines.py`: cursor の `disallowed_tools` を `_IGNORED_CAPABILITIES` に宣言し、CLI フラグが無いのに黙って無視されていた挙動を文書化された noop にした (nexus Issue #2640)

## 0.30.13 — 2026-08-30

### Fixed

- `llm/engines.py`: `_build_codex_flags` が `capabilities.permission_mode` を見ておらず、`permission="dangerous_full_access"` を指定しても codex の exec.jsonl コマンドに `--dangerously-bypass-approvals-and-sandbox` が付かなかった問題を修正。`render_exec_command` は builder を `dangerously_skip_permissions=False` 固定で呼ぶため、permission_mode を見ないとフラグが落ち、codex が workspace-write サンドボックス（writable = cwd + /tmp のみ、network 制限）のまま起動して cwd 外へ書き込めなかった。`_build_cursor_flags` と同じ判定に揃えた (nexus Issue #2627, PR #223)
- `github_client.py`: `_request` に一過性障害（RemoteDisconnected / BadStatusLine / ConnectionReset 系 / URLError / 502-504）の限定再試行を追加（上限3回・exponential backoff + jitter・Retry-After 尊重）。401/403/404 等の恒久エラーは再試行しない (nexus Issue #2563, PR #222)
- `github_client.py`: `issue_update` のラベル操作を DELETE+POST から `_converge_labels` に置換。応答喪失時は現在ラベルを再取得して残作業のみ再実行し目標状態へ収束する。DELETE 404（既に無い）は成功扱い (nexus Issue #2563, PR #222)

### Notes

- 0.30.13 は sumipan/nexus の秘書ペルソナを codex エンジンで動かす前提の修正を含む。nexus 側は `/var/tmp/ghdag` を本バージョンに更新し、常駐プロセス（ghdag_ui / ghdag_runner / mltgnt_daemon）を再起動する必要がある

## 0.30.12 — 2026-08-14

### Changed

- `CHANGELOG.md`: `## 0.30.10` エントリに `### Notes` セクションを追記。`call_text` / `TextResult` が sumipan/nexus#2459 の前提 API である旨を明記 (nexus Issue #2464)

## 0.30.11 — 2026-08-14

### Added

- `tests/llm/`: `call_text()` / `TextResult` のアダプタ固有テストを追加 (Issue #2463, PR #219)

## 0.30.10 — 2026-08-13

### Added

- `llm/engines.py`: `TextResult` frozen dataclass を追加。`body`（抽出テキスト）、`success`（returncode == 0）、`raw`（LLMResult）フィールドと `stderr` / `returncode` 委譲プロパティを持つ (Issue #2462)
- `llm/engines.py`: `call_text()` 関数を追加。`call()` と同一シグネチャで呼び出し可能な上位互換 API。engine アダプタ経由でテキスト抽出し、空の場合は `raw.stdout` にフォールバックして `TextResult` を返す (Issue #2462)
- `llm/__init__.py`: `call_text`、`TextResult` を export 追加
- `tests/llm/test_call_text.py`: `TextResult` と `call_text()` のユニットテスト 11 件を新規追加

### Notes

- `call_text` / `TextResult` は sumipan/nexus#2459（nexus 側 call_llm 縮退）の前提 API。nexus 側は本リリース以降の ghdag を editable install した状態で `call_text` への移行を開始する。

## 0.30.9 — 2026-08-11

### Added

- `llm/spec.py`: `_dedupe_extra_args()` ヘルパを追加。`_CAPABILITY_FLAG_BUILDERS` が出力するフラグと `EngineSpec.extra_args` が重複する場合に後者を除去する。codex エンジンで `--json --skip-git-repo-check` が argv に二重展開されエラー終了する問題を修正 (PR #215)

### Fixed

- `llm/engines.py`: codex エンジンの `allowed_tools` / `disallowed_tools` capability を `_UNSUPPORTED_CAPABILITIES` に登録し、`NotImplementedError` で即死していた挙動を noop 化 (PR #215)

## 0.30.7 — 2026-06-24

### Fixed

- `dag/task_launcher.py`: `get_output_adapter()` 配線を復旧。PR #2243 のリファクタで消失した adapter 呼び出しを再接続し、claude エンジンの stdout JSON を `adapter.extract_result_text()` 経由で変換して `result_path` に書き込む。`parse_token_count`（stderr grep）を廃止し adapter ベースの usage 抽出に統一 (Issue #2287)
- `dag/task_launcher.py`: `TaskMetrics` 全 8 経路（TIMEOUT / REJECTED / PIPELINE_FAILED / EMPTY_RESULT / SUCCESS / FANOUT_PARSE_FAILED / PROCESS_ERROR / UNKNOWN_FAILURE）に `cost_usd` / `cache_read_tokens` / `cache_creation_tokens` を追加 (Issue #2287)

### Added

- `tests/dag/test_task_launcher_adapter_wiring.py`: adapter 配線の退行検査テスト。claude JSON stdout が `result_path` に漏れないこと・メトリクスが非 null になることを契約として固定 (Issue #2287)

## 0.30.6 — 2026-06-23

### Fixed

- `dag/parser.py`, `pipeline/llm_pipeline.py`, `pipeline/state.py`: コード内コメント・docstring の `exec.md` 参照を `exec.jsonl` に修正 (Issue #2245)

## 0.30.5 — 2026-06-23

### Changed

- `cleanup/`: `cleanup.py` を `cleanup/` パッケージに変換。モジュール分割で保守性向上 (Issue #2244)
- `dag/`: `TaskLauncher`・`FanOutManager`・`CircuitBreakerPolicy` を `DagEngine` から分離抽出 (Issue #2243)

### Fixed

- `dag/task_launcher.py`: `fanout_manager` の型アノテーションを `FanOutManager` に修正 (mypy)
- `dag/task_launcher.py`: dead code `_persist_fail_stdout` を除去
- 複数モジュール: unused imports 除去 (ruff lint)

## 0.30.4 — 2026-06-23

### Added

- `llm/adapters/`: `EngineOutputAdapter` Protocol と `get_output_adapter()` レジストリを新設。claude エンジンの `--output-format json` stdout から `result` テキストと `TokenUsage`（`token_count` / `cost_usd` / `cache_read_tokens` / `cache_creation_tokens`）を抽出するアダプタ層を実装 (Issue #2266)
- `metrics/models.py`: `TaskMetrics` に `cost_usd`・`cache_read_tokens`・`cache_creation_tokens` フィールドを追加 (Issue #2266)

### Fixed

- `dag/_state.py`: `_remove_by_predicate` で LOCK_SH/LOCK_EX を分離していた競合（TOCTOU）を単一 LOCK_EX で修正 (Issue #2266)
- `metrics/parsers.py`: `parse_token_usage_json` を追加し、claude JSON stdout から usage を正確に取得。従来の stderr 経路はフォールバックとして温存 (Issue #2266)

## 0.30.3 — 2026-06-21

### Added

- `dispatcher.py`: `_READY_LABEL_RE` 正規表現でハイフン・コロン両対応のラベル区切り文字を受け入れる（`research:ready` / `research-ready` 両形式を正しく `*:running` / `*-running` に遷移）(Issue #2258)
- `state.py`: `_remove_by_predicate` ヘルパーと `remove_idempotency_for_handler(workflow, handler, issue)` — ハンドラ単位の冪等キー削除（他ハンドラの冪等記録を保持しつつ特定ハンドラのみリトライ可能）(Issue #2258)
- `LLMPipelineAPI`: `remove_idempotency_for_handler` の委譲メソッドを追加 (Issue #2258)

## 0.30.2 — 2026-06-14

### Changed

- `README.md`: v0.30.0 実装に合わせた OSS_QUALITY §4.1 準拠の全面書き直し（state_machine / tool/ / dashboard 追加、label_state_machine 削除、Error Reference 17+7 型）(Issue #2154)

## 0.30.1 — 2026-06-14

### Changed

- `README.md`: OSS_QUALITY.md 第4章の必須9セクション構成で全面書き直し（CLI Reference・Public API・Architecture・Configuration・Error Reference を現行実装に同期）(Issue #2144)

## 0.28.16 — 2026-06-13

### Added

- `TOOL_EXIT_CODES` 定数と `ToolDef.exit_codes` フィールド: Phase D exit_code 語彙（`success` / `failure` / `retry` / `skip`）のバリデーション (Issue #2091)
- `write_tool_fallback_audit()`: fallback chain 発動時の audit.jsonl 出力 (`event: tool.fallback`) (Issue #2091)

## 0.28.15 — 2026-06-13

### Added

- `LLMCapabilities.stream`: `stream=True` 時に claude エンジンで `--output-format stream-json --verbose` を生成し、JSONL ストリーム出力を `_extract_stream_result()` でパース (Issue #2084)

## 0.28.14 — 2026-06-13

### Added

- `DagConfig.serialize_mutating`: `annotations._mutates == "true"` のタスク同士を直列化する排他制御。`max_concurrency` とは独立して機能 (issuesmith #2079)

## 0.28.13 — 2026-06-13

### Added

- `dashboard.py`: audit.jsonl のダッシュボード集計（タスク状態・correlation_id 別トークン消費・CB 発火頻度） (issuesmith #2083)
- `/api/dashboard/status`, `/api/dashboard/tokens`, `/api/dashboard/cb-firing` エンドポイントを `server.py` に追加 (issuesmith #2083)

## 0.28.11 — 2026-06-13

### Added

- `DagEngine` サーキットブレーカー: 連続タスク失敗が `max_consecutive_failures` に達するとエンジンをシャットダウン。`DagConfig` に `max_consecutive_failures` / `failure_window_sec` を追加 (issuesmith #2082)
- `ToolDef` / `FallbackEntry` dataclass と `ToolRegistry.discover()`: ディレクトリ walk による Tool 定義の discovery、ファイル名規約 lint、多重定義検出 (issuesmith #2087)

## 0.28.10 — 2026-06-13

### Added

- `conditional_step.run_with_template()`: LLM CLI 実行の診断ログ（開始・完了・経過時間）を stderr に出力 (issuesmith #2070)
- `engine._check_completions()`: PROCESS_ERROR / TIMEOUT 時に stdout を `{result_path}.fail` へ永続化 (issuesmith #2070)

## 0.28.7 — 2026-06-08

### Added

- `detect_correlation_bursts()` / `get_correlation_top_n()`: audit.jsonl の correlation_id バースト検出ヘルパー (issuesmith #2007)
- `WorkflowDispatcher._observe_correlation_burst()`: ポーリングループ内で correlation バーストを検出し warning ログを出力。cooldown 機構で重複抑制 (issuesmith #2007)

## 0.28.2 — 2026-05-30

### Added

- `StepConfig.skill_name`: ワークフローステップが呼び出すスキル名を宣言するオプションフィールド（後方互換、デフォルト `None`）
- `typecheck_dag()`: スキル I/O 契約（`SkillIOSpec`）に基づく DAG 型検査。スキル存在・`content_type` 整合・`consumes` 充足警告を検出 (diary #1406)

## 0.28.0 — 2026-05-29

### BREAKING

- `watch` のリポジトリ解決を単数 `GITHUB_REPOSITORY` から複数 `GITHUB_REPOSITORIES`（カンマ区切り `owner/repo` リスト）へ移行。`GITHUB_REPOSITORY` は廃止 (diary #1322 関連)

### Added

- `create_github_clients()`: `GITHUB_REPOSITORIES` の各リポジトリに対するトークンクライアントのリストを生成（全クライアントで同一 PAT を共有）
- `WorkflowDispatcher` が複数クライアントを受け付け、`poll_once` が全リポを横断して Issue を収集。`dispatch`・ラベル遷移・エラーコメント・rate limit 観測を Issue 取得元クライアントへルーティング
- `WorkflowDispatcher(github_client=...)` は単一クライアントとクライアントのリストの両方を受け付ける（後方互換）

### Notes

- `gh` CLI には依存しない（トークン運用を維持）。`gh` フォールバックは追加しない
- ワークフローのラベルは実質的に単一リポにスコープされる運用のため、複数リポで同一 Issue 番号かつ同一ワークフローの衝突（design.md ファイル名・冪等キー）は本変更の対象外

## 0.26.0 — 2026-05-28

### BREAKING

- 非推奨エンジンアダプタ alias（`ClaudeAdapter` 等）を物理削除 (diary #1267)

### Added

- CI: ruff lint、coverage 70% 閾値、import-linter レイヤ検証を追加 (diary #1266)
- `GhdagError` 例外基底クラス導入。全カスタム例外を再親化。README に Error Reference セクション追加 (diary #1271)
- mypy `ignore_errors = true` を 6 モジュールで解消 (diary #1272)
- `llm.inference` audit イベント追加（`prompt_hash`・`latency_ms` フィールド） (diary #1275)

## 0.25.4 — 2026-05-26

### Fixed

- `ui`: `exec.jsonl` の `idempotency_key` が null のとき `_ISSUESMITH_KEY_RE.match(None)` で TypeError がクラッシュしていた問題を修正。`_parse_exec_jsonl` で `or ""` による入り口正規化と `cmd_preview` の None ガードで二重防御 (#141)

## 0.25.3 — 2026-05-26

### Added

- `fanout`: fanout アンカーを `---` セパレータ依存から `ghdag_fanout:` startswith 検出に変更。セパレータなしのファイルでも正しくパース可能 (diary #1214)
- `ui`: `MonitorTask` と `cmd_preview` に `idempotency_key` サポートを追加 (diary #1203)
- `pipeline/order`: `_check_missing_vars` が不足変数を全列挙する KeyError メッセージに改善 (diary #1193)

### Fixed

- `order`: `_check_missing_vars` の例外型を `KeyError` → `ValueError` に変更 (#137)
- `test`: gemini モデル検証テストを `GHDAG_LLM_MODELS` 環境変数から独立させる (#138)

## 0.25.2 — 2026-05-25

### Added

- `result_finalize` ポリシー分岐とリトライ時 result_path クリア追加 (diary #1140)

### Fixed

- `parser`: result_path の空文字列を `None` に正規化し `IsADirectoryError` を防ぐ
- ruff 指摘の未使用変数・import を除去

## 0.25.1 — 2026-05-25

### Added

- `ghdag.dag.check_pipeline_status` を `ghdag.dag.__all__` に追加し public API として公開 (diary #1126)

### Docs

- README を v0.25.0 向けに更新: `--permission-mode`、`--max-concurrency`、`AuditHooks`、`StepConfig.permission` フィールドの記述を追加

## 0.24.0 — 2026-05-24

### BREAKING

- `cleanup_queue()` のデフォルト動作変更: Case D（orphan）・Case F（dead entry）の自動修復を廃止。デフォルトは検出レポートのみ（stderr 出力 + `detected_orphan` / `detected_dead` カウント）。自動修復には `auto_repair=True` / `--auto-repair` フラグが必要 (diary #1057)
- `md_append()` のデフォルト動作変更: 部分書き込み（start marker 検出）時に `AppendRecoverError` を raise。従来の RECOVERED 動作には `allow_recover=True` が必要 (diary #1057)

### Changed

- `pipeline/audit.py`: 日次ローテーション（`_should_rotate_daily`）を廃止。サイズベース（64MB 超）ローテーションのみ維持。`cat jobs/audit.jsonl` で全ログが参照可能になる (diary #1057)
- `cleanup.py`: `CleanupResult` に `detected_orphan` / `detected_dead` フィールドを追加
- `cli.py`: `ghdag cleanup` に `--auto-repair` フラグを追加。出力フォーマットを `auto_repair` 有無で分岐

## [0.22.0] - 2026-05-24 — BREAKING

### Changed

- `workflow/engine.py`: 4 Adapter クラス（`ClaudeAdapter` / `GeminiAdapter` / `CursorAdapter` / `ShellAdapter`）を `_GenericAdapter(spec: EngineSpec)` に統合。`get_adapter()` は `ENGINE_SPECS` からオンデマンド生成するよう変更（diary #1056）
- `llm/engines.py`: `_validate_capabilities_for_engine()` を `_UNSUPPORTED_CAPABILITIES` 辞書駆動に置換、`build_llm_cmd()` を `EngineSpec` フィールド + `_CAPABILITY_FLAG_BUILDERS` マップに分割（engine 文字列ハードコード分岐を解消）
- `pipeline/llm_pipeline.py`: `_build_exec_record()` の `workflow.engine.get_adapter()` への import を `llm.spec` 直接参照に置換し、Layer 1 → Layer 2 逆依存を解消

### Removed

- **exec.md テキスト形式サポートを完全削除（破壊的変更）**
  - `dag/parser.py`: `parse_exec_md()` 関数を削除
  - `dag/__init__.py`: `parse_exec_md` エクスポートを削除
  - `pipeline/state.py`: `append_exec()` メソッド・`_is_jsonl_mode` プロパティ・テキスト形式パーサーを削除。`from_repo_root()` のデフォルトパスを `queue/exec.md` → `jobs/exec.jsonl` に変更
  - `pipeline/llm_pipeline.py`: `_submit_text()` / `_build_exec_line()` / `_jsonl_mode` プロパティを削除。`submit()` は常に exec.jsonl JSONL 形式で書き込む
  - `llm/spec.py`: `render_exec_line()` 関数を削除
  - `workflow/engine.py`: `EngineAdapter` Protocol および全 Adapter から `build_exec_line()` を削除
  - `ui/monitor.py`: `parse_exec_md()` / テキスト形式 exec.md 検出パスを削除
  - `cleanup.py`: `EXEC_LINE_RE` 正規表現・テキスト形式 UUID 抽出ブランチを削除
  - `dag/engine.py`: exec.md テキスト形式読み込みブランチを削除（常に JSONL パーサー使用）

### Deprecated

- `ClaudeAdapter` / `GeminiAdapter` / `CursorAdapter` / `ShellAdapter`: deprecated alias 化（0.24.0 で削除予定）。`get_adapter(name)` または `_GenericAdapter(ENGINE_SPECS[name])` を使用すること

### Migration

exec.md テキスト形式を exec.jsonl JSONL 形式に変換する:

```sh
# 変換例: uuid: command → {"uuid": "uuid", "command": "command", "depends": []}
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
  SHR functionality has been superseded by polling-based execution (diary #986).

## [0.17.1] - 2026-05-22

### Added

- `pipeline/audit.py`: `write_task_exit_audit()` を追加。タスク完了・失敗・拒否・依存失敗・空結果の出口イベントを audit.jsonl に JSONL 形式で記録
- `dag/hooks.py`: `DefaultHooks.__init__(audit_path)` を追加。各フックメソッド (`on_task_success`, `on_task_failure`, `on_task_rejected`, `on_task_dep_failed`, `on_task_empty_result`) で `write_task_exit_audit()` を呼び出し
- `dag/engine.py`: `hooks is None` の場合に `exec_md_path` 親ディレクトリの `audit.jsonl` を `audit_path` として `DefaultHooks` に渡す

### Fixed

- `tests/`: 未使用 pytest import を削除 (ruff F401)

## [0.16.0] - 2026-05-16

### Added

- `workflow/engine.py`: `ShellAdapter` を追加。`engine: shell` で order ファイルを bash スクリプトとして直接実行する。`prompt` / `model` パラメーターは無視され、command は `bash -o pipefail {order_path}` 固定
- `llm/_constants.py`, `llm/engines.py`: `shell` エンジンを `DEFAULT_ENGINE_MODELS` / `ENGINE_CLI` / `ENGINE_DEFAULTS` に追加（model は `bash` のみ）

## [0.15.1] - 2026-05-11

### Added

- `pipeline/`: `QueueTaskStore` と `QueueTask` を追加。result ファイルへのアクセス API を提供 (#76)
- `pipeline/audit.py`: JSON 形式 exec 行からの UUID 抽出に対応 (#78)

### Fixed

- `pipeline/maintenance.py`: JSONL prune 対応・orphan done マーカー付与・done 削除順序修正 (#77)
- `pipeline/maintenance.py`: `QUEUE_FILE_RE` を複合ツール名・stderr に対応 (#75)

## [0.15.0] - 2026-05-09

### Changed (BREAKING)

- `pipeline/`: exec.md サポートコードを全削除。exec ファイルは JSONL 形式（`jobs/exec.jsonl`）のみ対応。後方互換なし (#825)
- `pipeline/maintenance.py`: `remove_idempotency_matching` の text-mode idempotency 互換を削除し JSONL 形式に一本化

### Added

- `pipeline/maintenance.py`: キュー検査・修復 API (`validate_exec_jsonl`, `list_queue_tasks`, `inspect_exec_jsonl`, `repair_exec_jsonl`, `check_idempotency_key`) を追加 (#829)

### Fixed

- `pipeline/maintenance.py`: `remove_idempotency_matching` / `_submit_text` の text-mode 冪等性互換を部分復元（JSONL 主体、md フォールバック）

## [0.14.9] - 2026-05-08

### Added

- `dag/engine.py`: stdin リダイレクト先ファイルが不在の場合に `WARNING` ログを出して `SKIPPED_MISSING_INPUT` でタスクをスキップする。bash プロセスを起動しないため終了コード 1 の誤検知を防止

## [0.14.8] - 2026-05-08

### Added

- `pipeline/`: exec.jsonl に JSON レコードを書き込む JSONL モード対応
- `llm/cursor.py`: `dangerously_skip_permissions=True` 時に `--force` フラグを付与

## [0.14.7] - 2026-05-07

### Fixed

- `ui/monitor.py`: `result_path` を JSONL フィールドから直接取得するよう変更。claude 実行ジョブのようにコマンド文字列に result パスが含まれないケースで UI にリンクが表示されない問題を修正

## [0.14.6] - 2026-05-07

### Changed (BREAKING)

- `DagConfig.exec_done_dir` のデフォルト値を `"exec-done"` から `"jobs/done"` に変更
- `cleanup_queue()` の引数 `queue_done_dir` → `archive_dir`、`exec_done_dir` → `done_dir` に改名
- `ghdag cleanup` CLI のデフォルトパスを `queue-done/` → `jobs/archive/`、`exec-done/` → `jobs/done/` に変更
- `ui/monitor.py`: `_detect_exec_done_dir()` の `exec-done/` フォールバックを削除。常に `jobs/done/` を返す
- `ui/server.py`: retry ハンドラの done ファイルパスを `exec-done/` → `jobs/done/` に変更
- コメント・docstring 内の `exec-done` / `queue-done` 表記を `jobs/done/` / `jobs/archive/` に統一

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
