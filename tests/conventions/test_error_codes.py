"""Convention: every GhdagError subclass declares a unique code and valid exit_code."""

from __future__ import annotations

import importlib
import pkgutil
import re
from pathlib import Path

import ghdag
from ghdag.core.exceptions import GhdagError

_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]+$")
_VALID_EXIT = {1, 2, 3, 4}


def _iter_ghdag_modules():
    root = Path(ghdag.__file__).resolve().parent
    prefix = ghdag.__name__ + "."
    for importer, modname, ispkg in pkgutil.walk_packages([str(root)], prefix=prefix):
        if modname.endswith(".__main__"):
            continue
        yield modname


def _collect_subclasses(cls: type[GhdagError]) -> set[type[GhdagError]]:
    found: set[type[GhdagError]] = set()
    stack = list(cls.__subclasses__())
    while stack:
        sub = stack.pop()
        if sub in found:
            continue
        found.add(sub)
        stack.extend(sub.__subclasses__())
    return found


def test_all_ghdag_error_subclasses_declare_unique_codes() -> None:
    for modname in _iter_ghdag_modules():
        importlib.import_module(modname)

    subclasses = _collect_subclasses(GhdagError)
    codes: dict[str, type[GhdagError]] = {}
    errors: list[str] = []

    for cls in sorted(subclasses, key=lambda c: c.__name__):
        if "code" not in cls.__dict__:
            errors.append(f"{cls.__module__}.{cls.__name__}: missing class-level code")
            continue
        code = cls.__dict__["code"]
        if not isinstance(code, str) or not _CODE_RE.match(code):
            errors.append(f"{cls.__module__}.{cls.__name__}: invalid code {code!r}")
            continue
        if code in codes:
            errors.append(
                f"duplicate code {code!r}: {codes[code].__module__}.{codes[code].__name__}"
                f" and {cls.__module__}.{cls.__name__}"
            )
        else:
            codes[code] = cls

        exit_code = cls.__dict__.get("exit_code", GhdagError.exit_code)
        if exit_code not in _VALID_EXIT:
            errors.append(
                f"{cls.__module__}.{cls.__name__}: exit_code {exit_code!r} not in {_VALID_EXIT}"
            )

    assert not errors, "\n".join(errors)
