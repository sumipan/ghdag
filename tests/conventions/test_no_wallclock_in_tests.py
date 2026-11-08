"""Convention: docs/TEST.md T13 — tests must not depend on real wall-clock time.

AST scan of ``tests/**/*.py`` (excluding ``tests/conventions/``). Known files
live in ``known_wallclock_tests.txt`` (one repo-relative path per line). The
ledger may only shrink when wall-clock usage is removed, never grow.
"""
from __future__ import annotations

import ast
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 compatibility
    import tomli as tomllib

REPO_ROOT = Path(__file__).resolve().parents[2]
_LEDGER_FILE = Path(__file__).resolve().parent / "known_wallclock_tests.txt"
_CONVENTIONS_PREFIX = "tests/conventions/"

_SLEEP_KW = frozenset({"delay", "secs"})
_TIMEOUT_METHODS = frozenset({"communicate", "join", "wait", "result", "get"})
_TIME_CLOCK_ATTRS = frozenset({"monotonic", "perf_counter", "time"})
_DATETIME_NOW = frozenset({"now", "utcnow"})


def _positive_numeric_literal(node: ast.expr | None) -> bool:
    if node is None or not isinstance(node, ast.Constant):
        return False
    if isinstance(node.value, bool):
        return False
    if isinstance(node.value, (int, float)):
        return node.value > 0
    return False


def _attr_name_parts(node: ast.expr) -> list[str]:
    parts: list[str] = []
    cur: ast.expr = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        return list(reversed(parts))
    return []


def _sleep_has_positive_delay(call: ast.Call) -> bool:
    if call.args and _positive_numeric_literal(call.args[0]):
        return True
    return any(
        kw.arg in _SLEEP_KW and _positive_numeric_literal(kw.value) for kw in call.keywords
    )


def _has_positive_timeout_kw(call: ast.Call) -> bool:
    return any(kw.arg == "timeout" and _positive_numeric_literal(kw.value) for kw in call.keywords)


def _call_kind(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Attribute):
        chain = _attr_name_parts(func)
        if not chain:
            return None
        name = chain[-1]
        if name == "sleep" and len(chain) >= 2 and chain[-2] == "time":
            return "time.sleep"
        if name == "sleep" and chain[0] == "asyncio" and _sleep_has_positive_delay(call):
            return "asyncio.sleep"
        if name in _TIMEOUT_METHODS and _has_positive_timeout_kw(call):
            return f"{name}(timeout=...)"
        if len(chain) >= 2 and chain[-2] == "time" and name in _TIME_CLOCK_ATTRS:
            return f"time.{name}"
        if name in _DATETIME_NOW and "datetime" in chain:
            return f"datetime.{name}"
    elif isinstance(func, ast.Name) and func.id == "perf_counter":
        return "perf_counter"
    return None


def _find_wallclock_calls(source: str) -> list[tuple[int, str]]:
    candidates: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            kind = _call_kind(node)
            if kind is not None:
                candidates.append((node.lineno, kind))
    return sorted(candidates)


