"""Internal helper utilities for the DAG engine."""

from __future__ import annotations

import io
import json
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

from ghdag.core.vocabulary import PIPELINE_STATUS_RE

# Matches: tee [-a] "quoted path" or tee [-a] unquoted-path (any extension)
_TEE_RE = re.compile(r'\btee\s+(?:-a\s+)?(?:"([^"]+)"|(\S+))')


def check_pipeline_status(result_path: str) -> "str | None":
    """result ファイルから PIPELINE_STATUS 行を探し、最後にマッチした値を返す。

    Returns:
        マッチしたステータス文字列（例: "IMPL_FAILED"）。なければ None。
    """
    try:
        content = Path(result_path).read_text(encoding="utf-8", errors="replace")
    except (OSError, FileNotFoundError):
        return None
    matches = PIPELINE_STATUS_RE.findall(content)
    return matches[-1] if matches else None


def _stderr_reader(proc: subprocess.Popen, buf: io.BytesIO) -> None:
    """Read stderr from proc into buf in a daemon thread."""
    try:
        for chunk in iter(lambda: proc.stderr.read(4096), b""):
            buf.write(chunk)
    except (OSError, ValueError):
        pass
    finally:
        try:
            proc.stderr.close()
        except (OSError, ValueError):
            pass


def _stdout_reader(proc: subprocess.Popen, buf: io.BytesIO) -> None:
    """Read stdout from proc into buf in a daemon thread."""
    try:
        for chunk in iter(lambda: proc.stdout.read(4096), b""):
            buf.write(chunk)
    except (OSError, ValueError):
        pass
    finally:
        try:
            proc.stdout.close()
        except (OSError, ValueError):
            pass


def _stdout_line_reader(
    proc: subprocess.Popen,
    buf: io.BytesIO,
    events_path: Path,
    on_event: Callable[[dict], None] | None = None,
) -> None:
    """Read stdout line-by-line into buf, appending each line to events_path.

    Used for stream engines (claude / cursor / codex). Non-stream engines keep `_stdout_reader`.
    fsync は不要（追記のみ）。on_event は JSON としてパースできた行だけ呼ばれる。
    行が最終 result か進捗かは各 EngineOutputAdapter.is_terminal_result_event で判定できる。
    """
    try:
        stdout = proc.stdout
        if stdout is None:
            return
        events_path.parent.mkdir(parents=True, exist_ok=True)
        with open(events_path, "ab") as events_f:
            while True:
                line = stdout.readline()
                if not line:
                    break
                buf.write(line)
                events_f.write(line)
                events_f.flush()
                if on_event is None:
                    continue
                try:
                    text = line.decode("utf-8", errors="replace").strip()
                    if not text:
                        continue
                    obj = json.loads(text)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if isinstance(obj, dict):
                    try:
                        on_event(obj)
                    except Exception:
                        pass
    except (OSError, ValueError):
        pass
    finally:
        try:
            if proc.stdout is not None:
                proc.stdout.close()
        except (OSError, ValueError):
            pass


def _extract_tee_target(command: str, result_path: str | None = None) -> str | None:
    """Extract the tee output path from a command string.

    Args:
        command: タスクのコマンド文字列
        result_path: Task.result_path（JSONL 形式で明示指定された場合）

    Returns:
        結果ファイルパス。取得できなければ None
    """
    if result_path is not None:
        return result_path
    m = _TEE_RE.search(command)
    if not m:
        return None
    # group(1) = quoted path content, group(2) = unquoted path
    return m.group(1) if m.group(1) is not None else m.group(2)


def default_check_rejected(result_path: str) -> bool:
    """Check if result file contains PIPELINE_STATUS: REJECTED."""
    try:
        with open(result_path, encoding="utf-8") as f:
            for _ in range(10):
                line = f.readline()
                if line == "":
                    break
                stripped = line.strip()
                if stripped.startswith("REJECTED:"):
                    return True
                if stripped.startswith("ACCEPTED"):
                    return False
        return False
    except OSError:
        return False
