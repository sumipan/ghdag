"""Tests for ghdag.workflow — TC-1 to TC-11 (Issue #79 extended schema)."""

from __future__ import annotations

import logging

import pytest

# ---------------------------------------------------------------------------
# TC-1: YAML parse (happy path)
# ---------------------------------------------------------------------------
from ghdag_workflow_conftest_helpers import (
    _make_templates,
    _setup_extended_yaml,
)

from ghdag.core.models.workflow import (
    TriggerConfig,
    WorkflowConfig,
)
from ghdag.pipeline.llm_pipeline import DependencyError
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

    def test_impl_steps_ids_and_models(self, tmp_path):
        _setup_extended_yaml(tmp_path)
        config = load_workflows(tmp_path)[0]
        steps = config.handlers["impl"].steps
        assert steps[0].id == "p1"
        assert steps[0].model == "claude-sonnet-4-6"
        assert steps[1].id == "p2"
        assert steps[1].depends == ["p1"]
        assert steps[2].id == "p3"
        assert steps[2].depends == ["p2"]

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
        yaml_no_model = """\
name: test
triggers:
  - label: "pipeline:draft-ready"
    handler: brushup
handlers:
  brushup:
    steps:
      - template: brushup
"""
        (tmp_path / "bad.yml").write_text(yaml_no_model, encoding="utf-8")
        with pytest.raises(ValidationError, match="model"):
            load_workflows(tmp_path)

    def test_trigger_missing_handler_raises_validation_error(self, tmp_path):
        yaml_no_handler = """\
name: test
triggers:
  - label: "pipeline:draft-ready"
handlers:
  brushup:
    steps:
      - template: brushup
        model: claude-sonnet-4-6
"""
        (tmp_path / "bad.yml").write_text(yaml_no_handler, encoding="utf-8")
        with pytest.raises(ValidationError, match="handler"):
            load_workflows(tmp_path)

    def test_trigger_missing_label_raises_validation_error(self, tmp_path):
        yaml_no_label = """\
name: test
triggers:
  - handler: brushup
handlers:
  brushup:
    steps:
      - template: brushup
        model: claude-sonnet-4-6
"""
        (tmp_path / "bad.yml").write_text(yaml_no_label, encoding="utf-8")
        with pytest.raises(ValidationError, match="label"):
            load_workflows(tmp_path)

    def test_unknown_keys_ignored(self, tmp_path):
        yaml_unknown = """\
name: test
triggers:
  - label: "pipeline:draft-ready"
    handler: brushup
handlers:
  brushup:
    steps:
      - template: brushup
        model: claude-sonnet-4-6
        unknown_future_key: some_value
polling_interval: 30
"""
        (tmp_path / "test.yml").write_text(yaml_unknown, encoding="utf-8")
        _make_templates(tmp_path, "brushup")
        with pytest.raises(ValidationError, match=r"handlers\.brushup\.steps\[0\]\.unknown_future_key") as exc_info:
            load_workflows(tmp_path)
        assert exc_info.value.hint is not None and "Allowed:" in exc_info.value.hint


class TestTC8StashPipelineYaml:
    def test_stash_pipeline_parseable(self, tmp_path):
        stash_pipeline_content = """\
name: stash-pipeline

triggers:
  - label: "pipeline:draft-ready"
    handler: brushup
  - label: "pipeline:develop-ready"
    handler: impl
  - label: "pipeline:merge-ready"
    handler: merge
  - label: "pipeline:reset"
    handler: reset

handlers:
  brushup:
    on_trigger:
      issue_context: true
    steps:
      - template: brushup
        model: claude-opus-4-6

  impl:
    on_trigger:
      issue_context: true
    steps:
      - id: p1
        template: p1-implement
        model: claude-sonnet-4-6
      - id: p2
        template: p2-verify
        model: claude-sonnet-4-6
        depends: [p1]
      - id: p3
        template: p3-report
        model: claude-sonnet-4-6
        depends: [p2]

  merge:
    steps:
      - template: merge
        model: claude-opus-4-6

  reset:
    type: reset

polling_interval: 30
"""
        (tmp_path / "stash-pipeline.yml").write_text(stash_pipeline_content, encoding="utf-8")
        _make_templates(tmp_path, "brushup", "p1-implement", "p2-verify", "p3-report", "merge")
        configs = load_workflows(tmp_path)
        assert len(configs) == 1
        config = configs[0]
        assert config.name == "stash-pipeline"
        assert config.handlers["impl"].steps[0].id == "p1"


