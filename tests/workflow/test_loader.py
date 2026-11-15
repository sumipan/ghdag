"""Tests for ghdag.workflow — TC-1 to TC-11 (Issue #79 extended schema)."""

from __future__ import annotations

import logging

import pytest
from ghdag_workflow_conftest_helpers import _make_templates, _setup_extended_yaml

from ghdag.core.models.workflow import TriggerConfig, WorkflowConfig
from ghdag.workflow.loader import ValidationError, load_workflows


class TestTC1YamlParseOk:
    def test_load_returns_config_list(self, tmp_path):
        _setup_extended_yaml(tmp_path)
        configs = load_workflows(tmp_path)
        assert len(configs) == 1

    def test_workflow_name(self, tmp_path):
        _setup_extended_yaml(tmp_path)
        config = load_workflows(tmp_path)[0]
        assert config.name == "stash-pipeline"

    def test_trigger_count(self, tmp_path):
        _setup_extended_yaml(tmp_path)
        config = load_workflows(tmp_path)[0]
        assert len(config.triggers) == 4

    def test_trigger_handler_field(self, tmp_path):
        _setup_extended_yaml(tmp_path)
        config = load_workflows(tmp_path)[0]
        assert config.triggers[0].label == "pipeline:draft-ready"
        assert config.triggers[0].handler == "brushup"

    def test_handlers_is_dict(self, tmp_path):
        _setup_extended_yaml(tmp_path)
        config = load_workflows(tmp_path)[0]
        assert isinstance(config.handlers, dict)
        assert "impl" in config.handlers

    def test_impl_handler_has_three_steps(self, tmp_path):
        _setup_extended_yaml(tmp_path)
        config = load_workflows(tmp_path)[0]
        assert len(config.handlers["impl"].steps) == 3

    def test_on_trigger_issue_context(self, tmp_path):
        _setup_extended_yaml(tmp_path)
        config = load_workflows(tmp_path)[0]
        assert config.handlers["brushup"].on_trigger is not None
        assert config.handlers["brushup"].on_trigger.issue_context is True

    def test_reset_handler_type(self, tmp_path):
        _setup_extended_yaml(tmp_path)
        config = load_workflows(tmp_path)[0]
        assert config.handlers["reset"].type == "reset"

    def test_steps_model_missing_raises_validation_error(self, tmp_path):
        yaml_no_model = 'name: test\ntriggers:\n  - label: "pipeline:draft-ready"\n    handler: brushup\nhandlers:\n  brushup:\n    steps:\n      - template: brushup\n'
        (tmp_path / "bad.yml").write_text(yaml_no_model, encoding="utf-8")
        with pytest.raises(ValidationError, match="model"):
            load_workflows(tmp_path)

    def test_trigger_missing_handler_raises_validation_error(self, tmp_path):
        yaml_no_handler = 'name: test\ntriggers:\n  - label: "pipeline:draft-ready"\nhandlers:\n  brushup:\n    steps:\n      - template: brushup\n        model: claude-sonnet-4-6\n'
        (tmp_path / "bad.yml").write_text(yaml_no_handler, encoding="utf-8")
        with pytest.raises(ValidationError, match="handler"):
            load_workflows(tmp_path)

    def test_trigger_missing_label_raises_validation_error(self, tmp_path):
        yaml_no_label = "name: test\ntriggers:\n  - handler: brushup\nhandlers:\n  brushup:\n    steps:\n      - template: brushup\n        model: claude-sonnet-4-6\n"
        (tmp_path / "bad.yml").write_text(yaml_no_label, encoding="utf-8")
        with pytest.raises(ValidationError, match="label"):
            load_workflows(tmp_path)


class TestTC9ContextHook:
    def test_yaml_context_hook_parsed(self, tmp_path):
        """context_hook is parsed from YAML into HandlerConfig"""
        yaml_content = 'name: test-pipeline\ntriggers:\n  - label: "pipeline:draft-ready"\n    handler: brushup\nhandlers:\n  brushup:\n    context_hook: "python -m my_hook"\n    steps:\n      - template: brushup\n        model: claude-opus-4-6\n'
        (tmp_path / "test.yml").write_text(yaml_content, encoding="utf-8")
        _make_templates(tmp_path, "brushup")
        configs = load_workflows(tmp_path)
        assert configs[0].handlers["brushup"].context_hook == "python -m my_hook"


