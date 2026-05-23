"""llm/spec.py — EngineSpec 単一情報源"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

InputMode = Literal["cat_pipe", "stdin_redirect", "argv"]
DangerFlagPosition = Literal["after_prompt", "trailing", "none"]


@dataclass(frozen=True)
class EngineSpec:
    name: str
    cli: str
    input_mode: InputMode
    prompt_flag: str | None
    model_flag: str | None
    default_model: str | None
    danger_flag: str | None
    danger_flag_position: DangerFlagPosition
    extra_args: tuple[str, ...] = ()


ENGINE_SPECS: dict[str, EngineSpec] = {
    "claude": EngineSpec(
        name="claude", cli="claude",
        input_mode="cat_pipe",
        prompt_flag="-p", model_flag="--model",
        default_model="claude-sonnet-4-6",
        danger_flag="--dangerously-skip-permissions",
        danger_flag_position="trailing",
    ),
    "gemini": EngineSpec(
        name="gemini", cli="gemini",
        input_mode="cat_pipe",
        prompt_flag="-p", model_flag="--model",
        default_model="gemini-2.5-flash",
        danger_flag=None, danger_flag_position="none",
        extra_args=("--approval-mode", "yolo"),
    ),
    "cursor": EngineSpec(
        name="cursor", cli="agent",
        input_mode="stdin_redirect",
        prompt_flag="-p", model_flag="--model",
        default_model="auto",
        danger_flag="--force",
        danger_flag_position="after_prompt",
    ),
    "shell": EngineSpec(
        name="shell", cli="bash",
        input_mode="argv",
        prompt_flag=None, model_flag=None,
        default_model=None,
        danger_flag=None, danger_flag_position="none",
        extra_args=("-o", "pipefail"),
    ),
}


def render_exec_command(
    spec: EngineSpec,
    *,
    order_path: str,
    prompt: str,
    model: str | None,
) -> str:
    """exec.jsonl の command フィールド用（tee パイプを含まない）。"""
    if spec.input_mode == "cat_pipe":
        parts: list[str] = [spec.cli]
        if spec.prompt_flag:
            parts.append(spec.prompt_flag)
            parts.append(f"'{prompt}'")
        if spec.model_flag and model:
            parts.append(spec.model_flag)
            parts.append(f"'{model}'")
        if spec.extra_args:
            parts.extend(spec.extra_args)
        if spec.danger_flag_position == "trailing" and spec.danger_flag:
            parts.append(spec.danger_flag)
        return f"cat {order_path} | " + " ".join(parts)

    if spec.input_mode == "stdin_redirect":
        parts = [spec.cli]
        if spec.model_flag and model:
            parts.append(spec.model_flag)
            parts.append(f"'{model}'")
        if spec.prompt_flag:
            parts.append(spec.prompt_flag)
        if spec.danger_flag_position == "after_prompt" and spec.danger_flag:
            parts.append(spec.danger_flag)
        if spec.extra_args:
            parts.extend(spec.extra_args)
        return " ".join(parts) + f" < {order_path}"

    if spec.input_mode == "argv":
        parts = [spec.cli]
        if spec.extra_args:
            parts.extend(spec.extra_args)
        parts.append(order_path)
        return " ".join(parts)

    raise ValueError(f"Unknown input_mode: {spec.input_mode!r}")


