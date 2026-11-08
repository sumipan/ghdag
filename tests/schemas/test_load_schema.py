"""Tests for ghdag.schemas.load_schema."""

from __future__ import annotations

from importlib import resources

import pytest

from ghdag.exceptions import ConfigError
from ghdag.schemas import SCHEMA_NAMES, load_schema

_DRAFT_2020_12 = "https://json-schema.org/draft/2020-12/schema"


def test_load_schema_workflow_config_has_draft_and_examples() -> None:
    """load_schema returns draft 2020-12 metadata with at least one example."""
    schema = load_schema("workflow-config")
    assert schema["$schema"] == _DRAFT_2020_12
    assert len(schema.get("examples", [])) >= 1


def test_load_schema_unknown_raises_config_error() -> None:
    """Unknown schema name raises ConfigError with SCHEMA_UNKNOWN."""
    with pytest.raises(ConfigError) as exc_info:
        load_schema("nope")
    err = exc_info.value
    assert err.code == "SCHEMA_UNKNOWN"
    assert "workflow-config" in (err.hint or "")


def test_exec_record_schema_packaged() -> None:
    """exec-record JSON is shipped as package data."""
    path = resources.files("ghdag.schemas").joinpath("exec-record.schema.json")
    assert path.is_file()


def test_schema_names_cover_bundled_files() -> None:
    """SCHEMA_NAMES matches packaged *.schema.json stems."""
    pkg = resources.files("ghdag.schemas")
    on_disk = {
        p.name[: -len(".schema.json")]
        for p in pkg.iterdir()
        if p.name.endswith(".schema.json")
    }
    assert set(SCHEMA_NAMES) == on_disk
