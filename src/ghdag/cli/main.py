"""ghdag CLI — argparse-based entry point."""

from __future__ import annotations

import argparse
import json
import logging
import sys

from ghdag.core.exceptions import GhdagError


def main(argv: list[str] | None = None) -> None:
    """CLI main entry point. argv=None → sys.argv[1:]. Arguments can be injected in tests."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args)
    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(0)
    try:
        args.func(args)
    except GhdagError as exc:
        output_json = bool(getattr(args, "output_json", False))
        _print_error(exc, output_json=output_json)
        raise SystemExit(exc.exit_code) from None


def _print_error(exc: GhdagError, *, output_json: bool) -> None:
    if output_json:
        print(json.dumps({"error": exc.to_dict()}, ensure_ascii=False))
        return
    print(f"error[{exc.code}]: {exc.message}", file=sys.stderr)
    if exc.hint:
        print(f"  hint: {exc.hint}", file=sys.stderr)


def _add_json_flag(parser: argparse.ArgumentParser) -> None:
    if any(
        "--json" in (action.option_strings or [])
        for action in parser._actions
    ):
        return
    parser.add_argument(
        "--json",
        action="store_true",
        dest="output_json",
        help="Emit JSON",
    )


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


def _register_tools_parser(subparsers: argparse._SubParsersAction) -> None:
    from ghdag.tool.cli import cmd_tools_list

    tools_parser = subparsers.add_parser(
        "tools", allow_abbrev=False, help="Tool definition management",
    )
    tools_subparsers = tools_parser.add_subparsers(title="tools subcommands")
    list_parser = tools_subparsers.add_parser(
        "list", allow_abbrev=False, help="List tool definitions",
    )
    list_parser.add_argument("--path", required=True, help="Tool definition directory")
    list_parser.add_argument(
        "--json", action="store_true", dest="output_json", help="Output as JSON",
    )
    list_parser.set_defaults(func=cmd_tools_list)


def _build_parser() -> argparse.ArgumentParser:
    from ghdag.cli.commands.audit_query import add_parser as add_audit_query_parser
    from ghdag.cli.commands.cancel import add_parser as add_cancel_parser
    from ghdag.cli.commands.cleanup import add_parser as add_cleanup_parser
    from ghdag.cli.commands.llm import add_parser as add_llm_parser
    from ghdag.cli.commands.mcp import add_parser as add_mcp_parser
    from ghdag.cli.commands.quota import add_parser as add_quota_parser
    from ghdag.cli.commands.recover import add_parser as add_recover_parser
    from ghdag.cli.commands.run import add_parser as add_run_parser
    from ghdag.cli.commands.schema_show import add_parser as add_schema_parser
    from ghdag.cli.commands.status import add_parser as add_status_parser
    from ghdag.cli.commands.trigger import add_parser as add_trigger_parser
    from ghdag.cli.commands.ui import add_parser as add_ui_parser
    from ghdag.cli.commands.validate import add_parser as add_validate_parser
    from ghdag.cli.commands.watch import add_parser as add_watch_parser

    parser = argparse.ArgumentParser(
        prog="ghdag",
        description="Generic DAG execution engine",
        allow_abbrev=False,
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable DEBUG logging")
    parser.add_argument(
        "--quiet", "-q", action="store_true",
        help="Suppress INFO logging (WARNING and above only)",
    )
    subparsers = parser.add_subparsers(title="subcommands")

    add_run_parser(subparsers)
    add_watch_parser(subparsers)
    add_ui_parser(subparsers)
    add_mcp_parser(subparsers)
    add_llm_parser(subparsers)

    version_parser = subparsers.add_parser(
        "version", allow_abbrev=False, help="Show version and exit",
    )
    version_parser.set_defaults(func=_cmd_version)

    add_cleanup_parser(subparsers)
    add_trigger_parser(subparsers)
    add_status_parser(subparsers)

    dag_parser = subparsers.add_parser("dag", allow_abbrev=False, help="DAG utilities")
    dag_subparsers = dag_parser.add_subparsers(title="dag subcommands")
    add_recover_parser(dag_subparsers)
    add_cancel_parser(dag_subparsers)

    add_audit_query_parser(subparsers)
    _register_tools_parser(subparsers)
    add_quota_parser(subparsers)
    add_validate_parser(subparsers)
    add_schema_parser(subparsers)

    for leaf in _iter_leaf_parsers(parser):
        _add_json_flag(leaf)

    return parser


def _setup_logging(args: argparse.Namespace) -> None:
    if args.verbose:
        level = logging.DEBUG
    elif args.quiet:
        level = logging.WARNING
    else:
        level = logging.INFO
    logging.basicConfig(level=level, force=True)


def _cmd_version(args: argparse.Namespace) -> None:
    """Print __version__ to stdout."""
    from ghdag import __version__

    if args.output_json:
        print(json.dumps({"version": __version__}))
    else:
        print(__version__)
