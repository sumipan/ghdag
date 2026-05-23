# ghdag 脅威モデル — exec.md / exec.jsonl 書き込み経路の信頼境界

## 概要

本文書は `exec.md` / `exec.jsonl` への全書き込み経路を棚卸しし、信頼境界・各経路の信頼度の根拠・想定脅威と対策状況を整理する。

---

## 1. 書き込み経路の棚卸し

### 1.1 ghdag 内部の書き込み関数

| # | 関数 | ファイル | 対象 | ロック | 操作 |
|---|------|---------|------|--------|------|
| 1 | `DagEngine.append_task()` | `src/ghdag/dag/engine.py:142` | exec.md | `LOCK_EX` | 1行追記（ファンアウト子タスク含む） |
| 2 | `PipelineState.append_exec()` | `src/ghdag/pipeline/state.py:175` | exec.md | `LOCK_EX` | 複数行追記 + audit.jsonl 記録 |
| 3 | `PipelineState.append_exec_records()` | `src/ghdag/pipeline/state.py:151` | exec.jsonl | `LOCK_EX` | JSON レコード追記 + audit.jsonl 記録 |
| 4 | `PipelineState.remove_idempotency_matching()` | `src/ghdag/pipeline/state.py:63` | exec.md / exec.jsonl | `LOCK_EX` | パターンマッチで行削除（全ファイルリライト） |
| 5 | `PipelineState.remove_exec_entries()` | `src/ghdag/pipeline/state.py:284` | exec.md / exec.jsonl | `LOCK_EX` | UUID 指定で行削除（全ファイルリライト） |
| 6 | `LLMPipelineAPI._submit_text()` | `src/ghdag/pipeline/llm_pipeline.py:164` | exec.md | 間接（→ #2） | テキスト形式サブミット |
| 7 | `LLMPipelineAPI._submit_jsonl()` | `src/ghdag/pipeline/llm_pipeline.py:227` | exec.jsonl | 間接（→ #3） | JSONL 形式サブミット |

### 1.2 diary 側の enqueue 経路（ghdag API 経由で exec.jsonl に書き込む）

| # | スクリプト | 用途 | エンジン |
|---|-----------|------|---------|
| A | `scripts/order-redirect.py` | `指示.md` → exec.jsonl 変換 | cursor |
| B | `tools/benchmark/enqueue.py` | ベンチマーク一括投入 | claude / gemini / cursor |
| C | `tools/mltgnt_bridge/helpers.py` | Slack メッセージルーティング | claude / cursor / gemini |
| D | `tools/project/dag_utils.py` | DAG パイプライン汎用投入 | claude |
| E | `skills/ghdag-operate-cursor/scripts/enqueue.py` | Cursor ミッション分解 | cursor + claude |
| F | `skills/mltgnt-create-persona/scripts/ghdag-enqueue.py` | ペルソナリサーチ DAG | cursor + claude |
| G | `scripts/diary_hooks.py` | 失敗時 investigator 自動起動 | claude |

### 1.3 補助関数（フォーマット構築のみ、直接書き込みなし）

| 関数 | ファイル | 役割 |
|------|---------|------|
| `build_child_exec_line()` | `src/ghdag/dag/fanout.py:88` | ファンアウト子タスク用 exec.md 行を構築 |
| `build_child_jsonl_record()` | `src/ghdag/dag/fanout.py:93` | ファンアウト子タスク用 exec.jsonl レコードを構築 |

---

## 2. 信頼境界

### 2.1 境界の定義

`exec.md` / `exec.jsonl` への書き込みは **同一マシン上の同一 OS ユーザーで実行されるプロセスに限定される**。

```
┌─────────────────────────────────────────────┐
│ 信頼境界: ローカルマシン（同一 OS ユーザー）      │
│                                             │
│  ghdag 内部関数（#1〜#7） ──┐                │
│                             ├──→ exec.md / exec.jsonl
│  diary enqueue スクリプト   │                │
│  群（A〜G）────────────────┘                │
│                                             │
└─────────────────────────────────────────────┘
```

境界の外（ネットワーク越し・別 OS ユーザー・コンテナ外）からの直接書き込みは想定しない。

### 2.2 信頼度の根拠（経路別）

| 経路 | 信頼度の根拠 |
|------|------------|
| ghdag 内部関数（#1〜#7） | ghdag プロセス自身が実行。`fcntl.LOCK_EX` で排他制御。audit.jsonl に全操作を記録 |
| diary enqueue スクリプト群（A〜G）A を除く | 同一マシン・同一 OS ユーザーで実行。`PipelineState` API 経由で書き込むため、ロック・監査ログの保証を継承 |
| `tools/benchmark/enqueue.py`（B） | `_append_exec_jsonl()` が `PipelineState` を経由せず直接 `open(append)` する。`fcntl` ロック未使用。ベンチマーク専用スクリプトであり実運用への影響なし（後述） |

---

## 3. 想定脅威と対策状況

| 脅威 | リスク | 現状の対策 | 追加対策の要否 |
|------|--------|-----------|--------------|
| 悪意ある exec レコードの注入 | 任意コマンド実行 | OS ファイルパーミッション（同一 OS ユーザーのみ書き込み可） | 不要（信頼境界内） |
| 競合書き込みによるファイル破損 | タスク消失・重複実行 | `fcntl.LOCK_EX` による排他制御（#1〜#5、A・C〜G） | 不要 |
| 監査ログの欠落 | インシデント追跡不能 | `append_exec` / `append_exec_records` が `write_audit_log` を自動呼び出し | 不要（ベンチマーク経路 B のみ監査なし、許容範囲） |
| exec ファイルの改竄検知 | 気づかない変更 | 未実装（HMAC 署名なし） | 現時点では不要。信頼境界が拡大した場合に再検討 |

### 3.1 ベンチマーク経路（B）のロック未使用について

`tools/benchmark/enqueue.py` は `PipelineState` を経由せず `open(..., "a")` で直接 `exec.jsonl` に追記する。これは以下の理由で許容する:

- **用途の限定**: ベンチマーク専用スクリプトであり、実運用パイプラインとは独立して実行される
- **競合リスクの低さ**: ベンチマーク実行中は他の enqueue 経路が同時に走ることを想定しない
- **監査ログの省略**: ベンチマーク投入は実運用監査の対象外

実運用で同時多重 enqueue が発生するシナリオには使用しないこと。

---

## 4. 今後の検討事項

以下はリスクが顕在化した場合に再検討する。現時点では実装しない。

| 項目 | 検討トリガー |
|------|------------|
| HMAC 署名による exec ファイルの完全性検証 | 信頼境界が拡大し、ネットワーク越しの書き込みが発生する場合 |
| exec ファイルの暗号化 | 機密コマンドが exec に含まれる運用が発生する場合 |
| ベンチマーク経路（B）への `fcntl` ロック追加 | ベンチマークと実運用が同一 exec.jsonl を共有する運用に変わる場合 |
