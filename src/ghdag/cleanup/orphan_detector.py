from __future__ import annotations

from pathlib import Path

from ghdag.cleanup import file_timestamp
from ghdag.cleanup.archiver import QueueArchiver


class OrphanDetector:
    def __init__(
        self,
        queue_dir: Path,
        done_dir: Path,
        archiver: QueueArchiver,
        cutoff_ts: float,
        orphan_ts: float,
        dry_run: bool = False,
    ) -> None:
        self._queue_dir = queue_dir
        self._done_dir = done_dir
        self._archiver = archiver
        self._cutoff_ts = cutoff_ts
        self._orphan_ts = orphan_ts
        self._dry_run = dry_run

    def sweep(
        self,
        by_uuid: dict[str, dict],
        active_uuids: set[str],
        done_uuids: set[str],
        prune_uuids: set[str],
    ) -> tuple[int, int, list[tuple[Path, Path]]]:
        """exec.jsonl に存在しないファイルを走査し、条件に応じてアーカイブする。

        Returns:
            (archived_done, archived_orphan, moved_files)
        """
        archived_done = 0
        archived_orphan = 0
        all_moved: list[tuple[Path, Path]] = []

        for uuid, entry in by_uuid.items():
            if uuid in active_uuids:
                continue
            if uuid in prune_uuids:
                continue

            ref_path = entry.get("order") or entry.get("result") or entry.get("stderr")
            mtime = file_timestamp(ref_path)

            if uuid in done_uuids and mtime <= self._cutoff_ts:
                all_moved.extend(self._archiver.archive_files(entry, orphan=False))
                archived_done += 1
                flag = self._done_dir / uuid
                if flag.exists():
                    if self._dry_run:
                        print(f"[dry] remove jobs/done: {uuid}")
                    else:
                        flag.unlink()
            elif uuid not in done_uuids and mtime <= self._orphan_ts:
                if not self._dry_run:
                    self._done_dir.mkdir(parents=True, exist_ok=True)
                    flag = self._done_dir / uuid
                    flag.write_text("ORPHAN_ARCHIVED", encoding="utf-8")
                else:
                    print(f"[dry] create done marker (orphan): {uuid}")
                all_moved.extend(self._archiver.archive_files(entry, orphan=True))
                archived_orphan += 1

        return archived_done, archived_orphan, all_moved
