"""Language packs for user-facing state labels and UI strings.

ghdag ships only the English pack (:data:`EN`). Hosts may supply another pack
as a YAML file pointed to by ``GHDAG_LANGUAGE_PACK``::

    state_labels:
      pending_deps: ...
      ...
    ui:
      page_size_option: "{n} rows"
      ...

Both sections must list exactly the keys in :data:`STATE_IDS` / :data:`UI_KEYS`
with non-empty string values. CLI, log and exception messages are not part of
the pack.
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


def _freeze(mapping: Mapping[str, str]) -> Mapping[str, str]:
    return MappingProxyType(dict(mapping))


@dataclass(frozen=True)
class LanguagePack:
    """State labels (keyed by :data:`STATE_IDS`) and UI strings (keyed by :data:`UI_KEYS`)."""

    state_labels: Mapping[str, str] = field(default_factory=dict)
    ui: Mapping[str, str] = field(default_factory=dict)

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
    missing_top = [k for k in _SECTIONS if k not in data]
    extra_top = sorted(str(k) for k in data if k not in _SECTIONS)
    if missing_top:
        problems.append(f"missing top-level keys: {', '.join(missing_top)}")
    if extra_top:
        problems.append(f"unknown top-level keys: {', '.join(extra_top)}")

    sections: dict[str, dict[str, str]] = {}
    for name, expected in _SECTIONS.items():
        if name not in data:
            continue
        section = data[name]
        if not isinstance(section, dict):
            problems.append(f"{name} must be a mapping")
            continue
        missing = [k for k in expected if k not in section]
        extra = sorted(str(k) for k in section if k not in expected)
        invalid = [
            k
            for k in expected
            if k in section and (not isinstance(section[k], str) or not section[k])
        ]
        if missing:
            problems.append(f"{name}: missing keys: {', '.join(missing)}")
        if extra:
            problems.append(f"{name}: unknown keys: {', '.join(extra)}")
        if invalid:
            problems.append(
                f"{name}: values must be non-empty strings: {', '.join(invalid)}"
            )
        sections[name] = section

    if problems:
        raise GhdagError(f"invalid language pack {source}: " + "; ".join(problems))
    return LanguagePack(state_labels=sections["state_labels"], ui=sections["ui"])


def load_language_pack(path: str | Path) -> LanguagePack:
    """Load and validate a language pack YAML file.

    Raises :class:`GhdagError` when the file is missing or unreadable, is not
    valid YAML, or does not match :data:`STATE_IDS` / :data:`UI_KEYS` exactly.
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
