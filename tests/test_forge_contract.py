"""Contract tests: GitHub real fixtures vs LocalForge (nexus #3100).

Fixtures under tests/fixtures/forge/ were captured with the same CLI shapes
that github_cli emits via --json / --jq (CLAUDE.md §10: success / failure /
absence). LocalForge is seeded to mirror the recorded state and compared on
the normalized field set (_normalize_prs / issue_get fields).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ghdag.forge.local import LocalForge

FIXTURES = Path(__file__).parent / "fixtures" / "forge"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _seed_pr_178_open(forge: LocalForge) -> None:
    forge._force_next_number(178)  # noqa: SLF001
    forge._write_pull(  # noqa: SLF001
        {
            "number": 178,
            "title": "workflow/dispatcher: harden _observe_rate_limit against NetworkError",
            "body": "",
            "state": "open",
            "head": "feat/dispatcher-rate-limit-resilience",
            "base": "main",
            "merged": False,
            "url": "https://github.com/sumipan/ghdag/pull/178",
            "mergeable": None,
            "mergeable_state": None,
            "additions": None,
            "deletions": None,
        }
    )


@pytest.fixture
def forge(tmp_path: Path) -> LocalForge:
    return LocalForge(tmp_path, repo="sumipan/ghdag")


def test_contract_pr_list_empty_head_filter(forge: LocalForge) -> None:
    """Success (empty): nonexistent head returns []."""
    fixture = _load("pr_list_empty.json")
    _seed_pr_178_open(forge)
    data = forge.pr_list(head="nonexistent-branch-xyz", state="open")
    assert data == fixture["output"]


def _seed_prs(forge: LocalForge, count: int, state: str = "open") -> None:
    for i in range(1, count + 1):
        forge._write_pull(  # noqa: SLF001
            {
                "number": i,
                "title": f"PR {i}",
                "body": "",
                "state": state,
                "head": f"branch-{i}",
                "base": "main",
                "merged": state == "closed",
                "url": f"https://github.com/sumipan/ghdag/pull/{i}",
                "mergeable": None,
                "mergeable_state": None,
                "additions": None,
                "deletions": None,
            }
        )


def test_contract_pr_list_limit_truncates(forge: LocalForge) -> None:
    """pr_list(limit=3) returns only 3 items even when more are seeded."""
    _seed_prs(forge, 150, state="open")
    result = forge.pr_list(state="open", limit=3)
    assert len(result) == 3
