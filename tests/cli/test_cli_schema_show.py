"""Tests for ghdag schema list/show — Issue #6169."""

from __future__ import annotations

import json

import pytest

from ghdag.schemas import SCHEMA_NAMES, load_schema


class TestSchemaCli:
    def test_schema_list_json_names_match_schema_names(self, capsys):
        from ghdag.cli import main

        main(["schema", "list", "--json"])
        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        names = {item["name"] for item in payload["schemas"]}
        assert names == set(SCHEMA_NAMES)

    def test_schema_show_exec_record_matches_load_schema(self, capsys):
        from ghdag.cli import main

        main(["schema", "show", "exec-record"])
        captured = capsys.readouterr()
        assert json.loads(captured.out) == load_schema("exec-record")

    def test_schema_show_unknown_exit_three(self, capsys):
        from ghdag.cli import main

        with pytest.raises(SystemExit) as exc:
            main(["schema", "show", "nope"])
        assert exc.value.code == 3
        captured = capsys.readouterr()
        first_stderr = captured.err.splitlines()[0]
        assert first_stderr.startswith("error[SCHEMA_UNKNOWN]:")
