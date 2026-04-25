"""DAG monitoring logic — parse exec.md / exec-done and build display rows.

Ported from tools/dag_system/core.py for use in the ghdag Web UI.
"""

from __future__ import annotations

import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

QUEUE_TS = re.compile(r"queue/(\d{14})")

# State labels
STATE_PENDING_DEPS = "待機（依存未充足）"
STATE_PENDING_RUN = "待機（実行可能）"
STATE_RUNNING = "実行中"
STATE_OK = "完了（成功）"
STATE_FAIL = "完了（失敗）"
STATE_REJECTED = "完了（REJECTED）"
STATE_EMPTY = "完了（EMPTY_RESULT）"
STATE_UNKNOWN_DONE = "完了（その他）"

STATE_ALIASES = {
    "pending_deps": STATE_PENDING_DEPS,
    "pending_run": STATE_PENDING_RUN,
    "running": STATE_RUNNING,
    "ok": STATE_OK,
    "success": STATE_OK,
    "fail": STATE_FAIL,
    "failed": STATE_FAIL,
    "rejected": STATE_REJECTED,
    "empty": STATE_EMPTY,
    "empty_result": STATE_EMPTY,
    "unknown": STATE_UNKNOWN_DONE,
}


@dataclass
class MonitorTask:
    uuid: str
    command: str
    depends: set
    retry: int = 0


@dataclass
class Row:
    uuid: str
    state: str
    cmd_preview: str
    tree_ts: str = ""
    engine_model: str = ""
    order_path: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _parse_ts14(s: str) -> datetime:
    return datetime.strptime(s, "%Y%m%d%H%M%S")


def max_ts_in_command(cmd: str) -> Optional[datetime]:
    best: Optional[datetime] = None
    for m in QUEUE_TS.finditer(cmd):
        try:
            t = _parse_ts14(m.group(1))
        except ValueError:
            continue
        if best is None or t > best:
            best = t
    return best


def parse_exec_md(path: str) -> tuple[dict[str, MonitorTask], list[str]]:
    tasks: dict[str, MonitorTask] = {}
    file_order: list[str] = []
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return tasks, file_order

    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        m = re.match(r"^([a-fA-F0-9\-]+)((?:\[[^\]]+\])*)\s*:\s*(.+)$", line)
        if not m:
            continue

        uuid = m.group(1).strip()
        annotations = m.group(2)
        command = m.group(3).strip()

        depends_m = re.search(r"\[depends:([^\]]+)\]", annotations)
        depends = set(d.strip() for d in depends_m.group(1).split(",")) if depends_m else set()

        retry_m = re.search(r"\[retry:(\d+)\]", annotations)
        retry = int(retry_m.group(1)) if retry_m else 0

        if uuid not in tasks:
            file_order.append(uuid)
        tasks[uuid] = MonitorTask(uuid=uuid, command=command, depends=depends, retry=retry)

    return tasks, file_order


def topo_sort_tasks(tasks: dict[str, MonitorTask], file_order: list[str]) -> list[str]:
    file_idx = {u: i for i, u in enumerate(file_order)}
    in_deg = {u: sum(1 for d in tasks[u].depends if d in tasks) for u in tasks}
    rev: dict[str, list[str]] = defaultdict(list)
    for u, t in tasks.items():
        for d in t.depends:
            if d in tasks:
                rev[d].append(u)
    for d in rev:
        rev[d].sort(key=lambda x: file_idx.get(x, 10**9))

    ready = [u for u in tasks if in_deg[u] == 0]
    ready.sort(key=lambda x: file_idx.get(x, 10**9))
    result: list[str] = []
    while ready:
        u = ready.pop(0)
        result.append(u)
        for v in rev.get(u, []):
            in_deg[v] -= 1
            if in_deg[v] == 0:
                ready.append(v)
                ready.sort(key=lambda x: file_idx.get(x, 10**9))
    rest = [u for u in tasks if u not in result]
    rest.sort(key=lambda x: file_idx.get(x, 10**9))
    return result + rest


def ts_display(cmd: str) -> str:
    t = max_ts_in_command(cmd)
    if t is None:
        return "\u2014"
    return t.strftime("%Y-%m-%d %H:%M")


def read_done_content(exec_done_dir: Path, uuid: str) -> Optional[str]:
    p = exec_done_dir / uuid
    if not p.is_file():
        return None
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return None


