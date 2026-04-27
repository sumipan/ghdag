"""
llm/engines.py — エンジン・モデルのホワイトリストとワンショット LLM 呼び出し

ワークフローを伴わない単発の LLM 呼び出しを提供する。
ghdag 側でエンジンごとの許可モデルを管理し、スクリプト側の責務を軽減する。
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass

from ghdag.llm._config import load_engine_models
from ghdag.llm.capabilities import LLMCapabilities, LLMParseError, TEXT_ONLY


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


def _validate_capabilities_for_engine(engine: str, capabilities: LLMCapabilities) -> None:
    """エンジンが capabilities の機能をサポートしているか検証する。

    Raises:
        NotImplementedError: エンジンが対応していない機能が指定された場合
    """
    if engine == "gemini":
        if capabilities.disallowed_tools:
            raise NotImplementedError(
                f"gemini engine does not support disallowed_tools (got {capabilities.disallowed_tools!r})"
            )
        if capabilities.allowed_tools:
            raise NotImplementedError(
                f"gemini engine does not support allowed_tools (got {capabilities.allowed_tools!r})"
            )
        if capabilities.permission_mode != "default":
            raise NotImplementedError(
                f"gemini engine does not support permission_mode != default (got {capabilities.permission_mode!r})"
            )
    elif engine == "cursor":
        if capabilities.allowed_tools:
            raise NotImplementedError(
                f"cursor engine does not support allowed_tools (got {capabilities.allowed_tools!r})"
            )
        if capabilities.permission_mode != "default":
            raise NotImplementedError(
                f"cursor engine does not support permission_mode != default (got {capabilities.permission_mode!r})"
            )


@dataclass
class LLMResult:
    """ワンショット LLM 呼び出しの結果。"""
    stdout: str
    stderr: str
    returncode: int

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def validate(self, capabilities: LLMCapabilities) -> "LLMResult":
        """output_format 契約を検証する。失敗時は LLMParseError を送出。

        returncode != 0 の場合は検証をスキップ（エラー出力を優先）。
        Returns:
            self（チェーン呼び出し可能）
        Raises:
            LLMParseError: output_format == "json" かつ stdout が有効な JSON でない場合
        """
        if not self.ok:
            return self
        if capabilities.output_format == "json":
            try:
                json.loads(self.stdout)
            except json.JSONDecodeError as e:
                raise LLMParseError(raw=self.stdout, reason=str(e)) from e
        return self


def build_llm_cmd(
    engine: str,
    model: str,
    prompt: str,
    *,
    capabilities: LLMCapabilities = TEXT_ONLY,
) -> list[str]:
    """LLM CLI コマンドのリストを構築する。

    Args:
        engine: エンジン名
        model: 検証済みモデル ID
        prompt: プロンプト文字列
        capabilities: 能力制約値オブジェクト（デフォルト: TEXT_ONLY）
    Returns:
        subprocess 用のコマンドリスト
    """
    cli = ENGINE_CLI.get(engine, engine)
    cmd = [cli, "--model", model, "-p", prompt]

    if engine == "claude":
        cmd += ["--permission-mode", capabilities.permission_mode]

        if capabilities.output_format != "text":
            cmd += ["--output-format", capabilities.output_format]

        if capabilities.allowed_tools:
            cmd += ["--allowed-tools", ",".join(capabilities.allowed_tools)]

        if capabilities.disallowed_tools:
            cmd += ["--disallowed-tools", ",".join(capabilities.disallowed_tools)]

    return cmd


def call(
    prompt: str,
    *,
    engine: str = "claude",
    model: str | None = None,
    timeout: int | None = None,
    stdin_text: str | None = None,
    capabilities: LLMCapabilities = TEXT_ONLY,
) -> LLMResult:
    """ワンショットで LLM を呼び出し、結果を返す。

    Args:
        prompt: プロンプト文字列
        engine: エンジン名（デフォルト: "claude"）
        model: モデル ID（None でエンジンデフォルト）
        timeout: タイムアウト秒数（None で無制限）
        stdin_text: 標準入力として渡すテキスト（None で stdin なし）
        capabilities: 能力制約値オブジェクト（デフォルト: TEXT_ONLY）
    Returns:
        LLMResult
    Raises:
        EngineModelError: エンジン・モデルの検証失敗
        NotImplementedError: エンジンが対応していない capabilities 機能
        subprocess.TimeoutExpired: タイムアウト
    """
    _validate_capabilities_for_engine(engine, capabilities)
    resolved_model = validate_engine_model(engine, model)
    cmd = build_llm_cmd(
        engine,
        resolved_model,
        prompt,
        capabilities=capabilities,
    )

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        input=stdin_text,
        timeout=timeout,
    )

    llm_result = LLMResult(
        stdout=result.stdout,
        stderr=result.stderr,
        returncode=result.returncode,
    )
    return llm_result.validate(capabilities)
