"""
llm/engines.py — エンジン・モデルのホワイトリストとワンショット LLM 呼び出し

ワークフローを伴わない単発の LLM 呼び出しを提供する。
ghdag 側でエンジンごとの許可モデルを管理し、スクリプト側の責務を軽減する。
"""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass

from ghdag.exceptions import GhdagError
from ghdag.llm._config import load_engine_models
from ghdag.llm.capabilities import TEXT_ONLY, LLMCapabilities, LLMParseError
from ghdag.llm.spec import ENGINE_SPECS, PromptFlag


class EngineModelError(GhdagError):
    """Raised when an unknown engine or unauthorized model is specified."""


# ---------------------------------------------------------------------------
# エンジン・モデル ホワイトリスト（YAML 設定または DEFAULT_ENGINE_MODELS にフォールバック）
# ---------------------------------------------------------------------------

ENGINE_MODELS: dict[str, list[str]] = load_engine_models()

# エンジンごとの CLI コマンド名（spec から派生）
ENGINE_CLI: dict[str, str] = {name: spec.cli for name, spec in ENGINE_SPECS.items()}

# エンジンごとのデフォルトモデル（spec から派生）
ENGINE_DEFAULTS: dict[str, str | None] = {name: spec.default_model for name, spec in ENGINE_SPECS.items()}


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
            f"Unknown engine: {engine!r}. "
            f"Available: {sorted(ENGINE_MODELS.keys())}"
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
            f"Unknown engine: {engine!r}. "
            f"Available: {sorted(ENGINE_MODELS.keys())}"
        )

    if model is None:
        return ENGINE_DEFAULTS[engine]

    allowed = ENGINE_MODELS[engine]
    if model not in allowed:
        raise EngineModelError(
            f"Model not in allowlist: {model!r} (engine={engine}). "
            f"Allowed: {sorted(allowed)}"
        )
    return model


# ---------------------------------------------------------------------------
# Engine-specific capability validation — data-driven, no engine string branching
# ---------------------------------------------------------------------------

_UNSUPPORTED_CAPABILITIES: dict[str, set[str]] = {
    "gemini": {"disallowed_tools", "allowed_tools", "permission_mode", "stream"},
    "cursor": {"allowed_tools", "permission_mode", "stream"},
    "shell": {"stream"},
}


def _validate_capabilities_for_engine(engine: str, capabilities: LLMCapabilities) -> None:
    """エンジンが capabilities の機能をサポートしているか検証する。

    Raises:
        NotImplementedError: エンジンが対応していない機能が指定された場合
    """
    unsupported = _UNSUPPORTED_CAPABILITIES.get(engine, set())
    for attr in unsupported:
        val = getattr(capabilities, attr)
        if attr == "permission_mode":
            if val != "default":
                raise NotImplementedError(
                    f"{engine} engine does not support {attr} != default (got {val!r})"
                )
        elif attr == "stream":
            if val:
                raise NotImplementedError(
                    f"{engine} engine does not support {attr} (got {val!r})"
                )
        elif val:
            raise NotImplementedError(
                f"{engine} engine does not support {attr} (got {val!r})"
            )


# ---------------------------------------------------------------------------
# LLM command builders — per-engine capability flag helpers
# ---------------------------------------------------------------------------

def _build_claude_flags(
    capabilities: LLMCapabilities, dangerously_skip_permissions: bool
) -> list[str]:
    flags = ["--permission-mode", capabilities.permission_mode]
    if capabilities.stream:
        flags += ["--output-format", "stream-json", "--verbose"]
    elif capabilities.output_format != "text":
        flags += ["--output-format", capabilities.output_format]
    if capabilities.allowed_tools:
        flags += ["--allowed-tools", ",".join(capabilities.allowed_tools)]
    if capabilities.disallowed_tools:
        flags += ["--disallowed-tools", ",".join(capabilities.disallowed_tools)]
    if dangerously_skip_permissions:
        flags += ["--dangerously-skip-permissions"]
    return flags