def interpret_done(raw: Optional[str]) -> Optional[str]:
    if raw is None:
        return None
    first = raw.strip().splitlines()
    c = first[0].strip() if first else ""
    if c == "" or c == "0":
        return "success"
    if c in ("REJECTED", "REJECTED_FINAL"):
        return "rejected"
    if c == "EMPTY_RESULT":
        return "empty_result"
    try:
        n = int(c)
        return "success" if n == 0 else "failed_exit"
    except ValueError:
        return "other"


def dep_succeeded(exec_done_dir: Path, dep_uuid: str) -> bool:
    return interpret_done(read_done_content(exec_done_dir, dep_uuid)) == "success"


def label_for_done(raw: Optional[str]) -> Optional[str]:
    if raw is None:
        return None
    kind = interpret_done(raw)
    if kind == "success":
        return STATE_OK
    if kind == "failed_exit":
        return STATE_FAIL
    if kind == "rejected":
        return STATE_REJECTED
    if kind == "empty_result":
        return STATE_EMPTY
    return STATE_UNKNOWN_DONE


def task_state(
    uuid: str,
    task_depends: set[str],
    exec_done_dir: Path,
    running_uuids: Optional[set[str]] = None,
) -> str:
    raw = read_done_content(exec_done_dir, uuid)
    if raw is not None:
        lbl = label_for_done(raw)
        return lbl if lbl else STATE_UNKNOWN_DONE

    for d in task_depends:
        if not dep_succeeded(exec_done_dir, d):
            return STATE_PENDING_DEPS
    if running_uuids and uuid in running_uuids:
        return STATE_RUNNING
    return STATE_PENDING_RUN


def _ps_command_blob() -> str:
    for argv in (
        ["ps", "auxww"],
        ["ps", "ax", "-o", "command="],
    ):
        try:
            r = subprocess.run(argv, capture_output=True, text=True, timeout=15, check=False)
            if r.returncode == 0 and (r.stdout or "").strip():
                return r.stdout
        except (OSError, subprocess.TimeoutExpired):
            continue
    return ""


def running_uuids_from_ps(uuids: Iterable[str]) -> set[str]:
    lower = _ps_command_blob().lower()
    return {u for u in uuids if u.lower() in lower}


def extract_engine_model(cmd: str) -> str:
    m = re.search(r'\bclaude\s+--model\s+[\'"]?(\S+?)[\'"]?\s', cmd)
    if m:
        short = re.sub(r'^claude-', '', m.group(1))
        return f"claude/{short}"
    if re.search(r'\bclaude\s+-p\b', cmd):
        return "claude"

    m = re.search(r'\bgemini\s+.*?-m\s+(\S+)', cmd)
    if m:
        return f"gemini/{m.group(1)}"
    if re.search(r'\bgemini\s+-p\b', cmd):
        return "gemini"

    m = re.search(r'\bagent\s+--model\s+[\'"]?(\S+?)[\'"]?\s', cmd)
    if m:
        return f"cursor/{m.group(1)}"
    if re.search(r'\bagent\s+-p\b', cmd):
        return "cursor"

    return ""


_ORDER_PATH_RE = re.compile(r"queue/\S+-order-\S+\.md")
_TASK_NAME_MAX = 30


def extract_order_path(cmd: str) -> str:
    m = _ORDER_PATH_RE.search(cmd)
    return m.group(0) if m else ""


def order_task_name(cmd: str, repo_root: Path) -> Optional[str]:
    m = _ORDER_PATH_RE.search(cmd)
    if not m:
        return None
    order_path = repo_root / m.group(0)
    try:
        first = order_path.read_text(encoding="utf-8").splitlines()[0].strip()
    except (OSError, IndexError):
        return None
    if not first or first.startswith("#"):
        return None
    if len(first) <= _TASK_NAME_MAX:
        return first
    return None


def cmd_preview(cmd: str, n: int = 48, repo_root: Optional[Path] = None) -> str:
    if repo_root is not None:
        name = order_task_name(cmd, repo_root)
        if name:
            return name
    one = " ".join(cmd.split())
    return one if len(one) <= n else one[: n - 1] + "\u2026"


