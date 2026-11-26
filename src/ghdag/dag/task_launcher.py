"""TaskLauncher — subprocess launch, I/O threads, and completion detection."""

from __future__ import annotations

import fnmatch
import io
import json
import logging
import os
import re
import shlex
import signal
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from ghdag.config.env import (
    ghdag_redact_patterns_path,
    ghdag_task_env_allow,
    ghdag_task_env_deny,
    session_compaction_enabled,
    set_pending_env_denied_count,
    stall_guard_enabled,
    stall_guard_interval_sec,
    stall_guard_stall_sec,
)
from ghdag.core.command import inject_resume
from ghdag.core.engine_spec import ENGINE_SPECS, warn_if_unverified
from ghdag.core.models.metrics import FailureClass, TaskMetrics
from ghdag.core.reply_block import ReplyBlockError, parse_reply_block
from ghdag.core.stall_guard import StallTracker
from ghdag.core.vocabulary import (
    DONE_ORPHANED_ON_RESTART,
    DONE_SKIPPED_MISSING_INPUT,
    DONE_UNKNOWN_FAILURE,
    PIPELINE_REPLY_BLOCK_FAILED,
    PIPELINE_WAIT_QUOTA,
)
from ghdag.dag._util import WaitQuotaParseError, parse_wait_quota_result_path
from ghdag.io.audit import (
    append_audit_record,
    file_ts,
    now_ts,
    write_compaction_audit,
    write_quarantine_audit,
    write_repair_turn_audit,
    write_task_retry_audit,
)
from ghdag.io.done import mark_done as state_mark_done
from ghdag.io.redact import init_redact_patterns, redact_text
from ghdag.llm.adapters import get_output_adapter
from ghdag.llm.adapters.failure_classification import last_nonempty_line
from ghdag.llm.compaction import (
    CompactionPolicy,
    compact_resume_session,
    lookup_parent_token_usage,
)
from ghdag.llm.engines import list_engines, read_ps
from ghdag.llm.session import SessionStore
from ghdag.metrics.parsers import parse_engine_model
from ghdag.quota import QuotaGate

from ._nodes import NodeSpec, load_nodes, pick_node
from ._process_host import _STDIN_REDIR_RE, LocalProcessHost, SshProcessHost
from ._util import _extract_tee_target, _stderr_reader, _stdout_reader
from .circuit_breaker import CircuitBreakerPolicy
from .completion import (
    CircuitAction,
    CompletionDecision,
    CompletionFacts,
    ContractErrorFact,
    EngineErrorFact,
    FanoutFact,
    FanoutState,
    HookKind,
    QuotaPauseFact,
    ResultActionKind,
    WaitQuotaFact,
    decide_completion,
)
from .engine_quarantine import EngineQuarantine
from .fanout import FanoutError, parse_fanout_spec
from .fanout_manager import FanOutManager
from .hooks import DagHooks
from .models import DagConfig, Task, _RunningProcess

logger = logging.getLogger(__name__)

init_redact_patterns(ghdag_redact_patterns_path())

_MAX_INTERRUPTED_RERUNS = 1
_STDERR_ARTIFACT_MAX_BYTES = 1_048_576

_BASE_TASK_ENV_KEYS = frozenset({"PATH", "HOME", "LANG", "TMPDIR"})

_RESUME_ERROR_RE = re.compile(
    r"(resume|session|chat_id|not found|expired|invalid)",
    re.IGNORECASE,
)

_REPAIR_RESUME_ENGINES = frozenset({"claude", "cursor", "codex"})


@dataclass
class _ExitContext:
    was_cancelled: bool
    was_timeout: bool
    finished_at: float
    returncode: int
    stderr_text: str
    stdout_data: bytes
    stderr_bytes: bytes
    task: Task
    engine: str | None
    model: str | None
    started_at: float


def _repair_turn_count(annotations: dict[str, str]) -> int:
    raw = annotations.get("repair_turns")
    if raw is None:
        return 0
    try:
        return int(raw)
    except ValueError:
        return 0


def _replace_stdin_order(command: str, order_path: str) -> str:
    quoted = shlex.quote(order_path)
    return _STDIN_REDIR_RE.sub(f"< {quoted}", command, count=1)


def _repair_order_body(*, code: str, message: str, hint: str | None) -> str:
    hint_line = f"hint: {hint}\n" if hint else ""
    return (
        "Your previous reply violated the ghdag result contract.\n\n"
        f"code: {code}\n"
        f"message: {message}\n"
        f"{hint_line}\n"
        "Reply again with only the corrected "
        + "```"
        + "ghdag"
        + " block; "
        "do not repeat the previous answer.\n"
    )


_STREAM_EVENT_ENGINES = frozenset({"claude", "cursor", "codex"})


def _command_supports_streaming(engine: str | None, command: str) -> bool:
    """Whether the command has the flags required for stream output."""
    if engine == "claude":
        # DAG default is always stream-json; check engine only for backward compatibility (#2966).
        return True
    tokens = command.split()
    if engine == "cursor":
        has_print = "-p" in tokens or "--print" in tokens
        return has_print and "stream-json" in command
    if engine == "codex":
        return "--json" in tokens
    return False


def _engine_emits_stream_events(engine: str | None) -> bool:
    """Whether the engine can append per-line events to jobs/events on the DAG path."""
    return engine in _STREAM_EVENT_ENGINES


def _should_use_line_reader(engine: str | None, command: str, annotations: dict) -> bool:
    """Whether to use the per-line drain. If unavailable, record stream_fallback and return False."""
    if not _engine_emits_stream_events(engine):
        return False
    if _command_supports_streaming(engine, command):
        return True
    annotations["stream_fallback"] = "true"
    return False


def _task_request_id(task: Task) -> str | None:
    return task.annotations.get("_request_id")


def _resolve_task_timeout(task: Task, default: float | None) -> float | None:
    raw = task.annotations.get("timeout_sec")
    if raw is not None:
        try:
            value = float(raw)
            if value > 0:
                return value
        except (TypeError, ValueError):
            pass
    return default


def _parse_env_pattern_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def _env_name_matches(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns)


def _task_env_passthrough(task: Task) -> frozenset[str]:
    raw = task.annotations.get("env_passthrough")
    if not raw:
        return frozenset()
    if isinstance(raw, list):
        return frozenset(str(item) for item in raw)
    raw = str(raw)
    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return frozenset()
        if isinstance(parsed, list):
            return frozenset(str(item) for item in parsed)
        return frozenset()
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def _apply_task_env_policy(
    env: dict[str, str],
    task: Task,
) -> tuple[dict[str, str], int]:
    passthrough = _task_env_passthrough(task)
    allow_patterns = _parse_env_pattern_list(ghdag_task_env_allow())
    deny_patterns = _parse_env_pattern_list(ghdag_task_env_deny())
    denied_count = 0

    if allow_patterns:
        filtered: dict[str, str] = {}
        for key, value in env.items():
            if key in _BASE_TASK_ENV_KEYS or key.startswith("GHDAG_"):
                filtered[key] = value
            elif _env_name_matches(key, allow_patterns):
                filtered[key] = value
        env = filtered
    elif deny_patterns:
        for key in list(env):
            if key in passthrough:
                continue
            if _env_name_matches(key, deny_patterns):
                del env[key]
                denied_count += 1
    return env, denied_count


def _build_task_env(
    uuid: str,
    task: Task,
    previous_attempt: str | None = None,
) -> tuple[dict[str, str], int]:
    env = os.environ.copy()
    env["GHDAG_TASK_UUID"] = uuid
    env["GHDAG_RESULT_PATH"] = task.result_path or ""
    if previous_attempt is not None:
        env["GHDAG_PREVIOUS_ATTEMPT"] = previous_attempt
    env, denied_count = _apply_task_env_policy(env, task)
    env["GHDAG_TASK_UUID"] = uuid
    env["GHDAG_RESULT_PATH"] = task.result_path or ""
    if previous_attempt is not None:
        env["GHDAG_PREVIOUS_ATTEMPT"] = previous_attempt
    return env, denied_count


def _task_env(uuid: str, task: Task, previous_attempt: str | None = None) -> dict[str, str]:
    """Environment for a launched task: the parent's plus the task identity.

    ``GHDAG_TASK_UUID`` identifies the attempt in task and audit records so downstream
    dispatchers such as a workflow consumer can correlate it; ``GHDAG_RESULT_PATH`` is the result
    file ghdag will write for the attempt.
    """
    env, denied_count = _build_task_env(uuid, task, previous_attempt)
    set_pending_env_denied_count(denied_count)
    return env


