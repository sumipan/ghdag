"""ghdag dag cancel コマンド。"""

from __future__ import annotations

import sys
from pathlib import Path


def cmd_cancel(args) -> None:
    """ghdag dag cancel: jobs/cancel/<uuid> 制御ファイルを作成する（プロセスは触らない）。"""
    uuid = args.uuid.strip()
    queue_dir = Path(args.queue_dir).resolve()
    running_path = queue_dir / "running" / f"{uuid}.json"
    if not running_path.is_file():
        print(f"error: not running: {uuid}", file=sys.stderr)
        sys.exit(1)

    cancel_dir = queue_dir / "cancel"
    cancel_dir.mkdir(parents=True, exist_ok=True)
    cancel_path = cancel_dir / uuid
    cancel_path.write_text("", encoding="utf-8")
    print(f"cancel requested: {uuid}")
