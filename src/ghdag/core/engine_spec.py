"""ghdag.core.engine_spec — EngineSpec 単一情報源"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from ghdag.core.capabilities import LLMCapabilities

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
