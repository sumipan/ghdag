"""Language packs for user-facing state labels, UI strings and question suffixes.

ghdag ships only the English pack (:data:`EN`). Hosts may supply another pack
as a YAML file pointed to by ``GHDAG_LANGUAGE_PACK``::

    state_labels:
      pending_deps: ...
      ...
    ui:
      page_size_option: "{n} rows"
      ...
    question_suffixes:   # optional
      extra: ["..."]

``state_labels`` and ``ui`` may list any of the keys in :data:`STATE_IDS` /
:data:`UI_KEYS` with non-empty string values. A section or key the pack leaves
out falls back to :data:`EN`, so a pack written for an older release keeps
loading after a release adds a key. Unknown keys and invalid values raise
:class:`~ghdag.core.exceptions.GhdagError`.

``question_suffixes`` is optional. Its only key ``extra`` lists non-empty
strings that, in addition to the built-in ASCII ``?``, mark a line as a
question when classifying engine failures (see
:func:`ghdag.llm.adapters.failure_classification.looks_like_question`).
Omitting the section is the same as ``extra: []``; :data:`EN` adds nothing.

CLI, log and exception messages are not part of the pack.
"""

from __future__ import annotations

import functools
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

from ghdag.config import env
from ghdag.core.exceptions import GhdagError

__all__ = [
    "STATE_IDS",
    "UI_KEYS",
    "LanguagePack",
    "EN",
    "load_language_pack",
    "get_language_pack",
]

STATE_IDS: tuple[str, ...] = (
    "pending_deps",
    "pending_run",
    "running",
    "deferred",
    "ok",
    "fail",
    "rejected",
    "empty",
    "engine_error",
    "unknown",
)

UI_KEYS: tuple[str, ...] = (
    "page_size_option",
    "tab_tasks",
    "tab_bursts",
    "col_time_tree",
    "col_state",
    "col_engine_model",
    "col_label",
    "empty_tasks",
    "col_count",
    "col_last_seen",
    "empty_bursts",
    "confirm_stop",
)

_SECTIONS: dict[str, tuple[str, ...]] = {
    "state_labels": STATE_IDS,
    "ui": UI_KEYS,
}

_QUESTION_SUFFIXES = "question_suffixes"
_QUESTION_SUFFIXES_KEYS: tuple[str, ...] = ("extra",)
_OPTIONAL_SECTIONS: tuple[str, ...] = (_QUESTION_SUFFIXES,)


def _freeze(mapping: Mapping[str, str]) -> Mapping[str, str]:
    return MappingProxyType(dict(mapping))


@dataclass(frozen=True)
class LanguagePack:
    """State labels (keyed by :data:`STATE_IDS`), UI strings (keyed by :data:`UI_KEYS`)
    and extra question suffixes (added to the built-in ASCII ``?``)."""

    state_labels: Mapping[str, str] = field(default_factory=dict)
    ui: Mapping[str, str] = field(default_factory=dict)
    question_suffixes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "state_labels", _freeze(self.state_labels))
        object.__setattr__(self, "ui", _freeze(self.ui))


EN = LanguagePack(
    state_labels={
        "pending_deps": "Pending (waiting on deps)",
        "pending_run": "Pending (ready)",
        "running": "Running",
        "deferred": "Deferred",
        "ok": "Done (success)",
        "fail": "Done (failed)",
        "rejected": "Done (REJECTED)",
        "empty": "Done (EMPTY_RESULT)",
        "engine_error": "Done (ENGINE_ERROR)",
        "unknown": "Done (other)",
    },
    ui={
        "page_size_option": "{n} rows",
        "tab_tasks": "Tasks",
        "tab_bursts": "Bursts",
        "col_time_tree": "Time / Tree",
        "col_state": "State",
        "col_engine_model": "Engine / Model",
        "col_label": "Label",
        "empty_tasks": "No tasks",
        "col_count": "Count",
        "col_last_seen": "Last seen",
        "empty_bursts": "No burst data",
        "confirm_stop": "Force-stop the agent process?",
    },
)


def _validate(data: Any, source: Path) -> LanguagePack:
    if not isinstance(data, dict):
        raise GhdagError(
            f"language pack {source}: top level must be a mapping with keys "
            f"{', '.join(_SECTIONS)}"
        )
    problems: list[str] = []
    extra_top = sorted(
        str(k) for k in data if k not in _SECTIONS and k not in _OPTIONAL_SECTIONS
    )
    if extra_top:
        problems.append(f"unknown top-level keys: {', '.join(extra_top)}")

    defaults: dict[str, Mapping[str, str]] = {
        "state_labels": EN.state_labels,
        "ui": EN.ui,
    }
    sections: dict[str, dict[str, str]] = {}
    for name, expected in _SECTIONS.items():
        if name not in data:
            sections[name] = dict(defaults[name])
            continue
        section = data[name]
        if not isinstance(section, dict):
            problems.append(f"{name} must be a mapping")
            continue
        extra = sorted(str(k) for k in section if k not in expected)
        invalid = [
            k
            for k in expected
            if k in section and (not isinstance(section[k], str) or not section[k])
        ]
        if extra:
            problems.append(f"{name}: unknown keys: {', '.join(extra)}")
        if invalid:
            problems.append(
                f"{name}: values must be non-empty strings: {', '.join(invalid)}"
            )
        sections[name] = {**defaults[name], **section}

    question_suffixes: tuple[str, ...] = ()
    if _QUESTION_SUFFIXES in data:
        question_suffixes = _validate_question_suffixes(data[_QUESTION_SUFFIXES], problems)

    if problems:
        raise GhdagError(f"invalid language pack {source}: " + "; ".join(problems))
    return LanguagePack(
        state_labels=sections["state_labels"],
        ui=sections["ui"],
        question_suffixes=question_suffixes,
    )


def _validate_question_suffixes(section: Any, problems: list[str]) -> tuple[str, ...]:
    name = _QUESTION_SUFFIXES
    if not isinstance(section, dict):
        problems.append(f"{name} must be a mapping")
        return ()
    extra = sorted(str(k) for k in section if k not in _QUESTION_SUFFIXES_KEYS)
    if extra:
        problems.append(f"{name}: unknown keys: {', '.join(extra)}")
    if "extra" not in section:
        return ()
    values = section["extra"]
    if not isinstance(values, list):
        problems.append(f"{name}: extra must be a list")
        return ()
    if any(not isinstance(v, str) or not v for v in values):
        problems.append(f"{name}: extra values must be non-empty strings")
        return ()
    return tuple(values)


def load_language_pack(path: str | Path) -> LanguagePack:
    """Load and validate a language pack YAML file.

    Sections and keys the file leaves out fall back to :data:`EN`. Raises
    :class:`GhdagError` when the file is missing or unreadable, is not valid
    YAML, has keys outside :data:`STATE_IDS` / :data:`UI_KEYS`, has empty or
    non-string values, or has a malformed ``question_suffixes`` section.
    """
    source = Path(path)
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise GhdagError(f"cannot read language pack {source}: {exc}") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise GhdagError(f"invalid YAML in language pack {source}: {exc}") from exc
    return _validate(data, source)


@functools.cache
def get_language_pack() -> LanguagePack:
    """Return the active language pack (``GHDAG_LANGUAGE_PACK`` or :data:`EN`).

    The result is cached for the process; call ``get_language_pack.cache_clear()``
    to reload.
    """
    path = env.ghdag_language_pack()
    if path:
        return load_language_pack(path)
    return EN
