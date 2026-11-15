"""Strict done-marker semantics (Issue #5372)."""

from __future__ import annotations

import json


class TestInterpretDoneStrict:


    def test_json_ok_is_success(self) -> None:
        from ghdag.io.done import interpret_done

        body = json.dumps({"status": "ok", "exit_code": 0, "ts": "2026-01-01T00:00:00+00:00"})
        assert interpret_done(body) == "success"

    def test_json_non_ok_is_not_success(self) -> None:
        from ghdag.io.done import interpret_done

        body = json.dumps({"status": "failed", "exit_code": 1})
        assert interpret_done(body) != "success"

    def test_invalid_json_line_is_other(self) -> None:
        from ghdag.io.done import interpret_done

        assert interpret_done("{not-json") == "other"


