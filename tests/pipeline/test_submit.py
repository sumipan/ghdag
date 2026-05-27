"""tests/pipeline/test_submit.py — make_order_record / submit_order のテスト"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from ghdag.pipeline.audit import AuditContext
from ghdag.pipeline.submit import make_order_record, submit_order


@pytest.fixture()
def mock_state(tmp_path):
    state = MagicMock()
    state.write_order_file.return_value = "20260101000000-claude-order-test-uuid.md"
    return state


# ─── make_order_record ───────────────────────────────────────────────────────


def test_make_order_record_returns_record_and_uuid(mock_state):
    with patch("ghdag.pipeline.submit.uuid.uuid4", return_value=MagicMock(hex="abc", __str__=lambda s: "test-uuid")):
        record, uid = make_order_record(
            mock_state,
            engine="claude",
            content="test",
            model="claude-sonnet-4-6",
        )

    assert uid == "test-uuid"
    assert record["uuid"] == "test-uuid"


def test_make_order_record_engine_in_record(mock_state):
    with patch("ghdag.pipeline.submit.uuid.uuid4", return_value=MagicMock(__str__=lambda s: "u1")):
        record, _ = make_order_record(mock_state, engine="claude", content="test")
    assert record["engine"] == "claude"


def test_make_order_record_calls_write_order_file_once(mock_state):
    with patch("ghdag.pipeline.submit.uuid.uuid4", return_value=MagicMock(__str__=lambda s: "u1")):
        make_order_record(mock_state, engine="claude", content="hello")
    mock_state.write_order_file.assert_called_once()


def test_make_order_record_does_not_call_append_exec_records(mock_state):
    with patch("ghdag.pipeline.submit.uuid.uuid4", return_value=MagicMock(__str__=lambda s: "u1")):
        make_order_record(mock_state, engine="claude", content="test")
    mock_state.append_exec_records.assert_not_called()


def test_make_order_record_annotations(mock_state):
    with patch("ghdag.pipeline.submit.uuid.uuid4", return_value=MagicMock(__str__=lambda s: "u1")):
        record, _ = make_order_record(
            mock_state, engine="claude", content="test",
            annotations={"key": "val"},
        )
    assert record["annotations"]["key"] == "val"


def test_make_order_record_idempotency_key(mock_state):
    with patch("ghdag.pipeline.submit.uuid.uuid4", return_value=MagicMock(__str__=lambda s: "u1")):
        record, _ = make_order_record(
            mock_state, engine="claude", content="test",
            idempotency_key="idem-1",
        )
    assert record["idempotency_key"] == "idem-1"


def test_make_order_record_depends(mock_state):
    with patch("ghdag.pipeline.submit.uuid.uuid4", return_value=MagicMock(__str__=lambda s: "u1")):
        record, _ = make_order_record(
            mock_state, engine="claude", content="test",
            depends=["dep-uuid"],
        )
    assert record["depends"] == ["dep-uuid"]


def test_make_order_record_no_idempotency_key_by_default(mock_state):
    with patch("ghdag.pipeline.submit.uuid.uuid4", return_value=MagicMock(__str__=lambda s: "u1")):
        record, _ = make_order_record(mock_state, engine="claude", content="test")
    assert "idempotency_key" not in record


def test_make_order_record_record_uuid_matches_returned_uuid(mock_state):
    with patch("ghdag.pipeline.submit.uuid.uuid4", return_value=MagicMock(__str__=lambda s: "match-uuid")):
        record, uid = make_order_record(mock_state, engine="claude", content="test")
    assert record["uuid"] == uid


# ─── submit_order ─────────────────────────────────────────────────────────────


def test_submit_order_returns_record(mock_state):
    with patch("ghdag.pipeline.submit.uuid.uuid4", return_value=MagicMock(__str__=lambda s: "u2")):
        result = submit_order(
            mock_state, engine="claude", content="test", audit_source="test-src",
        )
    assert isinstance(result, dict)
    assert result["engine"] == "claude"


def test_submit_order_calls_append_exec_records_once(mock_state):
    with patch("ghdag.pipeline.submit.uuid.uuid4", return_value=MagicMock(__str__=lambda s: "u2")):
        submit_order(mock_state, engine="claude", content="test", audit_source="test-src")
    mock_state.append_exec_records.assert_called_once()


def test_submit_order_audit_source_passed(mock_state):
    with patch("ghdag.pipeline.submit.uuid.uuid4", return_value=MagicMock(__str__=lambda s: "u2")):
        submit_order(mock_state, engine="claude", content="test", audit_source="test-src")
    _, kwargs = mock_state.append_exec_records.call_args
    ctx: AuditContext = kwargs["audit_context"]
    assert ctx.source == "test-src"


def test_submit_order_correlation_id_explicit(mock_state):
    with patch("ghdag.pipeline.submit.uuid.uuid4", return_value=MagicMock(__str__=lambda s: "u2")):
        submit_order(
            mock_state, engine="claude", content="test",
            audit_source="test-src", correlation_id="corr-1",
        )
    _, kwargs = mock_state.append_exec_records.call_args
    ctx: AuditContext = kwargs["audit_context"]
    assert ctx.correlation_id == "corr-1"


def test_submit_order_correlation_id_defaults_to_uuid(mock_state):
    with patch("ghdag.pipeline.submit.uuid.uuid4", return_value=MagicMock(__str__=lambda s: "gen-uuid")):
        submit_order(mock_state, engine="claude", content="test", audit_source="src")
    _, kwargs = mock_state.append_exec_records.call_args
    ctx: AuditContext = kwargs["audit_context"]
    assert ctx.correlation_id == "gen-uuid"


# ─── public import ─────────────────────────────────────────────────────────────


def test_public_import():
    from ghdag.pipeline import make_order_record, submit_order  # noqa: F401
