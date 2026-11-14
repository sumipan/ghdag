"""
llm/engines.py — engine/model allowlist and one-shot LLM invocation

Provides single-shot LLM calls that do not involve a workflow.
ghdag manages the allowed models per engine, reducing the burden on scripts.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ghdag.config.env import (
    engine_isolation_enabled,
    stall_guard_enabled,
    stall_guard_interval_sec,
    stall_guard_stall_sec,
)
from ghdag.core.capabilities import TEXT_ONLY, Permission
from ghdag.core.command import (
    _CAPABILITY_FLAG_BUILDERS,
    _build_claude_flags,
    _build_codex_flags,
    _build_cursor_flags,
    build_llm_cmd,
)
from ghdag.core.engine_spec import ENGINE_SPECS, InputMode
from ghdag.core.exceptions import ConfigError, LLMParseError
from ghdag.core.models.metrics import FailureClass, TokenUsage
from ghdag.core.ports.output import EngineError
from ghdag.core.stall_guard import StallEvent, StallTracker
from ghdag.llm._config import load_engine_models
from ghdag.llm.adapters import get_output_adapter
from ghdag.llm.json_extract import extract_json_text
from ghdag.quota import QuotaGate

_NEXT_CANDIDATE_CLASSES = frozenset(
    {
        FailureClass.QUOTA_EXHAUSTED,
        FailureClass.AUTH,
        FailureClass.ENGINE_ENVIRONMENT_ERROR,
    }
)

_QUOTA_REPORT_CLASSES = frozenset(
    {
        FailureClass.QUOTA_EXHAUSTED,
        FailureClass.AUTH,
    }
)

__all__ = [
    "EngineModelError",
    "LLMResult",
    "build_llm_cmd",
    "call",
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


class EngineModelError(ConfigError):
    """Raised when an unknown engine or unauthorized model is specified."""

    code = "ENGINE_MODEL_INVALID"


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
        available = sorted(models.keys())
        raise EngineModelError(
            f"Unknown engine: {engine!r}. Available: {available}",
            code="ENGINE_UNKNOWN",
            hint=f"available engines: {available}",
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
        available = sorted(models.keys())
        raise EngineModelError(
            f"Unknown engine: {engine!r}. Available: {available}",
            code="ENGINE_UNKNOWN",
            hint=f"available engines: {available}",
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
        allowed_sorted = sorted(allowed)
        raise EngineModelError(
            f"Model not in allowlist: {model!r} (engine={engine}). Allowed: {allowed_sorted}",
            code="MODEL_NOT_ALLOWED",
            hint=(
                f"allowed models: {allowed_sorted}; "
                "override with llm-models.yml / GHDAG_LLM_MODELS"
            ),
        )
    return model


# ---------------------------------------------------------------------------
# Engine-specific capability validation — data-driven, no engine string branching
# ---------------------------------------------------------------------------

# Unsupported capabilities: passing a non-default value raises NotImplementedError.
# "isolation" is not a Permission attribute but a meta capability for supports_capability
# (whether global config isolation is possible). Unsupported on cursor because its CLI has no
# skills/rules isolation flag (physical isolation is the alternative).
_UNSUPPORTED_CAPABILITIES: dict[str, set[str]] = {
    "gemini": {
        "disallowed_tools",
        "allowed_tools",
        "permission_mode",
        "stream",
        "sandbox",
        "resume",
        "web_search",
    },
    "cursor": {"allowed_tools", "permission_mode", "isolation"},
    "shell": {"stream", "sandbox", "resume", "web_search"},
    # codex stream is --json JSONL. output_format remains unsupported (#2967).
    "codex": {"permission_mode", "output_format"},
}

# CODEX_HOME for codex isolation via call() (does not read the user's ~/.codex).
# Injected only when GHDAG_ENGINE_ISOLATION=1 and auth.json exists.
_CODEX_DAG_HOME = "/var/tmp/ghdag-dag-codex/"


def _resolve_isolation(isolation: bool | None) -> bool:
    """Resolve from the explicit isolation value, else from the GHDAG_ENGINE_ISOLATION env var."""
    if isolation is not None:
        return isolation
    return engine_isolation_enabled()

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
# Engines whose command builder turns permission_mode="bypassPermissions" into a bypass
# flag (codex: --dangerously-bypass-approvals-and-sandbox, cursor: --force).
_BYPASS_FLAG_ENGINES: frozenset[str] = frozenset({"codex", "cursor"})

_IGNORED_CAPABILITIES: dict[str, set[str]] = {
    "codex": {"allowed_tools", "disallowed_tools"},
    "cursor": {"disallowed_tools", "web_search"},
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


def _validate_permission_for_engine(engine: str, permission: Permission) -> None:
    """Verify that the engine supports the features in permission.

    Attributes listed in _IGNORED_CAPABILITIES skip validation and are accepted (noop).

    Raises:
        NotImplementedError: if a feature the engine does not support is specified
    """
    unsupported = _UNSUPPORTED_CAPABILITIES.get(engine, set())
    ignored = _IGNORED_CAPABILITIES.get(engine, set())
    if ignored:
        unsupported = unsupported - ignored
    for attr in unsupported:
        # Meta capabilities such as isolation are not on Permission (supports_capability only)
        if not hasattr(permission, attr):
            continue
        val = getattr(permission, attr)
        if attr == "permission_mode":
            # codex / cursor have no permission modes, but their command builders map
            # "bypassPermissions" to the engine's own bypass flag.
            if val == "bypassPermissions" and engine in _BYPASS_FLAG_ENGINES:
                continue
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
    """Result of a unified one-shot LLM call."""

    body: str
    stdout: str
    stderr: str
    returncode: int
    raw_stdout: str | None = None
    latency_ms: float = 0.0
    session_id: str | None = None
    failure_class: FailureClass | None = None
    error: EngineError | None = None
    usage: TokenUsage | None = None
    engine_used: str = ""
    model_used: str = ""
    attempts: int = 1
    quota_reported: bool = False
    additional_tags: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and self.error is None

    @property
    def timed_out(self) -> bool:
        return self.failure_class is FailureClass.TIMEOUT


@dataclass
class _SubprocessRun:
    """Raw output from a single subprocess invocation before completion."""

    stdout: str
    stderr: str
    returncode: int
    latency_ms: float
    failure_class: FailureClass | None = None
    raw_stdout: str | None = None
    session_id: str | None = None


def _normalize_subprocess_output(
    run: _SubprocessRun,
    *,
    engine: str,
    permission: Permission,
) -> None:
    """Apply stream / JSON normalization in place on a successful run."""
    if run.returncode != 0:
        return
    if permission.stream and engine != "codex":
        run.raw_stdout = run.stdout
        if engine == "cursor":
            from ghdag.llm.adapters.cursor_stream import reconstruct_assistant_turns

            reconstructed = reconstruct_assistant_turns(run.stdout.encode("utf-8"))
            if reconstructed:
                run.stdout = reconstructed
            else:
                run.stdout = _extract_stream_result(run.stdout)
        else:
            run.stdout = _extract_stream_result(run.stdout)
    if permission.output_format == "json":
        stream_preprocessed = permission.stream and engine != "codex"
        if stream_preprocessed:
            body = run.stdout
        elif engine in ("claude", "cursor"):
            adapter = get_output_adapter(engine)
            body = adapter.extract_result_text(
                run.stdout.encode("utf-8"),
                run.stderr.encode("utf-8"),
            ).decode("utf-8")
            if not body:
                body = run.stdout
        else:
            body = run.stdout
        if not stream_preprocessed:
            run.raw_stdout = run.stdout
        try:
            run.stdout = extract_json_text(body)
        except LLMParseError as e:
            raise LLMParseError(raw=body, reason=e.reason) from e


def _complete_result(
    run: _SubprocessRun,
    *,
    engine: str,
    model: str,
    permission: Permission,
) -> LLMResult:
    """Build a finished ``LLMResult`` from one subprocess run."""
    adapter = get_output_adapter(engine)
    stdout_for_adapter = run.raw_stdout if run.raw_stdout is not None else run.stdout
    stdout_bytes = stdout_for_adapter.encode("utf-8")
    stderr_bytes = run.stderr.encode("utf-8")
    failure_class = run.failure_class
    if failure_class is None and run.returncode != 0:
        failure_class = adapter.classify_failure(
            run.returncode, stdout_bytes, stderr_bytes
        )
    error = adapter.extract_error(stdout_bytes, stderr_bytes)
    if error is not None:
        return LLMResult(
            body="",
            stdout=run.stdout,
            stderr=run.stderr,
            raw_stdout=run.raw_stdout,
            returncode=run.returncode,
            latency_ms=run.latency_ms,
            session_id=run.session_id,
            failure_class=failure_class,
            error=error,
            usage=adapter.extract_token_usage(stdout_bytes, stderr_bytes),
            engine_used=engine,
            model_used=model,
        )
    extracted = adapter.extract_result_text(stdout_bytes, stderr_bytes).decode("utf-8")
    if permission.output_format == "json" and run.raw_stdout is not None:
        body = run.stdout
    else:
        body = extracted if extracted else run.stdout
    usage = adapter.extract_token_usage(stdout_bytes, stderr_bytes)
    return LLMResult(
        body=body,
        stdout=run.stdout,
        stderr=run.stderr,
        raw_stdout=run.raw_stdout,
        returncode=run.returncode,
        latency_ms=run.latency_ms,
        session_id=run.session_id,
        failure_class=failure_class,
        error=None,
        usage=usage,
        engine_used=engine,
        model_used=model,
    )


def _resolve_permission(
    engine: str,
    default: Permission,
    permission_overrides: Mapping[str, Permission] | None,
) -> Permission:
    if permission_overrides and engine in permission_overrides:
        return permission_overrides[engine]
    return default


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


def _launch_failure_result(executable: str, returncode: int, reason: str, t0: float) -> _SubprocessRun:
    """Turn a launch failure (FileNotFoundError / PermissionError) into a shell-style run."""
    return _SubprocessRun(
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
) -> _SubprocessRun:
    """Turn ``subprocess.TimeoutExpired`` into a run tagged ``TIMEOUT``.

    The process has already been killed by ``subprocess.run``. The partial
    stdout / stderr are kept so the caller can still inspect what the engine
    produced before the deadline, and a ``TIMEOUT: ...`` line is appended to
    stderr in the same shape as the exec-path task timeout.
    """
    stderr = _partial_output(exc.stderr)
    if stderr and not stderr.endswith("\n"):
        stderr += "\n"
    stderr += f"TIMEOUT: {executable} timed out after {exc.timeout}s\n"
    return _SubprocessRun(
        stdout=_partial_output(exc.stdout),
        stderr=stderr,
        returncode=TIMEOUT_RETURNCODE,
        latency_ms=(time.monotonic() - t0) * 1000,
        failure_class=FailureClass.TIMEOUT,
    )


def read_ps() -> str:
    """Return ``ps -axo pid=,ppid=,time=,comm=,args=`` output (stall-guard process snapshot)."""
    ps_exe = shutil.which("ps") or "ps"
    result = subprocess.run(
        [ps_exe, "-axo", "pid=,ppid=,time=,comm=,args="],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
        env={**os.environ, "LC_ALL": "C"},
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

    The cursor-agent shell can hang in ``zsh -> cat <&3``;
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


def _call_once(
    prompt: str,
    *,
    engine: str,
    model: str | None,
    timeout: int | None,
    stdin_text: str | None,
    cwd: Path | str | None,
    permission: Permission,
    resume_session_id: str | None,
    isolation: bool | None,
) -> _SubprocessRun:
    """Run the engine subprocess once (no retry, no adapter completion)."""
    _validate_permission_for_engine(engine, permission)
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
    # (on argv it would exceed Linux MAX_ARG_STRLEN and fail with E2BIG).
    spec = ENGINE_SPECS.get(engine)
    effective_stdin = stdin_text
    if spec and spec.input_mode is InputMode.STDIN:
        effective_stdin = _compose_stdin(prompt, stdin_text)

    cmd = build_llm_cmd(
        engine,
        resolved_model,
        prompt,
        permission=permission,
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
    # codex: start with an empty CODEX_HOME only when isolation=True and auth.json exists
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
        return _timeout_result(cmd[0], e, t0)
    latency_ms = (time.monotonic() - t0) * 1000
    session_id: str | None = None
    if result.returncode == 0:
        adapter = get_output_adapter(engine)
        session_id = adapter.extract_session_id(
            result.stdout.encode("utf-8"),
            result.stderr.encode("utf-8"),
        )

    return _SubprocessRun(
        stdout=result.stdout,
        stderr=result.stderr,
        returncode=result.returncode,
        latency_ms=latency_ms,
        session_id=session_id,
    )


def _run_completed_attempt(
    prompt: str,
    *,
    engine: str,
    model: str | None,
    timeout: int | None,
    stdin_text: str | None,
    cwd: Path | str | None,
    permission: Permission,
    resume_session_id: str | None,
    isolation: bool | None,
) -> LLMResult:
    resolved_model = validate_engine_model(engine, model)
    run = _call_once(
        prompt,
        engine=engine,
        model=model,
        timeout=timeout,
        stdin_text=stdin_text,
        cwd=cwd,
        permission=permission,
        resume_session_id=resume_session_id,
        isolation=isolation,
    )
    _normalize_subprocess_output(run, engine=engine, permission=permission)
    return _complete_result(
        run, engine=engine, model=resolved_model, permission=permission
    )


def _engine_paused(quota_gate: QuotaGate | None, engine: str) -> bool:
    if quota_gate is None:
        return False
    snapshot = quota_gate.snapshot()
    state = snapshot.engines.get(engine)
    return state is not None and state.status == "paused"


def _report_quota_failure(
    *,
    quota_gate: QuotaGate,
    engine: str,
    failure_class: FailureClass,
) -> bool:
    """Report quota pause for ``failure_class``; return whether pause was recorded."""
    now = datetime.now(timezone.utc)
    if failure_class is FailureClass.AUTH:
        strikes = quota_gate.increment_auth_strike(engine=engine, observed_at=now)
        if strikes >= 2:
            quota_gate.report(
                engine=engine,
                status="paused",
                observed_at=now,
                reason="call classified as AUTH",
                kind="failure",
                set_by="call",
            )
            return True
        return False
    quota_gate.report(
        engine=engine,
        status="paused",
        observed_at=now,
        reason=f"call classified as {failure_class.value}",
        kind="failure",
        set_by="call",
    )
    return True


def call(
    prompt: str,
    *,
    route: Sequence[tuple[str, str | None]],
    timeout: int | None = None,
    stdin_text: str | None = None,
    cwd: Path | str | None = None,
    permission: Permission = TEXT_ONLY,
    resume_session_id: str | None = None,
    isolation: bool | None = None,
    permission_overrides: Mapping[str, Permission] | None = None,
    quota_gate: QuotaGate | None = None,
    additional_tags: Mapping[str, str] | None = None,
) -> LLMResult:
    """Call the LLM one-shot and return a completed ``LLMResult``.

    Walks ``route`` from front to back, skipping paused engines when a
    ``quota_gate`` is provided, and stopping on the first non-retriable failure.
    """
    if not route:
        raise ValueError("route must not be empty")

    tags = dict(additional_tags or {})
    attempts = 0
    quota_reported = False
    last_result: LLMResult | None = None
    skipped_paused: list[str] = []
    primary = route[0]

    for engine, model in route:
        if _engine_paused(quota_gate, engine):
            skipped_paused.append(engine)
            continue

        candidate_permission = _resolve_permission(engine, permission, permission_overrides)
        session = resume_session_id if (engine, model) == primary else None
        result = _run_completed_attempt(
            prompt,
            engine=engine,
            model=model,
            timeout=timeout,
            stdin_text=stdin_text,
            cwd=cwd,
            permission=candidate_permission,
            resume_session_id=session,
            isolation=isolation,
        )
        attempts += 1
        last_result = result

        if result.ok:
            if quota_gate is not None:
                quota_gate.reset_auth_strike(engine=engine)
            result.attempts = attempts
            result.quota_reported = quota_reported
            result.additional_tags = tags
            return result

        failure_class = result.failure_class
        if (
            failure_class is not None
            and failure_class in _QUOTA_REPORT_CLASSES
            and quota_gate is not None
        ):
            if _report_quota_failure(
                quota_gate=quota_gate,
                engine=engine,
                failure_class=failure_class,
            ):
                quota_reported = True

        if failure_class not in _NEXT_CANDIDATE_CLASSES:
            result.attempts = attempts
            result.quota_reported = quota_reported
            result.additional_tags = tags
            return result

    if last_result is None:
        paused_names = ", ".join(skipped_paused)
        return LLMResult(
            body="",
            stdout="",
            stderr=f"all route candidates paused: {paused_names}\n",
            returncode=1,
            failure_class=FailureClass.QUOTA_EXHAUSTED,
            attempts=0,
            engine_used="",
            model_used="",
            quota_reported=quota_reported,
            additional_tags=tags,
        )

    last_result.attempts = attempts
    last_result.quota_reported = quota_reported
    last_result.additional_tags = tags
    return last_result
