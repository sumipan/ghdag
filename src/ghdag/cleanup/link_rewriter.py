from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from ghdag.files.links.obsidian import rewrite_links
from ghdag.files.writer import md_write


class LinkRewriter:
    def __init__(self, queue_dir: Path, dry_run: bool = False) -> None:
        self._queue_dir = queue_dir
        self._dry_run = dry_run

    def rewrite(self, all_moved: list[tuple[Path, Path]]) -> None:
        """移動されたファイルのパスマップに基づき、queue_dir 内の .md ファイルの
        wiki リンクを書き換える。"""
        if self._dry_run or not all_moved:
            return

        repo_root = self._queue_dir.parent
        cleanup_correlation_id = str(uuid4())
        path_map: dict[str, str] = {}
        for old_path, new_path in all_moved:
            old_rel = f"jobs/{old_path.name}"
            new_rel = str(new_path.relative_to(repo_root))
            path_map[old_rel] = new_rel

        def _maybe_rewrite(path: Path) -> None:
            if path.suffix != ".md":
                return
            content = path.read_text(encoding="utf-8")
            new_content = rewrite_links(content, path_map)
            if new_content == content:
                return
            rel = str(path.relative_to(repo_root))
            md_write(
                rel,
                new_content,
                repo_root=repo_root,
                source="cleanup_link_rewrite",
                correlation_id=cleanup_correlation_id,
            )

        for _old_path, new_path in all_moved:
            _maybe_rewrite(new_path)

        for path in self._queue_dir.iterdir():
            if path.is_file() and path.suffix == ".md":
                _maybe_rewrite(path)
