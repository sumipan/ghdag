"""Local and SSH subprocess launch and process-group control for DAG tasks."""

from __future__ import annotations

import logging
import os
import re
import shlex
import signal
import subprocess
from pathlib import Path

from ghdag.dag._nodes import NodeSpec

logger = logging.getLogger(__name__)

_STDIN_REDIR_RE = re.compile(r"(?<!<)<\s+(\S+)")


def split_stdin_redirect(command: str) -> tuple[str, str | None]:
    """Split a leading stdin redirect from *command* if present."""
    match = _STDIN_REDIR_RE.search(command)
    if match is None:
        return command, None
    path = match.group(1)
    remainder = _STDIN_REDIR_RE.sub("", command, count=1)
    return remainder, path


def build_wrapper_script() -> str:
    """Return the remote bash wrapper executed via ``bash -c`` on the node."""
    return (
        'cd -- "$1" || exit 125; '
        'mkdir -p .ghdag || exit 125; '
        'f=".ghdag/$2.pgid"; '
        "exec 3>&2 2>/dev/null; "
        "set -m; "
        'if [ -n "$3" ]; then '
        'GHDAG_TASK_UUID="$2" GHDAG_PREVIOUS_ATTEMPT="$3" bash -o pipefail -c "$4" 2>&3 3>&- & '
        "else "
        'GHDAG_TASK_UUID="$2" bash -o pipefail -c "$4" 2>&3 3>&- & '
        "fi; "
        "pid=$!; "
        "set +m; "
        'echo "$pid" > "$f"; '
        'wait "$pid"; rc=$?; '
        'rm -f "$f"; '
        'exit "$rc"'
    )


def build_ssh_argv(
    node: NodeSpec,
    uuid: str,
    command: str,
    previous_attempt: str | None,
) -> list[str]:
    """Build argv for ``ssh`` to run *command* on *node*."""
    wrapper = build_wrapper_script()
    remote_parts = [
        "bash",
        "-c",
        shlex.quote(wrapper),
        "ghdag-wrap",
        shlex.quote(node.workdir),
        shlex.quote(uuid),
        shlex.quote(previous_attempt or ""),
        shlex.quote(command),
    ]
    remote = " ".join(remote_parts)
    return ["ssh", "-o", "BatchMode=yes", node.ssh, remote]


def build_remote_signal_argv(
    node: NodeSpec,
    uuid: str,
    sig: signal.Signals,
) -> list[str]:
    """Build argv for ``ssh`` to signal a remote process group."""
    sig_name = "TERM" if sig == signal.SIGTERM else "KILL"
    pgid_file = f"{node.workdir}/.ghdag/{uuid}.pgid"
    remote = (
        f"f={shlex.quote(pgid_file)}; "
        'pgid=$(cat "$f" 2>/dev/null) || exit 1; '
        f"kill -{sig_name} -- -\"$pgid\""
    )
    return [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=5",
        node.ssh,
        remote,
    ]


class LocalProcessHost:
    """Start task subprocesses and signal their process groups on the local machine."""

    def start(
        self,
        command: str,
        *,
        cwd: str | None,
        env: dict[str, str],
        capture_stdout: bool,
    ) -> subprocess.Popen[bytes]:
        popen_kwargs: dict[str, object] = {
            "stderr": subprocess.PIPE,
            "cwd": cwd,
            "env": env,
            "start_new_session": True,
        }
        if capture_stdout:
            popen_kwargs["stdout"] = subprocess.PIPE
        return subprocess.Popen(
            ["bash", "-o", "pipefail", "-c", command],
            **popen_kwargs,  # type: ignore[arg-type]
        )

    def terminate_tree(self, proc: subprocess.Popen[bytes]) -> None:
        self._signal_tree(proc, signal.SIGTERM)

    def kill_tree(self, proc: subprocess.Popen[bytes]) -> None:
        self._signal_tree(proc, signal.SIGKILL)

    def group_exists(self, pgid: int) -> bool:
        """Return True if any process in the group is still alive."""
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def terminate_group(self, pgid: int) -> None:
        try:
            os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass

    def kill_group(self, pgid: int) -> None:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

    def group_id(self, pid: int) -> int | None:
        try:
            return os.getpgid(pid)
        except OSError:
            return None

    def _signal_tree(self, proc: subprocess.Popen[bytes], sig: signal.Signals) -> None:
        """Send *sig* to the task process group, falling back to the leader only."""
        pgid = proc.pid
        runner_pgid = os.getpgrp()
        if isinstance(pgid, int) and pgid == runner_pgid:
            logger.warning(
                "Refusing to signal runner process group %s; falling back to leader",
                pgid,
            )
        elif isinstance(pgid, int):
            try:
                os.killpg(pgid, sig)
                return
            except ProcessLookupError:
                return
            except OSError:
                logger.warning(
                    "Failed to signal process group %s with %s; falling back to leader",
                    pgid,
                    sig,
                    exc_info=True,
                )
        if proc.poll() is not None:
            return
        if sig == signal.SIGKILL:
            proc.kill()
        else:
            proc.terminate()


