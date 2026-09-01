from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from ghdag.core.capabilities import TEXT_ONLY
from ghdag.core.command import render_exec_command
from ghdag.core.engine_spec import ENGINE_SPECS
from ghdag.pipeline.audit import AuditContext
from ghdag.pipeline.llm_pipeline import DependencyError, LLMPipelineAPI
from ghdag.workflow.loader import ValidationError, load_workflows
from ghdag.workflow.schema import StepConfig

_TEST_AUDIT_CTX = AuditContext(source="test")


def _make_api() -> LLMPipelineAPI:
    pipeline_state = MagicMock()
    pipeline_state.write_order_file.return_value = "ts-claude-order-uuid.md"
    order_builder = MagicMock()
    order_builder.build_order.return_value = "order content"
    return LLMPipelineAPI(
        pipeline_state=pipeline_state,
        order_builder=order_builder,
        queue_dir="queue",
    )


def test_submit_records_resume_from_uuid_annotation():
    import uuid as _uuid

    api = _make_api()
    steps = [
        StepConfig(id="p1", template="p1", model="claude-sonnet-4-6", engine="claude"),
        StepConfig(
            id="p2",
            template="p2",
            model="claude-sonnet-4-6",
            engine="claude",
            depends=["p1"],
            resume_from="p1",
        ),
    ]
    lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)
    p1 = json.loads(lines[0])
    p2 = json.loads(lines[1])

    assert p2["annotations"]["resume_from_uuid"] == p1["uuid"]

    # 後方互換: resume_from 未指定ステップには annotation を出さない
    assert "resume_from_uuid" not in p1["annotations"]

    # UUID 形式の sanity check（実データでマップされていることを確認）
    _uuid.UUID(p2["annotations"]["resume_from_uuid"])


def test_validate_depends_rejects_resume_from_non_ancestor():
    api = _make_api()
    steps = [
        StepConfig(id="a", template="t", model="m"),
        StepConfig(id="b", template="t", model="m"),
        StepConfig(id="c", template="t", model="m", depends=["b"], resume_from="a"),
    ]
    with pytest.raises(DependencyError, match="ancestor"):
        api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)


def test_validate_depends_allows_transitive_ancestor_resume_from():
    api = _make_api()
    steps = [
        StepConfig(id="a", template="t", model="m"),
        StepConfig(id="b", template="t", model="m", depends=["a"]),
        StepConfig(id="c", template="t", model="m", depends=["b"], resume_from="a"),
    ]
    lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)
    assert len(lines) == 3


def test_loader_rejects_engine_mismatch_resume_from(tmp_path):
    wf = tmp_path / "w.yml"
    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / "a.md").write_text("a", encoding="utf-8")
    (tmp_path / "templates" / "b.md").write_text("b", encoding="utf-8")
    wf.write_text(
        """
name: test
triggers:
  - label: "pipeline:x"
    handler: h
handlers:
  h:
    steps:
      - id: a
        template: a
        model: claude-sonnet-4-6
        engine: claude
      - id: b
        template: b
        model: auto
        engine: cursor
        depends: [a]
        resume_from: a
""",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError, match="same engine"):
        load_workflows(tmp_path)


def test_loader_rejects_non_string_resume_from(tmp_path):
    wf = tmp_path / "w.yml"
    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / "a.md").write_text("a", encoding="utf-8")
    (tmp_path / "templates" / "b.md").write_text("b", encoding="utf-8")
    wf.write_text(
        """
name: test
triggers:
  - label: "pipeline:x"
    handler: h
handlers:
  h:
    steps:
      - id: a
        template: a
        model: claude-sonnet-4-6
      - id: b
        template: b
        model: claude-sonnet-4-6
        depends: [a]
        resume_from: [a, b]
""",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError, match="resume_from"):
        load_workflows(tmp_path)


@pytest.mark.parametrize(
    ("engine", "expected"),
    [
        ("claude", "--resume 'sess_1'"),
        ("cursor", "--resume 'sess_1'"),
    ],
)
def test_render_exec_command_includes_resume_for_claude_cursor(engine, expected):
    command = render_exec_command(
        ENGINE_SPECS[engine],
        order_path="queue/order.md",
        prompt="p",
        model="m",
        capabilities=TEXT_ONLY,
        resume_session_id="sess_1",
    )
    assert expected in command


def test_render_exec_command_switches_codex_to_resume_subcommand():
    command = render_exec_command(
        ENGINE_SPECS["codex"],
        order_path="queue/order.md",
        prompt="p",
        model="gpt-5.6-terra",
        capabilities=TEXT_ONLY,
        resume_session_id="sess_1",
    )
    assert "codex exec resume 'sess_1'" in command
    assert "codex exec -" not in command


@pytest.mark.parametrize("engine", ["gemini", "shell"])
def test_render_exec_command_degrades_without_resume_for_unsupported_engines(engine):
    command = render_exec_command(
        ENGINE_SPECS[engine],
        order_path="queue/order.md",
        prompt="p",
        model="m",
        capabilities=TEXT_ONLY,
        resume_session_id="sess_1",
    )
    assert "--resume" not in command
    assert "resume " not in command
