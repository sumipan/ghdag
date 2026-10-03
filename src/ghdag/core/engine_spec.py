"""ghdag.core.engine_spec — single source of truth for EngineSpec"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Literal


class InputMode(Enum):
    STDIN = auto()  # feed via `< <order>` (default)
    ARGV = auto()   # put the order path at the end of argv (shell)


class PromptFlag(Enum):
    NONE = auto()       # do not emit the flag at all (shell, codex)
    FLAG_ONLY = auto()  # `-p` only, no value (claude, gemini, cursor)


DangerFlagPosition = Literal["leading", "trailing"]


@dataclass(frozen=True)
class EngineSpec:
    name: str
    cli: str
    input_mode: InputMode
    prompt_flag: PromptFlag
    prompt_flag_token: str | None  # e.g. "-p". None when NONE
    model_flag: str | None
    default_model: str | None
    danger_flag: str | None = None
    danger_flag_position: DangerFlagPosition = "trailing"
    extra_args: tuple[str, ...] = ()
    subcommand: tuple[str, ...] = ()  # subcommand expanded right after cli (e.g. codex → ("exec", "-"))


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
        # --disable-slash-commands is added only when isolation=True (GHDAG_ENGINE_ISOLATION) (nexus #3174)
        extra_args=("--output-format", "stream-json", "--verbose"),
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
        # DAG default is stream-json (#2967). The required --print(-p) is ensured by prompt_flag.
        extra_args=("--output-format", "stream-json", "--stream-partial-output"),
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
