"""Tests for ghdag.config.language — language pack loading and validation."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from ghdag.config import language
from ghdag.config.language import (
    EN,
    STATE_IDS,
    UI_KEYS,
    LanguagePack,
    get_language_pack,
    load_language_pack,
)
from ghdag.core.exceptions import GhdagError


@pytest.fixture(autouse=True)
def _clear_cache() -> Iterator[None]:
    get_language_pack.cache_clear()
    yield
    get_language_pack.cache_clear()


def _full_data() -> dict[str, Any]:
    return {
        "state_labels": {k: f"S-{k}" for k in STATE_IDS},
        "ui": {k: f"U-{k}" for k in UI_KEYS},
    }


def _write(tmp_path: Path, data: Any) -> Path:
    path = tmp_path / "pack.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_key_sets() -> None:
    assert len(STATE_IDS) == 10
    assert len(UI_KEYS) == 12
    assert set(EN.state_labels) == set(STATE_IDS)
    assert set(EN.ui) == set(UI_KEYS)
    assert "{n}" in EN.ui["page_size_option"]
    assert EN.state_labels["running"] == "Running"
    assert EN.state_labels["ok"] == "Done (success)"
    assert EN.ui["tab_tasks"] == "Tasks"
    assert EN.ui["confirm_stop"] == "Force-stop the agent process?"


def test_pack_is_frozen() -> None:
    with pytest.raises(TypeError):
        EN.state_labels["running"] = "x"  # type: ignore[index]
    with pytest.raises(TypeError):
        EN.ui["tab_tasks"] = "x"  # type: ignore[index]
    with pytest.raises(AttributeError):
        EN.ui = {}  # type: ignore[misc]


def test_default_is_en(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GHDAG_LANGUAGE_PACK", raising=False)
    assert get_language_pack() is EN


def test_env_pack_replaces_all_fields(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = _write(tmp_path, _full_data())
    monkeypatch.setenv("GHDAG_LANGUAGE_PACK", str(path))
    pack = get_language_pack()
    assert isinstance(pack, LanguagePack)
    assert dict(pack.state_labels) == {k: f"S-{k}" for k in STATE_IDS}
    assert dict(pack.ui) == {k: f"U-{k}" for k in UI_KEYS}


def test_get_language_pack_is_cached(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = _write(tmp_path, _full_data())
    monkeypatch.setenv("GHDAG_LANGUAGE_PACK", str(path))
    first = get_language_pack()
    monkeypatch.delenv("GHDAG_LANGUAGE_PACK")
    assert get_language_pack() is first
    get_language_pack.cache_clear()
    assert get_language_pack() is EN


def test_load_accepts_str_path(tmp_path: Path) -> None:
    path = _write(tmp_path, _full_data())
    assert load_language_pack(str(path)) == load_language_pack(path)


def _assert_error(path: Path, *needles: str) -> None:
    with pytest.raises(GhdagError) as exc:
        load_language_pack(path)
    for needle in needles:
        assert needle in str(exc.value)


def test_unknown_state_key(tmp_path: Path) -> None:
    data = _full_data()
    data["state_labels"]["foo"] = "Foo"
    _assert_error(_write(tmp_path, data), "foo")


def test_missing_ui_key(tmp_path: Path) -> None:
    data = _full_data()
    del data["ui"]["tab_tasks"]
    _assert_error(_write(tmp_path, data), "tab_tasks")


def test_extra_top_level_key(tmp_path: Path) -> None:
    data = _full_data()
    data["extra"] = {}
    _assert_error(_write(tmp_path, data), "extra")


def test_missing_top_level_key(tmp_path: Path) -> None:
    data = _full_data()
    del data["ui"]
    _assert_error(_write(tmp_path, data), "ui")


def test_empty_value(tmp_path: Path) -> None:
    data = _full_data()
    data["state_labels"]["running"] = ""
    _assert_error(_write(tmp_path, data), "running")


def test_non_string_value(tmp_path: Path) -> None:
    data = _full_data()
    data["ui"]["col_state"] = 3
    _assert_error(_write(tmp_path, data), "col_state")


def test_section_not_mapping(tmp_path: Path) -> None:
    data = _full_data()
    data["ui"] = ["a"]
    _assert_error(_write(tmp_path, data), "ui")


def test_top_level_not_mapping(tmp_path: Path) -> None:
    _assert_error(_write(tmp_path, ["a"]), "state_labels")


def test_missing_file(tmp_path: Path) -> None:
    _assert_error(tmp_path / "nope.yaml", "nope.yaml")


def test_broken_yaml(tmp_path: Path) -> None:
    path = tmp_path / "broken.yaml"
    path.write_text("state_labels: [unclosed\n", encoding="utf-8")
    _assert_error(path, "broken.yaml")


def test_env_pack_invalid_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GHDAG_LANGUAGE_PACK", str(tmp_path / "missing.yaml"))
    with pytest.raises(GhdagError):
        get_language_pack()


def test_question_suffixes_omitted_defaults_to_empty(tmp_path: Path) -> None:
    pack = load_language_pack(_write(tmp_path, _full_data()))
    assert pack.question_suffixes == ()
    assert EN.question_suffixes == ()


def test_question_suffixes_extra_loaded_as_tuple(tmp_path: Path) -> None:
    data = _full_data()
    data["question_suffixes"] = {"extra": ["\u203d", "!?", "\u203d"]}
    pack = load_language_pack(_write(tmp_path, data))
    assert pack.question_suffixes == ("\u203d", "!?", "\u203d")


def test_question_suffixes_empty_list(tmp_path: Path) -> None:
    data = _full_data()
    data["question_suffixes"] = {"extra": []}
    assert load_language_pack(_write(tmp_path, data)).question_suffixes == ()


@pytest.mark.parametrize(
    ("section", "needle"),
    [
        ({"extra": ["?"], "other": ["x"]}, "other"),
        ({}, "extra"),
        ({"extra": "?"}, "extra"),
        ({"extra": None}, "extra"),
        ({"extra": ["?", 3]}, "extra"),
        ({"extra": ["?", ""]}, "extra"),
        (["?"], "question_suffixes"),
    ],
)
def test_question_suffixes_invalid(
    tmp_path: Path, section: Any, needle: str
) -> None:
    data = _full_data()
    data["question_suffixes"] = section
    _assert_error(_write(tmp_path, data), "question_suffixes", needle)


def test_module_exports() -> None:
    assert set(language.__all__) >= {
        "EN",
        "STATE_IDS",
        "UI_KEYS",
        "LanguagePack",
        "get_language_pack",
        "load_language_pack",
    }
