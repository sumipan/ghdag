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
    pruned_exec: int      # exec から除去した行数
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


def _archive_files(
    entry: dict,
    archive_dir: Path,
    orphan: bool,
    dry_run: bool,
) -> None:
    """entry に含まれるファイルをアーカイブディレクトリへ移動する。"""
    ts = entry["ts"]
    dest_dir = _archive_month_dir(archive_dir, ts, orphan=orphan)
    label = "orphan" if orphan else "done"
    for kind in ("order", "result", "stderr"):
        p: Path | None = entry.get(kind)
        if p and p.exists():
            dest = dest_dir / p.name
            if dry_run:
                print(f"[dry] archive {label}: {p.name} → {dest}")
            else:
                p.rename(dest)
                print(f"archive {label}: {p.name} → {dest}")


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

    処理の起点は exec.jsonl（または exec.md）のエントリ。
    by_uuid（jobs/ のファイル索引）は lookup テーブルとして使用し、
    ループドライバーには使わない。これにより「done 済みだがファイルが
    既にアーカイブ済み」の stuck エントリを確実に除去できる。

    Phase 1: exec.jsonl 起点のクリーンアップ（Case A〜F）
      Case A: done ✓, files ✓, old  → archive + defer done delete + prune exec
      Case B: done ✓, files ✓, new  → keep
      Case C: done ✓, files ✗       → prune exec のみ（done marker は保持）
      Case D: done ✗, files ✓, old  → orphan archive + done marker 作成 + prune exec
      Case E: done ✗, files ✓, new  → keep
      Case F: done ✗, files ✗       → prune exec（dead entry）

    Phase 2: exec.jsonl に存在しないファイルの sweep
    Phase 3: QUEUE_FILE_RE 不一致ファイルの catch-all sweep

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

    # jobs/ のファイルを UUID ごとに収集（lookup テーブル）
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
        entry[kind] = path  # "order", "result", or "stderr"

    # exec.jsonl / exec.md を読み込む（Phase 1 のループドライバー）
    exec_lines: list[str] = []
    all_exec_uuids: set[str] = set()
    if exec_md.exists():
        exec_lines = exec_md.read_text(encoding="utf-8").splitlines(keepends=True)
        for line in exec_lines:
            uuid = _extract_uuid_from_line(line)
            if uuid:
                all_exec_uuids.add(uuid)

    archived_done = 0
    archived_orphan = 0
    prune_uuids: set[str] = set()
    # done マーカーの削除は exec prune 後に行う（削除順序の保証）
    deferred_done_deletes: set[str] = set()

    # ── Phase 1: exec.jsonl 起点のクリーンアップ ──────────────────────────
    for line in exec_lines:
        uuid = _extract_uuid_from_line(line)
        if not uuid:
            continue

        entry = by_uuid.get(uuid)  # None = jobs/ にファイルなし
        is_done = uuid in done_uuids

        if is_done:
            if entry is not None:
                # Case A / B: done あり・ファイルあり
                ref_path = entry.get("order") or entry.get("result") or entry.get("stderr")
                mtime = file_timestamp(ref_path)
                if mtime <= cutoff_ts:
                    # Case A: old → archive + prune
                    _archive_files(entry, archive_dir, orphan=False, dry_run=dry_run)
                    deferred_done_deletes.add(uuid)
                    prune_uuids.add(uuid)
                    archived_done += 1
                # else Case B: new → keep（何もしない）
            else:
                # Case C: done あり・ファイルなし（stuck）
                # 前回の cleanup でファイルはアーカイブ済みだが exec.jsonl に残留
                if dry_run:
                    print(f"[dry] prune stuck exec entry: {uuid}")
                prune_uuids.add(uuid)
                # done マーカーは保持（graph-watcher とのレースコンディションを避ける）
        else:
            if entry is not None:
                # Case D / E: done なし・ファイルあり
                ref_path = entry.get("order") or entry.get("result") or entry.get("stderr")
                mtime = file_timestamp(ref_path)
                if mtime <= orphan_ts:
                    # Case D: old → orphan archive + prune
                    if not dry_run:
                        done_dir.mkdir(parents=True, exist_ok=True)
                        flag = done_dir / uuid
                        flag.write_text("ORPHAN_ARCHIVED", encoding="utf-8")
                    else:
                        print(f"[dry] create done marker (orphan): {uuid}")
                    _archive_files(entry, archive_dir, orphan=True, dry_run=dry_run)
                    prune_uuids.add(uuid)
                    archived_orphan += 1
                # else Case E: new → keep（何もしない）
            else:
                # Case F: done なし・ファイルなし（dead entry）
                # ファイルが作成されないまま exec.jsonl に残留しているエントリを除去
                if dry_run:
                    print(f"[dry] prune dead exec entry: {uuid}")
                prune_uuids.add(uuid)

    # exec.md / exec.jsonl のエントリ除去
    pruned_exec = 0
    if exec_lines and prune_uuids:
        new_lines = []
        for line in exec_lines:
            uuid_in_line = _extract_uuid_from_line(line)
            if uuid_in_line and uuid_in_line in prune_uuids:
                pruned_exec += 1
                if dry_run:
                    print(f"[dry] prune exec entry: {line.rstrip()[:80]}")
            else:
                new_lines.append(line)
        if pruned_exec > 0 and not dry_run:
            exec_md.write_text("".join(new_lines), encoding="utf-8")

    # exec prune 完了後に done マーカーを削除する（順序保証）
    for uuid in deferred_done_deletes:
        flag = done_dir / uuid
        if flag.exists():
            if dry_run:
                print(f"[dry] remove jobs/done: {uuid}")
            else:
                flag.unlink()

    # ── Phase 2: exec.jsonl に存在しないファイルの sweep ─────────────────
    # exec.jsonl がない場合は all_exec_uuids = {} → 全ファイルを対象（AC9 互換）
    active_uuids = all_exec_uuids - prune_uuids

    for uuid, entry in by_uuid.items():
        if uuid in active_uuids:
            continue  # exec.jsonl にまだ存在するアクティブエントリ
        if uuid in prune_uuids:
            continue  # Phase 1 で処理済み（ファイルは既に移動）

        # exec.jsonl に存在しない孤立ファイル
        ref_path = entry.get("order") or entry.get("result") or entry.get("stderr")
        mtime = file_timestamp(ref_path)

        if uuid in done_uuids and mtime <= cutoff_ts:
            _archive_files(entry, archive_dir, orphan=False, dry_run=dry_run)
            archived_done += 1
            # Phase 2 では exec.jsonl 更新済みなので done マーカーを即時削除
            flag = done_dir / uuid
            if flag.exists():
                if dry_run:
                    print(f"[dry] remove jobs/done: {uuid}")
                else:
                    flag.unlink()
        elif uuid not in done_uuids and mtime <= orphan_ts:
            if not dry_run:
                done_dir.mkdir(parents=True, exist_ok=True)
                flag = done_dir / uuid
                flag.write_text("ORPHAN_ARCHIVED", encoding="utf-8")
            else:
                print(f"[dry] create done marker (orphan): {uuid}")
            _archive_files(entry, archive_dir, orphan=True, dry_run=dry_run)
            archived_orphan += 1

    # ── Phase 3: catch-all sweep — QUEUE_FILE_RE 不一致ファイルの一括アーカイブ ──
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
