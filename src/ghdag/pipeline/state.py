"""
pipeline/state.py — パイプライン状態管理

移植元: tools/stash-developer/stash_developer/pipeline_state.py +
        tools/stash-developer/stash_developer/exec_writer.py

2つの永続化先を管理:
  (1) {state_dir}/{id}.json — パイプライン実行状態
  (2) exec.jsonl — 冪等性キー（idempotency_key フィールドを持つ JSONL レコード）
"""

from __future__ import annotations

import fcntl
import json
import os
from collections.abc import Callable
from pathlib import Path

import yaml

from ghdag.io import exec_jsonl
from ghdag.io.audit import AuditContext
from ghdag.quota import QuotaGate


def build_idempotency_key(
    workflow_name: str,
    handler_name: str,
    issue_number: int,
    generation: int = 0,
) -> str:
    """Build a handler idempotency key, preserving generation-0 backward compatibility."""
    base = f"{workflow_name}:{handler_name}:{issue_number}"
    if generation == 0:
        return base
    return f"{base}:{generation}"


def handler_generation_key(
    workflow_name: str,
    handler_name: str,
    issue_number: int,
) -> str:
    """Key used in generations.json (no generation suffix)."""
    return f"{workflow_name}:{handler_name}:{issue_number}"


class PipelineState:
    def __init__(self, state_dir: str | Path, exec_jsonl_path: str | Path):
        """
        Args:
            state_dir: JSON 状態ファイルの保存先ディレクトリ（例: .pipeline-state/）
            exec_jsonl_path: exec.jsonl のパス（冪等性キーの読み書き先）
        """
        self._state_dir = Path(state_dir)
        self._exec_jsonl_path = Path(exec_jsonl_path)

    # --- 冪等性（exec.jsonl レコード） ---

    def check_idempotency(self, key: str) -> bool:
        """exec.jsonl 内に指定キーの idempotency 記録がなければ True（未処理）。

        JSONL レコードの "idempotency_key" フィールドで判定する。
        ファイルが存在しない場合も True を返す。
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
        key = handler_generation_key(workflow_name, handler_name, issue_number)
        data = self._load_generations()
        value = data.get(key, 0)
        return int(value) if isinstance(value, int) else 0

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
                f.seek(0)
                f.truncate()
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.write("\n")
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
        """exec.jsonl から workflow_name:*:issue_number にマッチする冪等性記録を削除。

        世代なし (:issue_number 末尾) と世代あり (:issue_number:N) の両方にマッチする。

        Returns:
            削除したレコード数
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
        """exec.jsonl から workflow_name:handler_name:issue_number に完全一致する冪等性記録を削除。

        Returns:
            削除したレコード数
        """
        target_key = f"{workflow_name}:{handler_name}:{issue_number}"
        return self._remove_by_predicate(lambda rec: rec.get("idempotency_key") == target_key)

    def _remove_by_predicate(self, predicate: Callable[[dict], bool]) -> int:
        """exec.jsonl から predicate が True を返すレコードを削除する内部ヘルパー。

        Args:
            predicate: dict レコードを受け取り True なら削除対象とする関数

        Returns:
            削除したレコード数
        """
        return exec_jsonl.remove_by_predicate(self._exec_jsonl_path, predicate)

    # --- JSON 状態永続化 ---

    def save(self, pipeline_id: str, metadata: dict) -> None:
        """state_dir/{pipeline_id}.json に metadata を JSON で書き出し。"""
        self._state_dir.mkdir(parents=True, exist_ok=True)
        out_path = self._state_dir / f"{pipeline_id}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)

    def load(self, pipeline_id: str) -> dict | None:
        """state_dir/{pipeline_id}.json を読み出し。存在しなければ None。"""
        json_path = self._state_dir / f"{pipeline_id}.json"
        if not json_path.exists():
            return None
        with open(json_path, encoding="utf-8") as f:
            return json.load(f)

    def remove(self, pipeline_id: str) -> bool:
        """state_dir/{pipeline_id}.json を削除。存在しなければ False。"""
        json_path = self._state_dir / f"{pipeline_id}.json"
        if not json_path.exists():
            return False
        json_path.unlink()
        return True

    # --- exec 追記 ---

    def append_exec_records(
        self,
        records: list[dict],
        audit_context: AuditContext | None = None,
    ) -> None:
        """exec.jsonl に records を JSONL 形式で追記。fcntl 排他ロック付き。"""
        exec_jsonl.append(
            self._exec_jsonl_path,
            records,
            audit_context or AuditContext(),
            audit_path=self._exec_jsonl_path.parent / "audit.jsonl",
            quota_gate=QuotaGate(
                self._exec_jsonl_path.parent / "quota-gate.json",
                audit_path=self._exec_jsonl_path.parent / "audit.jsonl",
            ),
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
        """queue_dir/{ts}-{engine}-order-{order_uuid}.md に content を書き出し。

        Args:
            ts: タイムスタンプ "YYYYMMDDHHmmSS"
            order_uuid: UUID 文字列
            content: order ファイル本文
            queue_dir: 書き込み先ディレクトリパス
            engine: engine prefix（"claude", "cursor", "gemini" 等）。デフォルト "claude"。
            order_footer_fn: 指定時、content 末尾にフッター文字列を付与してから書き込む。

        Returns:
            書き出したファイル名（ディレクトリ含まず）

        Raises:
            ValueError: engine が空文字の場合
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
        """リポジトリルートから標準パスで PipelineState を生成する。

        Args:
            repo_root: リポジトリルートのパス

        Returns:
            PipelineState(state_dir=repo_root/.pipeline-state, exec_jsonl_path=repo_root/jobs/exec.jsonl)
        """
        root = Path(repo_root)
        return cls(
            state_dir=root / ".pipeline-state",
            exec_jsonl_path=root / "jobs" / "exec.jsonl",
        )

    def parse_exec_tasks(self) -> dict[str, str]:
        """exec.jsonl をパースし {uuid: command} の辞書を返す。

        ファイルが存在しない場合は空辞書を返す。

        Returns:
            {uuid: command} の辞書。
        """
        return exec_jsonl.parse_as_dict(self._exec_jsonl_path)

    def remove_exec_entries(self, uuids: set[str]) -> int:
        """exec.jsonl から指定 UUID のエントリ行を削除する。fcntl ロック付き。

        Args:
            uuids: 削除対象の UUID 集合

        Returns:
            削除した行数
        """
        return exec_jsonl.remove_by_uuids(self._exec_jsonl_path, uuids)


def status_rank(status: str, status_order: tuple[str, ...]) -> int:
    """status_order 内の status のインデックスを返す。不明なら -1。"""
    try:
        return status_order.index(status)
    except ValueError:
        return -1


def parse_frontmatter(path: str | Path) -> dict:
    """ファイル先頭の YAML frontmatter（--- で囲まれた部分）をパースして dict を返す。

    frontmatter がない場合は空 dict を返す。
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
