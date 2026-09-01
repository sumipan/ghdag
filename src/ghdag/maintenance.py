"""ghdag.maintenance — キュー検査・修復 API。"""

from __future__ import annotations

import json
from pathlib import Path

from ghdag.io.done import is_done as _is_done
from ghdag.io.done import mark_done as _mark_done


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
    from ghdag.io import exec_jsonl

    return exec_jsonl.validate(Path(exec_jsonl_path))


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
    from ghdag.io import exec_jsonl

    return exec_jsonl.repair(Path(exec_jsonl_path), dry_run=dry_run)


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
        done マーカーの書き込みには ``ghdag.io.done.mark_done`` を使用する（ops 層は dag に依存しない）。
    """
    base = Path(exec_jsonl_path).parent
    done_dir = Path(done_dir)

    restored = 0
    skipped = 0

    from ghdag.io import exec_jsonl

    text = exec_jsonl.read(Path(exec_jsonl_path))
    for raw in text.splitlines(keepends=True):
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

        if _is_done(done_dir, uuid):
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
            _mark_done(done_dir, uuid, status)
        restored += 1

    return {"restored": restored, "skipped": skipped}
