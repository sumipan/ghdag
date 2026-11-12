"""ghdag.core.command — pure command-string construction and Engine Adapter."""

from __future__ import annotations

import re
import shlex
from collections.abc import Callable
from typing import Protocol

from ghdag.core.capabilities import TEXT_ONLY, Permission
from ghdag.core.container import container_prefix
from ghdag.core.engine_spec import ENGINE_SPECS, EngineSpec, InputMode, PromptFlag
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
    "inject_resume",
    "register_adapter",
    "render_exec_command",
]

_RESUME_ALREADY_CLAUDE_CURSOR = re.compile(r"(?<!\S)--resume(?=[\s=]|$)")
_RESUME_ALREADY_CODEX = re.compile(r"(?<!\S)exec\s+resume(?!\S)")
_MODEL_WITH_VALUE = re.compile(r"(?<!\S)--model\s+(?:'[^']*'|\S+)")


def _dedupe_extra_args(
    extra_args: tuple[str, ...], perm_flags: list[str]
) -> list[str]:
    """Remove flags already emitted by perm_flags from extra_args.

    Flags from _CAPABILITY_FLAG_BUILDERS (perm_flags) and EngineSpec.extra_args are
    assembled independently, so the same flag can come from both and be duplicated in argv.
    CLIs that reject duplicates, such as codex's `--json`, die immediately with
    `error: the argument '--json' cannot be used multiple times`,
    so the builder side wins and the flag is dropped from extra_args.

    Whether a flag takes a value (`--output-format json`) is decided by whether the next token starts with `-`.

    claude's DAG default extra_args is `--output-format stream-json --verbose` (#2966).
    When the builder emits a different `--output-format` (e.g. json_only), not only the
    stream-json pair is dropped but also its companion `--verbose` (a stream-only flag).
    """
    emitted = {tok for tok in perm_flags if tok.startswith("-")}
    result: list[str] = []
    i = 0
    stripped_stream_format = False
    while i < len(extra_args):
        tok = extra_args[i]
        takes_value = (
            tok.startswith("-")
            and i + 1 < len(extra_args)
            and not extra_args[i + 1].startswith("-")
        )
        if tok.startswith("-") and tok in emitted:
            if (
                tok == "--output-format"
                and takes_value
                and extra_args[i + 1] == "stream-json"
            ):
                stripped_stream_format = True
            i += 2 if takes_value else 1
            continue
        result.append(tok)
        if takes_value:
            result.append(extra_args[i + 1])
            i += 2
        else:
            i += 1
    if stripped_stream_format:
        result = [
            tok for tok in result
            if tok not in {"--verbose", "--stream-partial-output"}
        ]
    return result


_CLAUDE_WEB_SEARCH_DEFAULT_TOOLS: tuple[str, ...] = (
    "WebFetch",
    "WebSearch",
    "Read",
    "Grep",
    "Glob",
)


def _claude_allowed_tools(permission: Permission) -> tuple[str, ...]:
    """Resolve --allowed-tools for claude from allowed_tools and web_search."""
    if permission.web_search:
        if not permission.allowed_tools:
            return _CLAUDE_WEB_SEARCH_DEFAULT_TOOLS
        merged = list(permission.allowed_tools)
        for tool in ("WebFetch", "WebSearch"):
            if tool not in merged:
                merged.append(tool)
        seen: set[str] = set()
        deduped: list[str] = []
        for tool in merged:
            if tool in seen:
                continue
            seen.add(tool)
            deduped.append(tool)
        return tuple(deduped)
    return permission.allowed_tools


def _capability_pre_subcommand_args(
    engine: str,
    spec: EngineSpec,
    permission: Permission | None,
) -> tuple[str, ...]:
    """Static spec.pre_subcommand_args plus capability-derived pre-subcommand args."""
    parts = list(spec.pre_subcommand_args)
    if permission is not None and engine == "codex" and permission.web_search:
        if "--search" not in parts:
            parts.append("--search")
    return tuple(parts)


