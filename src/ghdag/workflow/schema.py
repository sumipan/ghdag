"""workflow/schema.py — WorkflowConfig dataclass, YAML → dataclass 変換"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TriggerConfig:
    label: str  # マッチするラベルパターン (例: "pipeline:draft-ready")


@dataclass
class PhaseHandler:
    name: str           # フェーズ名 (例: "draft_design")
    template: str       # order テンプレートファイル名 (拡張子なし)
    agent: str = "claude"       # 実行エージェント
    model: str | None = None    # モデル上書き (None ならシステム既定)


@dataclass
class WorkflowConfig:
    name: str                           # ワークフロー名
    triggers: list[TriggerConfig]       # トリガー条件リスト
    handlers: list[PhaseHandler]        # フェーズハンドラーリスト
    polling_interval: int = 30          # ポーリング間隔（秒）