class TestTC9ContextHook:

    def test_yaml_context_hook_parsed(self, tmp_path):
        """context_hook is parsed from YAML into HandlerConfig"""
        yaml_content = """\
name: test-pipeline
triggers:
  - label: "pipeline:draft-ready"
    handler: brushup
handlers:
  brushup:
    context_hook: "python -m my_hook"
    steps:
      - template: brushup
        model: claude-opus-4-6
"""
        (tmp_path / "test.yml").write_text(yaml_content, encoding="utf-8")
        _make_templates(tmp_path, "brushup")
        configs = load_workflows(tmp_path)
        assert configs[0].handlers["brushup"].context_hook == "python -m my_hook"

    def test_yaml_no_context_hook_is_none(self, tmp_path):
        """context_hook is None when omitted"""
        yaml_content = """\
name: test-pipeline
triggers:
  - label: "pipeline:draft-ready"
    handler: brushup
handlers:
  brushup:
    steps:
      - template: brushup
        model: claude-opus-4-6
"""
        (tmp_path / "test.yml").write_text(yaml_content, encoding="utf-8")
        _make_templates(tmp_path, "brushup")
        configs = load_workflows(tmp_path)
        assert configs[0].handlers["brushup"].context_hook is None


class TestTC10TemplateDir:
    def test_template_dir_parsed_from_yaml(self, tmp_path):
        """template_dir is parsed from YAML into WorkflowConfig"""
        my_templates = tmp_path / "my-templates"
        my_templates.mkdir()
        (my_templates / "brushup.md").write_text("# brushup\n", encoding="utf-8")
        yaml_content = """\
name: test-pipeline
template_dir: my-templates
triggers:
  - label: "pipeline:draft-ready"
    handler: brushup
handlers:
  brushup:
    steps:
      - template: brushup
        model: claude-opus-4-6
"""
        (tmp_path / "test.yml").write_text(yaml_content, encoding="utf-8")
        configs = load_workflows(tmp_path)
        assert configs[0].template_dir is not None
        # Relative path resolved against workflow directory
        assert configs[0].template_dir == str(tmp_path.resolve() / "my-templates")

    def test_template_dir_none_when_not_specified(self, tmp_path):
        """template_dir is None when omitted"""
        yaml_content = """\
name: test-pipeline
triggers:
  - label: "pipeline:draft-ready"
    handler: brushup
handlers:
  brushup:
    steps:
      - template: brushup
        model: claude-opus-4-6
"""
        (tmp_path / "test.yml").write_text(yaml_content, encoding="utf-8")
        _make_templates(tmp_path, "brushup")
        configs = load_workflows(tmp_path)
        assert configs[0].template_dir is None

    def test_template_dir_absolute_path_preserved(self, tmp_path):
        """Absolute template_dir is kept as-is"""
        abs_templates = tmp_path / "abs-templates"
        abs_templates.mkdir()
        (abs_templates / "brushup.md").write_text("# brushup\n", encoding="utf-8")
        yaml_content = f"""\
name: test-pipeline
template_dir: {abs_templates}
triggers:
  - label: "pipeline:draft-ready"
    handler: brushup
handlers:
  brushup:
    steps:
      - template: brushup
        model: claude-opus-4-6
"""
        (tmp_path / "test.yml").write_text(yaml_content, encoding="utf-8")
        configs = load_workflows(tmp_path)
        assert configs[0].template_dir == str(abs_templates)

    def test_template_dir_relative_resolved_against_workflow_dir(self, tmp_path):
        """Relative template_dir is resolved against the workflow directory"""
        workflows_dir = tmp_path / "workflows"
        workflows_dir.mkdir()
        shared_templates = tmp_path / "shared-templates"
        shared_templates.mkdir()
        (shared_templates / "brushup.md").write_text("# brushup\n", encoding="utf-8")
        yaml_content = """\
name: test-pipeline
template_dir: ../shared-templates
triggers:
  - label: "pipeline:draft-ready"
    handler: brushup
handlers:
  brushup:
    steps:
      - template: brushup
        model: claude-opus-4-6
"""
        (workflows_dir / "test.yml").write_text(yaml_content, encoding="utf-8")
        configs = load_workflows(workflows_dir)
        expected = str(workflows_dir.resolve() / ".." / "shared-templates")
        assert configs[0].template_dir == expected

    def test_workflow_config_template_dir_default(self):
        """WorkflowConfig.template_dir defaults to None"""
        config = WorkflowConfig(
            name="test",
            triggers=[TriggerConfig(label="x", handler="y")],
            handlers={},
        )
        assert config.template_dir is None


