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


class TestAC7TemplateMissing:
    def test_missing_template_raises_validation_error(self, tmp_path):
        """AC-7: step template file not found raises ValidationError at load time."""
        yaml = """\
name: test
triggers:
  - label: "test:ready"
    handler: brushup
handlers:
  brushup:
    steps:
      - template: nonexistent
        model: claude-opus-4-6
"""
        _write_workflow(tmp_path, yaml)
        (tmp_path / "templates").mkdir()

        with pytest.raises(ValidationError, match="file not found"):
            load_workflows(tmp_path)

    def test_existing_template_passes(self, tmp_path):
        """AC-7: step with existing template file loads successfully."""
        yaml = """\
name: test
triggers:
  - label: "test:ready"
    handler: brushup
handlers:
  brushup:
    steps:
      - template: brushup
        model: claude-opus-4-6
"""
        _write_workflow(tmp_path, yaml)
        _make_templates(tmp_path, "brushup")

        configs = load_workflows(tmp_path)
        assert len(configs) == 1

    def test_missing_template_error_mentions_filename(self, tmp_path):
        """AC-7: error message includes the missing template filename."""
        yaml = """\
name: test
triggers:
  - label: "test:ready"
    handler: brushup
handlers:
  brushup:
    steps:
      - template: my-missing-template
        model: claude-opus-4-6
"""
        _write_workflow(tmp_path, yaml)
        (tmp_path / "templates").mkdir()

        with pytest.raises(ValidationError, match="my-missing-template"):
            load_workflows(tmp_path)


