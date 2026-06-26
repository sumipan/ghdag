"""llm/spec.py — EngineSpec 単一情報源"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from ghdag.llm.capabilities import LLMCapabilities


class InputMode(Enum):
    STDIN = "stdin"    # cli args < order_path
    ARGV = "argv"      # cli args order_path


class PromptFlag(Enum):
    FLAG_ONLY = "-p"   # フラグトークンのみ、プロンプトテキストは argv に載せない
    NONE = None        # フラグなし（shell 用）


DangerFlagPosition = Literal["after_prompt", "trailing", "none"]


@dataclass(frozen=True)
class EngineSpec:
    engine: str
    cli: str
    input_mode: InputMode
    prompt_flag: PromptFlag
    model_flag: str | None
    default_model: str | None
    danger_flag: str | None
    danger_flag_position: DangerFlagPosition
    extra_args: tuple[str, ...] = ()


ENGINE_SPECS: dict[str, EngineSpec] = {
    "claude": EngineSpec(
        engine="claude", cli="claude",
        input_mode=InputMode.STDIN,
        prompt_flag=PromptFlag.FLAG_ONLY, model_flag="--model",
        default_model="claude-sonnet-4-6",
        danger_flag="--dangerously-skip-permissions",
        danger_flag_position="trailing",
        extra_args=("--output-format", "json"),
    ),
    "gemini": EngineSpec(
        engine="gemini", cli="gemini",
        input_mode=InputMode.STDIN,
        prompt_flag=PromptFlag.FLAG_ONLY, model_flag="--model",
        default_model="gemini-2.5-flash",
        danger_flag=None, danger_flag_position="none",
        extra_args=("--approval-mode", "yolo"),
    ),
    "cursor": EngineSpec(
        engine="cursor", cli="agent",
        input_mode=InputMode.STDIN,
        prompt_flag=PromptFlag.FLAG_ONLY, model_flag="--model",
        default_model="auto",
        danger_flag="--force",
        danger_flag_position="after_prompt",
    ),
    "shell": EngineSpec(
        engine="shell", cli="bash",
        input_mode=InputMode.ARGV,
        prompt_flag=PromptFlag.NONE, model_flag=None,
        default_model=None,
        danger_flag=None, danger_flag_position="none",
        extra_args=("-o", "pipefail"),
    ),
}


def render_exec_command(
    spec: EngineSpec,
    *,
    order_path: str,
    prompt: str | None = None,
    model: str | None,
    capabilities: "LLMCapabilities | None" = None,
) -> str:
    """exec.jsonl の command フィールド用（tee パイプを含まない）。

    capabilities が None の場合は従来通り EngineSpec.danger_flag を使用。
    capabilities が指定された場合は _CAPABILITY_FLAG_BUILDERS 経由でフラグを生成。
    """
    perm_flags: list[str] = []
    if capabilities is not None:
        from ghdag.llm.engines import _CAPABILITY_FLAG_BUILDERS  # 遅延 import（循環回避）
        builder = _CAPABILITY_FLAG_BUILDERS.get(spec.engine)
        if builder:
            perm_flags = builder(capabilities, False)

    if spec.input_mode == InputMode.STDIN:
        parts: list[str] = [spec.cli]

        if spec.danger_flag_position == "after_prompt":
            if spec.model_flag and model:
                parts.append(spec.model_flag)
                parts.append(f"'{model}'")
            if spec.prompt_flag == PromptFlag.FLAG_ONLY:
                parts.append(spec.prompt_flag.value)
            if capabilities is None:
                if spec.danger_flag:
                    parts.append(spec.danger_flag)
            else:
                parts.extend(perm_flags)
            if spec.extra_args:
                parts.extend(spec.extra_args)
        else:  # "trailing" or "none"
            if spec.prompt_flag == PromptFlag.FLAG_ONLY:
                parts.append(spec.prompt_flag.value)
            if spec.model_flag and model:
                parts.append(spec.model_flag)
                parts.append(f"'{model}'")
            if spec.extra_args:
                parts.extend(spec.extra_args)
            if capabilities is None:
                if spec.danger_flag_position == "trailing" and spec.danger_flag:
                    parts.append(spec.danger_flag)
            else:
                parts.extend(perm_flags)

        return " ".join(parts) + f" < {order_path}"

    if spec.input_mode == InputMode.ARGV:
        parts = [spec.cli]
        if spec.extra_args:
            parts.extend(spec.extra_args)
        parts.append(order_path)
        return " ".join(parts)

    raise ValueError(f"Unknown input_mode: {spec.input_mode!r}")
