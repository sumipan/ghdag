"""AC-1: DagConfig.audit_path resolves next to exec.jsonl (jobs/audit.jsonl)."""

from __future__ import annotations

from pathlib import Path

from ghdag.core.models.dag import DagConfig


def test_audit_path_resolves_beside_exec_jsonl():
    config = DagConfig(exec_jsonl_path="jobs/exec.jsonl")
    assert config.audit_path == Path("jobs/audit.jsonl")


def test_audit_path_explicit_override():
    config = DagConfig(
        exec_jsonl_path="jobs/exec.jsonl",
        audit_path=Path("/tmp/custom-audit.jsonl"),
    )
    assert config.audit_path == Path("/tmp/custom-audit.jsonl")