def _build_claude_flags(
    permission: Permission, dangerously_skip_permissions: bool
) -> list[str]:
    # sandbox=readonly → --permission-mode plan (read-only Bash allowed, mutations denied).
    # Combining it with an explicit permission_mode is rejected because the meanings conflict.
    if permission.sandbox == "readonly":
        if permission.permission_mode != "default":
            raise ValueError(
                "sandbox='readonly' conflicts with explicit permission_mode="
                f"{permission.permission_mode!r}; use one or the other"
            )
        flags = ["--permission-mode", "plan"]
    else:
        flags = ["--permission-mode", permission.permission_mode]
    # stream=True → stream-json --verbose. EngineSpec.extra_args carries the same flags, so
    # render_exec_command deduplicates them with _dedupe_extra_args (#2966).
    if permission.stream:
        flags += ["--output-format", "stream-json", "--verbose"]
    elif permission.output_format != "text":
        flags += ["--output-format", permission.output_format]
    allowed_tools = _claude_allowed_tools(permission)
    if allowed_tools:
        flags += ["--allowed-tools", ",".join(allowed_tools)]
    if permission.disallowed_tools:
        flags += ["--disallowed-tools", ",".join(permission.disallowed_tools)]
    # --disable-slash-commands is added by build_llm_cmd / render_exec_command
    # only when isolation=True (nexus #3174).
    if dangerously_skip_permissions:
        flags += ["--dangerously-skip-permissions"]
    return flags


def _build_cursor_flags(
    permission: Permission, dangerously_skip_permissions: bool
) -> list[str]:
    # cursor CLI `--sandbox <enabled|disabled>` is a binary mode that overrides config
    # (observed via agent --help). When enabled, Cursor's sandbox is enforced.
    # Details of write/network blocking depend on the CLI/config. Combining with --force is contradictory.
    # When stream=True, emit --output-format stream-json --stream-partial-output and
    # avoid duplicates with EngineSpec.extra_args via _dedupe_extra_args (#2967 / #2968).
    flags: list[str] = []
    bypass = dangerously_skip_permissions or permission.permission_mode == "bypassPermissions"
    if permission.sandbox == "readonly":
        if bypass:
            raise ValueError(
                "sandbox='readonly' conflicts with --force (bypass permissions)"
            )
        flags += ["--sandbox", "enabled"]
    elif bypass:
        flags.append("--force")
    if permission.stream:
        flags += ["--output-format", "stream-json", "--stream-partial-output"]
    elif permission.output_format != "text":
        flags += ["--output-format", permission.output_format]
    return flags


def _build_codex_flags(
    permission: Permission, dangerously_skip_permissions: bool
) -> list[str]:
    # permission_mode is also checked for the exec.jsonl path (render_exec_command).
    # render_exec_command calls the builder with dangerously_skip_permissions=False fixed, so
    # without looking at capabilities DANGEROUS_FULL_ACCESS would not become a CLI flag and
    # codex would start in the workspace-write sandbox, unable to write outside cwd.
    # The call() path never reaches this branch, because _validate_permission_for_engine
    # rejects codex with permission_mode != "default".
    flags = ["--json", "--skip-git-repo-check"]
    bypass = dangerously_skip_permissions or permission.permission_mode == "bypassPermissions"
    if permission.sandbox == "readonly":
        if bypass:
            raise ValueError(
                "sandbox='readonly' conflicts with dangerously-bypass-sandbox"
            )
        flags += ["-s", "read-only"]
    elif bypass:
        flags.append("--dangerously-bypass-approvals-and-sandbox")
    return flags


_CAPABILITY_FLAG_BUILDERS: dict[str, Callable[[Permission, bool], list[str]]] = {
    "claude": _build_claude_flags,
    "cursor": _build_cursor_flags,
    "codex": _build_codex_flags,
}