def _rows_with_tree_layout(
    tasks: dict[str, MonitorTask],
    file_order: list[str],
    pending: dict[str, Row],
) -> list[Row]:
    if not pending:
        return []
    topo_order = topo_sort_tasks(tasks, file_order)
    topo_pos = {u: i for i, u in enumerate(topo_order)}
    file_idx = {u: i for i, u in enumerate(file_order)}
    visible = set(pending.keys())

    def root_sort_key(u: str) -> tuple:
        t = max_ts_in_command(tasks[u].command)
        fi = file_idx.get(u, 10**9)
        if t is None:
            return (1, fi)
        return (0, t, fi)

    def primary_visible(task: MonitorTask) -> Optional[str]:
        deps = [d for d in task.depends if d in visible]
        if not deps:
            return None
        return max(deps, key=lambda d: topo_pos[d])

    children: dict[str, list[str]] = defaultdict(list)
    roots: list[str] = []
    for u in topo_order:
        if u not in visible:
            continue
        p = primary_visible(tasks[u])
        if p is None:
            roots.append(u)
        else:
            children[p].append(u)

    roots.sort(key=root_sort_key)

    if not roots and visible:
        children = {}
        roots = sorted(visible, key=root_sort_key)

    rows: list[Row] = []

    def dfs(u: str, base_prefix: str, is_last_child: bool, is_root: bool) -> None:
        task = tasks[u]
        ts = ts_display(task.command)
        if is_root:
            left = ts
        else:
            conn = "\u2514\u2500\u2500 " if is_last_child else "\u251c\u2500\u2500 "
            left = base_prefix + conn + ts
        r = pending[u]
        rows.append(
            Row(
                uuid=r.uuid,
                state=r.state,
                cmd_preview=r.cmd_preview,
                tree_ts=left,
                engine_model=r.engine_model,
                order_path=r.order_path,
            )
        )
        ch = children.get(u, [])
        for i, c in enumerate(ch):
            last = i == len(ch) - 1
            nb = "" if is_root else (base_prefix + ("    " if is_last_child else "\u2502   "))
            dfs(c, nb, last, False)

    for root in roots:
        dfs(root, "", False, True)

    return rows


def build_rows(
    repo_root: Path,
    *,
    cmd_preview_len: int = 48,
    running_uuids_override: Optional[set[str]] = None,
    detect_running: bool = True,
) -> tuple[list[Row], dict[str, MonitorTask], list[str]]:
    exec_md = repo_root / "queue" / "exec.md"
    exec_done_dir = repo_root / "exec-done"
    tasks, file_order = parse_exec_md(str(exec_md))
    if not tasks:
        return [], tasks, file_order

    if running_uuids_override is not None:
        run_set = running_uuids_override
    elif detect_running:
        run_set = running_uuids_from_ps(tasks.keys())
    else:
        run_set = set()

    pending: dict[str, Row] = {}
    for uuid, task in tasks.items():
        st = task_state(uuid, task.depends, exec_done_dir, run_set)
        pending[uuid] = Row(
            uuid=uuid,
            state=st,
            cmd_preview=cmd_preview(task.command, n=cmd_preview_len, repo_root=repo_root),
            tree_ts="",
            engine_model=extract_engine_model(task.command),
            order_path=extract_order_path(task.command),
        )

    rows = _rows_with_tree_layout(tasks, file_order, pending)
    return rows, tasks, file_order


def relayout_tree_for_visible_rows(
    rows: list[Row],
    tasks: dict[str, MonitorTask],
    file_order: list[str],
) -> list[Row]:
    if not rows:
        return rows
    pending = {r.uuid: r for r in rows}
    return _rows_with_tree_layout(tasks, file_order, pending)


def _recency_sort_key(
    row: Row,
    tasks: dict[str, MonitorTask],
    file_idx: dict[str, int],
) -> tuple:
    t = max_ts_in_command(tasks[row.uuid].command)
    fi = float(file_idx.get(row.uuid, -1.0))
    if t is None:
        return (0, 0.0, fi)
    return (1, t.timestamp(), fi)


def apply_default_monitor_filters(
    rows: list[Row],
    tasks: dict[str, MonitorTask],
    file_order: list[str],
    *,
    full: bool,
    max_visible: int,
) -> tuple[list[Row], str]:
    total = len(rows)
    if full:
        return rows, ""

    file_idx = {u: i for i, u in enumerate(file_order)}
    cap = max(1, int(max_visible))
    sorted_rows = sorted(
        rows,
        key=lambda r: _recency_sort_key(r, tasks, file_idx),
        reverse=True,
    )
    out = sorted_rows[:cap]
    hidden = total - len(out)
    note = f"{total} tasks total, showing newest {len(out)}" if hidden > 0 else ""
    return out, note


def filter_rows(
    rows: list[Row],
    uuid_prefix: Optional[str],
    states: Optional[set[str]],
) -> list[Row]:
    out = rows
    if uuid_prefix:
        p = uuid_prefix.lower()
        out = [r for r in out if r.uuid.lower().startswith(p)]
    if states:
        resolved = set()
        for s in states:
            resolved.add(STATE_ALIASES.get(s.lower(), s))
        out = [r for r in out if r.state in resolved]
    return out
