"""pipeline/wait.py — jobs/done/ polling utility"""

from __future__ import annotations

import time
from pathlib import Path

from ghdag.io.done import read_done_content
from ghdag.pipeline.status import interpret_done


def wait_for_result(
    exec_done_dir: Path,
    uuid: str,
    *,
    timeout: float,
    poll_interval: float = 0.5,
) -> tuple[str, str]:
    """Poll for the jobs/done/<uuid> file to appear and return the completion status.

    Args:
        exec_done_dir: path to the jobs/done/ directory
        uuid: UUID of the task to wait for
        timeout: maximum wait in seconds
        poll_interval: polling interval (seconds)
    Returns:
        tuple of (status, raw_first_line).
        status is the result of interpret_done:
          "success"     — exit code 0 or empty
          "rejected"    — REJECTED / REJECTED_FINAL
          "engine_error" — ENGINE_ERROR / ENGINE_ERROR_FINAL
          "empty_result" — EMPTY_RESULT
          "failed_exit" — non-zero exit code
          "other"       — anything else
    Raises:
        TimeoutError: jobs/done/<uuid> did not appear within timeout seconds
    """
    deadline = time.monotonic() + timeout
    while True:
        raw = read_done_content(exec_done_dir, uuid)
        if raw is not None:
            status = interpret_done(raw)
            assert status is not None  # raw is not None => interpret_done never returns None
            lines = raw.strip().splitlines()
            first_line = lines[0].strip() if lines else ""
            return (status, first_line)

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"wait_for_result: {uuid} timed out after {timeout}s")
        time.sleep(min(poll_interval, remaining))
