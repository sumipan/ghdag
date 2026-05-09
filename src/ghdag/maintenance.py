"""ghdag.maintenance — キュー検査・修復 API。"""

from __future__ import annotations

import fcntl
import json
from pathlib import Path

from ghdag.dag.state import is_done, mark_done


def validate_exec_jsonl(exec_jsonl_path: Path) -> list[tuple[int, str]]:
    """exec.jsonl の各行を json.loads() で検証し、失敗した行を返す。

    Args:
        exec_jsonl_path: exec.jsonl のパス
    Returns:
        [(1始まり行番号, 不正行テキスト), ...]
        空行・空白のみの行はスキップし、報告対象外とする。
    Raises:
        FileNotFoundError: exec_jsonl_path が存在しない場合
    """
    if not Path(exec_jsonl_path).exists():
        raise FileNotFoundError(exec_jsonl_path)

    invalid: list[tuple[int, str]] = []
    with open(exec_jsonl_path, encoding="utf-8") as f:
        for lineno, raw in enumerate(f, start=1):
            stripped = raw.rstrip("\n")
            if not stripped.strip():
                continue
            try:
                json.loads(stripped)
            except json.JSONDecodeError:
                invalid.append((lineno, stripped))
    return invalid


def repair_exec_jsonl(exec_jsonl_path: Path, *, dry_run: bool = False) -> int:
    """json.loads() で解析できない行を exec.jsonl から除去する。

    Args:
        exec_jsonl_path: exec.jsonl のパス
        dry_run: True の場合、ファイルを変更せず除去対象行数のみ返す
    Returns:
        除去した（または除去対象の）行数
    Note:
        書き込み時は fcntl.LOCK_EX で排他ロックを取得する。
        空行・空白のみの行も除去対象とする。
    """
    p = Path(exec_jsonl_path)
    with open(p, encoding="utf-8") as f:
        lines = f.readlines()

    keep: list[str] = []
    removed = 0
    for raw in lines:
        stripped = raw.rstrip("\n")
        if not stripped.strip():
            removed += 1
            continue
        try:
            json.loads(stripped)
            keep.append(raw)
        except json.JSONDecodeError:
            removed += 1

    if removed == 0 or dry_run:
        return removed

    with open(p, "w", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.writelines(keep)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)

    return removed


def repair_jobs_done(
    exec_jsonl_path: Path,
    done_dir: Path,
    *,
    dry_run: bool = False,
) -> dict[str, int]:
    """exec.jsonl のエントリから done マーカーを復元する。

    Args:
        exec_jsonl_path: exec.jsonl のパス
        done_dir: done マーカーディレクトリのパス
        dry_run: True の場合、マーカーファイルを作成せず対象数のみ返す
    Returns:
        {"restored": int, "skipped": int}
    Note:
        result_path は exec_jsonl_path.parent を基準に相対パスを解決する。
        result_path が存在しないエントリは restored にも skipped にもカウントしない。
        done マーカーの書き込みには ghdag.dag.state.mark_done を使用する。
    """
    base = Path(exec_jsonl_path).parent
    done_dir = Path(done_dir)

    restored = 0
    skipped = 0

    with open(exec_jsonl_path, encoding="utf-8") as f:
        for raw in f:
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError:
                continue

            uuid = record.get("uuid")
            result_path_str = record.get("result_path")
            if not uuid or not result_path_str:
                continue

            result_path = base / result_path_str
            if not result_path.exists():
                continue

            if is_done(done_dir, uuid):
                skipped += 1
                continue

            content = result_path.read_text(encoding="utf-8")
            if content.startswith("REJECTED:"):
                status = "REJECTED"
            elif content == "":
                status = "EMPTY_RESULT"
            else:
                status = "0"

            if not dry_run:
                mark_done(done_dir, uuid, status)
            restored += 1

    return {"restored": restored, "skipped": skipped}
