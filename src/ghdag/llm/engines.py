"""
llm/engines.py — engine/model allowlist and one-shot LLM invocation

Provides single-shot LLM calls that do not involve a workflow.
ghdag manages the allowed models per engine, reducing the burden on scripts.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ghdag.config.env import (
    stall_guard_enabled,
    stall_guard_interval_sec,
    stall_guard_stall_sec,
)
from ghdag.core.command import (
    _CAPABILITY_FLAG_BUILDERS,
    _build_claude_flags,
    _build_codex_flags,
    _build_cursor_flags,
    build_llm_cmd,
)
from ghdag.core.models.metrics import FailureClass
from ghdag.core.ports.output import EngineError
from ghdag.core.stall_guard import StallEvent, StallTracker
from ghdag.exceptions import GhdagError
from ghdag.llm._config import load_engine_models
from ghdag.llm.adapters import get_output_adapter
from ghdag.llm.capabilities import TEXT_ONLY, LLMCapabilities, LLMParseError
from ghdag.llm.spec import ENGINE_SPECS, InputMode

__all__ = [
    "EngineModelError",
    "LLMResult",
    "TextResult",
    "build_llm_cmd",
    "call",
    "call_text",
    "extract_stream_result",
    "get_engine_models",
    "list_engines",
    "list_models",
    "validate_engine_model",
    "ENGINE_CLI",
    "ENGINE_DEFAULTS",
    "TIMEOUT_RETURNCODE",
    "_CAPABILITY_FLAG_BUILDERS",
    "_build_claude_flags",
    "_build_codex_flags",
    "_build_cursor_flags",
    "_IGNORED_CAPABILITIES",
    "_UNSUPPORTED_CAPABILITIES",
    "_extract_stream_result",
    "supports_capability",
]


logger = logging.getLogger(__name__)


class EngineModelError(GhdagError):
    """Raised when an unknown engine or unauthorized model is specified."""


# ---------------------------------------------------------------------------
# Engine/model allowlist (lazy init — env / cwd are not read at import time)
# ---------------------------------------------------------------------------

_ENGINE_MODELS: dict[str, list[str]] | None = None


def get_engine_models() -> dict[str, list[str]]:
    """Return the ENGINE_MODELS equivalent (runs load_engine_models on first call and caches it)."""
    global _ENGINE_MODELS
    if _ENGINE_MODELS is None:
        _ENGINE_MODELS = load_engine_models()
    return _ENGINE_MODELS


# CLI command name per engine (derived from spec)
ENGINE_CLI: dict[str, str] = {name: spec.cli for name, spec in ENGINE_SPECS.items()}

# Default model per engine (derived from spec)
ENGINE_DEFAULTS: dict[str, str | None] = {name: spec.default_model for name, spec in ENGINE_SPECS.items()}


def list_engines() -> list[str]:
    """Return the list of available engine names."""
    return sorted(get_engine_models().keys())


def list_models(engine: str) -> list[str]:
    """Return the allowed models for the given engine.

    Raises:
        EngineModelError: unknown engine
    """
    models = get_engine_models()
    if engine not in models:
        raise EngineModelError(
            f"Unknown engine: {engine!r}. "
            f"Available: {sorted(models.keys())}"
        )
    return sorted(models[engine])


def validate_engine_model(engine: str, model: str | None) -> str:
    """Validate the engine/model combination and return the resolved model ID.

    Args:
        engine: engine name ("claude", "gemini", etc.)
        model: model ID (None means the default)
    Returns:
        the validated model ID
    Raises:
        EngineModelError: unknown engine or disallowed model
    """
    models = get_engine_models()
    if engine not in models:
        raise EngineModelError(
            f"Unknown engine: {engine!r}. "
            f"Available: {sorted(models.keys())}"
        )

    if model is None:
        default = ENGINE_DEFAULTS[engine]
        if default is None:
            raise EngineModelError(
                f"Engine {engine!r} has no default model; specify model explicitly"
            )
        return default

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

# Unsupported capabilities: passing a non-default value raises NotImplementedError.
# "isolation" is not an LLMCapabilities attribute but a meta capability for supports_capability
# (whether global config isolation is possible). Unsupported on cursor because its CLI has no
# skills/rules isolation flag (nexus #3044; the alternative is physical isolation, nexus #2972).
_UNSUPPORTED_CAPABILITIES: dict[str, set[str]] = {
    "gemini": {"disallowed_tools", "allowed_tools", "permission_mode", "stream", "sandbox", "resume"},
    "cursor": {"allowed_tools", "permission_mode", "isolation"},
    "shell": {"stream", "sandbox", "resume"},
    # codex stream is --json JSONL. output_format remains unsupported (#2967).
    "codex": {"permission_mode", "output_format"},
}

# CODEX_HOME for codex isolation via call() (does not read the user's ~/.codex).
# Injected only when GHDAG_ENGINE_ISOLATION=1 and auth.json exists (nexus #3174).
_CODEX_DAG_HOME = "/var/tmp/ghdag-dag-codex/"


def _resolve_isolation(isolation: bool | None) -> bool:
    """Resolve from the explicit isolation value, else from the GHDAG_ENGINE_ISOLATION env var."""
    if isolation is not None:
        return isolation
    return bool(os.environ.get("GHDAG_ENGINE_ISOLATION"))

# Capabilities treated as noop (value accepted but not reflected in CLI flags) because
# the engine has no equivalent concept.
# codex: allowed_tools / disallowed_tools do not exist in codex-cli; permission control is done
#   at the OS level via --sandbox / --dangerously-bypass-approvals-and-sandbox.
#   Since the TEXT_ONLY / JSON_ONLY presets carry disallowed_tools by default, making these
#   noop instead of NotImplementedError lets callers invoke codex with the default capabilities
#   without writing a wrapper.
# cursor: there is no CLI flag equivalent to disallowed_tools, and _build_cursor_flags does not
#   reference it. The approval-deny when --force is not given is the effective guard. This
#   documents what was previously silently ignored.
_IGNORED_CAPABILITIES: dict[str, set[str]] = {
    "codex": {"allowed_tools", "disallowed_tools"},
    "cursor": {"disallowed_tools"},
}


def supports_capability(engine: str, capability: str) -> bool:
    """Whether the engine supports the capability (public API).

    effective_unsupported = _UNSUPPORTED_CAPABILITIES - _IGNORED_CAPABILITIES.
    Returns False if capability is in it, True otherwise.
    Unknown engines and unknown capabilities return True (conservative: do not reject).
    """
    unsupported = _UNSUPPORTED_CAPABILITIES.get(engine, set())
    ignored = _IGNORED_CAPABILITIES.get(engine, set())
    effective_unsupported = unsupported - ignored
    return capability not in effective_unsupported


def _validate_capabilities_for_engine(engine: str, capabilities: LLMCapabilities) -> None:
    """Verify that the engine supports the features in capabilities.

    Attributes listed in _IGNORED_CAPABILITIES skip validation and are accepted (noop).

    Raises:
        NotImplementedError: if a feature the engine does not support is specified
    """
    unsupported = _UNSUPPORTED_CAPABILITIES.get(engine, set())
    ignored = _IGNORED_CAPABILITIES.get(engine, set())
    if ignored:
        unsupported = unsupported - ignored
    for attr in unsupported:
        # Meta capabilities such as isolation are not on LLMCapabilities (supports_capability only)
        if not hasattr(capabilities, attr):
            continue
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
            # "off" is truthy, so the generic `elif val` would reject it. Compare explicitly.
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
    """Result of a one-shot LLM call."""
    stdout: str
    stderr: str
    returncode: int
    latency_ms: float = 0.0
    session_id: str | None = None
    failure_class: FailureClass | None = None
    """Failure class decided by ``call()`` itself (e.g. ``TIMEOUT``).

    ``None`` means the caller must classify from ``stdout`` / ``stderr``
    (``call_managed`` does this through the engine's output adapter).
    """

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def timed_out(self) -> bool:
        return self.failure_class is FailureClass.TIMEOUT

    def validate(
        self,
        capabilities: LLMCapabilities,
        *,
        engine: str | None = None,
    ) -> "LLMResult":
        """Validate the output_format contract. Raises LLMParseError on failure.

        Validation is skipped when returncode != 0 (error output takes precedence).
        With stream=True on cursor, the reconstructed assistant-turn body is preferred,
        falling back to the legacy stream result if absent. For claude, the final result
        is extracted from the JSONL and replaces stdout. For codex, raw JSONL is kept.
        Returns:
            self (allows chaining)
        Raises:
            LLMParseError: if output_format == "json" and stdout is not valid JSON
        """
        if not self.ok:
            return self
        if capabilities.stream and engine != "codex":
            if engine == "cursor":
                from ghdag.llm.adapters.cursor_stream import reconstruct_assistant_turns

                reconstructed = reconstruct_assistant_turns(self.stdout.encode("utf-8"))
                if reconstructed:
                    self.stdout = reconstructed
                else:
                    self.stdout = _extract_stream_result(self.stdout)
            else:
                self.stdout = _extract_stream_result(self.stdout)
        if capabilities.output_format == "json":
            try:
                json.loads(self.stdout)
            except json.JSONDecodeError as e:
                raise LLMParseError(raw=self.stdout, reason=str(e)) from e
        return self


@dataclass(frozen=True)
class TextResult:
    """Result of call_text(). A snapshot with text already extracted by the adapter."""
    body: str
    success: bool
    raw: LLMResult
    error: EngineError | None = None

    @property
    def stderr(self) -> str:
        return self.raw.stderr

    @property
    def returncode(self) -> int:
        return self.raw.returncode

    @property
    def session_id(self) -> str | None:
        return self.raw.session_id


def extract_stream_result(stdout: str) -> str:
    """Extract the final result text from stream-json JSONL (shared with claude_json)."""
    from ghdag.llm.adapters.claude_json import extract_stream_result as _impl

    return _impl(stdout)


# Backward-compatible alias (existing tests and callers)
_extract_stream_result = extract_stream_result


def _compose_stdin(prompt: str, stdin_text: str | None) -> str:
    """Build the stdin for a STDIN engine (join the non-empty parts with "\n\n")."""
    if stdin_text is None:
        return prompt
    if prompt == "":
        return stdin_text
    return prompt + "\n\n" + stdin_text


def _launch_failure_result(executable: str, returncode: int, reason: str, t0: float) -> LLMResult:
    """Turn a launch failure (FileNotFoundError / PermissionError) into a shell-style LLMResult."""
    return LLMResult(
        stdout="",
        stderr=f"{executable}: {reason}\n",
        returncode=returncode,
        latency_ms=(time.monotonic() - t0) * 1000,
    )


# Exit status of coreutils ``timeout(1)`` when the command is killed on expiry.
TIMEOUT_RETURNCODE = 124


def _partial_output(data: bytes | str | None) -> str:
    """Decode the partial stdout / stderr that ``TimeoutExpired`` carries.

    ``subprocess.run`` joins the raw chunks as bytes even in text mode, and
    leaves the attribute ``None`` when nothing was captured.
    """
    if data is None:
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return data


def _timeout_result(
    executable: str, exc: subprocess.TimeoutExpired, t0: float
) -> LLMResult:
    """Turn ``subprocess.TimeoutExpired`` into an ``LLMResult`` tagged ``TIMEOUT``.

    The process has already been killed by ``subprocess.run``. The partial
    stdout / stderr are kept so the caller can still inspect what the engine
    produced before the deadline, and a ``TIMEOUT: ...`` line is appended to
    stderr in the same shape as the exec-path task timeout (sumipan/nexus#4304).
    """
    stderr = _partial_output(exc.stderr)
    if stderr and not stderr.endswith("\n"):
        stderr += "\n"
    stderr += f"TIMEOUT: {executable} timed out after {exc.timeout}s\n"
    return LLMResult(
        stdout=_partial_output(exc.stdout),
        stderr=stderr,
        returncode=TIMEOUT_RETURNCODE,
        latency_ms=(time.monotonic() - t0) * 1000,
        failure_class=FailureClass.TIMEOUT,
    )


def read_ps() -> str:
    """Return ``ps -axo pid=,ppid=,time=,comm=,args=`` output (stall-guard process snapshot)."""
    result = subprocess.run(
        ["ps", "-axo", "pid=,ppid=,time=,comm=,args="],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode != 0:
        raise subprocess.SubprocessError(
            f"ps exited {result.returncode}: {result.stderr.strip()}"
        )
    return result.stdout


# Injection points for the cursor stall guard (tests monkeypatch these).
_stall_ps: Callable[[], str] = read_ps
_stall_kill: Callable[[int, int], None] = os.kill


def _append_stall_lines(stderr: str | None, events: list[StallEvent]) -> str:
    text = stderr or ""
    if not events:
        return text
    if text and not text.endswith("\n"):
        text += "\n"
    for ev in events:
        text += f"[stall-guard] killed cat pid={ev.pid} after {ev.stalled_sec:.0f}s\n"
    return text


def _run_with_stall_guard(
    cmd: list[str], run_kwargs: dict
) -> subprocess.CompletedProcess[str]:
    """Same contract as ``subprocess.run`` (returns ``CompletedProcess``, raises
    ``TimeoutExpired`` on expiry and ``FileNotFoundError`` / ``PermissionError``
    on launch failure) while watching cursor's state-reader ``cat``.

    The cursor-agent shell can hang in ``zsh -> cat <&3`` (sumipan/nexus#4931);
    every ``interval_sec`` the process tree is scanned and a stuck ``cat`` is
    SIGTERMed once it has stalled for ``stall_sec``.
    """
    input_text = run_kwargs.get("input")
    timeout = run_kwargs.get("timeout")
    popen_kwargs: dict = {
        "stdin": subprocess.PIPE if input_text is not None else None,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "cwd": run_kwargs.get("cwd"),
    }
    if "env" in run_kwargs:
        popen_kwargs["env"] = run_kwargs["env"]

    t0 = time.monotonic()
    proc = subprocess.Popen(cmd, **popen_kwargs)
    interval_sec = stall_guard_interval_sec()
    tracker = StallTracker(
        proc.pid,
        stall_sec=stall_guard_stall_sec(),
        interval_sec=interval_sec,
        kill=_stall_kill,
        ps=_stall_ps,
    )
    deadline = None if timeout is None else t0 + timeout
    pending_input = input_text
    try:
        while True:
            # The first scan baselines the stall clock before any wait.
            for ev in tracker.check(time.monotonic()):
                logger.warning(
                    "[stall-guard] engine=cursor sent SIGTERM to pid=%d after %.0fs: %s",
                    ev.pid,
                    ev.stalled_sec,
                    ev.parent_args_head,
                )
            if deadline is None:
                slice_sec = interval_sec
            else:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    proc.kill()
                    out, err = proc.communicate()
                    raise subprocess.TimeoutExpired(
                        cmd,
                        timeout,
                        output=out,
                        stderr=_append_stall_lines(err, tracker.events),
                    )
                slice_sec = min(interval_sec, remaining)
            try:
                # CPython rejects input once communication has started; the
                # retry keeps everything read so far.
                out, err = proc.communicate(input=pending_input, timeout=slice_sec)
                break
            except subprocess.TimeoutExpired:
                pending_input = None
    except BaseException:
        # Same as subprocess.run: never leave the child running on an error.
        if proc.returncode is None:
            proc.kill()
            proc.wait()
        raise
    return subprocess.CompletedProcess(
        cmd, proc.returncode, out, _append_stall_lines(err, tracker.events)
    )


def call(
    prompt: str,
    *,
    engine: str = "claude",
    model: str | None = None,
    timeout: int | None = None,
    stdin_text: str | None = None,
    cwd: Path | str | None = None,
    capabilities: LLMCapabilities = TEXT_ONLY,
    dangerously_skip_permissions: bool = False,
    resume_session_id: str | None = None,
    isolation: bool | None = None,
) -> LLMResult:
    """Call the LLM one-shot and return the result.

    Args:
        prompt: prompt string
        engine: engine name (default: "claude")
        model: model ID (None for the engine default)
        timeout: timeout in seconds (None for no limit)
        stdin_text: text passed on standard input. STDIN engines join it with prompt
            into a single stdin (stdin_text only when prompt is empty, prompt only when
            None, ``prompt + "\n\n" + stdin_text`` when both are non-empty)
        cwd: subprocess working directory (None for the current process cwd)
        capabilities: capability-constraint value object (default: TEXT_ONLY)
        isolation: engine isolation. When None, resolved from the GHDAG_ENGINE_ISOLATION env var
            (default False)
    Returns:
        LLMResult (missing binary: returncode=127; not executable: 126;
        timeout: returncode=124 with ``failure_class=FailureClass.TIMEOUT`` and
        the partial stdout / stderr captured before the process was killed)
    Raises:
        EngineModelError: engine/model validation failed
        NotImplementedError: capabilities feature not supported by the engine
    """
    _validate_capabilities_for_engine(engine, capabilities)
    if resume_session_id:
        unsupported = _UNSUPPORTED_CAPABILITIES.get(engine, set())
        ignored = _IGNORED_CAPABILITIES.get(engine, set())
        if "resume" in (unsupported - ignored):
            raise NotImplementedError(
                f"{engine} engine does not support resume (got {resume_session_id!r})"
            )
    resolved_model = validate_engine_model(engine, model)
    resolved_isolation = _resolve_isolation(isolation)

    # STDIN engines (claude / cursor / gemini / codex) receive the prompt via stdin
    # (on argv it would exceed Linux MAX_ARG_STRLEN and fail with E2BIG; nexus #3805).
    spec = ENGINE_SPECS.get(engine)
    effective_stdin = stdin_text
    if spec and spec.input_mode is InputMode.STDIN:
        effective_stdin = _compose_stdin(prompt, stdin_text)

    cmd = build_llm_cmd(
        engine,
        resolved_model,
        prompt,
        capabilities=capabilities,
        dangerously_skip_permissions=dangerously_skip_permissions,
        resume_session_id=resume_session_id,
        isolation=resolved_isolation,
    )

    t0 = time.monotonic()
    run_kwargs: dict = {
        "capture_output": True,
        "text": True,
        "input": effective_stdin,
        "timeout": timeout,
        "cwd": cwd,
    }
    # codex: start with an empty CODEX_HOME only when isolation=True and auth.json exists (nexus #3174)
    if engine == "codex" and resolved_isolation:
        auth_path = Path(_CODEX_DAG_HOME) / "auth.json"
        if auth_path.exists():
            Path(_CODEX_DAG_HOME).mkdir(parents=True, exist_ok=True)
            run_kwargs["env"] = {**os.environ, "CODEX_HOME": _CODEX_DAG_HOME}
        else:
            print(
                "warning: codex isolation skipped "
                f"(auth.json missing at {auth_path})",
                file=sys.stderr,
            )
    try:
        if engine == "cursor" and stall_guard_enabled():
            result = _run_with_stall_guard(cmd, run_kwargs)
        else:
            result = subprocess.run(cmd, **run_kwargs)
    except FileNotFoundError as e:
        # A missing cwd raises the same exception with filename=cwd; only the binary counts.
        if e.filename not in (None, cmd[0]):
            raise
        # Mirror the shell (exit 127 / "command not found") so adapters classify it
        # as ENGINE_ENVIRONMENT_ERROR.
        return _launch_failure_result(cmd[0], 127, "command not found", t0)
    except PermissionError as e:
        if e.filename not in (None, cmd[0]):
            raise
        return _launch_failure_result(cmd[0], 126, "permission denied", t0)
    except subprocess.TimeoutExpired as e:
        # The engine kept running past ``timeout`` (e.g. cursor ``agent -p`` idling
        # after it had already committed). Report it as a classified failure
        # instead of unwinding the caller (sumipan/nexus#4304).
        return _timeout_result(cmd[0], e, t0)
    latency_ms = (time.monotonic() - t0) * 1000
    session_id: str | None = None
    if result.returncode == 0:
        adapter = get_output_adapter(engine)
        session_id = adapter.extract_session_id(
            result.stdout.encode("utf-8"),
            result.stderr.encode("utf-8"),
        )

    llm_result = LLMResult(
        stdout=result.stdout,
        stderr=result.stderr,
        returncode=result.returncode,
        latency_ms=latency_ms,
        session_id=session_id,
    )
    return llm_result.validate(capabilities, engine=engine)


def call_text(
    prompt: str,
    *,
    engine: str = "claude",
    model: str | None = None,
    timeout: int | None = None,
    stdin_text: str | None = None,
    cwd: Path | str | None = None,
    capabilities: LLMCapabilities = TEXT_ONLY,
    dangerously_skip_permissions: bool = False,
    resume_session_id: str | None = None,
    isolation: bool | None = None,
) -> TextResult:
    """Call the LLM one-shot and return a TextResult with text extracted by the adapter.

    Same signature as call(). Usable as a drop-in, backward-compatible superset.
    Falls back to raw.stdout when the adapter output is empty.
    """
    result = call(
        prompt,
        engine=engine,
        model=model,
        timeout=timeout,
        stdin_text=stdin_text,
        cwd=cwd,
        capabilities=capabilities,
        dangerously_skip_permissions=dangerously_skip_permissions,
        resume_session_id=resume_session_id,
        isolation=isolation,
    )
    adapter = get_output_adapter(engine)
    stdout_bytes = result.stdout.encode("utf-8")
    stderr_bytes = result.stderr.encode("utf-8")
    error = adapter.extract_error(stdout_bytes, stderr_bytes)
    if error is not None:
        return TextResult(body="", success=False, error=error, raw=result)
    extracted = adapter.extract_result_text(stdout_bytes, stderr_bytes).decode("utf-8")
    body = extracted if extracted else result.stdout
    return TextResult(body=body, success=result.ok, error=None, raw=result)