class SshProcessHost:
    """Launch tasks on a remote host via SSH and signal remote process groups."""

    def __init__(self, node: NodeSpec) -> None:
        self._node = node
        self._local = LocalProcessHost()
        self._pid_to_uuid: dict[int, str] = {}

    @property
    def node(self) -> NodeSpec:
        return self._node

    def start(
        self,
        command: str,
        *,
        cwd: str | None,
        env: dict[str, str],
        capture_stdout: bool,
    ) -> subprocess.Popen[bytes]:
        body, stdin_path = split_stdin_redirect(command)
        uuid = env["GHDAG_TASK_UUID"]
        previous_attempt = env.get("GHDAG_PREVIOUS_ATTEMPT")
        argv = build_ssh_argv(self._node, uuid, body, previous_attempt)
        stdin_opened = None
        if stdin_path is not None:
            base = Path(cwd) if cwd else Path()
            file_path = Path(stdin_path) if Path(stdin_path).is_absolute() else base / stdin_path
            stdin_opened = open(file_path, "rb")
            stdin_arg: int | object = stdin_opened
        else:
            stdin_arg = subprocess.DEVNULL
        popen_kwargs: dict[str, object] = {
            "stdin": stdin_arg,
            "stderr": subprocess.PIPE,
            "cwd": cwd,
            "env": dict(os.environ),
            "start_new_session": True,
        }
        if capture_stdout:
            popen_kwargs["stdout"] = subprocess.PIPE
        proc = subprocess.Popen(argv, **popen_kwargs)  # type: ignore[arg-type]
        if stdin_opened is not None:
            stdin_opened.close()
        if isinstance(proc.pid, int):
            self._pid_to_uuid[proc.pid] = uuid
        return proc

    def signal_remote(self, uuid: str, sig: signal.Signals) -> bool:
        """Signal the remote process group for *uuid*."""
        try:
            completed = subprocess.run(
                build_remote_signal_argv(self._node, uuid, sig),
                timeout=10,
                capture_output=True,
                env=dict(os.environ),
            )
        except (subprocess.TimeoutExpired, OSError):
            logger.warning(
                "signal_remote failed for task %s on node %s",
                uuid,
                self._node.name,
                exc_info=True,
            )
            return False
        if completed.returncode != 0:
            logger.warning(
                "signal_remote non-zero exit for task %s on node %s (rc=%s)",
                uuid,
                self._node.name,
                completed.returncode,
            )
            return False
        return True

    def terminate_tree(self, proc: subprocess.Popen[bytes]) -> None:
        uuid = self._uuid_for_proc(proc)
        if uuid is not None:
            self.signal_remote(uuid, signal.SIGTERM)
        self._local._signal_tree(proc, signal.SIGTERM)

    def kill_tree(self, proc: subprocess.Popen[bytes]) -> None:
        uuid = self._uuid_for_proc(proc)
        if uuid is not None:
            self.signal_remote(uuid, signal.SIGKILL)
        self._local._signal_tree(proc, signal.SIGKILL)

    def group_exists(self, pgid: int) -> bool:
        return self._local.group_exists(pgid)

    def terminate_group(self, pgid: int) -> None:
        self._local.terminate_group(pgid)

    def kill_group(self, pgid: int) -> None:
        self._local.kill_group(pgid)

    def group_id(self, pid: int) -> int | None:
        return self._local.group_id(pid)

    def _uuid_for_proc(self, proc: subprocess.Popen[bytes]) -> str | None:
        if not isinstance(proc.pid, int):
            return None
        return self._pid_to_uuid.get(proc.pid)
