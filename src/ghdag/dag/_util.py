"""Internal helper utilities for the DAG engine."""

from __future__ import annotations

import io
import re
import subprocess


def _stderr_reader(proc: subprocess.Popen, buf: io.BytesIO) -> None:
    """Read stderr from proc into buf in a daemon thread."""
    for chunk in iter(lambda: proc.stderr.read(4096), b""):
        buf.write(chunk)
    proc.stderr.close()


def _extract_tee_target(command: str) -> str | None:
    """Extract the tee output path from a command string.

    Example: 'cat foo | claude -p "..." | tee result.md' -> 'result.md'
    Returns None if tee is not found.
    """
    m = re.search(r"\btee\s+(?:-a\s+)?(\S+\.md)", command)
    return m.group(1) if m else None


def default_check_rejected(result_path: str) -> bool:
    """Check if result file contains PIPELINE_STATUS: REJECTED."""
    try:
        with open(result_path, encoding="utf-8") as f:
            for _ in range(10):
                line = f.readline()
                if line == "":
                    break
                stripped = line.strip()
                if stripped.startswith("REJECTED:"):
                    return True
                if stripped.startswith("ACCEPTED"):
                    return False
        return False
    except OSError:
        return False