def _build_cursor_flags(
    capabilities: LLMCapabilities, dangerously_skip_permissions: bool
) -> list[str]:
    if dangerously_skip_permissions or capabilities.permission_mode == "bypassPermissions":
        return ["--force"]
    return []


_CAPABILITY_FLAG_BUILDERS: dict[str, Callable[[LLMCapabilities, bool], list[str]]] = {
    "claude": _build_claude_flags,
    "cursor": _build_cursor_flags,
}


@dataclass
class LLMResult:
    """ワンショット LLM 呼び出しの結果。"""
    stdout: str
    stderr: str
    returncode: int
    latency_ms: float = 0.0

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def validate(self, capabilities: LLMCapabilities) -> "LLMResult":
        """output_format 契約を検証する。失敗時は LLMParseError を送出。

        returncode != 0 の場合は検証をスキップ（エラー出力を優先）。
        stream=True の場合は JSONL から最終 result を抽出して stdout を置換する。
        Returns:
            self（チェーン呼び出し可能）
        Raises:
            LLMParseError: output_format == "json" かつ stdout が有効な JSON でない場合
        """
        if not self.ok:
            return self
        if capabilities.stream:
            self.stdout = _extract_stream_result(self.stdout)
        if capabilities.output_format == "json":
            try:
                json.loads(self.stdout)
            except json.JSONDecodeError as e:
                raise LLMParseError(raw=self.stdout, reason=str(e)) from e
        return self


def _extract_stream_result(stdout: str) -> str:
    """stream-json JSONL 出力から最終 result テキストを抽出する。"""
    last_result: str | None = None
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("type") == "result":
            result = obj.get("result", "")
            last_result = result if isinstance(result, str) else json.dumps(result)
    if last_result is None:
        raise LLMParseError(
            raw=stdout, reason="no result line in stream-json output"
        )
    return last_result


def build_llm_cmd(
    engine: str,
    model: str,
    prompt: str,
    *,
    capabilities: LLMCapabilities = TEXT_ONLY,
    dangerously_skip_permissions: bool = False,
) -> list[str]:
    """LLM CLI コマンドのリストを構築する。

    Args:
        engine: エンジン名
        model: 検証済みモデル ID
        prompt: プロンプト文字列
        capabilities: 能力制約値オブジェクト（デフォルト: TEXT_ONLY）
        dangerously_skip_permissions: claude エンジン時に --dangerously-skip-permissions を付与
    Returns:
        subprocess 用のコマンドリスト
    """
    spec = ENGINE_SPECS.get(engine)
    cli = spec.cli if spec else engine
    cmd = [cli]

    if spec is None:
        cmd += ["--model", model, "-p", prompt]
    else:
        if spec.model_flag:
            cmd += [spec.model_flag, model]
        if spec.prompt_flag != PromptFlag.NONE:
            cmd += [spec.prompt_flag.value, prompt]

    builder = _CAPABILITY_FLAG_BUILDERS.get(engine)
    if builder:
        cmd += builder(capabilities, dangerously_skip_permissions)
    elif dangerously_skip_permissions and spec and spec.danger_flag:
        cmd.append(spec.danger_flag)

    return cmd


def call(
    prompt: str,
    *,
    engine: str = "claude",
    model: str | None = None,
    timeout: int | None = None,
    stdin_text: str | None = None,
    capabilities: LLMCapabilities = TEXT_ONLY,
    dangerously_skip_permissions: bool = False,
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
        dangerously_skip_permissions=dangerously_skip_permissions,
    )

    t0 = time.monotonic()
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        input=stdin_text,
        timeout=timeout,
    )
    latency_ms = (time.monotonic() - t0) * 1000

    llm_result = LLMResult(
        stdout=result.stdout,
        stderr=result.stderr,
        returncode=result.returncode,
        latency_ms=latency_ms,
    )
    return llm_result.validate(capabilities)
