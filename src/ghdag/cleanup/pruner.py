from __future__ import annotations

from pathlib import Path

from ghdag.io import exec_jsonl


class ExecJsonlPruner:
    def __init__(self, exec_md: Path, dry_run: bool = False) -> None:
        self._exec_md = exec_md
        self._dry_run = dry_run

    def load(self) -> tuple[list[str], set[str]]:
        """Load exec.jsonl and return (list of lines, set of UUIDs)."""
        return exec_jsonl.load_uuids(self._exec_md)

    def prune(self, exec_lines: list[str], prune_uuids: set[str]) -> int:
        """Remove lines matching prune_uuids and rewrite the file.

        ``exec_lines`` is the caller's scan cache. The actual rewrite is
        performed by ``io.exec_jsonl.prune`` under LOCK_EX.

        Returns:
            Number of lines removed
        """
        del exec_lines  # scanning is done by the orchestrator; writes re-read by path
        return exec_jsonl.prune(self._exec_md, prune_uuids, dry_run=self._dry_run)

    @staticmethod
    def extract_uuid(line: str) -> str | None:
        """Extract the UUID from a JSON line."""
        return exec_jsonl.extract_uuid(line)
