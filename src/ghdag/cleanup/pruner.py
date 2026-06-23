from __future__ import annotations

import json
from pathlib import Path


class ExecJsonlPruner:
    def __init__(self, exec_md: Path, dry_run: bool = False) -> None:
        self._exec_md = exec_md
        self._dry_run = dry_run

    def load(self) -> tuple[list[str], set[str]]:
        """exec.jsonl を読み込み、(行リスト, UUID集合) を返す。"""
        if not self._exec_md.exists():
            return [], set()
        lines = self._exec_md.read_text(encoding="utf-8").splitlines(keepends=True)
        uuids: set[str] = set()
        for line in lines:
            uuid = self.extract_uuid(line)
            if uuid:
                uuids.add(uuid)
        return lines, uuids

    def prune(self, exec_lines: list[str], prune_uuids: set[str]) -> int:
        """prune_uuids に該当する行を除去し、ファイルを書き換える。

        Returns:
            除去した行数
        """
        pruned = 0
        new_lines = []
        for line in exec_lines:
            uuid_in_line = self.extract_uuid(line)
            if uuid_in_line and uuid_in_line in prune_uuids:
                pruned += 1
                if self._dry_run:
                    print(f"[dry] prune exec entry: {line.rstrip()[:80]}")
            else:
                new_lines.append(line)
        if pruned > 0 and not self._dry_run:
            self._exec_md.write_text("".join(new_lines), encoding="utf-8")
        return pruned

    @staticmethod
    def extract_uuid(line: str) -> str | None:
        """JSON 行から UUID を抽出する。"""
        stripped = line.strip()
        if not stripped or not stripped.startswith("{"):
            return None
        try:
            obj = json.loads(stripped)
            uuid = obj.get("uuid", "")
            return uuid.lower() or None
        except (json.JSONDecodeError, AttributeError):
            return None
