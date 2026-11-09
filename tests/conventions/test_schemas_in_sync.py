"""Conventions: bundled JSON schemas stay aligned with Python sources."""

from __future__ import annotations

import json
from dataclasses import fields
from importlib import resources
from pathlib import Path

import pytest

from ghdag.core import vocabulary as vocab
from ghdag.core.capabilities import PRESETS
from ghdag.core.engine_spec import ENGINE_SPECS
from ghdag.core.models.metrics import FailureClass
from ghdag.core.models.workflow import (
    HANDLER_KEYS,
    NONTERMINAL_CLOSED_KEYS,
    ON_TRIGGER_KEYS,
    STEP_KEYS,
    TRIGGER_KEYS,
    WORKFLOW_KEYS,
)
from ghdag.dag.fanout import FanOutItem
from ghdag.pipeline.audit import AuditContext
from ghdag.pipeline.llm_pipeline import LLMPipelineAPI
from ghdag.pipeline.order import InlineOrderBuilder
from ghdag.pipeline.state import PipelineState
from ghdag.schemas import SCHEMA_NAMES, load_schema
from ghdag.status import StepStatus
from ghdag.workflow.schema import StepConfig

_ISSUE_STEP_STATUSES = frozenset(
    {
        "success",
        "failed",
        "pending",
        "running",
        "skipped",
        "cancelled",
        "dep_failed",
    }
)


def _done_marker_enum_values() -> set[str]:
    values: set[str] = set()
    for name, value in vars(vocab).items():
        if not name.startswith("DONE_") or name == "DONE_PIPELINE_FAILED_PREFIX":
            continue
        if isinstance(value, str):
            values.add(value)
    values.add(vocab.LEGACY_DONE_DEFERRED)
    return values


def _done_enum_from_schema(schema: dict) -> set[str]:
    for branch in schema["oneOf"]:
        if branch.get("type") == "string" and "enum" in branch:
            return set(branch["enum"])
    raise AssertionError("done-marker schema missing string enum branch")


def test_workflow_config_key_sets_match_loader() -> None:
    schema = load_schema("workflow-config")
    assert set(schema["properties"]) == set(WORKFLOW_KEYS)
    assert set(schema["$defs"]["trigger"]["properties"]) == set(TRIGGER_KEYS)
    assert set(schema["$defs"]["handler"]["properties"]) == set(HANDLER_KEYS)
    assert set(schema["$defs"]["on_trigger"]["properties"]) == set(ON_TRIGGER_KEYS)
    assert set(schema["$defs"]["step"]["properties"]) == set(STEP_KEYS)
    assert set(schema["$defs"]["nonterminal_closed"]["properties"]) == set(NONTERMINAL_CLOSED_KEYS)

    step_engine = schema["$defs"]["step"]["properties"]["engine"]["enum"]
    assert step_engine == sorted(ENGINE_SPECS)

    step_permission = schema["$defs"]["step"]["properties"]["permission"]["enum"]
    assert step_permission == sorted(PRESETS)


def test_reply_block_status_enum_matches_vocabulary() -> None:
    schema = load_schema("reply-block")
    assert schema["properties"]["status"]["enum"] == list(vocab.REPLY_STATUS_VALUES)


def test_reply_block_children_keys_match_fanout_item() -> None:
    schema = load_schema("reply-block")
    item_props = schema["properties"]["children"]["items"]["properties"]
    assert set(item_props) == {f.name for f in fields(FanOutItem)}


def test_done_marker_enum_matches_vocabulary() -> None:
    schema = load_schema("done-marker")
    assert _done_enum_from_schema(schema) == _done_marker_enum_values()


def test_issue_status_step_fields_match_dataclass() -> None:
    schema = load_schema("issue-status")
    step_props = set(schema["$defs"]["step"]["properties"])
    assert step_props == {f.name for f in fields(StepStatus)}

    status_enum = set(schema["$defs"]["step"]["properties"]["status"]["enum"])
    assert status_enum == _ISSUE_STEP_STATUSES

    failure_enum = {v for v in schema["$defs"]["step"]["properties"]["failure_class"]["enum"] if v is not None}
    assert failure_enum == {member.value for member in FailureClass}


def test_exec_record_keys_subset_of_schema(tmp_path: Path) -> None:
    schema = load_schema("exec-record")
    schema_keys = set(schema["properties"])
    annotation_keys = set(schema["properties"]["annotations"]["properties"])

    exec_jsonl = tmp_path / "exec.jsonl"
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir()
    state = PipelineState(state_dir=tmp_path / "state", exec_jsonl_path=exec_jsonl)
    api = LLMPipelineAPI(
        pipeline_state=state,
        order_builder=InlineOrderBuilder(),
        queue_dir=str(queue_dir),
    )
    steps = [
        StepConfig(template="echo", engine="shell", id="prep"),
        StepConfig(
            template="order",
            model="claude-sonnet-4-6",
            engine="claude",
            id="llm",
            depends=["prep"],
        ),
    ]
    lines = api.submit(steps, {}, audit_context=AuditContext(source="test"))
    records = [json.loads(line) for line in lines]
    assert len(records) == 2
    for record in records:
        assert set(record) <= schema_keys
        assert set(record.get("annotations", {})) <= annotation_keys


def test_schema_names_match_package_files() -> None:
    pkg = resources.files("ghdag.schemas")
    stems = {p.name[: -len(".schema.json")] for p in pkg.iterdir() if p.name.endswith(".schema.json")}
    assert set(SCHEMA_NAMES) == stems


@pytest.mark.parametrize("name", list(SCHEMA_NAMES))
def test_schema_properties_have_descriptions(name: str) -> None:
    schema = load_schema(name)
    missing: list[str] = []

    def visit(node: object, path: str = "$") -> None:
        if isinstance(node, dict):
            properties = node.get("properties")
            if isinstance(properties, dict):
                for property_name, property_schema in properties.items():
                    if not isinstance(property_schema, dict) or not property_schema.get("description"):
                        missing.append(f"{path}.properties.{property_name}")
            for key, value in node.items():
                visit(value, f"{path}.{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                visit(value, f"{path}[{index}]")

    visit(schema)
    assert not missing, f"{name}: properties without descriptions: {missing}"


@pytest.mark.parametrize("name", list(SCHEMA_NAMES))
def test_schema_examples_validate(name: str) -> None:
    jsonschema = pytest.importorskip("jsonschema")
    schema = load_schema(name)
    examples = schema.get("examples")
    assert examples, f"{name}: examples must not be empty"
    validator = jsonschema.Draft202012Validator(schema)
    for example in examples:
        validator.validate(example)
