"""Internal helper utilities for the DAG engine."""

from __future__ import annotations

import io
import json
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ghdag.core.reply_block import (
    ReplyBlock,
    ReplyBlockError,
    find_reply_block,
    parse_reply_block,
)
from ghdag.core.vocabulary import (
    PIPELINE_REPLY_BLOCK_FAILED,
    PIPELINE_STATUS_RE,
    PIPELINE_WAIT_QUOTA,
    WAIT_QUOTA_COMPANION_RE,
)

# Matches: tee [-a] "quoted path" or tee [-a] unquoted-path (any extension)
_TEE_RE = re.compile(r'\btee\s+(?:-a\s+)?(?:"([^"]+)"|(\S+))')


@dataclass(frozen=True)
class WaitQuotaSignal:
    after: datetime | None
    engines: tuple[str, ...]


@dataclass(frozen=True)
class WaitQuotaParseError:
    reason: str


def _parse_reply(content: str) -> ReplyBlock | ReplyBlockError | None:
    """Parse a reply block when present in the result body."""
    if find_reply_block(content) is None:
        return None
    try:
        return parse_reply_block(content)
    except ReplyBlockError as exc:
        return exc


def _pipeline_status_from_block(block: ReplyBlock) -> str | None:
    if block.status in ("ok", "rejected"):
        return None
    if block.status == "wait_quota":
        return PIPELINE_WAIT_QUOTA
    code = block.code or "REPLY"
    if code.endswith("_FAILED"):
        return code
    return f"{code}_FAILED"


def check_pipeline_status(result_path: str) -> str | None:
    """Read result file; prefer reply block, else legacy PIPELINE_STATUS line."""
    try:
        content = Path(result_path).read_text(encoding="utf-8", errors="replace")
    except (OSError, FileNotFoundError):
        return None
    parsed = _parse_reply(content)
    if isinstance(parsed, ReplyBlockError):
        return PIPELINE_REPLY_BLOCK_FAILED
    if isinstance(parsed, ReplyBlock):
        return _pipeline_status_from_block(parsed)
    matches = PIPELINE_STATUS_RE.findall(content)
    return matches[-1] if matches else None


def _wait_quota_from_block(
    block: ReplyBlock,
    *,
    known_engines: frozenset[str],
) -> WaitQuotaSignal | WaitQuotaParseError:
    if block.status != "wait_quota":
        return WaitQuotaParseError("reply block status is not wait_quota")
    unknown = [name for name in block.engines if name not in known_engines]
    if unknown:
        return WaitQuotaParseError(f"unknown engine(s): {', '.join(unknown)}")
    after = None
    if block.after is not None:
        try:
            after = datetime.fromisoformat(block.after.replace("Z", "+00:00"))
        except ValueError:
            return WaitQuotaParseError(f"invalid after datetime: {block.after}")
        if after.tzinfo is None or after.tzinfo.utcoffset(after) is None:
            return WaitQuotaParseError("after must be timezone-aware")
    return WaitQuotaSignal(after=after, engines=block.engines)


def parse_wait_quota_signal(
    content: str,
    *,
    known_engines: frozenset[str],
) -> WaitQuotaSignal | WaitQuotaParseError:
    """Parse WAIT_QUOTA protocol from a result file body."""
    parsed = _parse_reply(content)
    if isinstance(parsed, ReplyBlock):
        return _wait_quota_from_block(parsed, known_engines=known_engines)
    if isinstance(parsed, ReplyBlockError):
        return WaitQuotaParseError(parsed.message)

    if not PIPELINE_STATUS_RE.findall(content) or PIPELINE_STATUS_RE.findall(content)[-1] != "WAIT_QUOTA":
        return WaitQuotaParseError("missing PIPELINE_STATUS: WAIT_QUOTA")

    companion_matches = WAIT_QUOTA_COMPANION_RE.findall(content)
    if not companion_matches:
        return WaitQuotaParseError("missing WAIT_QUOTA companion line")

    after_raw, engines_raw = companion_matches[-1]
    after_raw = after_raw.strip()
    after: datetime | None
    if after_raw == "":
        after = None
    else:
        try:
            parsed = datetime.fromisoformat(after_raw.replace("Z", "+00:00"))
        except ValueError:
            return WaitQuotaParseError(f"invalid after datetime: {after_raw}")
        if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
            return WaitQuotaParseError("after must be timezone-aware")
        after = parsed

    parts = [part.strip() for part in engines_raw.split(",")]
    engines: list[str] = []
    seen: set[str] = set()
    for part in parts:
        if not part:
            continue
        if part in seen:
            continue
        seen.add(part)
        engines.append(part)

    if not engines:
        return WaitQuotaParseError("empty engines list")

    unknown = [name for name in engines if name not in known_engines]
    if unknown:
        return WaitQuotaParseError(f"unknown engine(s): {', '.join(unknown)}")

    return WaitQuotaSignal(after=after, engines=tuple(engines))


def parse_wait_quota_result_path(
    result_path: str,
    *,
    known_engines: frozenset[str],
) -> WaitQuotaSignal | WaitQuotaParseError:
    try:
        content = Path(result_path).read_text(encoding="utf-8", errors="replace")
    except (OSError, FileNotFoundError) as exc:
        return WaitQuotaParseError(f"cannot read result: {exc}")
    return parse_wait_quota_signal(content, known_engines=known_engines)


def _stderr_reader(proc: subprocess.Popen, buf: io.BytesIO) -> None:
    """Read stderr from proc into buf in a daemon thread."""
    stderr = proc.stderr
    if stderr is None:
        return
    try:
        for chunk in iter(lambda: stderr.read(4096), b""):
            buf.write(chunk)
    except (OSError, ValueError):
        pass
    finally:
        try:
            stderr.close()
        except (OSError, ValueError):
            pass


def _stdout_reader(proc: subprocess.Popen, buf: io.BytesIO) -> None:
    """Read stdout from proc into buf in a daemon thread."""
    stdout = proc.stdout
    if stdout is None:
        return
    try:
        for chunk in iter(lambda: stdout.read(4096), b""):
            buf.write(chunk)
    except (OSError, ValueError):
        pass
    finally:
        try:
            stdout.close()
        except (OSError, ValueError):
            pass


def _stdout_line_reader(
    proc: subprocess.Popen,
    buf: io.BytesIO,
    events_path: Path,
    on_event: Callable[[dict], None] | None = None,
) -> None:
    """Read stdout line-by-line into buf, appending each line to events_path.

    Used for stream-json engines (claude). Non-stream engines keep `_stdout_reader`.
    No fsync needed (append-only). on_event is called only for lines that parse as JSON.
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
        command: The task command string
        result_path: Task.result_path (when explicitly specified in JSONL format)

    Returns:
        The result file path, or None if it cannot be determined
    """
    if result_path is not None:
        return result_path
    m = _TEE_RE.search(command)
    if not m:
        return None
    # group(1) = quoted path content, group(2) = unquoted path
    return m.group(1) if m.group(1) is not None else m.group(2)


def default_check_rejected(result_path: str) -> bool:
    """Return True when the result is rejected (reply block or legacy REJECTED: line)."""
    try:
        content = Path(result_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    parsed = _parse_reply(content)
    if isinstance(parsed, ReplyBlock):
        return parsed.status == "rejected"
    if isinstance(parsed, ReplyBlockError):
        return False
    for line in content.splitlines()[:10]:
        stripped = line.strip()
        if stripped.startswith("REJECTED:"):
            return True
        if stripped.startswith("ACCEPTED"):
            return False
    return False
