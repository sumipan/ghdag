"""workflow/schema.py — WorkflowConfig dataclass, YAML → dataclass 変換"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class StepConfig:
    template: str           # order テンプレートファイル名（拡張子なし）
    model: str              # 実行モデル（必須）
    id: str | None = None   # ステップ ID（depends 参照用）
    engine: str = "claude"  # LLM エンジン名（"claude", "gemini", "cursor" 等）
    depends: list[str] = field(default_factory=list)  # 依存ステップ ID リスト
    resume_from: str | None = None  # 親ステップのセッション継続元 ID
    permission: str | None = None  # capabilities プリセット名（None = エンジンデフォルト）
    skill_name: str | None = None  # このステップが呼び出すスキル名
    render: str = "frozen"  # "frozen"（enqueue 時展開）| "live"（実行時再展開 trampoline）
    role: str | None = None  # QuotaGate ロール名（省略時はエンジン単位チェック）


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
class NonterminalClosedConfig:
    action: str                          # "reopen" | "trigger"
    terminal_labels: list[str]           # 終端ラベル（いずれかを持つ CLOSED issue は対象外）
    trigger: str | None = None           # action="trigger" 時に起動する handler のラベル


@dataclass
class WorkflowConfig:
    name: str                              # ワークフロー名
    triggers: list[TriggerConfig]          # トリガー条件リスト（定義順が序列）
    handlers: dict[str, HandlerConfig]     # ハンドラー名 → HandlerConfig
    polling_interval: int = 30             # ポーリング間隔（秒）
    template_dir: str | None = None        # テンプレートディレクトリ（相対パスは workflow ファイル基準）
    label_namespace: str | None = None     # ラベルプレフィックス（例: "issuesmith"）
    transitions: dict[str, list[str]] | None = None  # 状態遷移マップ
    reset_label: str | None = None         # 任意の状態から遷移可能な特殊ラベル
    roles: dict[str, list[str]] = field(default_factory=dict)  # ロール名 → エンジン名リスト
    nonterminal_closed: NonterminalClosedConfig | None = None  # CLOSED 非終端 issue 検出設定


def validate_workflow_roles(config: WorkflowConfig) -> None:
    """Raise ValueError when a step references an undeclared role."""
    for handler_name, handler in config.handlers.items():
        for step in handler.steps:
            if step.role is None:
                continue
            if step.role not in config.roles:
                raise ValueError(
                    f"handler '{handler_name}' step '{step.id or step.template}': "
                    f"role '{step.role}' is not declared in workflow roles"
                )
