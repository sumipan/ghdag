"""Document contracts for project identity, release alignment, and LocalForge quick start.

Refs sumipan/nexus#6610.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest
import tomllib

_REPO_ROOT = Path(__file__).resolve().parents[2]
_README = _REPO_ROOT / "README.md"
_LLMS = _REPO_ROOT / "llms.txt"
_PYPROJECT = _REPO_ROOT / "pyproject.toml"
_CHANGELOG = _REPO_ROOT / "CHANGELOG.md"

_LOCALFORGE_MARKER = "<!-- ghdag-localforge-quick-start -->"
_FENCE_START = re.compile(r"^```bash\s*$")
_FENCE_END = re.compile(r"^```\s*$")

_INTAKE = re.compile(
    r"(intake|ingest|watch|trigger|exec\.jsonl|MCP|enqueue)",
    re.IGNORECASE,
)
_EXECUTION = re.compile(
    r"(depend|dependency|DAG|ghdag run|local)",
    re.IGNORECASE,
)
_RECORDING = re.compile(
    r"(audit|done/|record|result file|jobs/)",
    re.IGNORECASE,
)


def _project_version() -> str:
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    version = data.get("project", {}).get("version")
    assert isinstance(version, str) and version, "pyproject.toml must define [project].version"
    return version


def _readme_lead(text: str, *, max_lines: int = 20) -> str:
    lines = text.splitlines()
    start = 1 if lines and lines[0].startswith("# ") else 0
    collected: list[str] = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        collected.append(line)
        if len(collected) >= max_lines:
            break
    return "\n".join(collected)


def _llms_summary(text: str) -> str:
    for line in text.splitlines():
        if line.startswith(">"):
            return line
    return ""


def _matches_three_pillars(blob: str) -> bool:
    return bool(_INTAKE.search(blob) and _EXECUTION.search(blob) and _RECORDING.search(blob))


def _extract_localforge_bash(readme: str) -> str:
    lines = readme.splitlines()
    for idx, line in enumerate(lines):
        if line.strip() != _LOCALFORGE_MARKER:
            continue
        block: list[str] = []
        in_fence = False
        for follow in lines[idx + 1 :]:
            if not in_fence and _FENCE_START.match(follow):
                in_fence = True
                continue
            if in_fence and _FENCE_END.match(follow):
                return "\n".join(block) + ("\n" if block else "")
            if in_fence:
                block.append(follow)
        break
    raise AssertionError(f"README missing fenced bash block after {_LOCALFORGE_MARKER!r}")


def test_project_summaries_share_intake_execution_recording_contract() -> None:
    """Refs sumipan/nexus#6610 — summaries describe intake, execution, recording; no graph-watcher."""
    readme_intro = _readme_lead(_README.read_text(encoding="utf-8"))
    llms_line = _llms_summary(_LLMS.read_text(encoding="utf-8"))
    pyproject = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    description = str(pyproject.get("project", {}).get("description", ""))

    for label, blob in (
        ("README intro", readme_intro),
        ("llms.txt summary", llms_line),
        ("pyproject description", description),
    ):
        assert _matches_three_pillars(blob), f"{label} missing intake/execution/recording themes"
        assert "graph-watcher" not in blob.lower(), f"{label} still mentions graph-watcher"


def test_readme_release_references_match_project_version() -> None:
    """Refs sumipan/nexus#6610 — README release strings track pyproject [project].version."""
    version = _project_version()
    text = _README.read_text(encoding="utf-8")

    badge = re.search(r"badge/version-v([0-9]+\.[0-9]+\.[0-9]+)", text)
    assert badge is not None, "README version badge missing"
    assert badge.group(1) == version

    status = re.search(
        r"Current release\s*\|\s*\*\*v([0-9]+\.[0-9]+\.[0-9]+)\*\*.*version = \"([0-9]+\.[0-9]+\.[0-9]+)\"",
        text,
    )
    assert status is not None, "README Status table release row missing"
    assert status.group(1) == version
    assert status.group(2) == version

    install = re.search(r"github\.com/sumipan/ghdag\.git@v([0-9]+\.[0-9]+\.[0-9]+)", text)
    assert install is not None, "README git+https install pin missing"
    assert install.group(1) == version

    stale = re.findall(r"v0\.125\.[0-9]+|0\.125\.[0-9]+", text)
    assert not stale, f"README still contains stale 0.125.x references: {stale}"


