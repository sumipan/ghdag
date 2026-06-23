from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ghdag.cleanup import QUEUE_FILE_RE, CleanupResult, file_timestamp
from ghdag.cleanup.archiver import QueueArchiver
from ghdag.cleanup.link_rewriter import LinkRewriter
from ghdag.cleanup.orphan_detector import OrphanDetector
from ghdag.cleanup.pruner import ExecJsonlPruner


def cleanup_queue(
    queue_dir: Path,
    archive_dir: Path,
    done_dir: Path,
    exec_md: Path,
    cutoff_days: int = 1,
    orphan_days: int = 7,
    dry_run: bool = False,
    auto_repair: bool = False,
) -> CleanupResult:
    """jobs/ ディレクトリのクリーンアップを実行する。

    Phase 1: exec.jsonl 起点のクリーンアップ（Case A〜F）
    Phase 2: exec.jsonl に存在しないファイルの sweep（OrphanDetector）
    Phase 3: QUEUE_FILE_RE 不一致ファイルの catch-all sweep
    """
    if not queue_dir.is_dir():
        print(f"error: jobs/ が存在しません: {queue_dir}", file=sys.stderr)
        sys.exit(1)

    now = datetime.now(timezone.utc)
    cutoff_ts = (now - timedelta(days=cutoff_days)).timestamp()
    orphan_ts = (now - timedelta(days=orphan_days)).timestamp()

    done_uuids: set[str] = set()
    if done_dir.is_dir():
        done_uuids = {p.name.lower() for p in done_dir.iterdir()}

    by_uuid: dict[str, dict] = {}
    for path in queue_dir.iterdir():
        if not path.is_file() or path.suffix != ".md":
            continue
        m = QUEUE_FILE_RE.match(path.name)
        if not m:
            continue
        ts, tool, kind, uuid_raw = m.groups()
        uuid = uuid_raw.lower()
        _rec = by_uuid.setdefault(uuid, {"ts": ts, "tool": tool})
        _rec[kind] = path

    archiver = QueueArchiver(archive_dir, dry_run)
    pruner = ExecJsonlPruner(exec_md, dry_run)

    exec_lines, all_exec_uuids = pruner.load()

    archived_done = 0
    archived_orphan = 0
    detected_orphan = 0
    detected_dead = 0
    all_moved: list[tuple[Path, Path]] = []
    prune_uuids: set[str] = set()
    detected_uuids: set[str] = set()
    deferred_done_deletes: set[str] = set()

    # ── Phase 1: exec.jsonl 起点のクリーンアップ ──────────────────────────
    for line in exec_lines:
        uuid = ExecJsonlPruner.extract_uuid(line)
        if not uuid:
            continue

        entry = by_uuid.get(uuid)
        is_done = uuid in done_uuids

        if is_done:
            if entry is not None:
                ref_path = entry.get("order") or entry.get("result") or entry.get("stderr")
                assert ref_path is not None
                mtime = file_timestamp(ref_path)
                if mtime <= cutoff_ts:
                    # Case A: old → archive + prune
                    all_moved.extend(archiver.archive_files(entry, orphan=False))
                    deferred_done_deletes.add(uuid)
                    prune_uuids.add(uuid)
                    archived_done += 1
                # else Case B: new → keep
            else:
                # Case C: done あり・ファイルなし（stuck）
                if dry_run:
                    print(f"[dry] prune stuck exec entry: {uuid}")
                prune_uuids.add(uuid)
        else:
            if entry is not None:
                ref_path = entry.get("order") or entry.get("result") or entry.get("stderr")
                assert ref_path is not None
                mtime = file_timestamp(ref_path)
                if mtime <= orphan_ts:
                    # Case D: old orphan
                    if auto_repair:
                        if not dry_run:
                            done_dir.mkdir(parents=True, exist_ok=True)
                            flag = done_dir / uuid
                            flag.write_text("ORPHAN_ARCHIVED", encoding="utf-8")
                        else:
                            print(f"[dry] create done marker (orphan): {uuid}")
                        all_moved.extend(archiver.archive_files(entry, orphan=True))
                        prune_uuids.add(uuid)
                        archived_orphan += 1
                    else:
                        files = [k for k in ("order", "result", "stderr") if entry.get(k)]
                        age_days = int((now.timestamp() - mtime) / 86400)
                        print(
                            f"[cleanup] ORPHAN detected: uuid={uuid}, files={files}, "
                            f"age={age_days}d (use --auto-repair to fix)",
                            file=sys.stderr,
                        )
                        detected_orphan += 1
                        detected_uuids.add(uuid)
                # else Case E: new → keep
            else:
                # Case F: done なし・ファイルなし（dead entry）
                if auto_repair:
                    if dry_run:
                        print(f"[dry] prune dead exec entry: {uuid}")
                    prune_uuids.add(uuid)
                else:
                    print(
                        f"[cleanup] DEAD_ENTRY detected: uuid={uuid} (use --auto-repair to fix)",
                        file=sys.stderr,
                    )
                    detected_dead += 1

    pruned_exec = pruner.prune(exec_lines, prune_uuids)

    for uuid in deferred_done_deletes:
        flag = done_dir / uuid
        if flag.exists():
            if dry_run:
                print(f"[dry] remove jobs/done: {uuid}")
            else:
                flag.unlink()

    # ── Phase 2: exec.jsonl に存在しないファイルの sweep ─────────────────
    active_uuids = all_exec_uuids - prune_uuids
    detector = OrphanDetector(queue_dir, done_dir, archiver, cutoff_ts, orphan_ts, dry_run)
    p2_done, p2_orphan, p2_moved = detector.sweep(by_uuid, active_uuids, done_uuids, prune_uuids)
    archived_done += p2_done
    archived_orphan += p2_orphan
    all_moved.extend(p2_moved)

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
        m = QUEUE_FILE_RE.match(path.name)
        if m and m.group(4).lower() in detected_uuids:
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

    rewriter = LinkRewriter(queue_dir, dry_run)
    rewriter.rewrite(all_moved)

    return CleanupResult(
        archived_done=archived_done,
        archived_orphan=archived_orphan,
        pruned_exec=pruned_exec,
        swept_extras=swept_extras,
        detected_orphan=detected_orphan,
        detected_dead=detected_dead,
    )
