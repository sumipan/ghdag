"""Pagination tests for GitHubClient list methods.

Two-page fixture: page 1 returns 100 items (or 30 for issue_get comments),
page 2 returns 50 (or 28 for issue_get comments) items via a Link header.
"""

from __future__ import annotations

import io
import zipfile

import pytest

from ghdag.github_client import GitHubClient

_LINK_NEXT = '<https://api.github.com/fake-next?page=2>; rel="next"'
_NEXT_URL = "https://api.github.com/fake-next?page=2"


def _items(n: int, key: str = "number", start: int = 1) -> list[dict]:
    return [{key: i} for i in range(start, start + n)]


def _pr_items(n: int, start: int = 1) -> list[dict]:
    return [
        {
            "number": i,
            "title": f"PR {i}",
            "html_url": f"https://github.com/o/r/pull/{i}",
            "state": "closed",
            "head": {"ref": f"branch-{i}"},
            "mergeable_state": None,
            "mergeable": None,
            "additions": None,
            "deletions": None,
        }
        for i in range(start, start + n)
    ]


def _make_two_page_fake(page1_data: object, page2_data: object) -> object:
    def fake(method: str, path: str, **kwargs: object) -> object:
        if not kwargs.get("return_link_header"):
            return page1_data
        if path == _NEXT_URL:
            return page2_data, None
        return page1_data, _LINK_NEXT

    return fake


def _make_wrapped_two_page_fake(
    page1_items: list, page2_items: list, items_key: str, pr_data: dict | None = None
) -> object:
    """Fake that returns wrapped dict responses (check_runs, jobs)."""

    def fake(method: str, path: str, **kwargs: object) -> object:
        if not kwargs.get("return_link_header"):
            # Single resource GET (PR, run, etc.)
            if pr_data is not None:
                return pr_data
            return {items_key: page1_items}
        if path == _NEXT_URL:
            return {items_key: page2_items}, None
        return {items_key: page1_items}, _LINK_NEXT

    return fake


@pytest.fixture
def client() -> GitHubClient:
    return GitHubClient(token="tok", repo="o/r")



def test_milestone_list_fetches_all_pages(
    monkeypatch: pytest.MonkeyPatch, client: GitHubClient
) -> None:
    page1 = _items(100, "number")
    page2 = _items(50, "number", start=101)
    monkeypatch.setattr(client, "_request", _make_two_page_fake(page1, page2))
    result = client.milestone_list()
    assert len(result) == 150


def test_list_issues_fetches_all_pages(
    monkeypatch: pytest.MonkeyPatch, client: GitHubClient
) -> None:
    page1 = _items(100, "number")
    page2 = _items(50, "number", start=101)
    monkeypatch.setattr(client, "_request", _make_two_page_fake(page1, page2))
    result = client.list_issues("x", "all")
    assert len(result) == 150



def test_pr_checks_fetches_all_pages(
    monkeypatch: pytest.MonkeyPatch, client: GitHubClient
) -> None:
    pr_data = {"number": 1, "head": {"sha": "abc123"}}
    page1 = [{"name": f"check-{i}", "conclusion": "success"} for i in range(100)]
    page2 = [{"name": "check-last", "conclusion": "failure"}]
    monkeypatch.setattr(
        client,
        "_request",
        _make_wrapped_two_page_fake(page1, page2, "check_runs", pr_data=pr_data),
    )
    result = client.pr_checks(1)
    assert len(result) == 101
    assert result[-1]["conclusion"] == "failure"


def test_run_logs_failed_fetches_all_pages(
    monkeypatch: pytest.MonkeyPatch, client: GitHubClient
) -> None:
    page1_jobs = [{"name": f"job-{i}", "conclusion": "success"} for i in range(100)]
    page2_jobs = [{"name": "failing-job", "conclusion": "failure"}]
    calls: list[str] = []

    def fake_request(method: str, path: str, **kwargs: object) -> object:
        calls.append(path)
        if not kwargs.get("return_link_header"):
            return {"jobs": page1_jobs}
        if path == _NEXT_URL:
            return {"jobs": page2_jobs}, None
        return {"jobs": page1_jobs}, _LINK_NEXT

    monkeypatch.setattr(client, "_request", fake_request)

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as zf:
        zf.writestr("failing-job/1_step.txt", "error output here")
    zip_data = zip_buf.getvalue()

    captured_urls: list[str] = []

    class FakeResp:
        def read(self) -> bytes:
            return zip_data

        def __enter__(self) -> "FakeResp":
            return self

        def __exit__(self, *args: object) -> None:
            pass

    def fake_urlopen(req: object, timeout: int = 120) -> FakeResp:
        captured_urls.append(getattr(req, "full_url", str(req)))
        return FakeResp()

    monkeypatch.setattr("ghdag.github_client.urllib.request.urlopen", fake_urlopen)
    result = client.run_logs_failed(42)
    assert "error output here" in result
    assert len(captured_urls) == 1  # only the zip download




def test_pr_list_limit_100_returns_100(
    monkeypatch: pytest.MonkeyPatch, client: GitHubClient
) -> None:
    """pr_list(limit=100) returns exactly 100 items from a single full page."""
    page1 = _pr_items(100)

    def fake_request(method: str, path: str, **kwargs: object) -> object:
        if kwargs.get("return_link_header"):
            return page1, None
        return page1

    monkeypatch.setattr(client, "_request", fake_request)
    result = client.pr_list(limit=100, state="open")
    assert len(result) == 100


