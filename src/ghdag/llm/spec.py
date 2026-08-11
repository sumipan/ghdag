"""llm/spec.py — EngineSpec 単一情報源"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from ghdag.llm.capabilities import LLMCapabilities

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
    subcommand: tuple[str, ...] = ()  # cli 直後に展開されるサブコマンド（例: codex → ("exec", "-")）


ENGINE_SPECS: dict[str, EngineSpec] = {
    "claude": EngineSpec(
        name="claude", cli="claude",
        input_mode="cat_pipe",
        prompt_flag="-p", model_flag="--model",
        default_model="claude-sonnet-4-6",
        danger_flag="--dangerously-skip-permissions",
        danger_flag_position="trailing",
        extra_args=("--output-format", "json"),
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
    "codex": EngineSpec(
        name="codex", cli="codex",
        subcommand=("exec", "-"),
        input_mode="cat_pipe",
        prompt_flag=None, model_flag="--model",
        default_model="gpt-5.6-terra",
        danger_flag="--dangerously-bypass-approvals-and-sandbox",
        danger_flag_position="trailing",
        extra_args=("--json", "--skip-git-repo-check"),
    ),
}


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
    capabilities が指定された場合は _CAPABILITY_FLAG_BUILDERS 経由でフラグを生成。
    """
    perm_flags: list[str] = []
    if capabilities is not None:
        from ghdag.llm.engines import _CAPABILITY_FLAG_BUILDERS  # 遅延 import（循環回避）
        builder = _CAPABILITY_FLAG_BUILDERS.get(spec.name)
        if builder:
            perm_flags = builder(capabilities, False)

    if spec.input_mode == "cat_pipe":
        parts: list[str] = [spec.cli, *spec.subcommand]
        if spec.prompt_flag:
            parts.append(spec.prompt_flag)
            parts.append(f"'{prompt}'")
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
        if spec.extra_args:
            parts.extend(spec.extra_args)
        return " ".join(parts) + f" < {order_path}"

    if spec.input_mode == "argv":
        parts = [spec.cli, *spec.subcommand]
        if spec.extra_args:
            parts.extend(spec.extra_args)
        parts.append(order_path)
        return " ".join(parts)

    raise ValueError(f"Unknown input_mode: {spec.input_mode!r}")