def render_exec_command(
    spec: EngineSpec,
    *,
    order_path: str,
    model: str | None,
    prompt: str | None = None,
    permission: Permission | None = None,
    resume_session_id: str | None = None,
    isolation: bool = False,
) -> str:
    """For the command field of exec.jsonl (does not include the tee pipe).

    prompt is ignored for FLAG_ONLY / NONE (not put in argv; input is stdin / ARGV only).
    When permission is None, EngineSpec.danger_flag is used as before.
    When permission is given, flags are generated via _CAPABILITY_FLAG_BUILDERS and
    flags emitted by the builder are removed from extra_args (_dedupe_extra_args).
    When isolation=True and the engine is claude, --disable-slash-commands is added (nexus #3174).
    When isolation=True and the engine is codex, -c skills.include_instructions=false is added (#5541).
    sandbox="container" wraps the engine CLI with container_prefix (engine flags are
    the same as sandbox="off"; stdin is redirected on the host and forwarded via -i).
    """
    del prompt  # not put in argv for FLAG_ONLY / NONE

    perm_flags: list[str] = []
    if permission is not None:
        builder = _CAPABILITY_FLAG_BUILDERS.get(spec.name)
        if builder:
            perm_flags = builder(permission, False)

    effective_extra_args = _dedupe_extra_args(spec.extra_args, perm_flags)
    if isolation and spec.name == "claude":
        effective_extra_args = [*effective_extra_args, "--disable-slash-commands"]
    elif isolation and spec.name == "codex":
        effective_extra_args = [
            *effective_extra_args,
            "-c",
            "skills.include_instructions=false",
        ]
    resume_flags: list[str] = []
    subcommand = list(spec.subcommand)
    if resume_session_id:
        if spec.name in {"claude", "cursor"}:
            resume_flags = ["--resume", f"'{resume_session_id}'"]
        elif spec.name == "codex":
            subcommand = ["exec", "resume", f"'{resume_session_id}'"]

    in_container = permission is not None and permission.sandbox == "container"

    if spec.input_mode is InputMode.ARGV:
        if in_container:
            raise ValueError(
                f"sandbox='container' is not supported for ARGV engine {spec.name!r}"
            )
        pre_args = _capability_pre_subcommand_args(
            spec.name, spec, permission
        )
        parts = [spec.cli, *pre_args, *spec.subcommand]
        if spec.extra_args:
            parts.extend(spec.extra_args)
        parts.append(order_path)
        return " ".join(parts)

    if spec.input_mode is not InputMode.STDIN:
        raise ValueError(f"Unknown input_mode: {spec.input_mode!r}")

    pre_args = _capability_pre_subcommand_args(spec.name, spec, permission)
    if in_container:
        assert permission is not None
        parts = [*container_prefix(permission), spec.cli]
    else:
        parts = [spec.cli]
    parts.extend(pre_args)
    parts.extend(subcommand)

    def _append_prompt_flag() -> None:
        if spec.prompt_flag is PromptFlag.FLAG_ONLY and spec.prompt_flag_token:
            parts.append(spec.prompt_flag_token)

    def _append_model() -> None:
        if spec.model_flag and model:
            parts.append(spec.model_flag)
            parts.append(f"'{model}'")

    def _append_danger_or_perm() -> None:
        if permission is None:
            if spec.danger_flag:
                parts.append(spec.danger_flag)
        else:
            parts.extend(perm_flags)

    if spec.danger_flag_position == "leading":
        # cursor: model → prompt → resume → danger/perm → extra_args
        _append_model()
        _append_prompt_flag()
        if resume_flags:
            parts.extend(resume_flags)
        _append_danger_or_perm()
        if effective_extra_args:
            parts.extend(effective_extra_args)
    else:
        # claude / gemini / codex: prompt → model → resume → extra_args → danger/perm
        _append_prompt_flag()
        _append_model()
        if resume_flags:
            parts.extend(resume_flags)
        if effective_extra_args:
            parts.extend(effective_extra_args)
        _append_danger_or_perm()

    return " ".join(parts) + f" < {shlex.quote(order_path)}"


def inject_resume(spec: EngineSpec, command: str, session_id: str) -> str:
    """Insert resume flags into a render_exec_command-produced shell command string."""
    quoted = f"'{session_id}'"
    resume_snippet = f" --resume {quoted}"

    if spec.name in {"claude", "cursor"}:
        if _RESUME_ALREADY_CLAUDE_CURSOR.search(command):
            return command
        positions: list[int] = []
        if spec.prompt_flag_token:
            prompt_re = re.compile(
                rf"(?<!\S){re.escape(spec.prompt_flag_token)}(?=[\s=]|$)"
            )
            match = prompt_re.search(command)
            if match:
                positions.append(match.end())
        model_match = _MODEL_WITH_VALUE.search(command)
        if model_match:
            positions.append(model_match.end())
        if positions:
            insert_at = max(positions)
            return command[:insert_at] + resume_snippet + command[insert_at:]
        if " < " in command:
            idx = command.rfind(" < ")
            return command[:idx] + resume_snippet + command[idx:]
        return command + resume_snippet

    if spec.name == "codex":
        if _RESUME_ALREADY_CODEX.search(command):
            return command
        if len(spec.subcommand) >= 2:
            sub0, sub1 = spec.subcommand[0], spec.subcommand[1]
            pattern = re.compile(
                rf"(?<!\S){re.escape(sub0)}\s+{re.escape(sub1)}(?=\s)"
            )
            match = pattern.search(command)
            if match:
                replacement = f"{sub0} resume {quoted}"
                return command[: match.start()] + replacement + command[match.end() :]
        return command

    return command


