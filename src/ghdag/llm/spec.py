"""llm/spec.py — EngineSpec re-export shim + render_exec_command."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ghdag.core.engine_spec import (
    ENGINE_SPECS,
    DangerFlagPosition,
    EngineSpec,
    InputMode,
)

if TYPE_CHECKING:
    from ghdag.core.capabilities import LLMCapabilities

__all__ = [
    "InputMode",
    "DangerFlagPosition",
    "EngineSpec",
    "ENGINE_SPECS",
    "render_exec_command",
    "_dedupe_extra_args",
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


def render_exec_command(
    spec: EngineSpec,
    *,
    order_path: str,
    prompt: str,
    model: str | None,
    capabilities: "LLMCapabilities | None" = None,
) -> str:
    """exec.jsonl の command フィールド用（tee パイプを含まない）。

    capabilities が None の場合は従来通り EngineSpec.danger_flag を使用。
    capabilities が指定された場合は _CAPABILITY_FLAG_BUILDERS 経由でフラグを生成し、
    builder が出したフラグは extra_args 側から除去する（_dedupe_extra_args）。
    """
    perm_flags: list[str] = []
    if capabilities is not None:
        from ghdag.llm.engines import _CAPABILITY_FLAG_BUILDERS  # 遅延 import（循環回避）
        builder = _CAPABILITY_FLAG_BUILDERS.get(spec.name)
        if builder:
            perm_flags = builder(capabilities, False)

    effective_extra_args = _dedupe_extra_args(spec.extra_args, perm_flags)

    if spec.input_mode == "cat_pipe":
        parts: list[str] = [spec.cli, *spec.subcommand]
        if spec.prompt_flag:
            parts.append(spec.prompt_flag)
            parts.append(f"'{prompt}'")
        if spec.model_flag and model:
            parts.append(spec.model_flag)
            parts.append(f"'{model}'")
        if effective_extra_args:
            parts.extend(effective_extra_args)
        if capabilities is None:
            if spec.danger_flag_position == "trailing" and spec.danger_flag:
                parts.append(spec.danger_flag)
        else:
            parts.extend(perm_flags)
        return f"cat {order_path} | " + " ".join(parts)

    if spec.input_mode == "stdin_redirect":
        parts = [spec.cli, *spec.subcommand]
        if spec.model_flag and model:
            parts.append(spec.model_flag)
            parts.append(f"'{model}'")
        if spec.prompt_flag:
            parts.append(spec.prompt_flag)
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
