"""pipeline/wait.py — jobs/done/ polling ユーティリティ"""

from __future__ import annotations

import time
from pathlib import Path

from ghdag.io.done import read_done_content
from ghdag.pipeline.status import interpret_done


def wait_for_result(
    exec_done_dir: Path,
    uuid: str,
    *,
    timeout: float,
    poll_interval: float = 0.5,
) -> tuple[str, str]:
    """jobs/done/<uuid> ファイルの出現を polling し、完了ステータスを返す。

    Args:
        exec_done_dir: jobs/done/ ディレクトリのパス
        uuid: 待機対象のタスク UUID
        timeout: 最大待機秒数
        poll_interval: polling 間隔（秒）
    Returns:
        (status, raw_first_line) のタプル。
        status は interpret_done の結果:
          "success"     — exit code 0 または空
          "rejected"    — REJECTED / REJECTED_FINAL
          "engine_error" — ENGINE_ERROR / ENGINE_ERROR_FINAL
          "empty_result" — EMPTY_RESULT
          "failed_exit" — 非ゼロ exit code
          "other"       — 上記以外
    Raises:
        TimeoutError: timeout 秒以内に jobs/done/<uuid> が出現しなかった場合
    """
    deadline = time.monotonic() + timeout
    while True:
        raw = read_done_content(exec_done_dir, uuid)
        if raw is not None:
            status = interpret_done(raw)
            assert status is not None  # raw is not None => interpret_done never returns None
            lines = raw.strip().splitlines()
            first_line = lines[0].strip() if lines else ""
            return (status, first_line)

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"wait_for_result: {uuid} timed out after {timeout}s")
        time.sleep(min(poll_interval, remaining))
