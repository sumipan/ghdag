"""ghdag.core.command — 純粋なコマンド文字列構築と Engine Adapter。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from ghdag.core.capabilities import TEXT_ONLY, LLMCapabilities
from ghdag.core.engine_spec import ENGINE_SPECS, EngineSpec
from ghdag.core.exceptions import GhdagError

__all__ = [
    "AdapterNotFoundError",
    "EngineAdapter",
    "_CAPABILITY_FLAG_BUILDERS",
    "_GenericAdapter",
    "_build_claude_flags",
    "_build_codex_flags",
    "_build_cursor_flags",
    "_dedupe_extra_args",
    "build_llm_cmd",
    "get_adapter",
    "register_adapter",
    "render_exec_command",
]


def _dedupe_extra_args(
    extra_args: tuple[str, ...], perm_flags: list[str]
) -> list[str]:
    """perm_flags が既に出しているフラグを extra_args 側から取り除く。

    _CAPABILITY_FLAG_BUILDERS 由来のフラグ（perm_flags）と EngineSpec.extra_args は
    独立に組み立てられるため、同じフラグが両方から出て argv に重複しうる。
    codex の `--json` のように重複を許さない CLI では
    `error: the argument '--json' cannot be used multiple times` で即死するため、
    builder 側を優先して extra_args から落とす。

    フラグに値が続くか（`--output-format json`）はトークンが `-` で始まるかで判定する。
    """
    emitted = {tok for tok in perm_flags if tok.startswith("-")}
    result: list[str] = []
    i = 0
    while i < len(extra_args):
        tok = extra_args[i]
        takes_value = (
            tok.startswith("-")
            and i + 1 < len(extra_args)
            and not extra_args[i + 1].startswith("-")
        )
        if tok.startswith("-") and tok in emitted:
            i += 2 if takes_value else 1
            continue
        result.append(tok)
        if takes_value:
            result.append(extra_args[i + 1])
            i += 2
        else:
            i += 1
    return result


def _build_claude_flags(
    capabilities: LLMCapabilities, dangerously_skip_permissions: bool
) -> list[str]:
    # sandbox=readonly → --permission-mode plan（read-only Bash 可・変更系 deny）。
    # permission_mode 明示指定との同時指定は意味が衝突するため拒否する。
    if capabilities.sandbox == "readonly":
        if capabilities.permission_mode != "default":
            raise ValueError(
                "sandbox='readonly' conflicts with explicit permission_mode="
                f"{capabilities.permission_mode!r}; use one or the other"
            )
        flags = ["--permission-mode", "plan"]
    else:
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
    # cursor CLI `--sandbox <enabled|disabled>` は config を上書きする二値モード
    # （agent --help 実測）。enabled 時は Cursor のサンドボックスを強制する。
    # 書き込み・ネットワーク遮断の詳細は CLI/設定依存。--force との同時指定は矛盾。
    bypass = dangerously_skip_permissions or capabilities.permission_mode == "bypassPermissions"
    if capabilities.sandbox == "readonly":
        if bypass:
            raise ValueError(
                "sandbox='readonly' conflicts with --force (bypass permissions)"
            )
        return ["--sandbox", "enabled"]
    if bypass:
        return ["--force"]
    return []


def _build_codex_flags(
    capabilities: LLMCapabilities, dangerously_skip_permissions: bool
) -> list[str]:
    # permission_mode も見るのは exec.jsonl 経路（render_exec_command）のため。
    # render_exec_command は dangerously_skip_permissions=False 固定で builder を呼ぶので、
    # capabilities を見ないと DANGEROUS_FULL_ACCESS が CLI フラグに落ちず、
    # codex が workspace-write サンドボックスのまま起動して cwd 外へ書けない。
    # 一方 call() 経路は _validate_capabilities_for_engine が codex の
    # permission_mode != "default" を弾くため、この分岐には到達しない。
    flags = ["--json", "--skip-git-repo-check"]
    bypass = dangerously_skip_permissions or capabilities.permission_mode == "bypassPermissions"
    if capabilities.sandbox == "readonly":
        if bypass:
            raise ValueError(
                "sandbox='readonly' conflicts with dangerously-bypass-sandbox"
            )
        flags += ["-s", "read-only"]
    elif bypass:
        flags.append("--dangerously-bypass-approvals-and-sandbox")
    return flags


_CAPABILITY_FLAG_BUILDERS: dict[str, Callable[[LLMCapabilities, bool], list[str]]] = {
    "claude": _build_claude_flags,
    "cursor": _build_cursor_flags,
    "codex": _build_codex_flags,
}


def render_exec_command(
    spec: EngineSpec,
    *,
    order_path: str,
    prompt: str,
    model: str | None,
    capabilities: LLMCapabilities | None = None,
    resume_session_id: str | None = None,
) -> str:
    """exec.jsonl の command フィールド用（tee パイプを含まない）。

    capabilities が None の場合は従来通り EngineSpec.danger_flag を使用。
    capabilities が指定された場合は _CAPABILITY_FLAG_BUILDERS 経由でフラグを生成し、
    builder が出したフラグは extra_args 側から除去する（_dedupe_extra_args）。
    """
    perm_flags: list[str] = []
    if capabilities is not None:
        builder = _CAPABILITY_FLAG_BUILDERS.get(spec.name)
        if builder:
            perm_flags = builder(capabilities, False)

    effective_extra_args = _dedupe_extra_args(spec.extra_args, perm_flags)
    resume_flags: list[str] = []
    subcommand = list(spec.subcommand)
    if resume_session_id:
        if spec.name in {"claude", "cursor"}:
            resume_flags = ["--resume", f"'{resume_session_id}'"]
        elif spec.name == "codex":
            subcommand = ["exec", "resume", f"'{resume_session_id}'"]

    if spec.input_mode == "cat_pipe":
        parts: list[str] = [spec.cli, *subcommand]
        if spec.prompt_flag:
            parts.append(spec.prompt_flag)
            parts.append(f"'{prompt}'")
        if spec.model_flag and model:
            parts.append(spec.model_flag)
            parts.append(f"'{model}'")
        if resume_flags:
            parts.extend(resume_flags)
        if effective_extra_args:
            parts.extend(effective_extra_args)
        if capabilities is None:
            if spec.danger_flag_position == "trailing" and spec.danger_flag:
                parts.append(spec.danger_flag)
        else:
            parts.extend(perm_flags)
        return f"cat {order_path} | " + " ".join(parts)

    if spec.input_mode == "stdin_redirect":
        parts = [spec.cli, *subcommand]
        if spec.model_flag and model:
            parts.append(spec.model_flag)
            parts.append(f"'{model}'")
        if spec.prompt_flag:
            parts.append(spec.prompt_flag)
        if resume_flags:
            parts.extend(resume_flags)
        if capabilities is None:
            if spec.danger_flag_position == "after_prompt" and spec.danger_flag:
                parts.append(spec.danger_flag)
        else:
            parts.extend(perm_flags)
        if effective_extra_args:
            parts.extend(effective_extra_args)
        return " ".join(parts) + f" < {order_path}"

    if spec.input_mode == "argv":
        parts = [spec.cli, *spec.subcommand]
        if spec.extra_args:
            parts.extend(spec.extra_args)
        parts.append(order_path)
        return " ".join(parts)

    raise ValueError(f"Unknown input_mode: {spec.input_mode!r}")


def build_llm_cmd(
    engine: str,
    model: str,
    prompt: str,
    *,
    capabilities: LLMCapabilities = TEXT_ONLY,
    dangerously_skip_permissions: bool = False,
    resume_session_id: str | None = None,
) -> list[str]:
    """LLM CLI コマンドのリストを構築する。

    Args:
        engine: エンジン名
        model: 検証済みモデル ID
        prompt: プロンプト文字列
        capabilities: 能力制約値オブジェクト（デフォルト: TEXT_ONLY）
        dangerously_skip_permissions: claude エンジン時に --dangerously-skip-permissions を付与
        resume_session_id: 再開対象セッションID（対応エンジンのみ）
    Returns:
        subprocess 用のコマンドリスト
    """
    spec = ENGINE_SPECS.get(engine)
    cli = spec.cli if spec else engine
    cmd = [cli, *spec.subcommand] if spec else [cli]
    if spec and resume_session_id and spec.name == "codex":
        cmd = [cli, "exec", "resume", resume_session_id]

    if spec is None:
        cmd += ["--model", model, "-p", prompt]
    else:
        if spec.model_flag:
            cmd += [spec.model_flag, model]
        if spec.prompt_flag:
            cmd += [spec.prompt_flag, prompt]
        if resume_session_id and spec.name in {"claude", "cursor"}:
            cmd += ["--resume", resume_session_id]

    builder = _CAPABILITY_FLAG_BUILDERS.get(engine)
    if builder:
        cmd += builder(capabilities, dangerously_skip_permissions)
    elif dangerously_skip_permissions and spec and spec.danger_flag:
        cmd.append(spec.danger_flag)

    return cmd


class EngineAdapter(Protocol):
    """エンジンごとの exec レコード組み立てを担う"""

    @property
    def name(self) -> str:
        """エンジン名（"claude", "gemini"）"""
        ...

    def build_exec_record(
        self,
        *,
        uuid: str,
        order_path: str,
        result_path: str | None,
        prompt: str,
        model: str | None,
        depends: list[str],
        capabilities: LLMCapabilities | None = None,
    ) -> dict:
        """exec.jsonl に書き込む 1 レコード（dict）を組み立てる。
        command フィールドに tee パイプを含めない。
        """
        ...


class _GenericAdapter:
    """ENGINE_SPECS から生成される汎用アダプター。4 Adapter クラスを統合。"""

    def __init__(self, spec: EngineSpec) -> None:
        self._spec = spec

    @property
    def name(self) -> str:
        return self._spec.name

    def build_exec_record(
        self,
        *,
        uuid: str,
        order_path: str,
        result_path: str | None,
        prompt: str,
        model: str | None,
        depends: list[str],
        capabilities: LLMCapabilities | None = None,
    ) -> dict:
        return {
            "uuid": uuid,
            "engine": self._spec.name,
            "model": model if self._spec.model_flag else None,
            "command": render_exec_command(
                self._spec, order_path=order_path, prompt=prompt, model=model,
                capabilities=capabilities,
            ),
            "depends": depends,
            "result_path": result_path,
            "retry": 0,
            "annotations": {},
        }


_CUSTOM_ADAPTERS: dict[str, EngineAdapter] = {}


class AdapterNotFoundError(GhdagError, ValueError):
    """Raised when an unregistered engine adapter is requested."""


def register_adapter(adapter: EngineAdapter) -> None:
    _CUSTOM_ADAPTERS[adapter.name] = adapter


def get_adapter(name: str) -> EngineAdapter:
    spec = ENGINE_SPECS.get(name)
    if spec is not None:
        return _GenericAdapter(spec)
    if name in _CUSTOM_ADAPTERS:
        return _CUSTOM_ADAPTERS[name]
    raise AdapterNotFoundError(f"Unknown engine: {name!r}. Available: {sorted(ENGINE_SPECS)}")
