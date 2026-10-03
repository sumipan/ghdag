"""
pipeline/state.py — pipeline state management

Ported from: tools/stash-developer/stash_developer/pipeline_state.py +
              tools/stash-developer/stash_developer/exec_writer.py

Manages two persistence targets:
  (1) {state_dir}/{id}.json — pipeline execution state
  (2) exec.jsonl — idempotency keys (JSONL records with an idempotency_key field)
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path

import yaml

from ghdag.config.env import state_dir as resolve_state_dir
from ghdag.io import exec_jsonl
from ghdag.io.audit import AuditContext
from ghdag.quota import QuotaGate

build_idempotency_key = exec_jsonl.build_idempotency_key
handler_generation_key = exec_jsonl.handler_generation_key


class PipelineState:
    def __init__(self, state_dir: str | Path, exec_jsonl_path: str | Path):
        """
        Args:
            state_dir: directory for JSON state files (e.g. .pipeline-state/)
            exec_jsonl_path: path to exec.jsonl (where idempotency keys are read/written)
        """
        self._state_dir = Path(state_dir)
        self._exec_jsonl_path = Path(exec_jsonl_path)
        self._quota_gate = QuotaGate(
            resolve_state_dir(self._exec_jsonl_path.parent) / "quota-gate.json",
            audit_path=self._exec_jsonl_path.parent / "audit.jsonl",
        )

    # --- Idempotency (exec.jsonl records) ---

    def check_idempotency(self, key: str) -> bool:
        """Return True (not yet processed) if exec.jsonl has no idempotency record for the key.

        Determined by the "idempotency_key" field of JSONL records.
        Also returns True if the file does not exist.
        """
        return exec_jsonl.check_idempotency(self._exec_jsonl_path, key)

    @property
    def generations_path(self) -> Path:
        return self._state_dir / "generations.json"

    def get_generation(
        self,
        workflow_name: str,
        handler_name: str,
        issue_number: int,
    ) -> int:
        """Return the current redispatch generation for an Issue × handler (default 0)."""
        return exec_jsonl.get_generation(
            self._state_dir, workflow_name, handler_name, issue_number,
        )

    def increment_generation(
        self,
        workflow_name: str,
        handler_name: str,
        issue_number: int,
    ) -> int:
        """Increment and persist the redispatch generation. Returns the new value."""
        key = handler_generation_key(workflow_name, handler_name, issue_number)
        self._state_dir.mkdir(parents=True, exist_ok=True)
        with open(self.generations_path, "a+", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.seek(0)
                raw = f.read()
                data: dict[str, int] = json.loads(raw) if raw.strip() else {}
                current = int(data.get(key, 0)) if isinstance(data.get(key, 0), int) else 0
                new_value = current + 1
                data[key] = new_value
                fd, tmp = tempfile.mkstemp(dir=str(self._state_dir), suffix=".tmp")
                try:
                    with os.fdopen(fd, "w") as tf:
                        json.dump(data, tf, ensure_ascii=False, indent=2)
                        tf.write("\n")
                    os.replace(tmp, str(self.generations_path))
                except BaseException:
                    with contextlib.suppress(OSError):
                        os.unlink(tmp)
                    raise
                return new_value
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def find_records_by_idempotency_key(self, key: str) -> list[dict]:
        """Return exec.jsonl records matching the given idempotency key."""
        return exec_jsonl.find_records_by_idempotency_key(self._exec_jsonl_path, key)

    def _load_generations(self) -> dict[str, int]:
        path = self.generations_path
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {str(k): int(v) for k, v in data.items() if isinstance(v, int)}

    def remove_idempotency_matching(self, workflow_name: str, issue_number: int) -> int:
        """Remove idempotency records matching workflow_name:*:issue_number from exec.jsonl.

        Matches both without generation (ending in :issue_number) and with generation (:issue_number:N).

        Returns:
            number of records removed
        """
        prefix = f"{workflow_name}:"
        suffix_exact = f":{issue_number}"
        suffix_gen = f":{issue_number}:"
        return self._remove_by_predicate(
            lambda rec: (k := rec.get("idempotency_key", ""))
            and k.startswith(prefix)
            and (k.endswith(suffix_exact) or suffix_gen in k)
        )

    def remove_idempotency_for_handler(
        self,
        workflow_name: str,
        handler_name: str,
        issue_number: int,
    ) -> int:
        """Remove idempotency records exactly matching workflow_name:handler_name:issue_number from exec.jsonl.

        Returns:
            number of records removed
        """
        target_key = f"{workflow_name}:{handler_name}:{issue_number}"
        return self._remove_by_predicate(lambda rec: rec.get("idempotency_key") == target_key)

    def _remove_by_predicate(self, predicate: Callable[[dict], bool]) -> int:
        """Internal helper that removes records for which predicate returns True from exec.jsonl.

        Args:
            predicate: function taking a dict record; returns True if it should be removed

        Returns:
            number of records removed
        """
        return exec_jsonl.remove_by_predicate(self._exec_jsonl_path, predicate)

    # --- JSON state persistence ---

    def save(self, pipeline_id: str, metadata: dict) -> None:
        """Write metadata as JSON to state_dir/{pipeline_id}.json."""
        self._state_dir.mkdir(parents=True, exist_ok=True)
        out_path = self._state_dir / f"{pipeline_id}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)

    def load(self, pipeline_id: str) -> dict | None:
        """Read state_dir/{pipeline_id}.json. Returns None if it does not exist."""
        json_path = self._state_dir / f"{pipeline_id}.json"
        if not json_path.exists():
            return None
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None
        return data

    def remove(self, pipeline_id: str) -> bool:
        """Delete state_dir/{pipeline_id}.json. Returns False if it does not exist."""
        json_path = self._state_dir / f"{pipeline_id}.json"
        if not json_path.exists():
            return False
        json_path.unlink()
        return True

    # --- exec append ---

    def append_exec_records(
        self,
        records: list[dict],
        audit_context: AuditContext | None = None,
    ) -> None:
        """Append records to exec.jsonl in JSONL format, with an fcntl exclusive lock."""
        exec_jsonl.append(
            self._exec_jsonl_path,
            records,
            audit_context or AuditContext(),
            audit_path=self._exec_jsonl_path.parent / "audit.jsonl",
            quota_gate=self._quota_gate,
        )

    def write_order_file(
        self,
        ts: str,
        order_uuid: str,
        content: str,
        queue_dir: str,
        engine: str = "claude",
        order_footer_fn: Callable[[str, str, str], str] | None = None,
    ) -> str:
        """Write content to queue_dir/{ts}-{engine}-order-{order_uuid}.md.

        Args:
            ts: timestamp "YYYYMMDDHHmmSS"
            order_uuid: UUID string
            content: order file body
            queue_dir: destination directory path
            engine: engine prefix ("claude", "cursor", "gemini", etc.). Defaults to "claude".
            order_footer_fn: if given, appends a footer string to content before writing.

        Returns:
            written file name (without directory)

        Raises:
            ValueError: if engine is an empty string
        """
        if not engine:
            raise ValueError("engine must not be empty")
        if order_footer_fn is not None:
            content = content + order_footer_fn(ts, order_uuid, engine)
        filename = f"{ts}-{engine}-order-{order_uuid}.md"
        path = os.path.join(queue_dir, filename)
        with open(path, "a+", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.seek(0)
                f.truncate()
                f.write(content)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
        return filename


    @classmethod
    def from_repo_root(cls, repo_root: str | Path) -> "PipelineState":
        """Create a PipelineState with standard paths from the repository root.

        Args:
            repo_root: path to the repository root

        Returns:
            PipelineState(state_dir=repo_root/.pipeline-state, exec_jsonl_path=repo_root/jobs/exec.jsonl)
            (state_dir=$GHDAG_STATE_DIR/.pipeline-state when GHDAG_STATE_DIR is set)
        """
        root = Path(repo_root)
        return cls(
            state_dir=resolve_state_dir(root) / ".pipeline-state",
            exec_jsonl_path=root / "jobs" / "exec.jsonl",
        )

    def parse_exec_tasks(self) -> dict[str, str]:
        """Parse exec.jsonl and return a {uuid: command} dict.

        Returns an empty dict if the file does not exist.

        Returns:
            {uuid: command} dict.
        """
        return exec_jsonl.parse_as_dict(self._exec_jsonl_path)

    def remove_exec_entries(self, uuids: set[str]) -> int:
        """Remove entry lines for the given UUIDs from exec.jsonl, with an fcntl lock.

        Args:
            uuids: set of UUIDs to remove

        Returns:
            number of lines removed
        """
        return exec_jsonl.remove_by_uuids(self._exec_jsonl_path, uuids)


def status_rank(status: str, status_order: tuple[str, ...]) -> int:
    """Return the index of status in status_order, or -1 if unknown."""
    try:
        return status_order.index(status)
    except ValueError:
        return -1


def parse_frontmatter(path: str | Path) -> dict:
    """Parse the YAML frontmatter (enclosed by ---) at the top of a file and return a dict.

    Returns an empty dict if there is no frontmatter.
    """
    with open(path, encoding="utf-8") as f:
        content = f.read()

    if not content.startswith("---"):
        return {}

    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}

    try:
        return yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return {}
