"""Tests for loader.py template integrity validation (AC-3, AC-7) — Issue #3431."""

from __future__ import annotations

from pathlib import Path

import pytest

from ghdag.workflow.loader import ValidationError, load_workflows


def _write_workflow(tmp_path: Path, yaml_content: str) -> None:
    (tmp_path / "test.yml").write_text(yaml_content)


def _make_templates(base_dir: Path, *names: str) -> None:
    tdir = base_dir / "templates"
    tdir.mkdir(exist_ok=True)
    for name in names:
        (tdir / f"{name}.md").write_text(f"# {name}\n")


class TestAC3DependsResolution:
    def test_result_filename_without_depends_raises(self, tmp_path):
        """AC-3: ${p1_result_filename} in template without depends: [p1] → ValidationError."""
        yaml = 'name: test\ntriggers:\n  - label: "test:ready"\n    handler: impl\nhandlers:\n  impl:\n    steps:\n      - id: p1\n        template: p1\n        model: claude-sonnet-4-6\n      - id: p2\n        template: p2\n        model: claude-sonnet-4-6\n'
        _write_workflow(tmp_path, yaml)
        tdir = tmp_path / "templates"
        tdir.mkdir()
        (tdir / "p1.md").write_text("# step 1")
        (tdir / "p2.md").write_text("Result: ${p1_result_filename}")
        with pytest.raises(ValidationError, match="p1"):
            load_workflows(tmp_path)

    def test_result_content_without_depends_raises(self, tmp_path):
        """Same-handler ${p1_result_content} is rejected even without depends: [p1]."""
        yaml = 'name: test\ntriggers:\n  - label: "test:ready"\n    handler: impl\nhandlers:\n  impl:\n    steps:\n      - id: p1\n        template: p1\n        model: claude-sonnet-4-6\n      - id: p2\n        template: p2\n        model: claude-sonnet-4-6\n'
        _write_workflow(tmp_path, yaml)
        tdir = tmp_path / "templates"
        tdir.mkdir()
        (tdir / "p1.md").write_text("# step 1")
        (tdir / "p2.md").write_text("Content: ${p1_result_content}")
        with pytest.raises(ValidationError) as exc_info:
            load_workflows(tmp_path)
        msg = str(exc_info.value)
        hint = exc_info.value.hint or ""
        assert "impl" in msg
        assert "steps[1]" in msg
        assert "p2" in msg
        assert "p1_result_content" in msg
        assert "p1_result_filename" in hint
        assert "Put 'p1' in depends" in hint
        assert "enqueue" in hint.lower()

    def test_result_filename_with_depends_passes(self, tmp_path):
        """AC-3: ${p1_result_filename} with depends: [p1] → no error."""
        yaml = 'name: test\ntriggers:\n  - label: "test:ready"\n    handler: impl\nhandlers:\n  impl:\n    steps:\n      - id: p1\n        template: p1\n        model: claude-sonnet-4-6\n      - id: p2\n        template: p2\n        model: claude-sonnet-4-6\n        depends: [p1]\n'
        _write_workflow(tmp_path, yaml)
        tdir = tmp_path / "templates"
        tdir.mkdir()
        (tdir / "p1.md").write_text("# step 1")
        (tdir / "p2.md").write_text("Result: ${p1_result_filename}")
        configs = load_workflows(tmp_path)
        assert len(configs) == 1

    def test_unknown_id_in_result_content_passes(self, tmp_path):
        """${unknown_result_content} where 'unknown' is not a prior step id → no error."""
        yaml = 'name: test\ntriggers:\n  - label: "test:ready"\n    handler: impl\nhandlers:\n  impl:\n    steps:\n      - id: p1\n        template: p1\n        model: claude-sonnet-4-6\n'
        _write_workflow(tmp_path, yaml)
        tdir = tmp_path / "templates"
        tdir.mkdir()
        (tdir / "p1.md").write_text("${unknown_result_content}")
        configs = load_workflows(tmp_path)
        assert len(configs) == 1
