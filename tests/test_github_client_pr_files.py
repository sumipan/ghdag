"""Tests for pr_get paginated files fetch (sumipan/nexus#3401)."""
from __future__ import annotations

import pytest

from ghdag.github_client import GitHubClient


def _make_files(n: int, start: int = 1) -> list[dict]:
    return [{"filename": f"file{i}.py", "additions": i, "deletions": 1} for i in range(start, start + n)]


def _pr_body(
    *,
    additions: int | None = 1000,
    deletions: int | None = 200,
    changed_files: int | None = 50,
) -> dict:
    d: dict = {
        "number": 42,
        "title": "Test PR",
        "body": "body",
        "state": "open",
        "html_url": "https://github.com/o/r/pull/42",
        "head": {"ref": "feat/test"},
        "mergeable_state": "clean",
        "mergeable": True,
    }
    if additions is not None:
        d["additions"] = additions
    if deletions is not None:
        d["deletions"] = deletions
    if changed_files is not None:
        d["changed_files"] = changed_files
    return d




def test_pr_get_fallback_when_body_missing_stats(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-2b: fall back to file sums when PR body lacks additions/deletions/changed_files."""
    client = GitHubClient(token="tok", repo="o/r")
    files = _make_files(3, start=1)  # additions: 1+2+3=6, deletions: 1+1+1=3

    def fake_request(method: str, path: str, **kwargs: object) -> object:
        if "/files" in path:
            assert kwargs.get("return_link_header") is True
            return files, None
        return _pr_body(additions=None, deletions=None, changed_files=None)

    monkeypatch.setattr(client, "_request", fake_request)
    result = client.pr_get(42)

    assert result["additions"] == 6
    assert result["deletions"] == 3
    assert result["changedFiles"] == 3



def test_pr_get_warns_on_count_mismatch(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """AC-3: one WARN line when changed_files=51 but 50 files fetched; no exception; changedFiles==51."""
    client = GitHubClient(token="tok", repo="o/r")
    files = _make_files(50, start=1)

    def fake_request(method: str, path: str, **kwargs: object) -> object:
        if "/files" in path:
            assert kwargs.get("return_link_header") is True
            return files, None
        return _pr_body(additions=1000, deletions=200, changed_files=51)

    monkeypatch.setattr(client, "_request", fake_request)
    result = client.pr_get(42)

    err = capsys.readouterr().err
    warn_lines = [line for line in err.splitlines() if line.startswith("WARN")]
    assert len(warn_lines) == 1
    assert "50" in warn_lines[0]
    assert "51" in warn_lines[0]
    assert result["changedFiles"] == 51
    assert len(result["files"]) == 50


