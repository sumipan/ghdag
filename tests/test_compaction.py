"""CompactionPolicy / session compaction / resume fallback tests (Issue #90)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ghdag.dag.models import DagConfig, Task
from ghdag.dag.task_launcher import TaskLauncher
from ghdag.io.audit import write_compaction_audit
from ghdag.llm.adapters.claude_json import ClaudeJsonAdapter
from ghdag.llm.compaction import (
    GENSHIJIN_HANDOFF_PROMPT,
    CompactionPolicy,
    CompactionResult,
    compact_resume_session,
)
from ghdag.llm.engines import LLMResult
from ghdag.llm.session import SessionRecord, SessionStore


def _record(
    *,
    engine: str = "claude",
    session_id: str = "sess-parent",
) -> SessionRecord:
    return SessionRecord(
        engine=engine,
        session_id=session_id,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def test_policy_compacts_when_claude_over_threshold():
    policy = CompactionPolicy(token_threshold=100_000, enabled=True)
    assert policy.should_compact(_record(), token_usage=120_000) is True


def test_policy_skips_below_threshold():
    policy = CompactionPolicy(token_threshold=100_000, enabled=True)
    assert policy.should_compact(_record(), token_usage=50_000) is False


def test_policy_skips_unsupported_engine():
    policy = CompactionPolicy(token_threshold=1, enabled=True)
    assert policy.should_compact(_record(engine="cursor"), token_usage=999_999) is False


def test_policy_opt_in_disabled_by_default():
    policy = CompactionPolicy()
    assert policy.enabled is False
    assert policy.should_compact(_record(), token_usage=999_999) is False


def test_genshijin_prompt_is_machine_handoff_style():
    assert "honorific" in GENSHIJIN_HANDOFF_PROMPT or "background" in GENSHIJIN_HANDOFF_PROMPT
    assert "facts" in GENSHIJIN_HANDOFF_PROMPT.lower()
    for key in ("facts", "unresolved", "next", "files", "constraints"):
        assert f'"{key}"' in GENSHIJIN_HANDOFF_PROMPT
    assert "language of the conversation" in GENSHIJIN_HANDOFF_PROMPT
    cjk_ranges = ((0x3000, 0x30FF), (0x4E00, 0x9FFF), (0xFF00, 0xFFEF))
    assert not any(lo <= ord(c) <= hi for c in GENSHIJIN_HANDOFF_PROMPT for lo, hi in cjk_ranges)
    # Must not be a general output_style knob name
    assert "output_style" not in GENSHIJIN_HANDOFF_PROMPT


def _llm_result(body: str, *, session_id: str | None, ok: bool = True) -> LLMResult:
    return LLMResult(
        body=body,
        stdout=body,
        stderr="",
        returncode=0 if ok else 1,
        session_id=session_id,
    )


def _claude_json_llm_result(payload: dict) -> LLMResult:
    """Build an LLMResult as call(route=[("claude", None)]) would from claude --output-format json stdout."""
    stdout = json.dumps(payload)
    adapter = ClaudeJsonAdapter()
    body = adapter.extract_result_text(stdout.encode(), b"").decode()
    session_id = adapter.extract_session_id(stdout.encode(), b"")
    return LLMResult(
        body=body,
        stdout=stdout,
        stderr="",
        returncode=0,
        session_id=session_id,
    )


def test_compact_resume_passes_json_capabilities_to_both_calls(tmp_path):
    """Issue #6090: both summary and seed LLM calls use JSON output capabilities."""
    store = SessionStore(tmp_path / ".sessions")
    store.record("parent", "claude", "sess-parent")
    parent = store.lookup("parent")
    assert parent is not None

    calls: list[dict] = []

    def fake_call_fn(prompt, **kwargs):
        calls.append(kwargs)
        if kwargs.get("resume_session_id") == "sess-parent":
            return _llm_result(
                '{"facts":[],"unresolved":[],"next":[],"files":[],"constraints":[]}',
                session_id="sess-parent",
            )
        return _llm_result("ack", session_id="sess-compacted")

    compact_resume_session(
        store=store,
        parent_key="parent",
        parent_record=parent,
        compacted_key="child__compacted",
        policy=CompactionPolicy(token_threshold=10, enabled=True),
        token_usage=50_000,
        call_fn=fake_call_fn,
    )

    assert len(calls) == 2
    for call_kwargs in calls:
        caps = call_kwargs.get("permission")
        assert caps is not None
        assert caps.output_format == "json"


