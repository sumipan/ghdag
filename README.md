# ghdag

A DAG-based workflow engine for GitHub issues and projects.

## Installation

```bash
pip install git+https://github.com/sumipan/ghdag.git
```

## Quick Start

Run an exec.md file through the DAG engine:

```bash
# exec.md を処理（DAG 実行）
ghdag run queue/exec.md

# workflows/ 配下の YAML を監視し、GitHub イベントに応じて exec.md を生成・実行
ghdag watch workflows/
```

- `ghdag run <exec_md>` — exec.md を読み込み、DAG エンジンで全ステップを実行する
- `ghdag watch <workflows_dir>` — workflows ディレクトリの YAML を監視し、GitHub Issue のラベルイベントに応じて exec.md を生成・実行する

## Workflow YAML Example

```yaml
name: "triage"
polling_interval: 30
template_dir: "templates"
triggers:
  - label: "needs-triage"
    handler: "triage_handler"
handlers:
  triage_handler:
    steps:
      - template: "triage_order"
        model: "claude-sonnet-4-6"
        engine: "claude"
```

### WorkflowConfig フィールド

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `name` | str | ○ | ワークフロー名 |
| `triggers` | list[TriggerConfig] | ○ | ラベルマッチ条件 |
| `handlers` | dict[str, HandlerConfig] | ○ | ハンドラー定義 |
| `polling_interval` | int | — | ポーリング間隔（秒、デフォルト 30） |
| `template_dir` | str | — | テンプレートディレクトリ |

### StepConfig フィールド

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `template` | str | ○ | order テンプレートファイル名（拡張子なし） |
| `model` | str | ○ | 実行モデル |
| `engine` | str | — | LLM エンジン名（デフォルト: `claude`） |
| `id` | str | — | ステップ ID（depends 参照用） |
| `depends` | list[str] | — | 依存ステップ ID リスト |

## Architecture

ghdag is organized into three layers plus a CLI entry point:

```
Layer 0 — DAG Engine (src/ghdag/dag/):
  タスク実行・依存解決・状態管理。Pure Python、GitHub 非依存。
  主要モジュール: engine.py, models.py, parser.py, state.py

Layer 1 — Workflow (src/ghdag/workflow/):
  GitHub ポーリング・ワークフロー YAML 解釈・exec.md 生成。
  主要モジュール: dispatcher.py, schema.py, loader.py, engine.py, github.py

Layer 2 — Pipeline (src/ghdag/pipeline/):
  LLM 連携・order/result ファイル管理・テンプレートレンダリング。
  主要モジュール: llm_pipeline.py, order.py, result.py, state.py

CLI (src/ghdag/cli.py):
  run / watch / trigger / llm / ui / cleanup サブコマンド
```

## Engine Adapters

ghdag supports four built-in LLM/shell engine adapters:

| engine | CLI コマンド | 入力方式 | デフォルトモデル |
|---|---|---|---|
| `claude` | `claude` | cat pipe | `claude-sonnet-4-6` |
| `gemini` | `gemini` | cat pipe | `gemini-2.5-flash` |
| `cursor` | `agent` | stdin redirect | `auto` |
| `shell` | `bash` | argv | — |

ワークフロー YAML の `engine` フィールドでエンジンを指定する。省略時は `claude` が使われる。

## License

MIT