class TestTCEngineKey:
    """TC-engine-1–4: engine key priority and agent fallback"""

    def test_engine_key_only(self, tmp_path):
        """TC-engine-1: engine: cursor only → StepConfig.engine == 'cursor'"""
        yaml_content = """\
name: test-pipeline
triggers:
  - label: "pipeline:draft-ready"
    handler: brushup
handlers:
  brushup:
    steps:
      - template: brushup
        model: claude-opus-4-6
        engine: cursor
"""
        (tmp_path / "test.yml").write_text(yaml_content, encoding="utf-8")
        _make_templates(tmp_path, "brushup")
        configs = load_workflows(tmp_path)
        step = configs[0].handlers["brushup"].steps[0]
        assert step.engine == "cursor"

    def test_agent_only_fallback(self, tmp_path):
        """TC-engine-2 (updated): agent: gemini only → unknown key rejected (was ignored)"""
        yaml_content = """\
name: test-pipeline
triggers:
  - label: "pipeline:draft-ready"
    handler: brushup
handlers:
  brushup:
    steps:
      - template: brushup
        model: claude-opus-4-6
        agent: gemini
"""
        (tmp_path / "test.yml").write_text(yaml_content, encoding="utf-8")
        _make_templates(tmp_path, "brushup")
        with pytest.raises(ValidationError, match=r"handlers\.brushup\.steps\[0\]\.agent") as exc_info:
            load_workflows(tmp_path)
        assert exc_info.value.hint is not None and "Allowed:" in exc_info.value.hint

    def test_engine_takes_priority_over_agent(self, tmp_path):
        """TC-engine-3 (updated): agent key rejected even when engine is set"""
        yaml_content = """\
name: test-pipeline
triggers:
  - label: "pipeline:draft-ready"
    handler: brushup
handlers:
  brushup:
    steps:
      - template: brushup
        model: claude-opus-4-6
        engine: cursor
        agent: claude
"""
        (tmp_path / "test.yml").write_text(yaml_content, encoding="utf-8")
        _make_templates(tmp_path, "brushup")
        with pytest.raises(ValidationError, match=r"handlers\.brushup\.steps\[0\]\.agent") as exc_info:
            load_workflows(tmp_path)
        assert exc_info.value.hint is not None and "Allowed:" in exc_info.value.hint

    def test_neither_engine_nor_agent_defaults_to_claude(self, tmp_path):
        """TC-engine-4: neither set → default 'claude'"""
        yaml_content = """\
name: test-pipeline
triggers:
  - label: "pipeline:draft-ready"
    handler: brushup
handlers:
  brushup:
    steps:
      - template: brushup
        model: claude-opus-4-6
"""
        (tmp_path / "test.yml").write_text(yaml_content, encoding="utf-8")
        _make_templates(tmp_path, "brushup")
        configs = load_workflows(tmp_path)
        step = configs[0].handlers["brushup"].steps[0]
        assert step.engine == "claude"


