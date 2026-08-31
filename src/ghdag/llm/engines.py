"""
llm/engines.py — エンジン・モデルのホワイトリストとワンショット LLM 呼び出し

ワークフローを伴わない単発の LLM 呼び出しを提供する。
ghdag 側でエンジンごとの許可モデルを管理し、スクリプト側の責務を軽減する。
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass

from ghdag.core.command import (
    _CAPABILITY_FLAG_BUILDERS,
    _build_claude_flags,
    _build_codex_flags,
    _build_cursor_flags,
    build_llm_cmd,
)
from ghdag.exceptions import GhdagError
from ghdag.llm._config import load_engine_models
from ghdag.llm.adapters import get_output_adapter
from ghdag.llm.capabilities import TEXT_ONLY, LLMCapabilities, LLMParseError
from ghdag.llm.spec import ENGINE_SPECS

__all__ = [
    "EngineModelError",
    "LLMResult",
    "TextResult",
    "build_llm_cmd",
    "call",
    "call_text",
    "get_engine_models",
    "list_engines",
    "list_models",
    "validate_engine_model",
    "ENGINE_CLI",
    "ENGINE_DEFAULTS",
    "_CAPABILITY_FLAG_BUILDERS",
    "_build_claude_flags",
    "_build_codex_flags",
    "_build_cursor_flags",
    "_IGNORED_CAPABILITIES",
    "_UNSUPPORTED_CAPABILITIES",
]


class EngineModelError(GhdagError):
    """Raised when an unknown engine or unauthorized model is specified."""


# ---------------------------------------------------------------------------
# エンジン・モデル ホワイトリスト（遅延初期化 — import 時に env / cwd を読まない）
# ---------------------------------------------------------------------------

_ENGINE_MODELS: dict[str, list[str]] | None = None


def get_engine_models() -> dict[str, list[str]]:
    """ENGINE_MODELS 相当を返す（初回呼び出しで load_engine_models を実行しキャッシュ）。"""
    global _ENGINE_MODELS
    if _ENGINE_MODELS is None:
        _ENGINE_MODELS = load_engine_models()
    return _ENGINE_MODELS


# エンジンごとの CLI コマンド名（spec から派生）
ENGINE_CLI: dict[str, str] = {name: spec.cli for name, spec in ENGINE_SPECS.items()}

# エンジンごとのデフォルトモデル（spec から派生）
ENGINE_DEFAULTS: dict[str, str | None] = {name: spec.default_model for name, spec in ENGINE_SPECS.items()}


def list_engines() -> list[str]:
    """利用可能なエンジン名の一覧を返す。"""
    return sorted(get_engine_models().keys())


def list_models(engine: str) -> list[str]:
    """指定エンジンの許可モデル一覧を返す。

    Raises:
        EngineModelError: 未知のエンジン
    """
    models = get_engine_models()
    if engine not in models:
        raise EngineModelError(
            f"Unknown engine: {engine!r}. "
            f"Available: {sorted(models.keys())}"
        )
    return sorted(models[engine])


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
    models = get_engine_models()
    if engine not in models:
        raise EngineModelError(
            f"Unknown engine: {engine!r}. "
            f"Available: {sorted(models.keys())}"
        )

    if model is None:
        return ENGINE_DEFAULTS[engine]

    allowed = models[engine]
    if model not in allowed:
        raise EngineModelError(
            f"Model not in allowlist: {model!r} (engine={engine}). "
            f"Allowed: {sorted(allowed)}"
        )
    return model


# ---------------------------------------------------------------------------
# Engine-specific capability validation — data-driven, no engine string branching
# ---------------------------------------------------------------------------

# 非デフォルト値を渡されたら NotImplementedError を送出する未対応 capability。
_UNSUPPORTED_CAPABILITIES: dict[str, set[str]] = {
    "gemini": {"disallowed_tools", "allowed_tools", "permission_mode", "stream", "sandbox"},
    "cursor": {"allowed_tools", "permission_mode", "stream"},
    "shell": {"stream", "sandbox"},
    "codex": {"stream", "permission_mode", "output_format"},
}

# エンジン側に等価概念がないため noop（値を受理するが CLI フラグに反映しない）で扱う capability。
# codex: allowed_tools / disallowed_tools は codex-cli には存在せず、権限制御は
#   OS レベルの --sandbox / --dangerously-bypass-approvals-and-sandbox で行う。
#   TEXT_ONLY / JSON_ONLY プリセットが既定で disallowed_tools を持つため、これを
#   NotImplementedError にせず noop 化することで、呼び出し側がラッパを書かずに
#   既定 capabilities のまま codex を呼べるようにする。
# cursor: disallowed_tools 相当の CLI フラグがなく、_build_cursor_flags も参照しない。
#   --force 不付与時の approval-deny が実質の防壁。黙って無視されていたのを文書化。
_IGNORED_CAPABILITIES: dict[str, set[str]] = {
    "codex": {"allowed_tools", "disallowed_tools"},
    "cursor": {"disallowed_tools"},
}


def _validate_capabilities_for_engine(engine: str, capabilities: LLMCapabilities) -> None:
    """エンジンが capabilities の機能をサポートしているか検証する。

    _IGNORED_CAPABILITIES に列挙された attr は検証をスキップして受理する（noop）。

    Raises:
        NotImplementedError: エンジンが対応していない機能が指定された場合
    """
    unsupported = _UNSUPPORTED_CAPABILITIES.get(engine, set())
    ignored = _IGNORED_CAPABILITIES.get(engine, set())
    if ignored:
        unsupported = unsupported - ignored
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
        elif attr == "output_format":
            if val != "text":
                raise NotImplementedError(
                    f"{engine} engine does not support {attr} != 'text' (got {val!r})"
                )
        elif attr == "sandbox":
            # "off" は truthy なため汎用 `elif val` では弾いてしまう。明示比較する。
            if val != "off":
                raise NotImplementedError(
                    f"{engine} engine does not support {attr} != 'off' (got {val!r})"
                )
        elif val:
            raise NotImplementedError(
                f"{engine} engine does not support {attr} (got {val!r})"
            )


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


@dataclass(frozen=True)
class TextResult:
    """call_text() の呼び出し結果。adapter でテキスト抽出済みのスナップショット。"""
    body: str
    success: bool
    raw: LLMResult

    @property
    def stderr(self) -> str:
        return self.raw.stderr

    @property
    def returncode(self) -> int:
        return self.raw.returncode


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

    # prompt_flag がなく cat_pipe エンジン（codex 等）は prompt を stdin に回す。
    # stdin_text が明示指定済みの場合はそれを優先する。
    spec = ENGINE_SPECS.get(engine)
    effective_stdin = stdin_text
    if spec and spec.prompt_flag is None and spec.input_mode == "cat_pipe":
        if effective_stdin is None:
            effective_stdin = prompt

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
        input=effective_stdin,
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


def call_text(
    prompt: str,
    *,
    engine: str = "claude",
    model: str | None = None,
    timeout: int | None = None,
    stdin_text: str | None = None,
    capabilities: LLMCapabilities = TEXT_ONLY,
    dangerously_skip_permissions: bool = False,
) -> TextResult:
    """ワンショットで LLM を呼び出し、adapter でテキスト抽出した TextResult を返す。

    call() と同一シグネチャ。ドロップイン上位互換として利用できる。
    adapter 出力が空の場合は raw.stdout にフォールバックする。
    """
    result = call(
        prompt,
        engine=engine,
        model=model,
        timeout=timeout,
        stdin_text=stdin_text,
        capabilities=capabilities,
        dangerously_skip_permissions=dangerously_skip_permissions,
    )
    adapter = get_output_adapter(engine)
    extracted = adapter.extract_result_text(
        result.stdout.encode("utf-8"),
        result.stderr.encode("utf-8"),
    ).decode("utf-8")
    body = extracted if extracted else result.stdout
    return TextResult(body=body, success=result.ok, raw=result)
