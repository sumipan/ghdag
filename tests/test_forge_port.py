"""Tests for ForgePort protocol and get_forge factory (nexus #3099)."""

from __future__ import annotations

import inspect
from typing import get_type_hints
from unittest.mock import MagicMock

import pytest

from ghdag.core.ports.forge import ForgePort
from ghdag.forge import get_forge
from ghdag.github_client import GitHubClient
from ghdag.workflow import state_machine
from ghdag.workflow.state_machine import transition


def test_forge_port_is_runtime_checkable() -> None:
    assert getattr(ForgePort, "_is_runtime_protocol", False) is True


def test_github_client_satisfies_forge_port() -> None:
    client = GitHubClient(token="token", repo="owner/repo")
    assert isinstance(client, ForgePort)


def test_get_forge_unset_returns_github_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GHDAG_FORGE", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    forge = get_forge(repo="owner/repo")
    assert isinstance(forge, GitHubClient)
    assert isinstance(forge, ForgePort)
    assert forge.repo == "owner/repo"


def test_get_forge_github_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GHDAG_FORGE", "github")
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    forge = get_forge(repo="acme/widgets")
    assert isinstance(forge, GitHubClient)
    assert forge.repo == "acme/widgets"


def test_workflow_reexports_forge_port_and_get_forge() -> None:
    from ghdag.workflow import ForgePort as ExportedForgePort
    from ghdag.workflow import get_forge as exported_get_forge

    assert ExportedForgePort is ForgePort
    assert exported_get_forge is get_forge


def test_core_ports_reexports_forge_port() -> None:
    from ghdag.core.ports import ForgePort as Exported

    assert Exported is ForgePort


def test_state_machine_transition_uses_get_forge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """影響分析: state_machine.transition の bare GitHubClient() を get_forge 経由に."""
    fake = MagicMock()
    fake.issue_get.side_effect = [
        {"labels": [{"name": "phase:a"}]},
        {"labels": [{"name": "phase:b"}]},
    ]
    fake.issue_update.return_value = None

    calls: list[object] = []

    def _fake_get_forge(repo: str | None = None) -> ForgePort:
        calls.append(repo)
        return fake

    monkeypatch.setattr(state_machine, "get_forge", _fake_get_forge)

    src = inspect.getsource(transition)
    assert "get_forge()" in src
    assert "GitHubClient()" not in src

    transition(
        42,
        "phase:b",
        transitions={"phase:a": ["phase:b"]},
    )
    assert calls == [None]
    assert fake.issue_get.call_count == 2
    fake.issue_update.assert_called_once()


def test_label_names_accepts_forge_port_type_hint() -> None:
    hints = get_type_hints(state_machine._label_names)
    assert hints["client"] is ForgePort