class TestTC11StaticValidation:
    """Test group covering AC-3–AC-10"""

    def test_handler_reference_mismatch(self, tmp_path):
        """AC-3: trigger referencing nonexistent handler → ValueError"""
        yaml_content = """\
name: test
triggers:
  - label: "pipeline:draft-ready"
    handler: nonexistent
handlers:
  brushup:
    steps:
      - template: brushup
        model: claude-sonnet-4-6
"""
        (tmp_path / "bad.yml").write_text(yaml_content, encoding="utf-8")
        with pytest.raises(ValidationError, match="not defined in handlers"):
            load_workflows(tmp_path)

    def test_handler_reference_mismatch_includes_handler_name(self, tmp_path):
        """AC-4: error message includes unknown handler name and defined list"""
        yaml_content = """\
name: test
triggers:
  - label: "pipeline:draft-ready"
    handler: nonexistent
handlers:
  brushup:
    steps:
      - template: brushup
        model: claude-sonnet-4-6
"""
        (tmp_path / "bad.yml").write_text(yaml_content, encoding="utf-8")
        with pytest.raises(ValidationError) as exc_info:
            load_workflows(tmp_path)
        err = exc_info.value
        assert err.code == "TRIGGER_HANDLER_UNDEFINED"
        assert err.hint is not None and "brushup" in err.hint
        assert "nonexistent" in str(err)

    def test_template_file_not_found(self, tmp_path):
        """AC-5, AC-6: missing template file → ValueError"""
        yaml_content = """\
name: test
triggers:
  - label: "pipeline:draft-ready"
    handler: brushup
handlers:
  brushup:
    steps:
      - template: brushup
        model: claude-sonnet-4-6
"""
        (tmp_path / "test.yml").write_text(yaml_content, encoding="utf-8")
        (tmp_path / "templates").mkdir()
        with pytest.raises(ValidationError) as exc_info:
            load_workflows(tmp_path)
        assert "file not found" in str(exc_info.value)
        assert "brushup" in str(exc_info.value)

    def test_template_default_dir(self, tmp_path):
        """AC-7: without template_dir, 'templates' is used as default"""
        yaml_content = """\
name: test
triggers:
  - label: "pipeline:draft-ready"
    handler: brushup
handlers:
  brushup:
    steps:
      - template: brushup
        model: claude-sonnet-4-6
"""
        (tmp_path / "test.yml").write_text(yaml_content, encoding="utf-8")
        with pytest.raises(ValidationError) as exc_info:
            load_workflows(tmp_path)
        assert "templates" in str(exc_info.value)

    def test_reset_handler_skips_template_check(self, tmp_path):
        """AC-8: type: reset handler skips template validation (empty steps)"""
        yaml_content = """\
name: test
triggers:
  - label: "pipeline:reset"
    handler: reset
handlers:
  reset:
    type: reset
"""
        (tmp_path / "test.yml").write_text(yaml_content, encoding="utf-8")
        configs = load_workflows(tmp_path)
        assert len(configs) == 1

    def test_context_hook_not_found_warns(self, tmp_path, caplog):
        """AC-9: missing context_hook command logs warning (not an error)"""
        yaml_content = """\
name: test
triggers:
  - label: "pipeline:draft-ready"
    handler: brushup
handlers:
  brushup:
    context_hook: "nonexistent_cmd_ghdag_test_xyz"
    steps:
      - template: brushup
        model: claude-sonnet-4-6
"""
        (tmp_path / "test.yml").write_text(yaml_content, encoding="utf-8")
        _make_templates(tmp_path, "brushup")
        with caplog.at_level(logging.WARNING, logger="ghdag.workflow.loader"):
            configs = load_workflows(tmp_path)
        assert len(configs) == 1
        assert any(
            "nonexistent_cmd_ghdag_test_xyz" in r.message for r in caplog.records
        )

    def test_context_hook_multi_token_checks_first_only(self, tmp_path, caplog):
        """AC-10: multi-token context_hook validates only the first token (python)"""
        yaml_content = """\
name: test
triggers:
  - label: "pipeline:draft-ready"
    handler: brushup
handlers:
  brushup:
    context_hook: "python -m nonexistent_module_xyz"
    steps:
      - template: brushup
        model: claude-sonnet-4-6
"""
        (tmp_path / "test.yml").write_text(yaml_content, encoding="utf-8")
        _make_templates(tmp_path, "brushup")
        with caplog.at_level(logging.WARNING, logger="ghdag.workflow.loader"):
            configs = load_workflows(tmp_path)
        assert len(configs) == 1
        assert not any("python" in r.message for r in caplog.records)

    def test_no_warning_without_context_hook(self, tmp_path, caplog):
        """AC-2: handlers without context_hook emit no warning"""
        yaml_content = """\
name: test
triggers:
  - label: "pipeline:draft-ready"
    handler: brushup
handlers:
  brushup:
    steps:
      - template: brushup
        model: claude-sonnet-4-6
"""
        (tmp_path / "test.yml").write_text(yaml_content, encoding="utf-8")
        _make_templates(tmp_path, "brushup")
        with caplog.at_level(logging.WARNING, logger="ghdag.workflow.loader"):
            configs = load_workflows(tmp_path)
        assert len(configs) == 1
        assert len(caplog.records) == 0

    def test_valid_workflow_loads_successfully(self, tmp_path):
        """AC-1: valid workflow (templates exist, handler refs correct) succeeds"""
        yaml_content = """\
name: test
triggers:
  - label: "pipeline:draft-ready"
    handler: brushup
  - label: "pipeline:reset"
    handler: reset
handlers:
  brushup:
    steps:
      - template: brushup
        model: claude-opus-4-6
  reset:
    type: reset
"""
        (tmp_path / "test.yml").write_text(yaml_content, encoding="utf-8")
        _make_templates(tmp_path, "brushup")
        configs = load_workflows(tmp_path)
        assert len(configs) == 1
        assert configs[0].name == "test"