def test_localforge_quick_start_runs_documented_shell_block(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refs sumipan/nexus#6610 — documented LocalForge block runs one shell step without GitHub auth."""
    script = _extract_localforge_bash(_README.read_text(encoding="utf-8"))
    workdir = tmp_path / "quickstart"
    workdir.mkdir()

    for key in ("GITHUB_TOKEN", "GH_TOKEN", "GITHUB_REPOSITORIES"):
        monkeypatch.delenv(key, raising=False)

    env = {
        **{k: v for k, v in os.environ.items() if k not in ("GITHUB_TOKEN", "GH_TOKEN", "GITHUB_REPOSITORIES")},
        "GHDAG_QUICKSTART_WORKDIR": str(workdir),
        "PYTHONPATH": str(_REPO_ROOT / "src"),
    }

    completed = subprocess.run(
        ["bash", "-c", script],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, (
        f"LocalForge quick start failed\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )

    exec_jsonl = workdir / "jobs" / "exec.jsonl"
    assert exec_jsonl.is_file(), "expected jobs/exec.jsonl"
    lines = [ln for ln in exec_jsonl.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected one task line, got {len(lines)}"
    record = json.loads(lines[0])
    task_uuid = record["uuid"]

    done_path = workdir / "jobs" / "done" / task_uuid
    assert done_path.is_file(), f"missing done marker for {task_uuid}"
    assert done_path.read_text(encoding="utf-8").strip() == "0"

    assert (workdir / "quickstart.out").is_file(), "template output quickstart.out missing"


def test_execution_review_table_covers_artifacts_and_status_interfaces() -> None:
    """Refs sumipan/nexus#6610 — execution review table maps artifacts to inspectable facts."""
    text = _README.read_text(encoding="utf-8")
    start = text.find("## Reviewing task runs")
    assert start != -1, "README missing ## Reviewing task runs"
    section = text[start : text.find("\n## ", start + 1)]

    required = (
        "exec.jsonl",
        "done/<uuid>",
        "audit.jsonl",
        "result file",
        "stderr/<uuid>",
        "ghdag status",
        "ghdag_status",
    )
    missing = [item for item in required if item not in section]
    assert not missing, f"review table section missing: {missing}"

    rows = [ln for ln in section.splitlines() if ln.startswith("|") and "---" not in ln]
    assert len(rows) >= len(required) + 1, "expected header plus one row per artifact/interface"


def _unreleased_block(text: str) -> str:
    marker = "## [Unreleased]"
    assert marker in text, "CHANGELOG missing [Unreleased]"
    tail = text.split(marker, 1)[1]
    match = re.search(r"\n## \d", tail)
    return tail[: match.start()] if match else tail


def _current_change_block(text: str) -> str:
    """Return pending notes, or the shipped changelog section that cites #6610 (0.133.4)."""
    unreleased = _unreleased_block(text)
    if unreleased.strip():
        return unreleased

    issue_marker = "sumipan/nexus#6610"
    for match in re.finditer(
        r"^## [^\n]+\n(?P<body>.*?)(?=^## |\Z)",
        text,
        re.MULTILINE | re.DOTALL,
    ):
        if issue_marker in match.group("body"):
            return match.group("body")

    assert False, f"CHANGELOG missing a release section containing {issue_marker}"


def test_unreleased_changelog_records_identity_docs_update() -> None:
    """Refs sumipan/nexus#6610 — pending/current notes cover identity, version, LocalForge, review."""
    block = _current_change_block(_CHANGELOG.read_text(encoding="utf-8"))

    themes = (
        re.compile(r"identity|messaging|positioning", re.IGNORECASE),
        re.compile(r"version|release|README", re.IGNORECASE),
        re.compile(r"LocalForge|GitHub-free|without GitHub", re.IGNORECASE),
        re.compile(r"review|retrospect|artifact", re.IGNORECASE),
    )
    for pattern in themes:
        assert pattern.search(block), f"current change notes missing theme matching {pattern.pattern}"