def test_compact_resume_records_session_id_from_claude_json_output(tmp_path):
    """Issue #6090: compaction records session_id parsed from claude JSON stdout."""
    store = SessionStore(tmp_path / ".sessions")
    store.record("parent", "claude", "sess-parent")
    parent = store.lookup("parent")
    assert parent is not None

    summary_json = (
        '{"facts":[],"unresolved":[],"next":[],"files":[],"constraints":[]}'
    )

    def fake_call_fn(prompt, **kwargs):
        if kwargs.get("resume_session_id") == "sess-parent":
            return _claude_json_llm_result(
                {
                    "type": "result",
                    "result": summary_json,
                    "session_id": "sess-parent",
                }
            )
        return _claude_json_llm_result(
            {
                "type": "result",
                "result": "ack",
                "session_id": "sess-compacted",
            }
        )

    result = compact_resume_session(
        store=store,
        parent_key="parent",
        parent_record=parent,
        compacted_key="child__compacted",
        policy=CompactionPolicy(token_threshold=10, enabled=True),
        token_usage=50_000,
        call_fn=fake_call_fn,
    )

    assert result.status == "compacted"
    assert result.session_id == "sess-compacted"
    compacted = store.lookup("child__compacted")
    assert compacted is not None
    assert compacted.session_id == "sess-compacted"


def test_compact_resume_falls_back_when_seed_has_no_session_id(tmp_path):
    """Issue #6090: missing seed session_id falls back without recording compaction."""
    store = SessionStore(tmp_path / ".sessions")
    store.record("parent", "claude", "sess-parent")
    parent = store.lookup("parent")
    assert parent is not None

    def fake_call_fn(prompt, **kwargs):
        if kwargs.get("resume_session_id") == "sess-parent":
            return _llm_result(
                '{"facts":[],"unresolved":[],"next":[],"files":[],"constraints":[]}',
                session_id="sess-parent",
            )
        return _llm_result("ack", session_id=None)

    result = compact_resume_session(
        store=store,
        parent_key="parent",
        parent_record=parent,
        compacted_key="child__compacted",
        policy=CompactionPolicy(token_threshold=10, enabled=True),
        token_usage=50_000,
        call_fn=fake_call_fn,
    )

    assert result.status == "fallback"
    assert result.reason == "prompt_error"
    assert result.session_id == "sess-parent"
    assert store.lookup("child__compacted") is None


def test_readme_compaction_row_mentions_json_output_mode():
    """Issue #6090: README module inventory documents JSON output mode for compaction."""
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(
        encoding="utf-8"
    )
    for line in readme.splitlines():
        if "`llm/compaction.py`" in line:
            assert "JSON output mode" in line
            return
    pytest.fail("README.md has no llm/compaction.py module inventory row")


def test_compact_resume_creates_new_session_with_lineage(tmp_path):
    store = SessionStore(tmp_path / ".sessions")
    store.record("parent", "claude", "sess-parent")
    parent = store.lookup("parent")
    assert parent is not None

    calls: list[dict] = []

    def fake_call_fn(prompt, **kwargs):
        calls.append({"prompt": prompt, **kwargs})
        if kwargs.get("resume_session_id") == "sess-parent":
            return _llm_result(
                '{"facts":[],"unresolved":[],"next":[],"files":[],"constraints":[]}',
                session_id="sess-parent",
            )
        return _llm_result("ack", session_id="sess-compacted")

    result = compact_resume_session(
        store=store,
        parent_key="parent",
        parent_record=parent,
        compacted_key="child__compacted",
        policy=CompactionPolicy(token_threshold=10, enabled=True),
        token_usage=50_000,
        call_fn=fake_call_fn,
    )

    assert result.status == "compacted"
    assert result.session_id == "sess-compacted"
    assert result.parent_session_id == "sess-parent"
    assert result.summary_tokens is not None and result.summary_tokens > 0

    compacted = store.lookup("child__compacted")
    assert compacted is not None
    assert compacted.session_id != parent.session_id
    assert compacted.parent_session_id == parent.session_id
    assert compacted.is_compacted is True

    assert any(c.get("resume_session_id") == "sess-parent" for c in calls)
    assert GENSHIJIN_HANDOFF_PROMPT in calls[0]["prompt"] or calls[0]["prompt"] == GENSHIJIN_HANDOFF_PROMPT


