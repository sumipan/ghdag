"""Document contracts for project identity and intake/execution/recording summaries.

Refs sumipan/nexus#6610, sumipan/nexus#7794.
"""

from __future__ import annotations

import re
from pathlib import Path

import tomllib

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LLMS = _REPO_ROOT / "llms.txt"
_PYPROJECT = _REPO_ROOT / "pyproject.toml"

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


def _llms_summary(text: str) -> str:
    for line in text.splitlines():
        if line.startswith(">"):
            return line
    return ""


def _matches_three_pillars(blob: str) -> bool:
    return bool(_INTAKE.search(blob) and _EXECUTION.search(blob) and _RECORDING.search(blob))


def test_project_summaries_share_intake_execution_recording_contract() -> None:
    """Refs sumipan/nexus#6610 — summaries describe intake, execution, recording; no graph-watcher."""
    llms_line = _llms_summary(_LLMS.read_text(encoding="utf-8"))
    pyproject = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    description = str(pyproject.get("project", {}).get("description", ""))

    for label, blob in (
        ("llms.txt summary", llms_line),
        ("pyproject description", description),
    ):
        assert _matches_three_pillars(blob), f"{label} missing intake/execution/recording themes"
        assert "graph-watcher" not in blob.lower(), f"{label} still mentions graph-watcher"
