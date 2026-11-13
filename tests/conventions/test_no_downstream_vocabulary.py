"""Convention test: src/ghdag must not contain downstream vocabulary."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_VOCAB = re.compile(r"issuesmith|mltgnt|nexus|diary|persona", re.IGNORECASE)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC = _REPO_ROOT / "src" / "ghdag"


def downstream_vocabulary(source: str) -> list[int]:
    """Return 1-indexed line numbers containing downstream vocabulary."""
    lines: list[int] = []
    for i, line in enumerate(source.splitlines(), 1):
        if _VOCAB.search(line):
            lines.append(i)
    return lines


def _source_files() -> list[Path]:
    return sorted(_SRC.rglob("*.py"))


@pytest.mark.parametrize(
    "path",
    _source_files(),
    ids=lambda p: str(p.relative_to(_SRC)),
)
def test_no_downstream_vocabulary(path: Path) -> None:
    violations = downstream_vocabulary(path.read_text(encoding="utf-8"))
    assert violations == [], (
        f"{path.relative_to(_SRC)}: downstream vocabulary on lines {violations}"
    )


@pytest.mark.parametrize(
    "source",
    [
        "# see sumipan/nexus#3572",
        "# see nexus Issue #1234",
    ],
)
def test_downstream_vocabulary_detects_host_issue_reference(source: str) -> None:
    assert downstream_vocabulary(source) == [1]


def test_downstream_vocabulary_detects_issuesmith_label() -> None:
    source = 'label = "issuesmith:draft-ready"'
    assert downstream_vocabulary(source) == [1]


def test_downstream_vocabulary_detects_standalone_nexus() -> None:
    source = "# connects to nexus pipeline"
    assert downstream_vocabulary(source) == [1]