class TestLoadTimeStepValidation:
    """Issue #6167 — engine, model, permission, depends validated at load time."""

    def test_unknown_engine_raises_with_code_and_hint(self, tmp_path):
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
        (tmp_path / "test.yml").write_text(yaml_content, encoding="utf-8")
        _make_templates(tmp_path, "step1")
        with pytest.raises(ValidationError) as exc_info:
            load_workflows(tmp_path)
        err = exc_info.value
        assert err.code == "STEP_ENGINE_UNKNOWN"
        assert err.hint is not None and "claude" in err.hint

    def test_shell_step_without_model_loads_with_none_model(self, tmp_path):
        yaml_content = """\
name: test
triggers:
  - label: "pipeline:draft-ready"
    handler: h
handlers:
  h:
    steps:
      - template: step1
        engine: shell
"""
        (tmp_path / "test.yml").write_text(yaml_content, encoding="utf-8")
        _make_templates(tmp_path, "step1")
        config = load_workflows(tmp_path)[0]
        step = config.handlers["h"].steps[0]
        assert step.engine == "shell"
        assert step.model is None

    def test_non_shell_missing_model_raises_step_model_required(self, tmp_path):
        yaml_content = """\
name: test
triggers:
  - label: "pipeline:draft-ready"
    handler: h
handlers:
  h:
    steps:
      - template: step1
        engine: claude
"""
        (tmp_path / "test.yml").write_text(yaml_content, encoding="utf-8")
        _make_templates(tmp_path, "step1")
        with pytest.raises(ValidationError) as exc_info:
            load_workflows(tmp_path)
        assert exc_info.value.code == "STEP_MODEL_REQUIRED"

    def test_unknown_permission_preset_raises(self, tmp_path):
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
        permission: unknown_preset
"""
        (tmp_path / "test.yml").write_text(yaml_content, encoding="utf-8")
        _make_templates(tmp_path, "step1")
        with pytest.raises(ValidationError) as exc_info:
            load_workflows(tmp_path)
        err = exc_info.value
        assert err.code == "STEP_PERMISSION_UNKNOWN"
        assert err.hint is not None and "text_only" in err.hint

    def test_unknown_depends_id_raises_with_defined_ids_in_hint(self, tmp_path):
        yaml_content = """\
name: test
triggers:
  - label: "pipeline:draft-ready"
    handler: h
handlers:
  h:
    steps:
      - id: existing
        template: step1
        model: claude-sonnet-4-6
      - id: bad
        template: step2
        model: claude-sonnet-4-6
        depends: [nope]
"""
        (tmp_path / "test.yml").write_text(yaml_content, encoding="utf-8")
        _make_templates(tmp_path, "step1", "step2")
        with pytest.raises(ValidationError) as exc_info:
            load_workflows(tmp_path)
        err = exc_info.value
        assert err.code == "STEP_DEPENDENCY_UNKNOWN"
        assert err.hint is not None and "existing" in err.hint

    def test_dependency_cycle_surfaces_dependency_error_as_cause(self, tmp_path):
        yaml_content = """\
name: test
triggers:
  - label: "pipeline:draft-ready"
    handler: h
handlers:
  h:
    steps:
      - id: a
        template: step1
        model: claude-sonnet-4-6
        depends: [b]
      - id: b
        template: step2
        model: claude-sonnet-4-6
        depends: [a]
"""
        (tmp_path / "test.yml").write_text(yaml_content, encoding="utf-8")
        _make_templates(tmp_path, "step1", "step2")
        with pytest.raises(ValidationError) as exc_info:
            load_workflows(tmp_path)
        err = exc_info.value
        assert err.code == "STEP_DEPENDENCY_CYCLE"
        assert isinstance(err.__cause__, DependencyError)