class TestAC3DependsResolution:
    def test_result_filename_without_depends_raises(self, tmp_path):
        """AC-3: ${p1_result_filename} in template without depends: [p1] → ValidationError."""
        yaml = """\
name: test
triggers:
  - label: "test:ready"
    handler: impl
handlers:
  impl:
    steps:
      - id: p1
        template: p1
        model: claude-sonnet-4-6
      - id: p2
        template: p2
        model: claude-sonnet-4-6
"""
        _write_workflow(tmp_path, yaml)
        tdir = tmp_path / "templates"
        tdir.mkdir()
        (tdir / "p1.md").write_text("# step 1")
        # p2 references p1 result but has no depends: [p1]
        (tdir / "p2.md").write_text("Result: ${p1_result_filename}")

        with pytest.raises(ValidationError, match="p1"):
            load_workflows(tmp_path)

    def test_result_content_without_depends_raises(self, tmp_path):
        """AC-3: ${p1_result_content} in template without depends: [p1] → ValidationError."""
        yaml = """\
name: test
triggers:
  - label: "test:ready"
    handler: impl
handlers:
  impl:
    steps:
      - id: p1
        template: p1
        model: claude-sonnet-4-6
      - id: p2
        template: p2
        model: claude-sonnet-4-6
"""
        _write_workflow(tmp_path, yaml)
        tdir = tmp_path / "templates"
        tdir.mkdir()
        (tdir / "p1.md").write_text("# step 1")
        (tdir / "p2.md").write_text("Content: ${p1_result_content}")

        with pytest.raises(ValidationError, match="p1"):
            load_workflows(tmp_path)

    def test_result_filename_with_depends_passes(self, tmp_path):
        """AC-3: ${p1_result_filename} with depends: [p1] → no error."""
        yaml = """\
name: test
triggers:
  - label: "test:ready"
    handler: impl
handlers:
  impl:
    steps:
      - id: p1
        template: p1
        model: claude-sonnet-4-6
      - id: p2
        template: p2
        model: claude-sonnet-4-6
        depends: [p1]
"""
        _write_workflow(tmp_path, yaml)
        tdir = tmp_path / "templates"
        tdir.mkdir()
        (tdir / "p1.md").write_text("# step 1")
        (tdir / "p2.md").write_text("Result: ${p1_result_filename}")

        configs = load_workflows(tmp_path)
        assert len(configs) == 1

    def test_result_content_with_depends_passes(self, tmp_path):
        """AC-3: ${p1_result_content} with depends: [p1] → no error."""
        yaml = """\
name: test
triggers:
  - label: "test:ready"
    handler: impl
handlers:
  impl:
    steps:
      - id: p1
        template: p1
        model: claude-sonnet-4-6
      - id: p2
        template: p2
        model: claude-sonnet-4-6
        depends: [p1]
"""
        _write_workflow(tmp_path, yaml)
        tdir = tmp_path / "templates"
        tdir.mkdir()
        (tdir / "p1.md").write_text("# step 1")
        (tdir / "p2.md").write_text("Content: ${p1_result_content}")

        configs = load_workflows(tmp_path)
        assert len(configs) == 1

    def test_unknown_id_in_result_var_passes(self, tmp_path):
        """AC-3: ${unknown_result_filename} where 'unknown' is not a step id → no error."""
        yaml = """\
name: test
triggers:
  - label: "test:ready"
    handler: impl
handlers:
  impl:
    steps:
      - id: p1
        template: p1
        model: claude-sonnet-4-6
"""
        _write_workflow(tmp_path, yaml)
        tdir = tmp_path / "templates"
        tdir.mkdir()
        # "unknown" is not a step id, so this should be allowed
        (tdir / "p1.md").write_text("${unknown_result_filename}")

        configs = load_workflows(tmp_path)
        assert len(configs) == 1

    def test_error_message_contains_handler_and_step_info(self, tmp_path):
        """AC-3: error message includes handler name and referenced step id."""
        yaml = """\
name: test
triggers:
  - label: "test:ready"
    handler: impl
handlers:
  impl:
    steps:
      - id: p1
        template: p1
        model: claude-sonnet-4-6
      - id: p2
        template: p2
        model: claude-sonnet-4-6
"""
        _write_workflow(tmp_path, yaml)
        tdir = tmp_path / "templates"
        tdir.mkdir()
        (tdir / "p1.md").write_text("# step 1")
        (tdir / "p2.md").write_text("${p1_result_filename}")

        with pytest.raises(ValidationError) as exc_info:
            load_workflows(tmp_path)

        msg = str(exc_info.value)
        assert "p1" in msg
        assert "impl" in msg

    def test_multi_step_chain_with_proper_depends_passes(self, tmp_path):
        """AC-3: 3-step chain with proper depends resolves correctly."""
        yaml = """\
name: test
triggers:
  - label: "test:ready"
    handler: impl
handlers:
  impl:
    steps:
      - id: p1
        template: p1
        model: claude-sonnet-4-6
      - id: p2
        template: p2
        model: claude-sonnet-4-6
        depends: [p1]
      - id: p3
        template: p3
        model: claude-sonnet-4-6
        depends: [p2]
"""
        _write_workflow(tmp_path, yaml)
        tdir = tmp_path / "templates"
        tdir.mkdir()
        (tdir / "p1.md").write_text("# p1")
        (tdir / "p2.md").write_text("${p1_result_filename}")
        (tdir / "p3.md").write_text("${p2_result_filename} ${p2_result_content}")

        configs = load_workflows(tmp_path)
        assert len(configs) == 1

    def test_step_without_id_template_is_not_checked(self, tmp_path):
        """AC-3: steps without id are ignored in the depends check."""
        yaml = """\
name: test
triggers:
  - label: "test:ready"
    handler: brushup
handlers:
  brushup:
    steps:
      - template: brushup
        model: claude-opus-4-6
"""
        _write_workflow(tmp_path, yaml)
        tdir = tmp_path / "templates"
        tdir.mkdir()
        # Template references something that looks like a result var,
        # but since there are no step ids, no validation should fail
        (tdir / "brushup.md").write_text("${some_result_filename}")

        configs = load_workflows(tmp_path)
        assert len(configs) == 1
