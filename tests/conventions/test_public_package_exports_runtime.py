"""Runtime checks that selected subpackages expose the requested public contract."""

from __future__ import annotations

import types
from typing import Any

import pytest


def _assert_public_exports(
    package: types.ModuleType,
    names: frozenset[str],
    sources: dict[str, Any],
) -> None:
    assert names <= frozenset(package.__all__)
    for name in names:
        assert hasattr(package, name), f"{package.__name__} missing attribute {name!r}"
        assert getattr(package, name) is sources[name], f"{package.__name__}.{name} is not the canonical definition"


def test_dag_public_exports_match_requested_contract() -> None:
    """Issue #6327 — ghdag.dag re-exports recover, fanout, _util, and vocabulary symbols."""
    import ghdag.dag as dag
    from ghdag.core import vocabulary
    from ghdag.dag import _util, fanout, recover

    names = frozenset(
        {
            "running_uuids_from_queue_dir",
            "plan_recover",
            "execute_recover",
            "WaitQuotaSignal",
            "parse_wait_quota_signal",
            "FanOutSpec",
            "parse_fanout_spec",
            "build_child_jsonl_record",
            "DONE_SUCCESS",
            "LEGACY_DONE_DEFERRED",
            "PIPELINE_WAIT_QUOTA",
            "WAIT_QUOTA_COMPANION_RE",
        }
    )
    sources = {
        "running_uuids_from_queue_dir": recover.running_uuids_from_queue_dir,
        "plan_recover": recover.plan_recover,
        "execute_recover": recover.execute_recover,
        "WaitQuotaSignal": _util.WaitQuotaSignal,
        "parse_wait_quota_signal": _util.parse_wait_quota_signal,
        "FanOutSpec": fanout.FanOutSpec,
        "parse_fanout_spec": fanout.parse_fanout_spec,
        "build_child_jsonl_record": fanout.build_child_jsonl_record,
        "DONE_SUCCESS": vocabulary.DONE_SUCCESS,
        "LEGACY_DONE_DEFERRED": vocabulary.LEGACY_DONE_DEFERRED,
        "PIPELINE_WAIT_QUOTA": vocabulary.PIPELINE_WAIT_QUOTA,
        "WAIT_QUOTA_COMPANION_RE": vocabulary.WAIT_QUOTA_COMPANION_RE,
    }
    _assert_public_exports(dag, names, sources)


def test_forge_public_exports_match_requested_contract() -> None:
    """Issue #6327 — ghdag.forge exposes forge factory surface."""
    import ghdag.forge as forge
    from ghdag import github_client
    from ghdag.core import exceptions
    from ghdag.core.ports import forge as forge_port
    from ghdag.forge import local

    names = frozenset(
        {
            "DEFAULT_REPO",
            "ForgePort",
            "GitHubApiError",
            "GitHubClient",
            "LocalForge",
            "get_forge",
        }
    )
    sources = {
        "DEFAULT_REPO": github_client.DEFAULT_REPO,
        "ForgePort": forge_port.ForgePort,
        "GitHubApiError": exceptions.GitHubApiError,
        "GitHubClient": github_client.GitHubClient,
        "LocalForge": local.LocalForge,
        "get_forge": forge.get_forge,
    }
    _assert_public_exports(forge, names, sources)


def test_io_redact_is_public_submodule() -> None:
    """Issue #6327 — ghdag.io publishes the redact submodule."""
    import ghdag.io as io_pkg
    import ghdag.io.redact as redact_mod

    names = frozenset({"redact"})
    sources = {"redact": redact_mod}
    _assert_public_exports(io_pkg, names, sources)


def test_llm_public_exports_match_requested_contract() -> None:
    """Issue #6327 — ghdag.llm re-exports capability and adapter helpers."""
    import ghdag.llm as llm
    from ghdag.core import capabilities, container
    from ghdag.core.ports import output
    from ghdag.llm import engines
    from ghdag.llm.adapters import failure_classification

    names = frozenset(
        {
            "PRESETS",
            "supports_capability",
            "EngineError",
            "classify_common_failure",
            "container_prefix",
        }
    )
    sources = {
        "PRESETS": capabilities.PRESETS,
        "supports_capability": engines.supports_capability,
        "EngineError": output.EngineError,
        "classify_common_failure": failure_classification.classify_common_failure,
        "container_prefix": container.container_prefix,
    }
    _assert_public_exports(llm, names, sources)


def test_pipeline_submission_helpers_are_not_public() -> None:
    """Issue sumipan/nexus#6440 — legacy submit helpers are not package exports."""
    import ghdag.pipeline as pipeline

    for name in ("make_order_record", "submit_order"):
        assert name not in pipeline.__all__
        assert not hasattr(pipeline, name)


def test_audit_hooks_public_home_is_dag_only() -> None:
    """Issue sumipan/nexus#6451 — AuditHooks is exported from ghdag.dag only."""
    import ghdag.dag as dag
    import ghdag.pipeline as pipeline
    from ghdag.dag.audit_hooks import AuditHooks as canonical

    assert "AuditHooks" in dag.__all__
    assert getattr(dag, "AuditHooks") is canonical
    assert "AuditHooks" not in pipeline.__all__
    assert not hasattr(pipeline, "AuditHooks")


def test_legacy_permission_exports_are_absent() -> None:
    """Issue sumipan/nexus#6501 — Permission rename AC placeholder until P1 lands."""
    pytest.skip("Permission/PERMISSIONS rename not implemented yet (sumipan/nexus#6501)")
