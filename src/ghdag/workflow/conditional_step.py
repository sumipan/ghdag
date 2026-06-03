"""Conditional LLM step helper: substitute template vars and invoke claude or cursor.

Usage:
    python -m ghdag.workflow.conditional_step [--engine {claude|cursor}] [--model MODEL] \\
        TEMPLATE_PATH [KEY=VALUE ...]

Substitutes ${KEY} placeholders in the template file and un-escapes $$ → $
(matching ghdag template engine behavior), then invokes the selected engine.
"""

import argparse
import subprocess
import sys
from pathlib import Path


def substitute_vars(template: str, variables: dict[str, str]) -> str:
    """Replace ${KEY} placeholders and un-escape $$ → $ (ghdag-compat)."""
    result = template
    for key, value in variables.items():
        result = result.replace(f"${{{key}}}", value)
    result = result.replace("$$", "$")
    return result


def _build_command(engine: str, model: str) -> list[str]:
    if engine == "cursor":
        cmd = ["cursor", "agent", "-p", "--force"]
        if model:
            cmd.extend(["--model", model])
        return cmd
    if engine == "claude":
        return [
            "claude",
            "-p",
            "--model",
            model,
            "--dangerously-skip-permissions",
        ]
    raise ValueError(f"unsupported engine: {engine!r}")


def run_with_template(
    template_path: str,
    variables: dict[str, str],
    model: str = "claude-sonnet-4-6",
    engine: str = "claude",
) -> int:
    """Read template, substitute variables, and run the LLM CLI. Returns exit code."""
    content = Path(template_path).read_text()
    order = substitute_vars(content, variables)
    proc = subprocess.run(
        _build_command(engine, model),
        input=order,
        text=True,
    )
    return proc.returncode


def main() -> None:
    parser = argparse.ArgumentParser(description="Run conditional LLM step")
    parser.add_argument(
        "--engine",
        choices=("claude", "cursor"),
        default="claude",
        help="LLM CLI engine (default: claude)",
    )
    parser.add_argument("--model", default="claude-sonnet-4-6")
    parser.add_argument("template_path")
    parser.add_argument("variables", nargs="*", help="KEY=VALUE pairs")
    args = parser.parse_args()

    variables: dict[str, str] = {}
    for var in args.variables:
        if "=" in var:
            key, _, value = var.partition("=")
            variables[key] = value

    sys.exit(
        run_with_template(
            args.template_path,
            variables,
            model=args.model,
            engine=args.engine,
        )
    )


if __name__ == "__main__":
    main()
