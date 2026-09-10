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

from ghdag.core.exceptions import GitHubApiError
from ghdag.forge.local import LocalForge
from ghdag.github_cli import apply_jq

FIXTURES = Path(__file__).parent / "fixtures" / "forge"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _seed_issue_3100(forge: LocalForge) -> None:
    """Seed LocalForge so issue #3100 matches issue_view_success fixture."""
    # Allocate up to 3099 unused numbers then create 3100 with exact fields.
    forge._force_next_number(3100)  # noqa: SLF001 — test seam for fixture parity
    n = forge.issue_create(
        "LocalForge 実装と契約テスト",
        "body",
        labels=["issuesmith:draft-done", "issuesmith:develop-running"],
    )
    assert n == 3100


def _seed_pr_178_open(forge: LocalForge) -> None:
    forge._force_next_number(178)  # noqa: SLF001
    forge._write_pull(  # noqa: SLF001
        {
            "number": 178,
            "title": "workflow/dispatcher: _observe_rate_limit を NetworkError に耐性化",
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


def _seed_pr_273_closed(forge: LocalForge) -> None:
    forge._force_next_number(273)  # noqa: SLF001
    forge._write_pull(  # noqa: SLF001
        {
            "number": 273,
            "title": "実装: sumipan/nexus#3099",
            "body": "P1/P2 result より自動生成。\n\nRefs sumipan/nexus#3099",
            "state": "closed",
            "head": "feat/issue-3099-2980ffb8",
            "base": "main",
            "merged": True,
            "url": "https://github.com/sumipan/ghdag/pull/273",
            "mergeable": None,
            "mergeable_state": None,
            "additions": 269,
            "deletions": 5,
        }
    )


@pytest.fixture
def forge(tmp_path: Path) -> LocalForge:
    return LocalForge(tmp_path, repo="sumipan/ghdag")


def test_contract_issue_view_success_json(forge: LocalForge) -> None:
    """成功: issue view --json fields が fixture と一致."""
    fixture = _load("issue_view_success.json")
    assert fixture["meta"]["exit_code"] == 0
    _seed_issue_3100(forge)

    data = forge.issue_get(3100, fields=["number", "title", "state", "labels"])
    assert data == fixture["output"]


def test_contract_issue_view_jq_state(forge: LocalForge) -> None:
    """成功: --jq '.state' が GitHub fixture と一致."""
    fixture = _load("issue_view_jq_state.json")
    _seed_issue_3100(forge)
    data = forge.issue_get(3100, fields=["number", "title", "state"])
    assert apply_jq(data, ".state") == fixture["output"]


def test_contract_issue_view_absent_404(forge: LocalForge) -> None:
    """不在/失敗: 存在しない Issue は 404（CLI exit 1 相当）."""
    fixture = _load("issue_view_absent.json")
    assert fixture["meta"]["exit_code"] == 1
    assert "404" in fixture["meta"]["stderr"]
    with pytest.raises(GitHubApiError) as exc_info:
        forge.issue_get(999999999, fields=["number", "title", "state"])
    assert exc_info.value.status_code == 404


def test_contract_pr_list_success_normalized(forge: LocalForge) -> None:
    """成功: pr list 正規化形 (_normalize_prs) が fixture と一致."""
    fixture = _load("pr_list_success.json")
    _seed_pr_178_open(forge)
    data = forge.pr_list(state="open", limit=3)
    assert data == fixture["output"]


def test_contract_pr_list_empty_head_filter(forge: LocalForge) -> None:
    """成功(空): 存在しない head は []."""
    fixture = _load("pr_list_empty.json")
    _seed_pr_178_open(forge)
    data = forge.pr_list(head="nonexistent-branch-xyz", state="open")
    assert data == fixture["output"]


def test_contract_pr_view_success_json_fields(forge: LocalForge) -> None:
    """成功: pr view --json フィールド部分集合が fixture と一致.

    実測どおり merge 済みでも state は CLOSED、mergeable は UNKNOWN
    （正規化層に merged / mergedAt は無い）。
    """
    fixture = _load("pr_view_success.json")
    _seed_pr_273_closed(forge)
    data = forge.pr_get(273)
    fields = ["number", "title", "state", "headRefName", "mergeable", "mergeStateStatus", "url"]
    selected = {k: data.get(k) for k in fields}
    assert selected == fixture["output"]


def test_contract_pr_view_absent_404(forge: LocalForge) -> None:
    """不在/失敗: 存在しない PR は 404."""
    fixture = _load("pr_view_absent.json")
    assert fixture["meta"]["exit_code"] == 1
    assert "404" in fixture["meta"]["stderr"]
    with pytest.raises(GitHubApiError) as exc_info:
        forge.pr_get(999999999)
    assert exc_info.value.status_code == 404
