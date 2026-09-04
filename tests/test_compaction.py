"""CompactionPolicy / session compaction / resume fallback tests (Issue #90)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ghdag.dag.models import DagConfig, Task
from ghdag.dag.task_launcher import TaskLauncher
from ghdag.llm.compaction import (
    GENSHIJIN_HANDOFF_PROMPT,
    CompactionPolicy,
    CompactionResult,
    compact_resume_session,
)
from ghdag.llm.engines import TextResult, LLMResult
from ghdag.llm.session import SessionRecord, SessionStore
from ghdag.pipeline.audit import write_compaction_audit


def _record(
    *,
    engine: str = "claude",
    session_id: str = "sess-parent",
) -> SessionRecord:
    return SessionRecord(
        engine=engine,
        session_id=session_id,
        created_at=datetime.now(timezone.utc),
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
    assert "敬語" in GENSHIJIN_HANDOFF_PROMPT or "背景説明" in GENSHIJIN_HANDOFF_PROMPT
    assert "facts" in GENSHIJIN_HANDOFF_PROMPT.lower() or "事実" in GENSHIJIN_HANDOFF_PROMPT
    # Must not be a general output_style knob name
    assert "output_style" not in GENSHIJIN_HANDOFF_PROMPT


def test_genshijin_not_applied_to_result_or_human_outputs():
    """genshijin は compaction プロンプト専用。result / Slack / 日記経路に漏れない。"""
    import ghdag.dag.task_launcher as launcher_mod
    import ghdag.llm.adapters.claude_json as claude_json
    import ghdag.llm.adapters.claude_text as claude_text
    import ghdag.llm.adapters.codex as codex
    import ghdag.llm.adapters.cursor as cursor

    launcher_src = Path(launcher_mod.__file__).read_text(encoding="utf-8")
    assert "GENSHIJIN" not in launcher_src
    assert "genshijin" not in launcher_src.lower()

    # Adapter result extraction must not reference genshijin style
    for mod in (claude_json, claude_text, cursor, codex):
        src = Path(mod.__file__).read_text(encoding="utf-8")
        assert "genshijin" not in src.lower()


def _text_result(body: str, *, session_id: str | None, ok: bool = True) -> TextResult:
    raw = LLMResult(
        stdout=body,
        stderr="",
        returncode=0 if ok else 1,
        session_id=session_id,
    )
    return TextResult(body=body, success=ok, raw=raw, error=None)


def test_compact_resume_creates_new_session_with_lineage(tmp_path):
    store = SessionStore(tmp_path / ".sessions")
    store.record("parent", "claude", "sess-parent")
    parent = store.lookup("parent")
    assert parent is not None

    calls: list[dict] = []

    def fake_call_text(prompt, **kwargs):
        calls.append({"prompt": prompt, **kwargs})
        if kwargs.get("resume_session_id") == "sess-parent":
            return _text_result(
                '{"facts":[],"unresolved":[],"next":[],"files":[],"constraints":[]}',
                session_id="sess-parent",
            )
        return _text_result("ack", session_id="sess-compacted")

    result = compact_resume_session(
        store=store,
        parent_key="parent",
        parent_record=parent,
        compacted_key="child__compacted",
        policy=CompactionPolicy(token_threshold=10, enabled=True),
        token_usage=50_000,
        call_text_fn=fake_call_text,
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

    def fake_call_text(*_a, **_k):
        if call_fails:
            return _text_result("", session_id=None, ok=False)
        return _text_result("ok", session_id="sess-new")

    result = compact_resume_session(
        store=store,
        parent_key="parent",
        parent_record=parent,
        compacted_key="compacted",
        policy=CompactionPolicy(token_threshold=100_000, enabled=True),
        token_usage=kwargs.get("token_usage"),
        call_text_fn=fake_call_text,
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
    """同一 workflow の compact あり／なし比較を audit に残せる。"""
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
