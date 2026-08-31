from __future__ import annotations

from pathlib import Path

from ghdag.io import exec_jsonl


class ExecJsonlPruner:
    def __init__(self, exec_md: Path, dry_run: bool = False) -> None:
        self._exec_md = exec_md
        self._dry_run = dry_run

    def load(self) -> tuple[list[str], set[str]]:
        """exec.jsonl を読み込み、(行リスト, UUID集合) を返す。"""
        return exec_jsonl.load_uuids(self._exec_md)

    def prune(self, exec_lines: list[str], prune_uuids: set[str]) -> int:
        """prune_uuids に該当する行を除去し、ファイルを書き換える。

        ``exec_lines`` は呼び出し側の走査用キャッシュ。実際の rewrite は
        ``io.exec_jsonl.prune`` が LOCK_EX 付きで行う。

        Returns:
            除去した行数
        """
        del exec_lines  # 走査は orchestrator 側; 書き込みは path 基準で再読込
        return exec_jsonl.prune(self._exec_md, prune_uuids, dry_run=self._dry_run)

    @staticmethod
    def extract_uuid(line: str) -> str | None:
        """JSON 行から UUID を抽出する。"""
        return exec_jsonl.extract_uuid(line)
