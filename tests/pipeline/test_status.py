"""Tests for task_status / interpret_done — AC-3 (Issue #678)."""

from __future__ import annotations

import pytest

from ghdag.config.language import EN, STATE_IDS, LanguagePack
from ghdag.pipeline.status import (
    STATE_DEFERRED,
    STATE_EMPTY,
    STATE_ENGINE_ERROR,
    STATE_FAIL,
    STATE_OK,
    STATE_PENDING_DEPS,
    STATE_PENDING_RUN,
    STATE_REJECTED,
    STATE_RUNNING,
    STATE_UNKNOWN_DONE,
    interpret_done,
    label_for_done,
    state_label,
    task_status,
)


class TestInterpretDone:
    def test_none_returns_none(self):
        assert interpret_done(None) is None

    def test_exit_zero_returns_success(self):
        assert interpret_done("0\n") == "success"

    def test_empty_string_returns_success(self):
        assert interpret_done("") == "success"

    def test_rejected_returns_rejected(self):
        assert interpret_done("REJECTED\n") == "rejected"

    def test_rejected_final_returns_rejected(self):
        assert interpret_done("REJECTED_FINAL\n") == "rejected"

    def test_empty_result_returns_empty_result(self):
        assert interpret_done("EMPTY_RESULT\n") == "empty_result"

    def test_engine_error_returns_engine_error(self):
        assert interpret_done("ENGINE_ERROR\n") == "engine_error"
        assert interpret_done("ENGINE_ERROR_FINAL\n") == "engine_error"
        assert interpret_done("ENGINE_ENVIRONMENT_ERROR\n") == "engine_error"

    def test_nonzero_exit_returns_failed_exit(self):
        assert interpret_done("1\n") == "failed_exit"
        assert interpret_done("127\n") == "failed_exit"

    def test_unknown_string_returns_other(self):
        assert interpret_done("SOMETHING_ELSE\n") == "other"


class TestTaskStatus:
    def test_completed_success(self, tmp_path):
        exec_done = tmp_path / "jobs" / "done"
        exec_done.mkdir(parents=True)
        (exec_done / "uuid-1").write_text("0\n", encoding="utf-8")
        assert task_status("uuid-1", exec_done) == STATE_OK

    def test_completed_failed(self, tmp_path):
        exec_done = tmp_path / "jobs" / "done"
        exec_done.mkdir(parents=True)
        (exec_done / "uuid-1").write_text("1\n", encoding="utf-8")
        assert task_status("uuid-1", exec_done) == STATE_FAIL

    def test_completed_rejected(self, tmp_path):
        exec_done = tmp_path / "jobs" / "done"
        exec_done.mkdir(parents=True)
        (exec_done / "uuid-1").write_text("REJECTED\n", encoding="utf-8")
        assert task_status("uuid-1", exec_done) == STATE_REJECTED

    def test_completed_empty_result(self, tmp_path):
        exec_done = tmp_path / "jobs" / "done"
        exec_done.mkdir(parents=True)
        (exec_done / "uuid-1").write_text("EMPTY_RESULT\n", encoding="utf-8")
        assert task_status("uuid-1", exec_done) == STATE_EMPTY

    def test_completed_engine_error(self, tmp_path):
        exec_done = tmp_path / "jobs" / "done"
        exec_done.mkdir(parents=True)
        (exec_done / "uuid-1").write_text("ENGINE_ERROR\n", encoding="utf-8")
        assert task_status("uuid-1", exec_done) == STATE_ENGINE_ERROR

    def test_pending_deps_when_dep_not_done(self, tmp_path):
        exec_done = tmp_path / "jobs" / "done"
        exec_done.mkdir(parents=True)
        assert task_status(
            "uuid-1",
            exec_done,
            task_depends={"dep-uuid"},
        ) == STATE_PENDING_DEPS

    def test_running(self, tmp_path):
        exec_done = tmp_path / "jobs" / "done"
        exec_done.mkdir(parents=True)
        assert task_status(
            "uuid-1",
            exec_done,
            running_uuids={"uuid-1"},
        ) == STATE_RUNNING

    def test_deferred_when_not_done_or_running(self, tmp_path):
        exec_done = tmp_path / "jobs" / "done"
        exec_done.mkdir(parents=True)
        assert task_status(
            "uuid-1",
            exec_done,
            deferred_uuids={"uuid-1"},
        ) == STATE_DEFERRED

    def test_running_precedes_dependency_pending(self, tmp_path):
        exec_done = tmp_path / "jobs" / "done"
        exec_done.mkdir(parents=True)
        assert task_status(
            "uuid-1",
            exec_done,
            task_depends={"dep-uuid"},
            running_uuids={"uuid-1"},
        ) == STATE_RUNNING

    def test_pending_run_when_no_deps_and_not_running(self, tmp_path):
        exec_done = tmp_path / "jobs" / "done"
        exec_done.mkdir(parents=True)
        assert task_status("uuid-1", exec_done) == STATE_PENDING_RUN

    def test_deps_succeeded_not_pending(self, tmp_path):
        exec_done = tmp_path / "jobs" / "done"
        exec_done.mkdir(parents=True)
        (exec_done / "dep-uuid").write_text("0\n", encoding="utf-8")
        assert task_status(
            "uuid-1",
            exec_done,
            task_depends={"dep-uuid"},
        ) == STATE_PENDING_RUN


