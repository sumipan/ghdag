"""ghdag cleanup — queue ディレクトリのクリーンアップロジック。"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

# queue/[ts]-[tool]-order/result/stderr-[UUID].md
QUEUE_FILE_RE = re.compile(
    r"^(\d{14})-([\w-]+)-(order|result|stderr)-([a-fA-F0-9\-]{36})\.md$"
)
# exec.md の行: UUID[depends:...]: command
EXEC_LINE_RE = re.compile(r"^([a-fA-F0-9\-]+)(?:\[depends:[^\]]+\])?\s*:")


@dataclass
class CleanupResult:
    archived_done: int    # 完了済みアーカイブ件数
    archived_orphan: int  # 孤立アーカイブ件数
    pruned_exec: int      # exec.md から除去した行数
    swept_extras: int = 0  # sweep フェーズでアーカイブした件数


def file_timestamp(path: Path) -> float:
    """ファイルのタイムスタンプを返す。

    macOS では st_birthtime（作成日時）を優先し、
    取得できない環境では st_mtime（更新日時）にフォールバックする。
    """
    try:
        st = path.stat()
    except OSError:
        return 0.0
    return getattr(st, "st_birthtime", st.st_mtime)


def _extract_uuid_from_line(line: str) -> str | None:
    """exec.md / exec.jsonl 両形式から UUID を抽出する。"""
    stripped = line.strip()
    if not stripped:
        return None
    # JSONL 形式
    if stripped.startswith("{"):
        try:
            obj = json.loads(stripped)
            uuid = obj.get("uuid", "")
            return uuid.lower() or None
        except (json.JSONDecodeError, AttributeError):
            return None
    # exec.md 形式（後方互換）
    m = EXEC_LINE_RE.match(stripped)
    return m.group(1).lower() if m else None


def cleanup_queue(
    queue_dir: Path,
    archive_dir: Path,
    done_dir: Path,
    exec_md: Path,
    cutoff_days: int = 1,
    orphan_days: int = 7,
    dry_run: bool = False,
) -> CleanupResult:
    """jobs/ ディレクトリのクリーンアップを実行する。

    Args:
        queue_dir: jobs/ ディレクトリのパス
        archive_dir: jobs/archive/ ディレクトリのパス
        done_dir: jobs/done/ ディレクトリのパス
        exec_md: exec ファイルのパス（exec.jsonl または exec.md）
        cutoff_days: 完了タスクをアーカイブするまでの日数
        orphan_days: 未完了タスクを孤立扱いにする日数
        dry_run: True の場合、対象を表示するのみで変更しない

    Returns:
        CleanupResult: アーカイブ件数と除去エントリ数
    """
    if not queue_dir.is_dir():
        print(f"error: jobs/ が存在しません: {queue_dir}", file=sys.stderr)
        sys.exit(1)

    now = datetime.now(timezone.utc)
    cutoff_ts = (now - timedelta(days=cutoff_days)).timestamp()
    orphan_ts = (now - timedelta(days=orphan_days)).timestamp()

    # jobs/done/ フラグ収集
    done_uuids: set[str] = set()
    if done_dir.is_dir():
        done_uuids = {p.name.lower() for p in done_dir.iterdir()}

    # jobs/ のファイルを UUID ごとに収集
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
    # done マーカーの削除は exec prune 後に行う（AC3: 削除順序の保証）
    deferred_done_deletes: set[str] = set()

    for uuid, entry in by_uuid.items():
        order_path: Path | None = entry.get("order")
        result_path: Path | None = entry.get("result")
        stderr_path: Path | None = entry.get("stderr")
        ts = entry["ts"]
        ref_path = order_path or result_path or stderr_path
        mtime = file_timestamp(ref_path)

        if uuid in done_uuids:
            # 完了済み: cutoff を過ぎていたらアーカイブ
            if mtime <= cutoff_ts:
                dest_dir = _archive_month_dir(archive_dir, ts, orphan=False)
                for p in (order_path, result_path, stderr_path):
                    if p and p.exists():
                        dest = dest_dir / p.name
                        if dry_run:
                            print(f"[dry] archive done: {p.name} → {dest}")
                        else:
                            p.rename(dest)
                            print(f"archive done: {p.name} → {dest}")
                # done マーカー削除は exec prune 後に defer する（AC3）
                deferred_done_deletes.add(uuid)
                pruned_uuids.add(uuid)
                archived_done += 1
        else:
            # 未完了: orphan_days を過ぎていたら孤立アーカイブ
            if mtime <= orphan_ts:
                # ファイル移動前に done マーカーを付与する（AC2）
                if not dry_run:
                    done_dir.mkdir(parents=True, exist_ok=True)
                    flag = done_dir / uuid
                    flag.write_text("ORPHAN_ARCHIVED", encoding="utf-8")
                else:
                    print(f"[dry] create done marker (orphan): {uuid}")
                dest_dir = _archive_month_dir(archive_dir, ts, orphan=True)
                for p in (order_path, result_path, stderr_path):
                    if p and p.exists():
                        dest = dest_dir / p.name
                        if dry_run:
                            print(f"[dry] archive orphan: {p.name} → {dest}")
                        else:
                            p.rename(dest)
                            print(f"archive orphan: {p.name} → {dest}")
                pruned_uuids.add(uuid)
                archived_orphan += 1

    # exec.md / exec.jsonl のエントリ除去（AC1: JSONL 対応・AC5: exec.md 後方互換）
    pruned_exec = 0
    if exec_md.exists() and pruned_uuids:
        lines = exec_md.read_text(encoding="utf-8").splitlines(keepends=True)
        new_lines = []
        for line in lines:
            uuid_in_line = _extract_uuid_from_line(line)
            if uuid_in_line and uuid_in_line in pruned_uuids:
                pruned_exec += 1
                if dry_run:
                    print(f"[dry] prune exec.md: {line.rstrip()[:80]}")
            else:
                new_lines.append(line)
        if pruned_exec > 0 and not dry_run:
            exec_md.write_text("".join(new_lines), encoding="utf-8")

    # exec prune 完了後に done マーカーを削除する（AC3: 順序保証）
    for uuid in deferred_done_deletes:
        flag = done_dir / uuid
        if flag.exists():
            if dry_run:
                print(f"[dry] remove jobs/done: {uuid}")
            else:
                flag.unlink()

    # Phase 2: catch-all sweep — QUEUE_FILE_RE 不一致ファイルの一括アーカイブ
    _SWEEP_WHITELIST_SUFFIXES = {".jsonl"}
    _SWEEP_WHITELIST_NAMES = {".gitkeep", ".ghdag.lock"}
    swept_extras = 0
    for path in queue_dir.iterdir():
        if not path.is_file():
            continue
        if path.suffix in _SWEEP_WHITELIST_SUFFIXES:
            continue
        if path.name in _SWEEP_WHITELIST_NAMES:
            continue
        mtime = file_timestamp(path)
        if mtime > orphan_ts:
            continue
        mtime_dt = datetime.fromtimestamp(mtime, tz=timezone.utc)
        yyyymm = f"{mtime_dt.year}-{mtime_dt.month:02d}"
        dest_dir = archive_dir / yyyymm / "extras"
        if dry_run:
            print(f"[dry] sweep extras: {path.name} → {dest_dir / path.name}")
        else:
            dest_dir.mkdir(parents=True, exist_ok=True)
            path.rename(dest_dir / path.name)
            print(f"sweep extras: {path.name} → {dest_dir / path.name}")
        swept_extras += 1

    return CleanupResult(
        archived_done=archived_done,
        archived_orphan=archived_orphan,
        pruned_exec=pruned_exec,
        swept_extras=swept_extras,
    )


def _archive_month_dir(base: Path, ts_str: str, orphan: bool = False) -> Path:
    """archive/YYYY-MM/ または archive/YYYY-MM/orphan/ を返す（作成含む）。"""
    year, month = ts_str[:4], ts_str[4:6]
    d = base / f"{year}-{month}"
    if orphan:
        d = d / "orphan"
    d.mkdir(parents=True, exist_ok=True)
    return d
