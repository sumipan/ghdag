"""
llm/engines.py — エンジン・モデルのホワイトリストとワンショット LLM 呼び出し

ワークフローを伴わない単発の LLM 呼び出しを提供する。
ghdag 側でエンジンごとの許可モデルを管理し、スクリプト側の責務を軽減する。
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

from ghdag.llm._config import load_engine_models


class EngineModelError(Exception):
    """未知のエンジンまたは許可外モデルが指定された場合に送出。"""


# ---------------------------------------------------------------------------
# エンジン・モデル ホワイトリスト（YAML 設定または DEFAULT_ENGINE_MODELS にフォールバック）
# ---------------------------------------------------------------------------

ENGINE_MODELS: dict[str, list[str]] = load_engine_models()

# エンジンごとの CLI コマンド名
ENGINE_CLI: dict[str, str] = {
    "claude": "claude",
    "gemini": "gemini",
    "cursor": "agent",
}

# エンジンごとのデフォルトモデル
ENGINE_DEFAULTS: dict[str, str] = {
    "claude": "claude-sonnet-4-6",
    "gemini": "gemini-2.5-flash",
    "cursor": "auto",
}


def list_engines() -> list[str]:
    """利用可能なエンジン名の一覧を返す。"""
    return sorted(ENGINE_MODELS.keys())


def list_models(engine: str) -> list[str]:
    """指定エンジンの許可モデル一覧を返す。

    Raises:
        EngineModelError: 未知のエンジン
    """
    if engine not in ENGINE_MODELS:
        raise EngineModelError(
            f"未知のエンジンです: {engine!r}. "
            f"利用可能: {sorted(ENGINE_MODELS.keys())}"
        )
    return sorted(ENGINE_MODELS[engine])


def validate_engine_model(engine: str, model: str | None) -> str:
    """エンジンとモデルの組み合わせを検証し、解決済みモデル ID を返す。

    Args:
        engine: エンジン名（"claude", "gemini" など）
        model: モデル ID（None の場合はデフォルト）
    Returns:
        検証済みモデル ID
    Raises:
        EngineModelError: 未知のエンジンまたは許可外モデル
    """
    if engine not in ENGINE_MODELS:
        raise EngineModelError(
            f"未知のエンジンです: {engine!r}. "
            f"利用可能: {sorted(ENGINE_MODELS.keys())}"
        )

    if model is None:
        return ENGINE_DEFAULTS[engine]

    allowed = ENGINE_MODELS[engine]
    if model not in allowed:
        raise EngineModelError(
            f"許可リストにないモデルです: {model!r} (engine={engine}). "
            f"許可リスト: {sorted(allowed)}"
        )
    return model


@dataclass
class LLMResult:
    """ワンショット LLM 呼び出しの結果。"""
    stdout: str
    stderr: str
    returncode: int

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def build_llm_cmd(
    engine: str,
    model: str,
    prompt: str,
    *,
    dangerously_skip_permissions: bool = False,
) -> list[str]:
    """LLM CLI コマンドのリストを構築する。

    Args:
        engine: エンジン名
        model: 検証済みモデル ID
        prompt: プロンプト文字列
        dangerously_skip_permissions: --dangerously-skip-permissions を付与するか
    Returns:
        subprocess 用のコマンドリスト
    """
    cli = ENGINE_CLI.get(engine, engine)
    cmd = [cli, "--model", model, "-p", prompt]
    if dangerously_skip_permissions:
        if engine == "claude":
            cmd.append("--dangerously-skip-permissions")
        elif engine == "cursor":
            cmd.append("--force")
    return cmd


def call(
    prompt: str,
    *,
    engine: str = "claude",
    model: str | None = None,
    timeout: int | None = None,
    stdin_text: str | None = None,
    dangerously_skip_permissions: bool = False,
    action: str | None = None,
) -> LLMResult:
    """ワンショットで LLM を呼び出し、結果を返す。

    Args:
        prompt: プロンプト文字列
        engine: エンジン名（デフォルト: "claude"）
        model: モデル ID（None でエンジンデフォルト）
        timeout: タイムアウト秒数（None で無制限）
        stdin_text: 標準入力として渡すテキスト（None で stdin なし）
        dangerously_skip_permissions: claude に --dangerously-skip-permissions を付与
        action: アクション種別（"skill" のとき dangerously_skip_permissions を自動で True にする）
    Returns:
        LLMResult
    Raises:
        EngineModelError: エンジン・モデルの検証失敗
        subprocess.TimeoutExpired: タイムアウト
    """
    if action == "skill":
        dangerously_skip_permissions = True
    resolved_model = validate_engine_model(engine, model)
    cmd = build_llm_cmd(
        engine,
        resolved_model,
        prompt,
        dangerously_skip_permissions=dangerously_skip_permissions,
    )

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        input=stdin_text,
        timeout=timeout,
    )

    return LLMResult(
        stdout=result.stdout,
        stderr=result.stderr,
        returncode=result.returncode,
    )
