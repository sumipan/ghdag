from __future__ import annotations

from pathlib import Path


class QueueArchiver:
    def __init__(self, archive_dir: Path, dry_run: bool = False) -> None:
        self._archive_dir = archive_dir
        self._dry_run = dry_run

    def archive_files(self, entry: dict, orphan: bool) -> list[tuple[Path, Path]]:
        """Move the files in entry to archive_dir/YYYY-MM/.

        Returns:
            List of (old path, new path) (empty when dry_run)
        """
        ts = entry["ts"]
        dest_dir = self._month_dir(ts, orphan=orphan)
        label = "orphan" if orphan else "done"
        moved: list[tuple[Path, Path]] = []
        for kind in ("order", "result", "stderr"):
            p: Path | None = entry.get(kind)
            if p and p.exists():
                dest = dest_dir / p.name
                if self._dry_run:
                    print(f"[dry] archive {label}: {p.name} → {dest}")
                else:
                    p.rename(dest)
                    print(f"archive {label}: {p.name} → {dest}")
                    moved.append((p, dest))
        return moved

    def _month_dir(self, ts_str: str, orphan: bool = False) -> Path:
        """Return archive/YYYY-MM/ or archive/YYYY-MM/orphan/ (creating it if needed)."""
        year, month = ts_str[:4], ts_str[4:6]
        d = self._archive_dir / f"{year}-{month}"
        if orphan:
            d = d / "orphan"
        d.mkdir(parents=True, exist_ok=True)
        return d