@pytest.mark.parametrize(
    "scenario,kwargs,expected_reason",
    [
        ("session_miss", {"parent_record": None}, "session_miss"),
        (
            "engine_unsupported",
            {
                "parent_record": _record(engine="gemini"),
                "token_usage": 999_999,
            },
            "engine_unsupported",
        ),
        (
            "below_threshold",
            {"parent_record": _record(), "token_usage": 10},
            "below_threshold",
        ),
        (
            "prompt_error",
            {
                "parent_record": _record(),
                "token_usage": 999_999,
                "call_fails": True,
            },
            "prompt_error",
        ),
    ],
)
def test_compact_fallbacks(tmp_path, scenario, kwargs, expected_reason):
    store = SessionStore(tmp_path / ".sessions")
    store.record("parent", "claude", "sess-parent")
    parent = kwargs.get("parent_record", store.lookup("parent"))
    call_fails = kwargs.pop("call_fails", False)

    def fake_call_fn(*_a, **_k):
        if call_fails:
            return _llm_result("", session_id=None, ok=False)
        return _llm_result("ok", session_id="sess-new")

    result = compact_resume_session(
        store=store,
        parent_key="parent",
        parent_record=parent,
        compacted_key="compacted",
        policy=CompactionPolicy(token_threshold=100_000, enabled=True),
        token_usage=kwargs.get("token_usage"),
        call_fn=fake_call_fn,
    )

    assert isinstance(result, CompactionResult)
    assert result.status in {"skipped", "fallback"}
    assert result.reason == expected_reason
    # Fall back to parent session when available
    if parent is not None and scenario != "engine_unsupported":
        assert result.session_id == parent.session_id
    elif parent is not None:
        assert result.session_id == parent.session_id


def test_write_compaction_audit_records_lineage(tmp_path):
    audit_path = tmp_path / "audit.jsonl"
    write_compaction_audit(
        audit_path,
        task_uuid="task-1",
        status="compacted",
        reason="over_threshold",
        parent_session_id="sess-parent",
        compacted_session_id="sess-compacted",
        summary_tokens=321,
        tokens_before=150_000,
        tokens_after=321,
        engine="claude",
    )
    rec = json.loads(audit_path.read_text(encoding="utf-8").strip())
    assert rec["event_type"] == "session_compaction"
    assert rec["parent_session_id"] == "sess-parent"
    assert rec["compacted_session_id"] == "sess-compacted"
    assert rec["summary_tokens"] == 321
    assert rec["tokens_before"] == 150_000
    assert rec["tokens_after"] == 321
    assert rec["reason"] == "over_threshold"


def test_replay_comparison_records_token_delta(tmp_path):
    """Audit can record a with/without compact comparison for the same workflow."""
    audit_path = tmp_path / "audit.jsonl"
    write_compaction_audit(
        audit_path,
        task_uuid="replay-no-compact",
        status="skipped",
        reason="policy_disabled",
        parent_session_id="sess-parent",
        compacted_session_id=None,
        summary_tokens=None,
        tokens_before=150_000,
        tokens_after=150_000,
        engine="claude",
        comparison_group="wf-replay-1",
    )
    write_compaction_audit(
        audit_path,
        task_uuid="replay-compact",
        status="compacted",
        reason="over_threshold",
        parent_session_id="sess-parent",
        compacted_session_id="sess-compacted",
        summary_tokens=400,
        tokens_before=150_000,
        tokens_after=400,
        engine="claude",
        comparison_group="wf-replay-1",
    )
    lines = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 2
    assert lines[0]["comparison_group"] == lines[1]["comparison_group"] == "wf-replay-1"
    assert lines[1]["tokens_after"] < lines[0]["tokens_after"]


