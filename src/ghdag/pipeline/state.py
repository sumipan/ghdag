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
from pathlib import Path

import yaml

from ghdag.pipeline.audit import AuditContext, write_audit_log


class PipelineState:
    def __init__(self, state_dir: str | Path, exec_md_path: str | Path):
        """
        Args:
            state_dir: JSON 状態ファイルの保存先ディレクトリ（例: .pipeline-state/）
            exec_md_path: exec.md のパス（冪等性キーの読み書き先）
        """
        self._state_dir = Path(state_dir)
        self._exec_md_path = Path(exec_md_path)

    # --- 冪等性（exec.jsonl レコード） ---

    def check_idempotency(self, key: str) -> bool:
        """exec.jsonl 内に指定キーの idempotency 記録がなければ True（未処理）。

        JSONL レコードの "idempotency_key" フィールドで判定する。
        ファイルが存在しない場合も True を返す。
        """
        if not self._exec_md_path.exists():
            return True
        with open(self._exec_md_path, encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    if rec.get("idempotency_key") == key:
                        return False
                except json.JSONDecodeError:
                    continue
        return True

    def remove_idempotency_matching(self, workflow_name: str, issue_number: int) -> int:
        """exec.jsonl から workflow_name:*:issue_number にマッチする冪等性記録を削除。

        Returns:
            削除したレコード数
        """
        if not self._exec_md_path.exists():
            return 0

        prefix = f"{workflow_name}:"
        suffix = f":{issue_number}"

        with open(self._exec_md_path, encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            try:
                lines = f.readlines()
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

        new_lines = []
        removed = 0

        for line in lines:
            stripped = line.strip()
            if not stripped:
                new_lines.append(line)
                continue
            try:
                data = json.loads(stripped)
                key = data.get("idempotency_key", "")
                if key and key.startswith(prefix) and key.endswith(suffix):
                    removed += 1
                    continue
            except json.JSONDecodeError:
                pass
            new_lines.append(line)

        if removed > 0:
            with open(self._exec_md_path, "w", encoding="utf-8") as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                try:
                    f.writelines(new_lines)
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)

        return removed

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
        lines = [json.dumps(r, ensure_ascii=False) for r in records]
        with open(self._exec_md_path, "a", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.write("\n".join(lines) + "\n")
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

        ctx = audit_context or AuditContext()
        audit_path = self._exec_md_path.parent / "audit.jsonl"
        uuids = [r["uuid"] for r in records if "uuid" in r]
        write_audit_log(
            audit_path,
            task_uuids=uuids,
            exec_lines_count=len(records),
            context=ctx,
        )

    def write_order_file(
        self,
        ts: str,
        order_uuid: str,
        content: str,
        queue_dir: str,
        engine: str = "claude",
    ) -> str:
        """queue_dir/{ts}-{engine}-order-{order_uuid}.md に content を書き出し。

        Args:
            ts: タイムスタンプ "YYYYMMDDHHmmSS"
            order_uuid: UUID 文字列
            content: order ファイル本文
            queue_dir: 書き込み先ディレクトリパス
            engine: engine prefix（"claude", "cursor", "gemini" 等）。デフォルト "claude"。

        Returns:
            書き出したファイル名（ディレクトリ含まず）

        Raises:
            ValueError: engine が空文字の場合
        """
        if not engine:
            raise ValueError("engine must not be empty")
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
            PipelineState(state_dir=repo_root/.pipeline-state, exec_md_path=repo_root/jobs/exec.jsonl)
        """
        root = Path(repo_root)
        return cls(
            state_dir=root / ".pipeline-state",
            exec_md_path=root / "jobs" / "exec.jsonl",
        )

    def parse_exec_tasks(self) -> dict[str, str]:
        """exec.jsonl をパースし {uuid: command} の辞書を返す。

        ファイルが存在しない場合は空辞書を返す。

        Returns:
            {uuid: command} の辞書。
        """
        if not self._exec_md_path.exists():
            return {}

        result: dict[str, str] = {}
        with open(self._exec_md_path, encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    data = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                uuid = data.get("uuid")
                command = data.get("command")
                if uuid and command:
                    result[uuid] = command
        return result

    def remove_exec_entries(self, uuids: set[str]) -> int:
        """exec.jsonl から指定 UUID のエントリ行を削除する。fcntl ロック付き。

        Args:
            uuids: 削除対象の UUID 集合

        Returns:
            削除した行数
        """
        if not self._exec_md_path.exists():
            return 0

        with open(self._exec_md_path, "a+", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.seek(0)
                lines = f.readlines()
                new_lines = []
                removed = 0
                for line in lines:
                    stripped = line.strip()
                    if not stripped:
                        new_lines.append(line)
                        continue
                    try:
                        data = json.loads(stripped)
                        if data.get("uuid") in uuids:
                            removed += 1
                            continue
                    except json.JSONDecodeError:
                        pass
                    new_lines.append(line)
                if removed > 0:
                    f.seek(0)
                    f.truncate()
                    f.writelines(new_lines)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
        return removed


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
