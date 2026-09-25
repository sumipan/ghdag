"""Convention test: src/ghdag must not contain downstream vocabulary."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_VOCAB = re.compile(r"issuesmith|mltgnt|nexus|diary|persona", re.IGNORECASE)
_ISSUE_REF = re.compile(r"(?:sumipan/)?nexus(?:\s+[Ii]ssue)?\s*#\d+")

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC = _REPO_ROOT / "src" / "ghdag"

_KNOWN_VIOLATIONS: frozenset[str] = frozenset({
    "audit/span.py",
    "cli/main.py",
    "config/env.py",
    "core/models/workflow.py",
    "dag/task_launcher.py",
    "github_client.py",
    "llm/compaction.py",
    "pipeline/order.py",
})


def downstream_vocabulary(source: str) -> list[int]:
    """Return 1-indexed line numbers containing downstream vocabulary.

    Issue references matching ``(sumipan/)?nexus #N`` are stripped before
    checking so that cross-repo citations do not trigger a violation.
    """
    lines: list[int] = []
    for i, line in enumerate(source.splitlines(), 1):
        cleaned = _ISSUE_REF.sub("", line)
        if _VOCAB.search(cleaned):
            lines.append(i)
    return lines


def _source_files() -> list[Path]:
    return sorted(_SRC.rglob("*.py"))


@pytest.mark.parametrize(
    "path",
    [
        pytest.param(
            p,
            marks=pytest.mark.xfail(strict=True, reason="sumipan/nexus#3572 baseline")
            if str(p.relative_to(_SRC)) in _KNOWN_VIOLATIONS
            else [],
        )
        for p in _source_files()
    ],
    ids=lambda p: str(p.relative_to(_SRC)),
)
def test_no_downstream_vocabulary(path: Path) -> None:
    violations = downstream_vocabulary(path.read_text(encoding="utf-8"))
    assert violations == [], (
        f"{path.relative_to(_SRC)}: downstream vocabulary on lines {violations}"
    )


def test_downstream_vocabulary_detects_issuesmith_label() -> None:
    source = 'label = "issuesmith:draft-ready"'
    assert downstream_vocabulary(source) == [1]


def test_downstream_vocabulary_allows_nexus_issue_ref() -> None:
    source = "# see sumipan/nexus#3572"
    assert downstream_vocabulary(source) == []


def test_downstream_vocabulary_allows_nexus_hash_ref() -> None:
    source = "# see nexus #1234"
    assert downstream_vocabulary(source) == []


def test_downstream_vocabulary_detects_standalone_nexus() -> None:
    source = "# connects to nexus pipeline"
    assert downstream_vocabulary(source) == [1]