def _scan_test_files(root: Path) -> dict[str, list[tuple[int, str]]]:
    hits: dict[str, list[tuple[int, str]]] = {}
    tests_root = root / "tests"
    if not tests_root.is_dir():
        return hits
    for path in sorted(tests_root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if rel.startswith(_CONVENTIONS_PREFIX):
            continue
        source = path.read_text(encoding="utf-8")
        found = _find_wallclock_calls(source)
        if found:
            hits[rel] = found
    return hits


def _read_ledger(path: Path) -> set[str]:
    rows: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        rows.add(line)
    return rows


def _wallclock_file_set(root: Path | None = None) -> set[str]:
    root = root or REPO_ROOT
    return set(_scan_test_files(root))


def _ledger_violations(root: Path, ledger: Path) -> str:
    """Return the unlisted / stale report, or an empty string when in sync."""
    measured = _wallclock_file_set(root)
    known = _read_ledger(ledger)
    unlisted = sorted(measured - known)
    stale = sorted(known - measured)
    parts: list[str] = []
    if unlisted:
        parts.append(
            "Unlisted wall-clock test files (fix tests; do not grow the ledger):\n"
            + "\n".join(f"  {p}" for p in unlisted)
        )
    if stale:
        parts.append(
            "Stale paths in known_wallclock_tests.txt (remove these lines):\n"
            + "\n".join(f"  {p}" for p in stale)
        )
    return "\n\n".join(parts)


def test_positive_literal_waits_are_wallclock_candidates() -> None:
    source = (
        "import asyncio\n"
        "import subprocess\n"
        "import threading\n"
        "import time\n\n"
        "def test_waits():\n"
        "    time.sleep(0.1)\n"
        "    asyncio.sleep(1)\n"
        "    proc = subprocess.Popen(['true'])\n"
        "    proc.communicate(timeout=5)\n"
        "    thread = threading.Thread(target=lambda: None)\n"
        "    thread.start()\n"
        "    thread.join(timeout=2.0)\n"
    )
    assert _find_wallclock_calls(source) == [
        (7, "time.sleep"),
        (8, "asyncio.sleep"),
        (10, "communicate(timeout=...)"),
        (13, "join(timeout=...)"),
    ]


def test_unfixed_clock_calls_are_wallclock_candidates() -> None:
    source = (
        "import time\n"
        "from datetime import datetime\n"
        "from time import perf_counter\n\n"
        "def test_clocks():\n"
        "    time.monotonic()\n"
        "    time.perf_counter()\n"
        "    perf_counter()\n"
        "    time.time()\n"
        "    datetime.now()\n"
        "    datetime.utcnow()\n"
    )
    assert _find_wallclock_calls(source) == [
        (6, "time.monotonic"),
        (7, "time.perf_counter"),
        (8, "perf_counter"),
        (9, "time.time"),
        (10, "datetime.now"),
        (11, "datetime.utcnow"),
    ]


def test_nonliteral_waits_and_monkeypatch_references_are_not_candidates() -> None:
    source = (
        "import asyncio\n"
        "import time\n\n"
        "class Worker:\n"
        "    def wait(self, timeout=None):\n"
        "        pass\n\n"
        "def test_ok(asyncio_mod, timeout_value, monkeypatch):\n"
        "    asyncio.sleep(0)\n"
        "    Worker().wait(timeout=timeout_value)\n"
        "    monkeypatch.setattr(time, 'sleep', lambda _: None)\n"
    )
    assert _find_wallclock_calls(source) == []


def test_wallclock_policy_rejects_unlisted_sleep(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    test_path = root / "tests" / "test_new_wallclock.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(
        "import time\n\ndef test_x():\n    time.sleep(0.1)\n",
        encoding="utf-8",
    )
    ledger = root / "tests" / "conventions" / "known_wallclock_tests.txt"
    ledger.parent.mkdir(parents=True)
    ledger.write_text("# empty\n", encoding="utf-8")
    report = _ledger_violations(root, ledger)
    assert "Unlisted wall-clock test files" in report
    assert "  tests/test_new_wallclock.py" in report
    assert "Stale" not in report


def test_wallclock_ledger_entries_still_have_findings(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    test_path = root / "tests" / "test_stale_ledger.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    rel = "tests/test_stale_ledger.py"
    ledger = root / "tests" / "conventions" / "known_wallclock_tests.txt"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(f"{rel}\n", encoding="utf-8")
    report = _ledger_violations(root, ledger)
    assert report.startswith("Stale paths in known_wallclock_tests.txt")
    assert f"  {rel}" in report
    assert "Unlisted" not in report


def test_wallclock_known_files_match_scanner() -> None:
    """Files with wall-clock calls must match known_wallclock_tests.txt exactly."""
    report = _ledger_violations(REPO_ROOT, _LEDGER_FILE)
    assert not report, report


def test_pytest_timeout_is_the_only_suite_deadline() -> None:
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dev = data["project"]["optional-dependencies"]["dev"]
    assert sum(1 for dep in dev if dep.startswith("pytest-timeout")) == 1
    ini = data["tool"]["pytest"]["ini_options"]
    assert ini.get("timeout") == 300