class TestTC10TemplateDir:
    def test_template_dir_absolute_path_preserved(self, tmp_path):
        """Absolute template_dir is kept as-is"""
        abs_templates = tmp_path / "abs-templates"
        abs_templates.mkdir()
        (abs_templates / "brushup.md").write_text("# brushup\n", encoding="utf-8")
        yaml_content = f'name: test-pipeline\ntemplate_dir: {abs_templates}\ntriggers:\n  - label: "pipeline:draft-ready"\n    handler: brushup\nhandlers:\n  brushup:\n    steps:\n      - template: brushup\n        model: claude-opus-4-6\n'
        (tmp_path / "test.yml").write_text(yaml_content, encoding="utf-8")
        configs = load_workflows(tmp_path)
        assert configs[0].template_dir == str(abs_templates)

    def test_workflow_config_template_dir_default(self):
        """WorkflowConfig.template_dir defaults to None"""
        config = WorkflowConfig(name="test", triggers=[TriggerConfig(label="x", handler="y")], handlers={})
        assert config.template_dir is None


class TestTC11StaticValidation:
    """Test group covering AC-3–AC-10"""

    def test_handler_reference_mismatch(self, tmp_path):
        """AC-3: trigger referencing nonexistent handler → ValueError"""
        yaml_content = 'name: test\ntriggers:\n  - label: "pipeline:draft-ready"\n    handler: nonexistent\nhandlers:\n  brushup:\n    steps:\n      - template: brushup\n        model: claude-sonnet-4-6\n'
        (tmp_path / "bad.yml").write_text(yaml_content, encoding="utf-8")
        with pytest.raises(ValidationError, match="not defined in handlers"):
            load_workflows(tmp_path)

    def test_template_default_dir(self, tmp_path):
        """AC-7: without template_dir, 'templates' is used as default"""
        yaml_content = 'name: test\ntriggers:\n  - label: "pipeline:draft-ready"\n    handler: brushup\nhandlers:\n  brushup:\n    steps:\n      - template: brushup\n        model: claude-sonnet-4-6\n'
        (tmp_path / "test.yml").write_text(yaml_content, encoding="utf-8")
        with pytest.raises(ValidationError) as exc_info:
            load_workflows(tmp_path)
        assert "templates" in str(exc_info.value)

    def test_context_hook_not_found_warns(self, tmp_path, caplog):
        """AC-9: missing context_hook command logs warning (not an error)"""
        yaml_content = 'name: test\ntriggers:\n  - label: "pipeline:draft-ready"\n    handler: brushup\nhandlers:\n  brushup:\n    context_hook: "nonexistent_cmd_ghdag_test_xyz"\n    steps:\n      - template: brushup\n        model: claude-sonnet-4-6\n'
        (tmp_path / "test.yml").write_text(yaml_content, encoding="utf-8")
        _make_templates(tmp_path, "brushup")
        with caplog.at_level(logging.WARNING, logger="ghdag.workflow.loader"):
            configs = load_workflows(tmp_path)
        assert len(configs) == 1
        assert any(("nonexistent_cmd_ghdag_test_xyz" in r.message for r in caplog.records))


class TestLoadTimeStepValidation:
    """Issue #6167 — engine, model, permission, depends validated at load time."""

    def test_unknown_permission_preset_raises(self, tmp_path):
        yaml_content = 'name: test\ntriggers:\n  - label: "pipeline:draft-ready"\n    handler: h\nhandlers:\n  h:\n    steps:\n      - template: step1\n        model: claude-sonnet-4-6\n        permission: unknown_preset\n'
        (tmp_path / "test.yml").write_text(yaml_content, encoding="utf-8")
        _make_templates(tmp_path, "step1")
        with pytest.raises(ValidationError) as exc_info:
            load_workflows(tmp_path)
        err = exc_info.value
        assert err.code == "STEP_PERMISSION_UNKNOWN"
        assert err.hint is not None and "text_only" in err.hint

    def test_unknown_depends_id_raises_with_defined_ids_in_hint(self, tmp_path):
        yaml_content = 'name: test\ntriggers:\n  - label: "pipeline:draft-ready"\n    handler: h\nhandlers:\n  h:\n    steps:\n      - id: existing\n        template: step1\n        model: claude-sonnet-4-6\n      - id: bad\n        template: step2\n        model: claude-sonnet-4-6\n        depends: [nope]\n'
        (tmp_path / "test.yml").write_text(yaml_content, encoding="utf-8")
        _make_templates(tmp_path, "step1", "step2")
        with pytest.raises(ValidationError) as exc_info:
            load_workflows(tmp_path)
        err = exc_info.value
        assert err.code == "STEP_DEPENDENCY_UNKNOWN"
        assert err.hint is not None and "existing" in err.hint
