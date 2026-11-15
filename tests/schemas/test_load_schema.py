"""Tests for ghdag.schemas.load_schema."""

from __future__ import annotations

from importlib import resources

from ghdag.schemas import SCHEMA_NAMES


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