class TestStateIdentifiers:
    def test_constants_are_identifiers(self):
        assert (
            STATE_PENDING_DEPS,
            STATE_PENDING_RUN,
            STATE_RUNNING,
            STATE_DEFERRED,
            STATE_OK,
            STATE_FAIL,
            STATE_REJECTED,
            STATE_EMPTY,
            STATE_ENGINE_ERROR,
            STATE_UNKNOWN_DONE,
        ) == STATE_IDS

    def test_task_status_success_returns_ok(self, tmp_path):
        exec_done = tmp_path / "jobs" / "done"
        exec_done.mkdir(parents=True)
        (exec_done / "uuid-1").write_text("0\n", encoding="utf-8")
        assert task_status("uuid-1", exec_done) == "ok"

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (None, None),
            ("0\n", "ok"),
            ("1\n", "fail"),
            ("REJECTED\n", "rejected"),
            ("EMPTY_RESULT\n", "empty"),
            ("ENGINE_ERROR\n", "engine_error"),
            ("SOMETHING_ELSE\n", "unknown"),
        ],
    )
    def test_label_for_done_returns_identifier(self, raw, expected):
        assert label_for_done(raw) == expected


class TestStateLabel:
    @pytest.fixture(autouse=True)
    def _reset_pack_cache(self, monkeypatch):
        from ghdag.config.language import get_language_pack

        monkeypatch.delenv("GHDAG_LANGUAGE_PACK", raising=False)
        get_language_pack.cache_clear()
        yield
        get_language_pack.cache_clear()

    def test_default_pack_is_en(self):
        assert state_label("ok") == "Done (success)"
        assert state_label("running") == "Running"

    def test_all_ids_resolve_in_en(self):
        for state_id in STATE_IDS:
            assert state_label(state_id) == EN.state_labels[state_id]

    def test_explicit_pack_overrides(self):
        pack = LanguagePack(
            state_labels={**EN.state_labels, "ok": "OK!"}, ui=EN.ui,
        )
        assert state_label("ok", pack) == "OK!"

    def test_env_pack_overrides(self, tmp_path, monkeypatch):
        import yaml

        data = {
            "state_labels": {**EN.state_labels, "ok": "Finished fine"},
            "ui": dict(EN.ui),
        }
        path = tmp_path / "pack.yaml"
        path.write_text(yaml.safe_dump(data), encoding="utf-8")
        monkeypatch.setenv("GHDAG_LANGUAGE_PACK", str(path))
        assert state_label("ok") == "Finished fine"

    def test_unknown_identifier_returned_as_is(self):
        assert state_label("no_such_state") == "no_such_state"

    def test_exported_from_pipeline_package(self):
        import ghdag.pipeline

        assert ghdag.pipeline.state_label is state_label
        assert "state_label" in ghdag.pipeline.__all__
