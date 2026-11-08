"""Tests for ghdag validate — Issue #6169."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _make_templates(workflow_dir: Path, *names: str) -> None:
    templates = workflow_dir / "templates"
    templates.mkdir(exist_ok=True)
    for name in names:
        (templates / f"{name}.md").write_text(f"# {name}\n", encoding="utf-8")


class TestValidateCli:
    def test_valid_workflows_exit_zero_and_summary_line(self, tmp_path, capsys):
        from ghdag.cli import main

        yaml_content = """\
name: test
triggers:
  - label: "pipeline:draft-ready"
    handler: h
handlers:
  h:
    steps:
      - template: step1
        model: claude-sonnet-4-6
"""
        (tmp_path / "test.yml").write_text(yaml_content, encoding="utf-8")
        _make_templates(tmp_path, "step1")

        main(["validate", str(tmp_path)])
        captured = capsys.readouterr()
        assert captured.out.strip().splitlines()[-1] == "validated 1 file(s), 0 error(s)"

    def test_unknown_engine_json_exit_three(self, tmp_path, capsys):
        from ghdag.cli import main

        yaml_content = """\
name: test
triggers:
  - label: "pipeline:draft-ready"
    handler: h
handlers:
  h:
    steps:
      - template: step1
        model: claude-sonnet-4-6
        engine: nonexistent
"""
        (tmp_path / "bad.yml").write_text(yaml_content, encoding="utf-8")
        _make_templates(tmp_path, "step1")

        with pytest.raises(SystemExit) as exc:
            main(["validate", str(tmp_path), "--json"])
        assert exc.value.code == 3
        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        assert payload["valid"] is False
        assert payload["errors"][0]["code"] == "STEP_ENGINE_UNKNOWN"
        assert str(payload["errors"][0]["file"]).endswith("bad.yml")

    def test_two_invalid_files_yield_two_errors(self, tmp_path, capsys):
        from ghdag.cli import main

        bad_engine = """\
name: one
triggers:
  - label: "pipeline:draft-ready"
    handler: h
handlers:
  h:
    steps:
      - template: step1
        model: claude-sonnet-4-6
        engine: nonexistent
"""
        bad_model = """\
name: two
triggers:
  - label: "pipeline:draft-ready"
    handler: h
handlers:
  h:
    steps:
      - template: step1
        engine: claude
"""
        (tmp_path / "a.yml").write_text(bad_engine, encoding="utf-8")
        (tmp_path / "b.yml").write_text(bad_model, encoding="utf-8")
        _make_templates(tmp_path, "step1")

        with pytest.raises(SystemExit) as exc:
            main(["validate", str(tmp_path), "--json"])
        assert exc.value.code == 3
        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        assert len(payload["errors"]) == 2

    def test_missing_directory_exit_two(self, capsys):
        from ghdag.cli import main

        with pytest.raises(SystemExit) as exc:
            main(["validate", "/nonexistent-dir"])
        assert exc.value.code == 2
        captured = capsys.readouterr()
        first_stderr = captured.err.splitlines()[0]
        assert first_stderr.startswith("error[WORKFLOWS_DIR_NOT_FOUND]:")
