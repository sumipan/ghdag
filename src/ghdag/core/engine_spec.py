"""ghdag.core.engine_spec — EngineSpec 単一情報源"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Literal


class InputMode(Enum):
    STDIN = auto()  # `< <order>` で食わせる（既定）
    ARGV = auto()   # order パスを argv 末尾に置く（shell）


class PromptFlag(Enum):
    NONE = auto()       # フラグ自体を出さない（shell, codex）
    FLAG_ONLY = auto()  # `-p` のみ、値なし（claude, gemini, cursor）


DangerFlagPosition = Literal["leading", "trailing"]


@dataclass(frozen=True)
class EngineSpec:
    name: str
    cli: str
    input_mode: InputMode
    prompt_flag: PromptFlag
    prompt_flag_token: str | None  # 例 "-p"。NONE のとき None
    model_flag: str | None
    default_model: str | None
    danger_flag: str | None = None
    danger_flag_position: DangerFlagPosition = "trailing"
    extra_args: tuple[str, ...] = ()
    subcommand: tuple[str, ...] = ()  # cli 直後に展開されるサブコマンド（例: codex → ("exec", "-")）


ENGINE_SPECS: dict[str, EngineSpec] = {
    "claude": EngineSpec(
        name="claude", cli="claude",
        input_mode=InputMode.STDIN,
        prompt_flag=PromptFlag.FLAG_ONLY,
        prompt_flag_token="-p",
        model_flag="--model",
        default_model="claude-sonnet-4-6",
        danger_flag="--dangerously-skip-permissions",
        danger_flag_position="trailing",
        extra_args=("--output-format", "json"),
    ),
    "gemini": EngineSpec(
        name="gemini", cli="gemini",
        input_mode=InputMode.STDIN,
        prompt_flag=PromptFlag.FLAG_ONLY,
        prompt_flag_token="-p",
        model_flag="--model",
        default_model="gemini-2.5-flash",
        danger_flag=None,
        danger_flag_position="trailing",
        extra_args=("--approval-mode", "yolo"),
    ),
    "cursor": EngineSpec(
        name="cursor", cli="agent",
        input_mode=InputMode.STDIN,
        prompt_flag=PromptFlag.FLAG_ONLY,
        prompt_flag_token="-p",
        model_flag="--model",
        default_model="auto",
        danger_flag="--force",
        danger_flag_position="leading",
    ),
    "shell": EngineSpec(
        name="shell", cli="bash",
        input_mode=InputMode.ARGV,
        prompt_flag=PromptFlag.NONE,
        prompt_flag_token=None,
        model_flag=None,
        default_model=None,
        danger_flag=None,
        danger_flag_position="trailing",
        extra_args=("-o", "pipefail"),
    ),
    "codex": EngineSpec(
        name="codex", cli="codex",
        subcommand=("exec", "-"),
        input_mode=InputMode.STDIN,
        prompt_flag=PromptFlag.NONE,
        prompt_flag_token=None,
        model_flag="--model",
        default_model="gpt-5.6-terra",
        danger_flag="--dangerously-bypass-approvals-and-sandbox",
        danger_flag_position="trailing",
        extra_args=("--json", "--skip-git-repo-check"),
    ),
}