def _stdout_line_reader_redacting(
    proc: subprocess.Popen,
    buf: io.BytesIO,
    events_path: Path,
    on_event: Callable[[dict], None] | None = None,
) -> None:
    """Like ``_util._stdout_line_reader`` but redacts secrets before writing events."""
    redacted_total = 0
    try:
        stdout = proc.stdout
        if stdout is None:
            return
        events_path.parent.mkdir(parents=True, exist_ok=True)
        with open(events_path, "ab") as events_f:
            while True:
                line = stdout.readline()
                if not line:
                    break
                buf.write(line)
                try:
                    text = line.decode("utf-8", errors="replace")
                except UnicodeDecodeError:
                    events_f.write(line)
                    events_f.flush()
                    continue
                result = redact_text(text)
                redacted_total += result.count
                out_line = result.text
                if not out_line.endswith("\n") and text.endswith("\n"):
                    out_line += "\n"
                events_f.write(out_line.encode("utf-8"))
                events_f.flush()
                if on_event is None:
                    continue
                try:
                    stripped = out_line.strip()
                    if not stripped:
                        continue
                    obj = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    try:
                        on_event(obj)
                    except Exception:
                        pass
            if redacted_total > 0:
                summary = json.dumps(
                    {"redacted_count": redacted_total},
                    ensure_ascii=False,
                )
                events_f.write((summary + "\n").encode("utf-8"))
                events_f.flush()
    except (OSError, ValueError):
        pass
    finally:
        try:
            if proc.stdout is not None:
                proc.stdout.close()
        except (OSError, ValueError):
            pass


def _stderr_payload_bytes(text: str) -> bytes:
    """Redact and cap diagnostic text at 1 MiB, keeping the UTF-8 tail."""
    redacted = redact_text(text).text
    payload = redacted.encode("utf-8")
    if len(payload) <= _STDERR_ARTIFACT_MAX_BYTES:
        return payload
    tail = payload[-_STDERR_ARTIFACT_MAX_BYTES:]
    while tail and (tail[0] & 0xC0) == 0x80:
        tail = tail[1:]
    return tail


@dataclass
class AdoptedTask:
    uuid: str
    task: Task
    pgid: int
    started_at: float
    interrupted_reruns: int = 0
    node: str | None = None


