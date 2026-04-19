"""ghdag cleanup — queue ディレクトリのクリーンアップロジック。"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

# queue/[ts]-[tool]-order/result-[UUID].md
QUEUE_FILE_RE = re.compile(
    r"^(\d{14})-(\w+)-(order|result)-([a-fA-F0-9\-]{36})\.md$"
)
# exec.md の行: UUID[depends:...]: command
EXEC_LINE_RE = re.compile(r"^([a-fA-F0-9\-]+)(?:\[depends:[^\]]+\])?\s*:")


@dataclass
class CleanupResult:
    archived_done: int    # 完了済みアーカイブ件数
    archived_orphan: int  # 孤立アーカイブ件数
    pruned_exec: int      # exec.md から除去した行数


def file_timestamp(path: Path) -> float:
    """ファイルのタイムスタンプを返す。

    macOS では st_birthtime（作成日時）を優先し、
    取得できない環境では st_mtime（更新日時）にフォールバックする。
    """
    st = path.stat()
    return getattr(st, "st_birthtime", st.st_mtime)


def cleanup_queue(
    queue_dir: Path,
    queue_done_dir: Path,
    exec_done_dir: Path,
    exec_md: Path,
    cutoff_days: int = 1,
    orphan_days: int = 7,
    dry_run: bool = False,
) -> CleanupResult:
    """queue ディレクトリのクリーンアップを実行する。

    Args:
        queue_dir: queue/ ディレクトリのパス
        queue_done_dir: queue-done/ ディレクトリのパス
        exec_done_dir: exec-done/ ディレクトリのパス
        exec_md: exec.md ファイルのパス
        cutoff_days: 完了タスクをアーカイブするまでの日数
        orphan_days: 未完了タスクを孤立扱いにする日数
        dry_run: True の場合、対象を表示するのみで変更しない

    Returns:
        CleanupResult: アーカイブ件数と除去エントリ数
    """
    if not queue_dir.is_dir():
        print(f"error: queue/ が存在しません: {queue_dir}", file=sys.stderr)
        sys.exit(1)

    now = datetime.now(timezone.utc)
    cutoff_ts = (now - timedelta(days=cutoff_days)).timestamp()
    orphan_ts = (now - timedelta(days=orphan_days)).timestamp()

    # exec-done フラグ収集
    done_uuids: set[str] = set()
    if exec_done_dir.is_dir():
        done_uuids = {p.name.lower() for p in exec_done_dir.iterdir()}

    # queue/ のファイルを UUID ごとに収集
    by_uuid: dict[str, dict] = {}
    for path in queue_dir.iterdir():
        if not path.is_file() or path.suffix != ".md":
            continue
        m = QUEUE_FILE_RE.match(path.name)
        if not m:
            continue
        ts, tool, kind, uuid_raw = m.groups()
        uuid = uuid_raw.lower()
        entry = by_uuid.setdefault(uuid, {"ts": ts, "tool": tool})
        entry[kind] = path  # "order" or "result"

    archived_done = 0
    archived_orphan = 0
    pruned_uuids: set[str] = set()

    for uuid, entry in by_uuid.items():
        order_path: Path | None = entry.get("order")
        result_path: Path | None = entry.get("result")
        ts = entry["ts"]
        ref_path = order_path or result_path
        mtime = file_timestamp(ref_path)

        if uuid in done_uuids:
            # 完了済み: cutoff を過ぎていたらアーカイブ
            if mtime <= cutoff_ts:
                dest_dir = _archive_month_dir(queue_done_dir, ts, orphan=False)
                for p in (order_path, result_path):
                    if p and p.exists():
                        dest = dest_dir / p.name
                        if dry_run:
                            print(f"[dry] archive done: {p.name} → {dest}")
                        else:
                            p.rename(dest)
                            print(f"archive done: {p.name} → {dest}")
                # exec-done フラグ削除
                flag = exec_done_dir / uuid
                if flag.exists():
                    if dry_run:
                        print(f"[dry] remove exec-done: {uuid}")
                    else:
                        flag.unlink()
                pruned_uuids.add(uuid)
                archived_done += 1
        else:
            # 未完了: orphan_days を過ぎていたら孤立アーカイブ
            if mtime <= orphan_ts:
                dest_dir = _archive_month_dir(queue_done_dir, ts, orphan=True)
                for p in (order_path, result_path):
                    if p and p.exists():
                        dest = dest_dir / p.name
                        if dry_run:
                            print(f"[dry] archive orphan: {p.name} → {dest}")
                        else:
                            p.rename(dest)
                            print(f"archive orphan: {p.name} → {dest}")
                pruned_uuids.add(uuid)
                archived_orphan += 1

    # exec.md のエントリ除去
    pruned_exec = 0
    if exec_md.exists() and pruned_uuids:
        lines = exec_md.read_text(encoding="utf-8").splitlines(keepends=True)
        new_lines = []
        for line in lines:
            m = EXEC_LINE_RE.match(line.strip())
            if m and m.group(1).lower() in pruned_uuids:
                pruned_exec += 1
                if dry_run:
                    print(f"[dry] prune exec.md: {line.rstrip()[:80]}")
            else:
                new_lines.append(line)
        if pruned_exec > 0 and not dry_run:
            exec_md.write_text("".join(new_lines), encoding="utf-8")

    return CleanupResult(
        archived_done=archived_done,
        archived_orphan=archived_orphan,
        pruned_exec=pruned_exec,
    )


def _archive_month_dir(base: Path, ts_str: str, orphan: bool = False) -> Path:
    """queue-done/YYYY-MM/ または queue-done/YYYY-MM/orphan/ を返す（作成含む）。"""
    year, month = ts_str[:4], ts_str[4:6]
    d = base / f"{year}-{month}"
    if orphan:
        d = d / "orphan"
    d.mkdir(parents=True, exist_ok=True)
    return d
