"""Convention test: all CLI commands are registered and all --help exits 0."""

from __future__ import annotations

import argparse
import importlib.util
import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC = _REPO_ROOT / "src" / "ghdag"

_BACKTICK_RE = re.compile(r"`ghdag\s+([a-z][a-z-]*)(?:\s+([a-z][a-z-]*))?")
_PROMPT_RE = re.compile(r"(?:^|\s)\$\s+ghdag\s+([a-z][a-z-]*)(?:\s+([a-z][a-z-]*))?")
_COMMANDS_TABLE = re.compile(r"^\|\s*`ghdag\s+([^`]+)`\s*\|")



def registered_commands(parser: argparse.ArgumentParser) -> set[tuple[str, ...]]:
    """Return the set of all registered command paths from an argparse parser."""
    result: set[tuple[str, ...]] = set()
    for action in parser._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        for name, subparser in action.choices.items():
            result.add((name,))
            for sub in registered_commands(subparser):
                result.add((name,) + sub)
    return result


def parse_readme_cli_command_paths(readme_text: str) -> set[tuple[str, ...]]:
    """Extract command path tuples from the README CLI Commands table."""
    start = readme_text.find("## CLI Reference")
    assert start != -1, "README missing ## CLI Reference"
    tail = readme_text[start:]
    end = tail.find("## Public API")
    if end != -1:
        tail = tail[:end]
    commands_start = tail.find("### Commands")
    assert commands_start != -1, "README CLI Reference missing ### Commands"
    section = tail[commands_start:]
    args_at = section.find("### Arguments")
    if args_at != -1:
        section = section[:args_at]
    paths: set[tuple[str, ...]] = set()
    for line in section.splitlines():
        match = _COMMANDS_TABLE.match(line)
        if not match:
            continue
        paths.add(tuple(match.group(1).split()))
    return paths


def extract_cli_references(lines: list[str]) -> set[tuple[str, ...]]:
    """Extract ``ghdag <cmd> [sub]`` tuples from backtick spans and ``$ ghdag`` prompts."""
    refs: set[tuple[str, ...]] = set()
    for line in lines:
        for pattern in (_BACKTICK_RE, _PROMPT_RE):
            for m in pattern.finditer(line):
                cmd = m.group(1)
                sub = m.group(2)
                if sub:
                    refs.add((cmd, sub))
                else:
                    refs.add((cmd,))
    return refs


def unregistered(
    refs: set[tuple[str, ...]],
    registered: set[tuple[str, ...]],
) -> list[str]:
    """Return violation strings for command references not in the registered set.

    A two-word ref ``(cmd, sub)`` is only a violation when ``cmd`` itself has
    registered subcommands; commands without subparsers accept free positional
    arguments that should not be treated as subcommand references.
    """
    top_level = {t[0] for t in registered}
    has_subparsers = {t[0] for t in registered if len(t) > 1}
    violations: list[str] = []
    for ref in sorted(refs):
        cmd = ref[0]
        if cmd not in top_level:
            violations.append(f"unknown command: {' '.join(ref)}")
        elif len(ref) > 1 and cmd in has_subparsers and ref not in registered:
            violations.append(f"unknown subcommand: {' '.join(ref)}")
    return violations


def unresolvable_modules(names: set[str]) -> list[str]:
    """Return module names from ``python -m ghdag.<mod>`` refs that cannot run."""
    violations: list[str] = []
    for name in sorted(names):
        spec = importlib.util.find_spec(name)
        if spec is None:
            violations.append(f"{name}: find_spec returned None")
            continue
        origin = spec.origin or ""
        if origin.endswith("__init__.py") or not origin:
            pkg_dir = Path(origin).parent if origin else None
            if pkg_dir and (pkg_dir / "__main__.py").exists():
                continue
            violations.append(f"{name}: package has no __main__.py")
            continue
        source = Path(origin).read_text(encoding="utf-8")
        if "__main__" in source:
            continue
        violations.append(f"{name}: no __main__.py and no if __name__ == '__main__' guard")
    return violations


def _changelog_unreleased_lines() -> list[str]:
    changelog = _REPO_ROOT / "CHANGELOG.md"
    text = changelog.read_text(encoding="utf-8")
    start = text.find("## [Unreleased]")
    if start == -1:
        return []
    end = text.find("\n## ", start + 1)
    section = text[start:end] if end != -1 else text[start:]
    return section.splitlines()