class TaskLauncher:
    """Manage subprocess launch and completion detection for DAG tasks."""

    def __init__(
        self,
        config: DagConfig,
        hooks: DagHooks,
        circuit_breaker: CircuitBreakerPolicy,
        fanout_manager: FanOutManager,
        promote_fn: Callable[[str | None], None],
        quota_gate: QuotaGate | None = None,
    ) -> None:
        self._config = config
        self._hooks = hooks
        self._circuit_breaker = circuit_breaker
        self._fanout_manager = fanout_manager
        self._promote_fn = promote_fn
        self._running: dict[str, _RunningProcess] = {}
        self._adopted: dict[str, AdoptedTask] = {}
        self._cancelled: set[str] = set()
        self._interrupting: set[str] = set()
        self._pending_reruns: dict[str, int] = {}
        self._session_store = SessionStore(self._state_dir() / ".sessions")
        self._quota_gate = quota_gate or QuotaGate(
            Path(config.quota_state_path or self._state_dir() / "quota-gate.json"),
            audit_path=Path(config.quota_audit_path or self._state_dir() / "audit.jsonl"),
        )
        self._engine_quarantine = EngineQuarantine()
        self._last_quota_deferred: dict[str, tuple[str | None, str | None]] = {}
        self._waiting_engines: dict[str, tuple[str, ...]] = {}
        self._process_host = LocalProcessHost()
        self._nodes: list[NodeSpec] = []
        self._node_hosts: dict[str, SshProcessHost] = {}
        if config.nodes_path is not None:
            self._nodes = load_nodes(config.nodes_path)
            self._node_hosts = {n.name: SshProcessHost(n) for n in self._nodes}
        self._last_node_deferred: dict[str, str] = {}

    @property
    def quota_gate(self) -> QuotaGate:
        return self._quota_gate

    def _spawn(
        self,
        uuid: str,
        task: Task,
        command: str,
        engine: str | None,
        cwd: str | None,
        *,
        previous_attempt: str | None = None,
        host: LocalProcessHost | SshProcessHost | None = None,
        node_name: str | None = None,
    ) -> _RunningProcess:
        warn_if_unverified(ENGINE_SPECS.get(engine) if engine else None)
        process_host = host or self._process_host
        proc = process_host.start(
            command,
            cwd=cwd,
            env=_task_env(uuid, task, previous_attempt),
            capture_stdout=task.result_path is not None,
        )
        stdout_buf: io.BytesIO | None
        t_stdout: threading.Thread | None
        if task.result_path is not None:
            stdout_buf = io.BytesIO()
            if _should_use_line_reader(engine, command, task.annotations):
                events_path = self._events_path(uuid)

                def _on_event(event: dict, _uuid: str = uuid) -> None:
                    cb = getattr(self._hooks, "on_task_progress", None)
                    if cb is not None:
                        cb(_uuid, event)

                t_stdout = threading.Thread(
                    target=_stdout_line_reader_redacting,
                    args=(proc, stdout_buf, events_path, _on_event),
                    daemon=True,
                )
            else:
                t_stdout = threading.Thread(
                    target=_stdout_reader, args=(proc, stdout_buf), daemon=True
                )
            t_stdout.start()
        else:
            stdout_buf = None
            t_stdout = None

        stderr_buf = io.BytesIO()
        t_stderr = threading.Thread(target=_stderr_reader, args=(proc, stderr_buf), daemon=True)
        t_stderr.start()
        stall_tracker: StallTracker | None = None
        if (
            host is None
            and engine == "cursor"
            and stall_guard_enabled()
            and isinstance(proc.pid, int)
        ):
            stall_tracker = StallTracker(
                proc.pid,
                stall_sec=stall_guard_stall_sec(),
                interval_sec=stall_guard_interval_sec(),
                ps=read_ps,
            )
        rt = _RunningProcess(
            uuid=uuid,
            task=task,
            proc=proc,
            started_at=time.time(),
            started_at_mono=time.monotonic(),
            stderr_buf=stderr_buf,
            retry_depth=task.retry,
            stdout_buf=stdout_buf,
            stderr_thread=t_stderr,
            stdout_thread=t_stdout,
            stall_tracker=stall_tracker,
            host=host,
            node=node_name,
        )
        self._running[uuid] = rt
        return rt

    def _process_host_for(
        self, rt: _RunningProcess,
    ) -> LocalProcessHost | SshProcessHost:
        return rt.host or self._process_host

    def _node_busy_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for rt in self._running.values():
            if rt.node:
                counts[rt.node] = counts.get(rt.node, 0) + 1
        for at in self._adopted.values():
            if at.node:
                counts[at.node] = counts.get(at.node, 0) + 1
        return counts

    def _fail_missing_node_label(self, uuid: str, task: Task, label: str) -> None:
        message = f"no node has label '{label}'"
        if task.result_path is not None:
            try:
                Path(task.result_path).write_text(
                    f"ghdag: {message}\n",
                    encoding="utf-8",
                )
            except OSError:
                logger.warning(
                    "Failed to write missing-node result for [%s]", uuid, exc_info=True,
                )
        state_mark_done(self._config.exec_done_dir, uuid, DONE_UNKNOWN_FAILURE)
        metrics = TaskMetrics(
            uuid=uuid,
            engine=task.engine,
            model=task.model,
            wall_time_sec=0,
            token_count=None,
            status="failure",
            started_at=time.time(),
            finished_at=time.time(),
            correlation_id=task.idempotency_key,
            failure_class=FailureClass.UNKNOWN_FAILURE,
            request_id=_task_request_id(task),
            cost_usd=None,
            cache_read_tokens=None,
            cache_creation_tokens=None,
        )
        self._call_on_task_failure(uuid, task, -1, message, metrics)

    def launch(self, uuid: str, task: Task) -> bool:
        """Start a subprocess for the given task and register it in _running."""
        cwd = str(self._config.cwd) if self._config.cwd else None
        launch_engine = task.engine
        if launch_engine is None:
            launch_engine, _ = parse_engine_model(task.command)
        if isinstance(launch_engine, str):
            had_entry = self._engine_quarantine.has_entry(launch_engine)
            if self._engine_quarantine.is_quarantined(launch_engine):
                logger.warning("Task [%s] launch skipped: engine %s is quarantined", uuid, launch_engine)
                return False
            if had_entry:
                write_quarantine_audit(
                    self._queue_audit_path(),
                    engine=launch_engine,
                    action="expire",
                    cooldown_sec=None,
                )

        m = _STDIN_REDIR_RE.search(task.command)
        if m:
            input_file = m.group(1)
            base = Path(cwd) if cwd else Path()
            check_path = (
                Path(input_file)
                if Path(input_file).is_absolute()
                else base / input_file
            )
            if not check_path.exists():
                logger.warning(
                    "Task [%s] skipped — stdin input file missing: %s (command: %s)",
                    uuid, input_file, task.command,
                )
                state_mark_done(self._config.exec_done_dir, uuid, DONE_SKIPPED_MISSING_INPUT)
                return False

        waiting = self._waiting_engines.get(uuid)
        if waiting:
            allowed = False
            defer_reason: str | None = None
            for eng in waiting:
                decision = self._quota_gate.admit(
                    task_uuid=uuid,
                    engine=eng,
                    phase="launch",
                )
                if decision.allowed:
                    allowed = True
                    break
                defer_reason = decision.reason
            if not allowed:
                defer_key: tuple[str | None, str | None] = (",".join(waiting), defer_reason)
                if self._last_quota_deferred.get(uuid) != defer_key:
                    logger.info(
                        "Task [%s] launch deferred by quota gate: engine=%s reason=%s",
                        uuid,
                        ",".join(waiting),
                        defer_reason,
                    )
                    self._last_quota_deferred[uuid] = defer_key
                return False
            self._waiting_engines.pop(uuid, None)
            self._remove_quota_waiting_file(uuid)

        spawn_host: LocalProcessHost | SshProcessHost | None = None
        spawn_node_name: str | None = None
        node_label = task.annotations.get("node_label")
        if node_label:
            pick = pick_node(self._nodes, node_label, self._node_busy_counts())
            if pick == "wait":
                if self._last_node_deferred.get(uuid) != node_label:
                    logger.info(
                        "Task [%s] launch deferred: no free node for label %s",
                        uuid,
                        node_label,
                    )
                    self._last_node_deferred[uuid] = node_label
                return False
            if pick == "missing":
                self._last_node_deferred.pop(uuid, None)
                self._fail_missing_node_label(uuid, task, node_label)
                return False
            self._last_node_deferred.pop(uuid, None)
            spawn_host = self._node_hosts[pick.name]
            spawn_node_name = pick.name

        decision = self._quota_gate.begin_run(
            task_uuid=uuid,
            engine=launch_engine,
        )
        if not decision.allowed:
            defer_key = (launch_engine, decision.reason)
            if self._last_quota_deferred.get(uuid) != defer_key:
                logger.info(
                    "Task [%s] launch deferred by quota gate: engine=%s reason=%s",
                    uuid,
                    launch_engine,
                    decision.reason,
                )
                self._last_quota_deferred[uuid] = defer_key
            return False

        self._last_quota_deferred.pop(uuid, None)
        logger.info("Launching [%s]: %s", uuid, task.command)

        command_before_resume = task.command
        self._apply_resume_if_available(uuid, task)

        interrupted_reruns = self._pending_reruns.pop(uuid, None)
        previous_attempt = "interrupted" if interrupted_reruns is not None else None

        try:
            rt = self._spawn(
                uuid,
                task,
                task.command,
                launch_engine,
                cwd,
                previous_attempt=previous_attempt,
                host=spawn_host,
                node_name=spawn_node_name,
            )
        except Exception:
            self._quota_gate.finish_run(task_uuid=uuid)
            if task.command != command_before_resume:
                task.command = command_before_resume
                task.annotations.pop("_command_without_resume", None)
                task.annotations.pop("resumed_session_id", None)
            raise

        self._write_running_file(
            uuid,
            rt.proc,
            task,
            launch_engine,
            interrupted_reruns,
            node=spawn_node_name,
        )
        try:
            self._hooks.on_task_start(uuid, task)
        except Exception as exc:
            logger.exception(
                "on_task_start hook failed for task [%s]: %s",
                uuid,
                exc,
            )
            try:
                append_audit_record(
                    self._queue_audit_path(),
                    {
                        "schema_version": 3,
                        "event_type": "task_start_hook_failed",
                        "timestamp": now_ts(self._config.timezone),
                        "uuid": uuid,
                        "status": "running",
                        "component": "hooks.on_task_start",
                        "exception_type": type(exc).__name__,
                        "error_message": str(exc),
                        "correlation_id": task.idempotency_key,
                        "request_id": _task_request_id(task),
                    },
                )
            except Exception:
                logger.exception(
                    "Failed to write on_task_start hook failure audit for task [%s]",
                    uuid,
                )
        return True

    def request_cancel(self, uuid: str) -> None:
        """Apply a cancel request: SIGTERM now, SIGKILL after kill_grace via check_completions."""
        rt = self._running.get(uuid)
        if rt is None:
            return
        self._cancelled.add(uuid)
        if rt.term_sent_at is not None:
            return
        logger.warning("Task [%s] cancel requested, sending SIGTERM", uuid)
        self._process_host_for(rt).terminate_tree(rt.proc)
        rt.term_sent_at = time.monotonic()

    def _poll_running(self, uuid: str, rt: _RunningProcess, now: float) -> bool:
        """Return True when the process has exited and exit handling should run."""
        if rt.term_sent_at is None:
            if rt.proc.poll() is None:
                task_timeout = _resolve_task_timeout(rt.task, self._config.task_timeout)
                if (
                    uuid not in self._cancelled
                    and task_timeout is not None
                    and (now - rt.started_at_mono) > task_timeout
                ):
                    logger.warning(
                        "Task [%s] exceeded timeout %.1fs, sending SIGTERM",
                        uuid,
                        task_timeout,
                    )
                    self._process_host_for(rt).terminate_tree(rt.proc)
                    rt.term_sent_at = now
                else:
                    if rt.stall_tracker is not None:
                        for event in rt.stall_tracker.check(now):
                            logger.warning(
                                "[stall-guard] task=%s sent SIGTERM to pid=%d after %.0fs: %s",
                                uuid,
                                event.pid,
                                event.stalled_sec,
                                event.parent_args_head,
                            )
                            try:
                                append_audit_record(
                                    self._queue_audit_path(),
                                    {
                                        "schema_version": 1,
                                        "event_type": "stall_guard",
                                        "timestamp": now_ts(self._config.timezone),
                                        "uuid": uuid,
                                        "pid": event.pid,
                                        "stalled_sec": event.stalled_sec,
                                        "parent_args_head": event.parent_args_head,
                                    },
                                )
                            except OSError as exc:
                                logger.warning(
                                    "[stall-guard] audit write failed: %s",
                                    exc,
                                )
                    return False

        proc_host = self._process_host_for(rt)
        if rt.term_sent_at is not None:
            leader_exited = rt.proc.poll() is not None
            group_alive = (
                proc_host.group_exists(rt.proc.pid)
                if isinstance(rt.proc.pid, int)
                else (not leader_exited)
            )
            if group_alive:
                if (now - rt.term_sent_at) > self._config.kill_grace:
                    logger.warning(
                        "Task [%s] still alive after grace period, sending SIGKILL",
                        uuid,
                    )
                    proc_host.kill_tree(rt.proc)
                    if rt.proc.poll() is None:
                        return False
                else:
                    return False
            elif not leader_exited:
                return False
        elif rt.proc.poll() is None:
            return False
        return True

    def _collect_process_exit(
        self, uuid: str, rt: _RunningProcess, cwd: str | None
    ) -> _ExitContext | None:
        """Tear down a finished process; return None to continue the outer loop."""
        was_cancelled = uuid in self._cancelled
        was_interrupted = uuid in self._interrupting
        was_timeout = rt.term_sent_at is not None and not was_cancelled and not was_interrupted
        finished_at = time.time()
        del self._running[uuid]
        self._cancelled.discard(uuid)
        self._interrupting.discard(uuid)
        self._join_reader_threads(rt)
        stderr_bytes = rt.stderr_buf.getvalue()
        stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()
        returncode = rt.proc.returncode if rt.proc.returncode is not None else 1

        task = rt.task
        engine = task.engine
        model = task.model
        if engine is None:
            engine, model = parse_engine_model(task.command)

        stdout_data = rt.stdout_buf.getvalue() if rt.stdout_buf else b""

        resume_fallback_relaunched = False
        try:
            if (
                not was_cancelled
                and not was_timeout
                and not was_interrupted
                and returncode != 0
                and self._is_resume_fallback_target(task, stderr_text)
            ):
                original_command = task.annotations.get("_command_without_resume")
                if original_command:
                    logger.info(
                        "Retrying [%s] without --resume due to session-related failure",
                        uuid,
                    )
                    task.annotations["_resume_fallback_launched"] = "true"
                    task.command = original_command
                    fallback = self._spawn(uuid, task, original_command, engine, cwd)
                    self._write_running_file(uuid, fallback.proc, task, engine)
                    resume_fallback_relaunched = True
        finally:
            if not resume_fallback_relaunched:
                if was_interrupted:
                    self._cancel_path(uuid).unlink(missing_ok=True)
                else:
                    self._cleanup_control_files(uuid)
                try:
                    self._quota_gate.finish_run(task_uuid=uuid)
                except ValueError:
                    logger.exception("Failed to update running registry for [%s]", uuid)

        if resume_fallback_relaunched or was_interrupted:
            return None

        return _ExitContext(
            was_cancelled=was_cancelled,
            was_timeout=was_timeout,
            finished_at=finished_at,
            returncode=returncode,
            stderr_text=stderr_text,
            stdout_data=stdout_data,
            stderr_bytes=stderr_bytes,
            task=task,
            engine=engine,
            model=model,
            started_at=rt.started_at,
        )

    def _observe_quota_from_output(
        self,
        engine: str | None,
        adapter,
        stdout_data: bytes,
        stderr_bytes: bytes,
    ) -> None:
        if not isinstance(engine, str):
            return
        try:
            windows = adapter.extract_quota_windows(stdout_data, stderr_bytes)
            self._quota_gate.observe(
                engine=engine,
                windows=windows,
                observed_at=datetime.now(timezone.utc),
            )
        except (OSError, ValueError) as exc:
            logger.warning(
                "quota usage observe failed for %s: %s",
                engine,
                exc,
            )

    def _read_completion_facts(
        self,
        uuid: str,
        exit_ctx: _ExitContext,
    ) -> tuple[CompletionFacts, str | None]:
        adapter = get_output_adapter(exit_ctx.engine)
        self._observe_quota_from_output(
            exit_ctx.engine, adapter, exit_ctx.stdout_data, exit_ctx.stderr_bytes
        )

        engine_error_fact: EngineErrorFact | None = None
        err = adapter.extract_error(exit_ctx.stdout_data, exit_ctx.stderr_bytes)
        if err is not None:
            engine_error_fact = EngineErrorFact(
                kind=err.kind.value,
                message=err.message,
                retryable=err.retryable,
                resume_at=err.resume_at,
            )

        effective_result_path: str | None = None
        rejected = False
        pipeline_status: str | None = None
        wait_quota: WaitQuotaFact | None = None
        reply_block_error: ContractErrorFact | None = None
        result_empty = False
        fanout: FanoutFact | None = None
        failure_class: FailureClass | None = None
        failure_output = ""
        failure_class_reason: str | None = None

        decided_early = (
            exit_ctx.was_cancelled
            or exit_ctx.was_timeout
            or engine_error_fact is not None
        )
        if not decided_early and exit_ctx.returncode == 0:
            session_id = adapter.extract_session_id(
                exit_ctx.stdout_data, exit_ctx.stderr_bytes
            )
            if session_id and isinstance(exit_ctx.engine, str):
                self._session_store.record(uuid, exit_ctx.engine, session_id)
            transformed = adapter.extract_result_text(
                exit_ctx.stdout_data, exit_ctx.stderr_bytes
            )
            task = exit_ctx.task
            if task.result_path is not None:
                rp = Path(task.result_path)
                policy = task.result_finalize or "preserve_nonempty"
                if policy == "stdout_only" or not (rp.exists() and rp.stat().st_size > 0):
                    rp.write_bytes(transformed)
                effective_result_path = task.result_path
            else:
                effective_result_path = _extract_tee_target(task.command)

            if effective_result_path and self._hooks.check_rejected(effective_result_path):
                rejected = True
            elif effective_result_path:
                pipeline_status = self._hooks.check_pipeline_status(effective_result_path)
                if pipeline_status == PIPELINE_WAIT_QUOTA:
                    known = frozenset(list_engines())
                    parsed = parse_wait_quota_result_path(
                        effective_result_path,
                        known_engines=known,
                    )
                    if isinstance(parsed, WaitQuotaParseError):
                        wait_quota = WaitQuotaFact(invalid_reason=parsed.reason)
                    else:
                        wait_quota = WaitQuotaFact(
                            engines=tuple(parsed.engines),
                            after=parsed.after,
                        )
                elif pipeline_status == PIPELINE_REPLY_BLOCK_FAILED:
                    block_err = self._reply_block_error_from_result(effective_result_path)
                    if block_err is not None:
                        reply_block_error = ContractErrorFact(
                            block_err.code,
                            block_err.message,
                            block_err.hint,
                        )
                elif pipeline_status and pipeline_status.endswith("_FAILED"):
                    pass
                elif (
                    os.path.exists(effective_result_path)
                    and os.path.getsize(effective_result_path) == 0
                ):
                    result_empty = True
                else:
                    try:
                        fanout_spec = parse_fanout_spec(effective_result_path)
                    except FanoutError as exc:
                        fanout = FanoutFact(
                            state=FanoutState.PARSE_FAILED,
                            error=ContractErrorFact(exc.code, exc.message, exc.hint),
                        )
                    else:
                        if fanout_spec:
                            fanout = FanoutFact(state=FanoutState.PRESENT)
                        else:
                            fanout = FanoutFact(state=FanoutState.NONE)
        elif not decided_early:
            failure_class = adapter.classify_failure(
                exit_ctx.returncode,
                exit_ctx.stdout_data,
                exit_ctx.stderr_bytes,
            )
            if failure_class == FailureClass.INTERACTIVE_PROMPT:
                full_question = adapter.extract_result_text(
                    exit_ctx.stdout_data, exit_ctx.stderr_bytes
                ).decode("utf-8", errors="replace")
                failure_output = full_question
                failure_class_reason = last_nonempty_line(full_question) or None

        facts = CompletionFacts(
            returncode=exit_ctx.returncode,
            cancelled=exit_ctx.was_cancelled,
            timed_out=exit_ctx.was_timeout,
            retry=exit_ctx.task.retry,
            engine=exit_ctx.engine if isinstance(exit_ctx.engine, str) else None,
            stderr_text=exit_ctx.stderr_text,
            engine_error=engine_error_fact,
            failure_class=failure_class,
            failure_output=failure_output,
            failure_class_reason=failure_class_reason,
            rejected=rejected,
            pipeline_status=pipeline_status,
            wait_quota=wait_quota,
            reply_block_error=reply_block_error,
            result_empty=result_empty,
            fanout=fanout,
        )
        return facts, effective_result_path

    def _usage_from_output(self, exit_ctx: _ExitContext):
        adapter = get_output_adapter(exit_ctx.engine)
        usage = adapter.extract_token_usage(exit_ctx.stdout_data, exit_ctx.stderr_bytes)
        if usage is None:
            return None, None, None, None
        return (
            usage.token_count,
            usage.cost_usd,
            usage.cache_read_tokens,
            usage.cache_creation_tokens,
        )

    def _build_completion_metrics(
        self,
        uuid: str,
        exit_ctx: _ExitContext,
        decision: CompletionDecision,
        *,
        token_count,
        cost_usd,
        cache_read_tokens,
        cache_creation_tokens,
    ) -> TaskMetrics | None:
        if decision.metrics_status is None:
            return None
        return TaskMetrics(
            uuid=uuid,
            engine=exit_ctx.engine,
            model=exit_ctx.model,
            wall_time_sec=round(exit_ctx.finished_at - exit_ctx.started_at, 3),
            token_count=token_count,
            status=decision.metrics_status,
            started_at=exit_ctx.started_at,
            finished_at=exit_ctx.finished_at,
            correlation_id=exit_ctx.task.idempotency_key,
            failure_class=decision.failure_class,
            failure_class_reason=decision.failure_class_reason,
            request_id=_task_request_id(exit_ctx.task),
            cost_usd=cost_usd,
            cache_read_tokens=cache_read_tokens,
            cache_creation_tokens=cache_creation_tokens,
        )

    def _report_quota_pause(
        self,
        pause: QuotaPauseFact,
        engine: str | None,
        uuid: str,
    ) -> None:
        engines = pause.engines
        if not engines and isinstance(engine, str):
            engines = (engine,)
        observed = datetime.now(timezone.utc)
        for eng in engines:
            if not eng:
                continue
            if pause.set_by is not None:
                self._quota_gate.report(
                    engine=eng,
                    status="paused",
                    observed_at=observed,
                    resume_at=pause.resume_at,
                    reason=pause.reason,
                    set_by=pause.set_by,
                )
            else:
                self._quota_gate.report(
                    engine=eng,
                    status="paused",
                    observed_at=observed,
                    resume_at=pause.resume_at,
                    reason=pause.reason,
                )

    def _unlink_task_result(self, task: Task) -> None:
        if task.result_path and Path(task.result_path).exists():
            Path(task.result_path).unlink()

    def _apply_result_action(
        self,
        task: Task,
        effective_result_path: str | None,
        decision: CompletionDecision,
    ) -> None:
        if decision.result_action == ResultActionKind.WRITE_ENGINE_ERROR:
            if task.result_path is not None and decision.engine_error_summary:
                Path(task.result_path).write_text(
                    decision.engine_error_summary, encoding="utf-8"
                )

    def _resolve_completion_repair(
        self,
        uuid: str,
        exit_ctx: _ExitContext,
        decision: CompletionDecision,
        *,
        effective_result_path: str | None,
        cwd: str | None,
    ) -> tuple[CompletionDecision, bool]:
        if decision.repair is None:
            return decision, False
        rep = decision.repair
        if self._try_contract_repair_turn(
            uuid,
            exit_ctx.task,
            exit_ctx.engine if isinstance(exit_ctx.engine, str) else None,
            code=rep.code,
            message=rep.message,
            hint=rep.hint,
            result_path=effective_result_path,
            cwd=cwd,
        ):
            return decision, True
        assert decision.fallback is not None
        resolved = decision.fallback
        if resolved.failure_class == FailureClass.FANOUT_PARSE_FAILED:
            logger.warning("FanOut error for [%s]: %s", uuid, rep.message)
        return resolved, False

    def _apply_completion_state(
        self,
        uuid: str,
        exit_ctx: _ExitContext,
        decision: CompletionDecision,
        effective_result_path: str | None,
    ) -> None:
        self._apply_result_action(exit_ctx.task, effective_result_path, decision)

        if decision.quota_pause is not None:
            self._report_quota_pause(decision.quota_pause, exit_ctx.engine, uuid)

        wait_quota_active = (
            decision.wait_quota is not None
            and decision.wait_quota.invalid_reason is None
        )
        if decision.result_action == ResultActionKind.UNLINK and decision.done is None:
            if not wait_quota_active:
                self._unlink_task_result(exit_ctx.task)

        if (
            decision.wait_quota is not None
            and decision.wait_quota.invalid_reason is None
            and decision.wait_quota.engines
        ):
            observed = datetime.now(timezone.utc)
            for eng_name in decision.wait_quota.engines:
                self._quota_gate.report(
                    engine=eng_name,
                    status="paused",
                    observed_at=observed,
                    resume_at=decision.wait_quota.after,
                    reason="pipeline WAIT_QUOTA",
                    set_by=f"task:{uuid}",
                )
            append_audit_record(
                self._queue_audit_path(),
                {
                    "schema_version": 1,
                    "event_type": "task_waiting",
                    "uuid": uuid,
                    "engines": list(decision.wait_quota.engines),
                    "after": (
                        decision.wait_quota.after.isoformat()
                        if decision.wait_quota.after is not None
                        else None
                    ),
                    "reason": "pipeline WAIT_QUOTA",
                    "observed_at": observed.isoformat(),
                    "correlation_id": uuid,
                },
            )
            self._waiting_engines[uuid] = tuple(decision.wait_quota.engines)
            self._write_quota_waiting_file(uuid, self._waiting_engines[uuid])
            if effective_result_path:
                try:
                    Path(effective_result_path).unlink()
                except OSError:
                    logger.warning(
                        "Failed to remove WAIT_QUOTA result for [%s]",
                        uuid,
                        exc_info=True,
                    )

        if decision.quarantine and isinstance(exit_ctx.engine, str):
            self._engine_quarantine.enter(exit_ctx.engine, cooldown=300)
            write_quarantine_audit(
                self._queue_audit_path(),
                engine=exit_ctx.engine,
                action="enter",
                cooldown_sec=300,
            )

        if decision.done is not None:
            state_mark_done(self._config.exec_done_dir, uuid, decision.done)
            if decision.result_action == ResultActionKind.UNLINK:
                self._unlink_task_result(exit_ctx.task)

        if decision.retry_audit:
            write_task_retry_audit(
                self._queue_audit_path(),
                uuid=uuid,
                attempt=exit_ctx.task.retry + 1,
                failure_class=FailureClass.ENGINE_ERROR,
                stderr_excerpt=(
                    exit_ctx.stderr_text[:200] if exit_ctx.stderr_text else None
                ),
            )

    def _dispatch_completion_hook(
        self,
        uuid: str,
        exit_ctx: _ExitContext,
        decision: CompletionDecision,
        metrics: TaskMetrics | None,
    ) -> None:
        hook = decision.hook
        if hook is None:
            return
        if hook.kind == HookKind.CANCELLED:
            on_cancel = getattr(self._hooks, "on_task_cancelled", None)
            if on_cancel is not None:
                on_cancel(uuid, exit_ctx.task)
        elif hook.kind == HookKind.FAILURE and metrics is not None:
            self._call_on_task_failure(
                uuid,
                exit_ctx.task,
                hook.returncode if hook.returncode is not None else exit_ctx.returncode,
                hook.message or "",
                metrics,
            )
        elif hook.kind == HookKind.REJECTED and metrics is not None:
            self._hooks.on_task_rejected(
                uuid,
                exit_ctx.task,
                exit_ctx.task.retry,
                bool(hook.is_final),
                metrics,
            )
        elif hook.kind == HookKind.EMPTY_RESULT and metrics is not None:
            self._call_on_task_empty_result(
                uuid, exit_ctx.task, exit_ctx.stderr_text, metrics
            )
        elif hook.kind == HookKind.SUCCESS and metrics is not None:
            self._run_success_postprocess(
                uuid,
                exit_ctx.task,
                "on_task_success",
                lambda: self._hooks.on_task_success(uuid, exit_ctx.task, metrics),
            )

    def _apply_completion_circuit(
        self,
        uuid: str,
        exit_ctx: _ExitContext,
        decision: CompletionDecision,
    ) -> None:
        if decision.circuit == CircuitAction.RECORD_FAILURE:
            if self._circuit_breaker.record_failure():
                append_audit_record(
                    self._queue_audit_path(),
                    {
                        "schema_version": 1,
                        "event_type": "circuit_breaker_tripped",
                        "trigger_uuid": uuid,
                        "consecutive_failures": self._circuit_breaker.consecutive_failures,
                        "observed_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
        elif decision.circuit == CircuitAction.RESET:
            self._run_success_postprocess(
                uuid,
                exit_ctx.task,
                "circuit_breaker.reset",
                self._circuit_breaker.reset,
            )

    def _spawn_completion_fanout(
        self,
        uuid: str,
        exit_ctx: _ExitContext,
        facts: CompletionFacts,
        decision: CompletionDecision,
        metrics: TaskMetrics | None,
        *,
        effective_result_path: str | None,
        cwd: str | None,
        token_count,
        cost_usd,
        cache_read_tokens,
        cache_creation_tokens,
    ) -> bool:
        if not decision.fanout_spawn or metrics is None:
            return False
        fanout_spec = parse_fanout_spec(effective_result_path)
        assert fanout_spec is not None
        try:
            self._fanout_manager.spawn(
                uuid,
                exit_ctx.task,
                fanout_spec,
                metrics,
                queue_dir=self._state_dir(),
            )
        except FanoutError as exc:
            spawn_facts = replace(
                facts,
                fanout=FanoutFact(
                    state=FanoutState.SPAWN_FAILED,
                    error=ContractErrorFact(exc.code, exc.message, exc.hint),
                ),
            )
            spawn_decision = decide_completion(
                spawn_facts,
                max_retry=self._config.max_retry,
                task_timeout=self._config.task_timeout,
            )
            return self._apply_completion_decision(
                uuid,
                exit_ctx,
                spawn_facts,
                spawn_decision,
                effective_result_path=effective_result_path,
                cwd=cwd,
                token_count=token_count,
                cost_usd=cost_usd,
                cache_read_tokens=cache_read_tokens,
                cache_creation_tokens=cache_creation_tokens,
            )
        return False

    def _apply_completion_decision(
        self,
        uuid: str,
        exit_ctx: _ExitContext,
        facts: CompletionFacts,
        decision: CompletionDecision,
        *,
        effective_result_path: str | None,
        cwd: str | None,
        token_count,
        cost_usd,
        cache_read_tokens,
        cache_creation_tokens,
    ) -> bool:
        """Apply one decision. Return True when a repair turn relaunched the task."""
        decision, repair_relaunched = self._resolve_completion_repair(
            uuid, exit_ctx, decision,
            effective_result_path=effective_result_path, cwd=cwd,
        )
        if repair_relaunched:
            return True
        self._apply_completion_state(uuid, exit_ctx, decision, effective_result_path)
        metrics = self._build_completion_metrics(
            uuid,
            exit_ctx,
            decision,
            token_count=token_count,
            cost_usd=cost_usd,
            cache_read_tokens=cache_read_tokens,
            cache_creation_tokens=cache_creation_tokens,
        )
        self._dispatch_completion_hook(uuid, exit_ctx, decision, metrics)
        self._apply_completion_circuit(uuid, exit_ctx, decision)
        if self._spawn_completion_fanout(
            uuid,
            exit_ctx,
            facts,
            decision,
            metrics,
            effective_result_path=effective_result_path,
            cwd=cwd,
            token_count=token_count,
            cost_usd=cost_usd,
            cache_read_tokens=cache_read_tokens,
            cache_creation_tokens=cache_creation_tokens,
        ):
            return True

        if decision.promote:
            self._run_success_postprocess(
                uuid,
                exit_ctx.task,
                "promote",
                lambda: self._promote_fn(effective_result_path),
            )
        return False

    def _handle_completed_process(
        self,
        uuid: str,
        exit_ctx: _ExitContext,
        cwd: str | None,
    ) -> bool:
        """Read facts, decide, and apply. Return True to continue the outer loop."""
        facts, effective_result_path = self._read_completion_facts(uuid, exit_ctx)
        # Shell failure output is written only on the plain non-zero exit path.
        adapter = get_output_adapter(exit_ctx.engine)
        if (
            exit_ctx.returncode != 0
            and exit_ctx.engine == "shell"
            and not facts.cancelled
            and not facts.timed_out
            and facts.engine_error is None
        ):
            task = exit_ctx.task
            if task.result_path is not None:
                failure_output = adapter.extract_result_text(
                    exit_ctx.stdout_data, exit_ctx.stderr_bytes
                ).decode("utf-8", errors="replace")
                if failure_output and not failure_output.endswith("\n"):
                    failure_output += "\n"
                failure_output += f"EXIT_CODE: {exit_ctx.returncode}\n"
                try:
                    Path(task.result_path).write_text(failure_output, encoding="utf-8")
                except OSError:
                    logger.warning(
                        "Failed to write failure result for shell task [%s]",
                        uuid,
                        exc_info=True,
                    )

        decision = decide_completion(
            facts,
            max_retry=self._config.max_retry,
            task_timeout=self._config.task_timeout,
        )
        (
            token_count,
            cost_usd,
            cache_read_tokens,
            cache_creation_tokens,
        ) = self._usage_from_output(exit_ctx)

        repair_relaunched = self._apply_completion_decision(
            uuid,
            exit_ctx,
            facts,
            decision,
            effective_result_path=effective_result_path,
            cwd=cwd,
            token_count=token_count,
            cost_usd=cost_usd,
            cache_read_tokens=cache_read_tokens,
            cache_creation_tokens=cache_creation_tokens,
        )
        return repair_relaunched

    def check_completions(self) -> None:
        """Inspect running processes and process any that have finished."""
        for uuid in list(self._adopted):
            at = self._adopted[uuid]
            if not self._process_host.group_exists(at.pgid):
                del self._adopted[uuid]
                if at.task.annotations.get("_exclusive") == "true":
                    self._finish_orphan(uuid, at.task, at.started_at)
                else:
                    self._rerun_or_orphan(
                        uuid,
                        at.task,
                        at.started_at,
                        at.interrupted_reruns,
                        self._running_path(uuid),
                    )

        cwd = str(self._config.cwd) if self._config.cwd else None
        for uuid in list(self._running):
            rt = self._running[uuid]
            now = time.monotonic()
            if not self._poll_running(uuid, rt, now):
                continue

            exit_ctx = self._collect_process_exit(uuid, rt, cwd)
            if exit_ctx is None:
                continue

            task = exit_ctx.task
            engine = exit_ctx.engine
            model = exit_ctx.model
            try:
                if self._handle_completed_process(uuid, exit_ctx, cwd):
                    continue
            except Exception as exc:
                logger.exception(
                    "Unexpected error handling completion for task [%s]: %s", uuid, exc
                )
                state_mark_done(self._config.exec_done_dir, uuid, DONE_UNKNOWN_FAILURE)
                token_count, cost_usd, cr, cc = self._usage_from_output(exit_ctx)
                metrics = TaskMetrics(
                    uuid=uuid,
                    engine=engine,
                    model=model,
                    wall_time_sec=round(exit_ctx.finished_at - exit_ctx.started_at, 3),
                    token_count=token_count,
                    status="failure",
                    started_at=exit_ctx.started_at,
                    finished_at=exit_ctx.finished_at,
                    correlation_id=task.idempotency_key,
                    failure_class=FailureClass.UNKNOWN_FAILURE,
                    request_id=_task_request_id(task),
                    cost_usd=cost_usd,
                    cache_read_tokens=cr,
                    cache_creation_tokens=cc,
                )
                self._call_on_task_failure(uuid, task, -1, str(exc), metrics)
    def _run_success_postprocess(
        self,
        uuid: str,
        task: Task,
        component: str,
        callback: Callable[[], None],
    ) -> None:
        """Run one success postprocess step without changing the success done marker."""
        try:
            callback()
        except Exception as exc:
            logger.exception(
                "Success postprocess %s failed for task [%s]: %s",
                component,
                uuid,
                exc,
            )
            try:
                append_audit_record(
                    self._queue_audit_path(),
                    {
                        "schema_version": 3,
                        "event_type": "task_success_postprocess_failed",
                        "timestamp": now_ts(self._config.timezone),
                        "uuid": uuid,
                        "status": "success",
                        "component": component,
                        "exception_type": type(exc).__name__,
                        "error_message": str(exc),
                        "correlation_id": task.idempotency_key,
                        "request_id": _task_request_id(task),
                    },
                )
            except Exception:
                logger.exception(
                    "Failed to write success postprocess audit for task [%s] "
                    "component %s",
                    uuid,
                    component,
                )

    def _state_dir(self) -> Path:
        return Path(self._config.exec_done_dir).parent

    def _persist_failure_stderr(
        self,
        uuid: str,
        retry_depth: int,
        diagnostic: str,
    ) -> str | None:
        if not diagnostic:
            return None
        try:
            payload = _stderr_payload_bytes(diagnostic)
            if not payload:
                return None
            queue_dir = self._state_dir()
            dest_dir = queue_dir / "stderr" / uuid
            dest_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
            stem = f"{ts}-r{retry_depth}"
            dest_path = dest_dir / f"{stem}.log"
            suffix = 0
            while dest_path.exists():
                suffix += 1
                dest_path = dest_dir / f"{stem}-{suffix}.log"
            fd, tmp = tempfile.mkstemp(dir=dest_dir, prefix=".tmp-", suffix=".log")
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(payload)
                os.replace(tmp, dest_path)
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise
            return dest_path.relative_to(queue_dir).as_posix()
        except OSError as exc:
            logger.warning("Failed to persist stderr artifact for [%s]: %s", uuid, exc)
            return None

    def _enrich_metrics_stderr_path(
        self,
        uuid: str,
        task: Task,
        diagnostic: str,
        metrics: TaskMetrics,
    ) -> TaskMetrics:
        rel = self._persist_failure_stderr(uuid, task.retry, diagnostic)
        if rel is None:
            return metrics
        return replace(metrics, stderr_path=rel)

    def _call_on_task_failure(
        self,
        uuid: str,
        task: Task,
        returncode: int,
        diagnostic: str,
        metrics: TaskMetrics,
    ) -> None:
        metrics = self._enrich_metrics_stderr_path(uuid, task, diagnostic, metrics)
        self._hooks.on_task_failure(uuid, task, returncode, diagnostic, metrics)

    def _call_on_task_empty_result(
        self,
        uuid: str,
        task: Task,
        stderr_text: str,
        metrics: TaskMetrics,
    ) -> None:
        metrics = self._enrich_metrics_stderr_path(uuid, task, stderr_text, metrics)
        self._hooks.on_task_empty_result(uuid, task, stderr_text, metrics)

    def _running_path(self, uuid: str) -> Path:
        return self._state_dir() / "running" / f"{uuid}.json"

    def _quota_waiting_path(self, uuid: str) -> Path:
        return self._state_dir() / "quota-waiting" / f"{uuid}.json"

    def _write_quota_waiting_file(self, uuid: str, engines: tuple[str, ...]) -> None:
        path = self._quota_waiting_path(uuid)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"engines": list(engines)}
        fd, tmp = tempfile.mkstemp(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def _remove_quota_waiting_file(self, uuid: str) -> None:
        path = self._quota_waiting_path(uuid)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning(
                "Failed to remove quota-waiting file for [%s]", uuid, exc_info=True
            )

    def restore_waiting(
        self, tasks: dict[str, Task], known_done: set[str]
    ) -> list[str]:
        """Reload WAIT_QUOTA engine lists from disk after restart."""
        waiting_dir = self._state_dir() / "quota-waiting"
        if not waiting_dir.is_dir():
            return []
        restored: list[str] = []
        for path in sorted(waiting_dir.glob("*.json")):
            uuid = path.stem
            if uuid not in tasks or uuid in known_done:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    logger.warning(
                        "Failed to remove stale quota-waiting file %s",
                        path,
                        exc_info=True,
                    )
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                logger.warning("Corrupt quota-waiting file %s — ignoring", path)
                continue
            if not isinstance(payload, dict):
                logger.warning("Corrupt quota-waiting file %s — ignoring", path)
                continue
            engines = payload.get("engines")
            if not isinstance(engines, list) or not all(
                isinstance(e, str) for e in engines
            ):
                logger.warning("Corrupt quota-waiting file %s — ignoring", path)
                continue
            self._waiting_engines[uuid] = tuple(engines)
            restored.append(uuid)
        return restored

    def _cancel_path(self, uuid: str) -> Path:
        return self._state_dir() / "cancel" / uuid

    def _events_path(self, uuid: str) -> Path:
        return self._state_dir() / "events" / f"{uuid}.jsonl"

    def mark_interrupted_all(self) -> list[str]:
        """Add interrupted_at to running files for all currently running tasks.

        Called from signal handler; no exceptions are raised.
        Returns list of uuids that were successfully recorded.
        """
        recorded: list[str] = []
        now_iso = datetime.now(timezone.utc).isoformat()
        for uuid in list(self._running):
            path = self._running_path(uuid)
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                data["interrupted_at"] = now_iso
                fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        json.dump(data, f)
                    os.replace(tmp, path)
                except BaseException:
                    Path(tmp).unlink(missing_ok=True)
                    raise
                recorded.append(uuid)
            except Exception:
                logger.warning(
                    "mark_interrupted_all: failed to update running file for [%s]", uuid,
                    exc_info=True,
                )
        return recorded

    def terminate_all(self) -> None:
        """Send SIGTERM to all running task process groups (drain deadline exceeded)."""
        for uuid, rt in list(self._running.items()):
            self._interrupting.add(uuid)
            if rt.term_sent_at is None:
                self._process_host_for(rt).terminate_tree(rt.proc)
                rt.term_sent_at = time.monotonic()

    def _write_running_file(
        self,
        uuid: str,
        proc: subprocess.Popen,
        task: Task,
        engine: str | None,
        interrupted_reruns: int | None = None,
        *,
        node: str | None = None,
    ) -> None:
        raw_pid = getattr(proc, "pid", None)
        if not isinstance(raw_pid, int):
            return
        path = self._running_path(uuid)
        path.parent.mkdir(parents=True, exist_ok=True)
        pgid = raw_pid
        actual_pgid = self._process_host.group_id(raw_pid)
        if actual_pgid is not None and actual_pgid != raw_pid:
            logger.warning(
                "Task [%s] process group mismatch: pid=%s getpgid=%s",
                uuid,
                raw_pid,
                actual_pgid,
            )
        payload: dict = {
            "pid": raw_pid,
            "pgid": pgid,
            "engine": engine if isinstance(engine, str) else "",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "has_resume": "resumed_session_id" in task.annotations,
        }
        if interrupted_reruns is not None:
            payload["interrupted_reruns"] = interrupted_reruns
        if node is not None:
            payload["node"] = node
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def _cleanup_control_files(self, uuid: str) -> None:
        for path in (self._running_path(uuid), self._cancel_path(uuid)):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.warning("Failed to remove control file %s", path, exc_info=True)

    def _queue_audit_path(self) -> Path:
        return Path(self._config.audit_path or self._state_dir() / "audit.jsonl")

    def _compaction_policy(self) -> CompactionPolicy:
        """Opt-in: enable via GHDAG_SESSION_COMPACTION=1 (default off)."""
        return CompactionPolicy(enabled=session_compaction_enabled())

    def _engine_supports_repair_resume(self, engine: str | None) -> bool:
        return isinstance(engine, str) and engine in _REPAIR_RESUME_ENGINES

    def _reply_block_error_from_result(self, result_path: str | None) -> ReplyBlockError | None:
        if not result_path:
            return None
        try:
            content = Path(result_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        try:
            parse_reply_block(content)
        except ReplyBlockError as exc:
            return exc
        return None

    def _try_contract_repair_turn(
        self,
        uuid: str,
        task: Task,
        engine: str | None,
        *,
        code: str,
        message: str,
        hint: str | None,
        result_path: str | None,
        cwd: str | None,
    ) -> bool:
        if self._config.max_repair_turns <= 0:
            return False
        if _repair_turn_count(task.annotations) >= self._config.max_repair_turns:
            return False
        if not self._engine_supports_repair_resume(engine):
            return False
        resolved = self._session_store.lookup(uuid)
        if resolved is None:
            return False

        engine_name = engine or resolved.engine
        decision = self._quota_gate.begin_run(task_uuid=uuid, engine=engine_name)
        if not decision.allowed:
            logger.info(
                "Repair turn skipped by quota gate: uuid=%s engine=%s reason=%s",
                uuid,
                engine_name,
                decision.reason,
            )
            return False

        turn = _repair_turn_count(task.annotations) + 1
        base_command = task.annotations.get("_command_without_resume") or task.command
        ts = file_ts(self._config.timezone)
        queue_dir = self._state_dir()
        order_name = f"{ts}-{engine_name}-order-{uuid}-repair{turn}.md"
        order_path = queue_dir / order_name
        order_path.write_text(
            _repair_order_body(code=code, message=message, hint=hint),
            encoding="utf-8",
        )
        relaunch_command = _replace_stdin_order(base_command, str(order_path))
        relaunch_command = self._inject_resume_command(
            relaunch_command, engine_name, resolved.session_id
        )
        if "_command_without_resume" not in task.annotations:
            task.annotations["_command_without_resume"] = task.command

        if result_path:
            rp = Path(result_path)
            if rp.exists():
                archive = rp.with_name(f"{rp.name}.repair{turn}.md")
                try:
                    rp.replace(archive)
                except OSError:
                    logger.warning(
                        "Failed to archive result for repair turn [%s]", uuid, exc_info=True
                    )

        task.annotations["repair_turns"] = str(turn)
        task.command = relaunch_command
        write_repair_turn_audit(
            self._queue_audit_path(),
            uuid=uuid,
            engine=engine_name,
            turn=turn,
            code=code,
            message=message,
            hint=hint,
            correlation_id=task.idempotency_key,
            tz_name=self._config.timezone,
        )
        if not self._relaunch_running_task(uuid, task, engine, relaunch_command, cwd):
            try:
                self._quota_gate.finish_run(task_uuid=uuid)
            except ValueError:
                logger.exception("Failed to update running registry for [%s]", uuid)
            return False
        return True

    def _relaunch_running_task(
        self,
        uuid: str,
        task: Task,
        engine: str | None,
        command: str,
        cwd: str | None,
    ) -> bool:
        try:
            rt = self._spawn(uuid, task, command, engine, cwd)
            self._write_running_file(uuid, rt.proc, task, engine)
            return True
        except OSError:
            logger.warning("Failed to relaunch task [%s] for repair turn", uuid, exc_info=True)
            return False

    def _apply_resume_if_available(self, uuid: str, task: Task) -> None:
        resume_from_uuid = task.annotations.get("resume_from_uuid")
        if not resume_from_uuid:
            return
        resolved = self._session_store.lookup(resume_from_uuid)
        if not resolved:
            write_compaction_audit(
                self._queue_audit_path(),
                task_uuid=uuid,
                status="fallback",
                reason="session_miss",
                parent_session_id=None,
                compacted_session_id=None,
                engine=task.engine,
            )
            return
        parent_engine = resolved.engine
        session_id = resolved.session_id
        engine = task.engine or parent_engine
        if task.engine and parent_engine != task.engine:
            logger.warning(
                "Task [%s] resume skipped due to engine mismatch: parent=%s task=%s",
                uuid,
                parent_engine,
                task.engine,
            )
            write_compaction_audit(
                self._queue_audit_path(),
                task_uuid=uuid,
                status="fallback",
                reason="engine_mismatch",
                parent_session_id=session_id,
                compacted_session_id=None,
                engine=task.engine,
            )
            return

        token_usage = lookup_parent_token_usage(
            self._state_dir() / "metrics.jsonl",
            resume_from_uuid,
        )
        raw_anno = task.annotations.get("_parent_token_usage")
        if raw_anno and token_usage is None:
            try:
                token_usage = int(raw_anno)
            except ValueError:
                token_usage = None

        compacted_key = f"{resume_from_uuid}__compacted__{uuid}"
        compact_result = compact_resume_session(
            store=self._session_store,
            parent_key=resume_from_uuid,
            parent_record=resolved,
            compacted_key=compacted_key,
            policy=self._compaction_policy(),
            token_usage=token_usage,
            cwd=self._config.cwd,
            model=task.model,
        )
        write_compaction_audit(
            self._queue_audit_path(),
            task_uuid=uuid,
            status=compact_result.status,
            reason=compact_result.reason,
            parent_session_id=compact_result.parent_session_id,
            compacted_session_id=(
                compact_result.session_id
                if compact_result.status == "compacted"
                else None
            ),
            summary_tokens=compact_result.summary_tokens,
            tokens_before=compact_result.tokens_before,
            tokens_after=compact_result.tokens_after,
            engine=engine,
        )
        if compact_result.session_id:
            session_id = compact_result.session_id

        command = self._inject_resume_command(task.command, engine, session_id)
        if command == task.command:
            return
        task.annotations["_command_without_resume"] = task.command
        task.annotations["resumed_session_id"] = session_id
        task.command = command

    def _inject_resume_command(self, command: str, engine: str | None, session_id: str) -> str:
        if not engine:
            return command
        spec = ENGINE_SPECS.get(engine)
        if spec is None:
            return command
        return inject_resume(spec, command, session_id)

    def _is_resume_fallback_target(self, task: Task, stderr_text: str) -> bool:
        if task.annotations.get("_resume_fallback_launched"):
            return False
        if "resumed_session_id" not in task.annotations:
            return False
        if "_command_without_resume" not in task.annotations:
            return False
        return bool(_RESUME_ERROR_RE.search(stderr_text))

    def is_running(self, uuid: str) -> bool:
        """Return True if the given task uuid is currently running or adopted."""
        return uuid in self._running or uuid in self._adopted

    @property
    def running_count(self) -> int:
        """Number of currently running and adopted tasks."""
        return len(self._running) + len(self._adopted)

    def _join_reader_threads(self, rt: _RunningProcess) -> None:
        for name, th in [("stderr", rt.stderr_thread), ("stdout", rt.stdout_thread)]:
            if th is None:
                continue
            th.join(timeout=2.0)
            if th.is_alive():
                logger.warning(
                    "Task [%s] %s reader thread did not terminate within 2.0s",
                    rt.uuid, name,
                )

    def adopt_orphans(self, tasks: dict[str, Task]) -> list[str]:
        """Read jobs/running/*.json and adopt or immediately close orphaned tasks.

        Returns the list of uuids that were registered as adopted (still alive).
        """
        running_dir = self._state_dir() / "running"
        if not running_dir.is_dir():
            return []
        adopted_uuids: list[str] = []
        for path in running_dir.glob("*.json"):
            uuid = path.stem
            if uuid not in tasks:
                logger.warning("adopt_orphans: unknown uuid %s in running file — skipping", uuid)
                continue
            task = tasks[uuid]
            pgid: int | None = None
            started_at: float = time.time()
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                raw_pgid = data.get("pgid")
                if not isinstance(raw_pgid, int):
                    raise ValueError(f"pgid is not int: {raw_pgid!r}")
                pgid = raw_pgid
                raw_started = data.get("started_at")
                if raw_started:
                    try:
                        started_at = datetime.fromisoformat(raw_started).timestamp()
                    except (ValueError, TypeError):
                        pass
            except Exception:
                logger.warning(
                    "adopt_orphans: corrupt running file for %s — treating as dead", uuid
                )
                self._finish_orphan(uuid, task, started_at)
                continue
            interrupted_at = data.get("interrupted_at") if isinstance(data, dict) else None
            interrupted_reruns_val = (
                data.get("interrupted_reruns", 0) if isinstance(data, dict) else 0
            )
            raw_node = data.get("node") if isinstance(data, dict) else None
            node_name = raw_node if isinstance(raw_node, str) else None
            if interrupted_at is not None and task.annotations.get("_exclusive") == "true":
                if self._process_host.group_exists(pgid):
                    self._adopted[uuid] = AdoptedTask(
                        uuid=uuid,
                        task=task,
                        pgid=pgid,
                        started_at=started_at,
                        node=node_name,
                    )
                    adopted_uuids.append(uuid)
                    logger.info(
                        "adopt_orphans: adopted live exclusive task %s (pgid=%s)",
                        uuid,
                        pgid,
                    )
                else:
                    self._finish_orphan(
                        uuid,
                        task,
                        started_at,
                        detail="exclusive task is not rerun after interruption",
                    )
                continue
            if interrupted_at is not None:
                if self._process_host.group_exists(pgid):
                    logger.info(
                        "adopt_orphans: interrupted task %s still alive (pgid=%s) — killing",
                        uuid, pgid,
                    )
                    if node_name is not None:
                        remote_host = self._node_hosts.get(node_name)
                        if remote_host is not None:
                            remote_host.signal_remote(uuid, signal.SIGTERM)
                        else:
                            logger.warning(
                                "adopt_orphans: task %s ran on unknown node %s — "
                                "stopping local ssh only",
                                uuid,
                                node_name,
                            )
                    self._process_host.terminate_group(pgid)
                    deadline = time.monotonic() + self._config.kill_grace
                    while time.monotonic() < deadline and self._process_host.group_exists(pgid):
                        time.sleep(0.05)
                    if self._process_host.group_exists(pgid):
                        self._process_host.kill_group(pgid)

                self._rerun_or_orphan(
                    uuid, task, started_at, interrupted_reruns_val, path,
                )
            elif self._process_host.group_exists(pgid):
                self._adopted[uuid] = AdoptedTask(
                    uuid=uuid,
                    task=task,
                    pgid=pgid,
                    started_at=started_at,
                    interrupted_reruns=interrupted_reruns_val,
                    node=node_name,
                )
                adopted_uuids.append(uuid)
                logger.info("adopt_orphans: adopted live task %s (pgid=%s)", uuid, pgid)
            elif task.annotations.get("_exclusive") == "true":
                logger.info(
                    "adopt_orphans: task %s already dead (pgid=%s) — closing as orphan",
                    uuid, pgid,
                )
                self._finish_orphan(uuid, task, started_at)
            else:
                self._rerun_or_orphan(
                    uuid, task, started_at, interrupted_reruns_val, path,
                )
        return adopted_uuids

    def _rerun_or_orphan(
        self,
        uuid: str,
        task: Task,
        started_at: float,
        reruns: int,
        running_path: Path,
    ) -> bool:
        """Queue one interrupted rerun, or close as orphan when the limit is reached."""
        if reruns < _MAX_INTERRUPTED_RERUNS:
            self._pending_reruns[uuid] = reruns + 1
            running_path.unlink(missing_ok=True)
            try:
                self._quota_gate.finish_run(task_uuid=uuid)
            except ValueError:
                pass
            logger.info(
                "adopt_orphans: interrupted task %s queued for rerun "
                "(interrupted_reruns=%d)",
                uuid,
                reruns,
            )
            return True
        logger.warning(
            "adopt_orphans: interrupted task %s reached rerun limit — orphaning",
            uuid,
        )
        self._finish_orphan(
            uuid,
            task,
            started_at,
            detail="interrupted rerun limit reached",
        )
        return False

    def _finish_orphan(
        self, uuid: str, task: Task, started_at: float, detail: str | None = None,
    ) -> None:
        """Close an orphaned task with ORPHANED_ON_RESTART done marker."""
        finished_at = time.time()
        if task.result_path is not None:
            message = (
                f"ORPHANED_ON_RESTART: {detail}"
                if detail is not None
                else "ORPHANED_ON_RESTART: runner restarted while task was running; "
                "exit code and stdout are unavailable"
            )
            try:
                Path(task.result_path).write_text(message, encoding="utf-8")
            except OSError:
                logger.warning("Failed to write orphan result for [%s]", uuid, exc_info=True)
        state_mark_done(self._config.exec_done_dir, uuid, DONE_ORPHANED_ON_RESTART)
        metrics = TaskMetrics(
            uuid=uuid,
            engine=task.engine,
            model=task.model,
            wall_time_sec=round(finished_at - started_at, 3),
            token_count=None,
            status="failure",
            started_at=started_at,
            finished_at=finished_at,
            correlation_id=task.idempotency_key,
            failure_class=FailureClass.UNKNOWN_FAILURE,
            request_id=_task_request_id(task),
            cost_usd=None,
            cache_read_tokens=None,
            cache_creation_tokens=None,
        )
        self._call_on_task_failure(uuid, task, -1, "orphaned_on_restart", metrics)
        self._cleanup_control_files(uuid)
        try:
            self._quota_gate.finish_run(task_uuid=uuid)
        except ValueError:
            logger.exception("Failed to update running registry for orphan [%s]", uuid)
