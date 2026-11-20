"""Local subprocess launch and process-group control for DAG tasks."""

from __future__ import annotations

import logging
import os
import signal
import subprocess

logger = logging.getLogger(__name__)


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
