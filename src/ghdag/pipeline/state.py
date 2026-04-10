"""
pipeline/state.py — パイプライン状態管理

移植元: tools/stash-developer/stash_developer/pipeline_state.py +
        tools/stash-developer/stash_developer/exec_writer.py

2つの永続化先を管理:
  (1) {state_dir}/{id}.json — パイプライン実行状態
  (2) exec.md — 冪等性キー（# idempotency: {key} コメント行）
"""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path

import yaml


class PipelineState:
    def __init__(self, state_dir: str | Path, exec_md_path: str | Path):
        """
        Args:
            state_dir: JSON 状態ファイルの保存先ディレクトリ（例: .pipeline-state/）
            exec_md_path: exec.md のパス（冪等性キーの読み書き先）
        """
        self._state_dir = Path(state_dir)
        self._exec_md_path = Path(exec_md_path)

    # --- 冪等性（exec.md コメント行） ---

    def check_idempotency(self, key: str) -> bool:
        """exec.md 内に "# idempotency: {key}" が存在しなければ True（未処理）。

        exec.md が存在しない場合も True を返す。
        """
        if not self._exec_md_path.exists():
            return True
        needle = f"# idempotency: {key}"
        with open(self._exec_md_path, encoding="utf-8") as f:
            for line in f:
                if needle in line:
                    return False
        return True

    def record_dispatch(self, key: str) -> None:
        """exec.md に "# idempotency: {key}" を追記。fcntl ロック付き。"""
        self.append_exec([f"# idempotency: {key}"])

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

    # --- exec.md 追記 ---

    def append_exec(self, lines: list[str]) -> None:
        """exec.md に lines を追記。fcntl 排他ロック付き。"""
        with open(self._exec_md_path, "a", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.write("\n".join(lines) + "\n")
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def write_order_file(
        self, ts: str, order_uuid: str, content: str, queue_dir: str
    ) -> str:
        """queue_dir/{ts}-claude-order-{order_uuid}.md に content を書き出し。

        Returns:
            書き出したファイル名（ディレクトリ含まず）
        """
        filename = f"{ts}-claude-order-{order_uuid}.md"
        path = os.path.join(queue_dir, filename)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return filename


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