def _make_launcher(tmp_path: Path) -> TaskLauncher:
    done = tmp_path / "jobs" / "done"
    done.mkdir(parents=True)
    config = DagConfig(
        exec_jsonl_path=tmp_path / "jobs" / "exec.jsonl",
        exec_done_dir=done,
    )
    return TaskLauncher(
        config,
        hooks=MagicMock(),
        circuit_breaker=MagicMock(),
        fanout_manager=MagicMock(),
        promote_fn=MagicMock(),
    )


def test_apply_resume_uses_compacted_session(tmp_path):
    launcher = _make_launcher(tmp_path)
    launcher._session_store.record("parent-uuid", "claude", "sess-parent")
    task = Task(
        uuid="child-uuid",
        command="claude -p 'continue'",
        engine="claude",
        annotations={"resume_from_uuid": "parent-uuid"},
    )

    compacted = CompactionResult(
        status="compacted",
        reason="over_threshold",
        session_id="sess-compacted",
        parent_session_id="sess-parent",
        summary_tokens=100,
        tokens_before=120_000,
        tokens_after=100,
        compacted_key="parent-uuid__compacted__child-uuid",
    )

    with patch(
        "ghdag.dag.task_launcher.compact_resume_session",
        return_value=compacted,
    ) as mock_compact, patch(
        "ghdag.dag.task_launcher.write_compaction_audit"
    ) as mock_audit:
        launcher._apply_resume_if_available("child-uuid", task)

    mock_compact.assert_called_once()
    assert task.annotations["resumed_session_id"] == "sess-compacted"
    assert "--resume 'sess-compacted'" in task.command
    mock_audit.assert_called_once()


def test_apply_resume_falls_back_on_session_miss(tmp_path):
    launcher = _make_launcher(tmp_path)
    task = Task(
        uuid="child-uuid",
        command="claude -p 'continue'",
        engine="claude",
        annotations={"resume_from_uuid": "missing-parent"},
    )
    with patch("ghdag.dag.task_launcher.write_compaction_audit") as mock_audit:
        launcher._apply_resume_if_available("child-uuid", task)

    assert "resumed_session_id" not in task.annotations
    assert "--resume" not in task.command
    mock_audit.assert_called_once()
    assert mock_audit.call_args.kwargs["reason"] == "session_miss"


def test_apply_resume_falls_back_on_engine_mismatch(tmp_path):
    launcher = _make_launcher(tmp_path)
    launcher._session_store.record("parent-uuid", "claude", "sess-parent")
    task = Task(
        uuid="child-uuid",
        command="cursor -p 'continue'",
        engine="cursor",
        annotations={"resume_from_uuid": "parent-uuid"},
    )
    with patch("ghdag.dag.task_launcher.write_compaction_audit") as mock_audit:
        launcher._apply_resume_if_available("child-uuid", task)

    assert "resumed_session_id" not in task.annotations
    mock_audit.assert_called_once()
    assert mock_audit.call_args.kwargs["reason"] == "engine_mismatch"


def test_apply_resume_falls_back_when_compaction_skips(tmp_path):
    launcher = _make_launcher(tmp_path)
    launcher._session_store.record("parent-uuid", "claude", "sess-parent")
    task = Task(
        uuid="child-uuid",
        command="claude -p 'continue'",
        engine="claude",
        annotations={"resume_from_uuid": "parent-uuid"},
    )
    skipped = CompactionResult(
        status="skipped",
        reason="below_threshold",
        session_id="sess-parent",
        parent_session_id="sess-parent",
        summary_tokens=None,
        tokens_before=10,
        tokens_after=10,
        compacted_key=None,
    )
    with patch(
        "ghdag.dag.task_launcher.compact_resume_session",
        return_value=skipped,
    ), patch("ghdag.dag.task_launcher.write_compaction_audit"):
        launcher._apply_resume_if_available("child-uuid", task)

    assert task.annotations["resumed_session_id"] == "sess-parent"
    assert "--resume 'sess-parent'" in task.command