def _src_lines() -> list[str]:
    lines: list[str] = []
    for pyfile in sorted(_SRC.rglob("*.py")):
        lines.extend(pyfile.read_text(encoding="utf-8").splitlines())
    return lines


def _llms_txt_lines() -> list[str]:
    path = _REPO_ROOT / "llms.txt"
    if not path.is_file():
        return []
    return path.read_text(encoding="utf-8").splitlines()


def _all_reference_lines() -> list[str]:
    return (
        _changelog_unreleased_lines()
        + _llms_txt_lines()
        + _src_lines()
    )


def _python_m_modules() -> set[str]:
    pattern = re.compile(r"python\s+-m\s+(ghdag\.[a-zA-Z0-9_.]+)")
    names: set[str] = set()
    for line in _all_reference_lines():
        for m in pattern.finditer(line):
            names.add(m.group(1))
    return names


def test_all_registered_commands_help_exits_zero() -> None:
    from ghdag.cli.main import _build_parser, main
    parser = _build_parser()
    for path in registered_commands(parser):
        with pytest.raises(SystemExit) as exc_info:
            main([*path, "--help"])
        assert exc_info.value.code == 0, f"ghdag {' '.join(path)} --help exited {exc_info.value.code}"


def test_no_unregistered_cli_references() -> None:
    from ghdag.cli.main import _build_parser
    parser = _build_parser()
    registered = registered_commands(parser)
    refs = extract_cli_references(_all_reference_lines())
    violations = unregistered(refs, registered)
    assert violations == [], f"unregistered CLI references: {violations}"


def test_unresolvable_modules_none() -> None:
    modules = _python_m_modules()
    violations = unresolvable_modules(modules)
    assert violations == [], f"unresolvable python -m modules: {violations}"


def test_extract_cli_references_finds_backtick_and_prompt_refs() -> None:
    """Issue sumipan/nexus#7794 — extract_cli_references handles backtick and prompt lines."""
    lines = ["`ghdag quota status`", "$ ghdag run --help"]
    refs = extract_cli_references(lines)
    assert refs == {("quota", "status"), ("run",)}


def test_unregistered_detects_missing_quota() -> None:
    """Issue sumipan/nexus#7794 — unregistered flags refs absent from a truncated registry."""
    from ghdag.cli.main import _build_parser
    parser = _build_parser()
    full_registered = registered_commands(parser)
    truncated = {r for r in full_registered if r[0] != "quota"}
    refs = {("quota", "status")}
    violations = unregistered(refs, truncated)
    assert any("quota" in v for v in violations), (
        f"Expected quota violations but got: {violations}"
    )


def test_unregistered_full_parser_gives_no_violations() -> None:
    from ghdag.cli.main import _build_parser
    parser = _build_parser()
    registered = registered_commands(parser)
    refs = extract_cli_references(_all_reference_lines())
    violations = unregistered(refs, registered)
    assert violations == [], violations


def _iter_leaf_parsers(parser: argparse.ArgumentParser):
    for action in parser._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        for subparser in action.choices.values():
            if any(
                isinstance(a, argparse._SubParsersAction)
                for a in subparser._actions
            ):
                yield from _iter_leaf_parsers(subparser)
            else:
                yield subparser


def test_readme_cli_inventory_matches_registered_commands() -> None:
    """Issue sumipan/nexus#7854 — README CLI Commands table matches argparse registration."""
    from ghdag.cli.main import _build_parser, main

    readme = (_REPO_ROOT / "README.md").read_text(encoding="utf-8")
    documented = parse_readme_cli_command_paths(readme)
    parser = _build_parser()
    registered = registered_commands(parser)
    assert registered == documented, (
        f"missing from README: {sorted(registered - documented)}; "
        f"extra in README: {sorted(documented - registered)}"
    )
    for path in registered:
        with pytest.raises(SystemExit) as exc_info:
            main([*path, "--help"])
        assert exc_info.value.code == 0, f"ghdag {' '.join(path)} --help exited {exc_info.value.code}"


def test_leaf_subcommands_register_json_flag() -> None:
    """Issue #6153 — every leaf CLI subcommand exposes --json."""
    from ghdag.cli.main import _build_parser

    parser = _build_parser()
    missing: list[str] = []
    for leaf in _iter_leaf_parsers(parser):
        prog = leaf.prog or "?"
        has_json = any(
            "--json" in (action.option_strings or [])
            for action in leaf._actions
        )
        if not has_json:
            missing.append(prog)
    assert missing == [], f"missing --json on: {missing}"
