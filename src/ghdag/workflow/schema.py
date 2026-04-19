"""workflow/schema.py — WorkflowConfig dataclass, YAML → dataclass 変換"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class StepConfig:
    template: str           # order テンプレートファイル名（拡張子なし）
    model: str              # 実行モデル（必須）
    id: str | None = None   # ステップ ID（depends 参照用）
    agent: str = "claude"   # LLM エンジン名（"claude", "gemini"）
    depends: list[str] = field(default_factory=list)  # 依存ステップ ID リスト
    agent: str = "claude"   # LLM エンジン名（"claude", "gemini" 等）


@dataclass
class OnTriggerConfig:
    issue_context: bool = False  # True: Issue body + comments を design.md に書き出し


@dataclass
class HandlerConfig:
    steps: list[StepConfig]
    on_trigger: OnTriggerConfig | None = None
    type: str | None = None  # "reset" 等の特殊ハンドラー種別
    context_hook: str | None = None  # context 生成カスタムスクリプト


@dataclass
class TriggerConfig:
    label: str     # マッチするラベル（例: "pipeline:draft-ready"）
    handler: str   # ハンドラー名（handlers の key）


@dataclass
class DispatchResult:
    status: str              # "dispatched" | "skipped" | "reset"
    reason: str = ""
    exec_lines: list[str] = field(default_factory=list)


@dataclass
class WorkflowConfig:
    name: str                              # ワークフロー名
    triggers: list[TriggerConfig]          # トリガー条件リスト（定義順が序列）
    handlers: dict[str, HandlerConfig]     # ハンドラー名 → HandlerConfig
    polling_interval: int = 30             # ポーリング間隔（秒）
    template_dir: str | None = None        # テンプレートディレクトリ（相対パスは workflow ファイル基準）