def build_llm_cmd(
    engine: str,
    model: str,
    prompt: str,
    *,
    permission: Permission = TEXT_ONLY,
    dangerously_skip_permissions: bool = False,
    resume_session_id: str | None = None,
    isolation: bool = False,
) -> list[str]:
    """Build the LLM CLI command list.

    Args:
        engine: Engine name
        model: Validated model ID
        prompt: prompt text. STDIN engines do not put it on argv (the caller passes it via stdin)
        permission: Capability-constraint value object (default: TEXT_ONLY)
        dangerously_skip_permissions: Add --dangerously-skip-permissions for the claude engine
        resume_session_id: Session ID to resume (supported engines only)
        isolation: When True, claude gets --disable-slash-commands (nexus #3174); codex gets
            -c skills.include_instructions=false (#5541).
    Returns:
        Command list for subprocess

    Raises:
        ValueError: permission.sandbox == "container" (only render_exec_command wraps
            commands in a container).
    """
    if permission.sandbox == "container":
        raise ValueError(
            "sandbox='container' is not supported by build_llm_cmd; "
            "use render_exec_command"
        )
    spec = ENGINE_SPECS.get(engine)
    cli = spec.cli if spec else engine
    if spec:
        pre_args = _capability_pre_subcommand_args(engine, spec, permission)
        subcommand = list(spec.subcommand)
        if resume_session_id and spec.name == "codex":
            subcommand = ["exec", "resume", resume_session_id]
        cmd = [cli, *pre_args, *subcommand]
    else:
        cmd = [cli]

    if spec is None:
        cmd += ["--model", model, "-p", prompt]
    else:
        if spec.model_flag:
            cmd += [spec.model_flag, model]
        if (
            spec.input_mode is InputMode.STDIN
            and spec.prompt_flag is PromptFlag.FLAG_ONLY
            and spec.prompt_flag_token
        ):
            # STDIN engines never put the prompt body on argv (the caller passes it via
            # stdin; avoids Linux MAX_ARG_STRLEN). Same rule as render_exec_command, nexus #3805.
            cmd += [spec.prompt_flag_token]
        elif spec.prompt_flag_token:
            cmd += [spec.prompt_flag_token, prompt]
        if resume_session_id and spec.name in {"claude", "cursor"}:
            cmd += ["--resume", resume_session_id]

    builder = _CAPABILITY_FLAG_BUILDERS.get(engine)
    if builder:
        cmd += builder(permission, dangerously_skip_permissions)
    elif dangerously_skip_permissions and spec and spec.danger_flag:
        cmd.append(spec.danger_flag)

    if isolation and engine == "claude":
        cmd.append("--disable-slash-commands")
    elif isolation and engine == "codex":
        cmd += ["-c", "skills.include_instructions=false"]

    return cmd


class EngineAdapter(Protocol):
    """Responsible for assembling exec records per engine."""

    @property
    def name(self) -> str:
        """Engine name ("claude", "gemini")."""
        ...

    def build_exec_record(
        self,
        *,
        uuid: str,
        order_path: str,
        result_path: str | None,
        model: str | None,
        depends: list[str],
        prompt: str | None = None,
        permission: Permission | None = None,
    ) -> dict:
        """Assemble one record (dict) to write to exec.jsonl.
        The command field does not include the tee pipe.
        """
        ...


class _GenericAdapter:
    """Generic adapter generated from ENGINE_SPECS. Unifies the 4 Adapter classes."""

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
        model: str | None,
        depends: list[str],
        prompt: str | None = None,
        permission: Permission | None = None,
    ) -> dict:
        return {
            "uuid": uuid,
            "engine": self._spec.name,
            "model": model if self._spec.model_flag else None,
            "command": render_exec_command(
                self._spec, order_path=order_path, prompt=prompt, model=model,
                permission=permission,
            ),
            "depends": depends,
            "result_path": result_path,
            "retry": 0,
            "annotations": {},
        }


_CUSTOM_ADAPTERS: dict[str, EngineAdapter] = {}


class AdapterNotFoundError(GhdagError, ValueError):
    """Raised when an unregistered engine adapter is requested."""

    code = "ADAPTER_NOT_FOUND"
    exit_code = 3


def register_adapter(adapter: EngineAdapter) -> None:
    _CUSTOM_ADAPTERS[adapter.name] = adapter


def get_adapter(name: str) -> EngineAdapter:
    spec = ENGINE_SPECS.get(name)
    if spec is not None:
        return _GenericAdapter(spec)
    if name in _CUSTOM_ADAPTERS:
        return _CUSTOM_ADAPTERS[name]
    raise AdapterNotFoundError(f"Unknown engine: {name!r}. Available: {sorted(ENGINE_SPECS)}")
