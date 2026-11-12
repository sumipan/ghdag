"""Conditional LLM step helper: substitute template vars and invoke claude or cursor.

Usage:
    python -m ghdag.workflow.conditional_step [--engine {claude|cursor|codex}] [--model MODEL] \\
        TEMPLATE_PATH [KEY=VALUE ...]

Substitutes ${KEY} placeholders in the template file and un-escapes $$ → $
(matching ghdag template engine behavior), then invokes the selected engine.
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

from ghdag.core.capabilities import DANGEROUS_FULL_ACCESS
from ghdag.core.command import build_llm_cmd
from ghdag.core.engine_spec import ENGINE_SPECS


def substitute_vars(template: str, variables: dict[str, str]) -> str:
    """Replace ${KEY} placeholders and un-escape $$ → $ (ghdag-compat)."""
    result = template
    for key, value in variables.items():
        result = result.replace(f"${{{key}}}", value)
    result = result.replace("$$", "$")
    return result


def _default_model_for_engine(engine: str) -> str:
    spec = ENGINE_SPECS.get(engine)
    if spec is None or spec.default_model is None:
        return ""
    return spec.default_model


def _build_command(engine: str, model: str) -> list[str]:
    return build_llm_cmd(
        engine,
        model,
        "",
        permission=DANGEROUS_FULL_ACCESS,
        dangerously_skip_permissions=True,
    )


def run_with_template(
    template_path: str,
    variables: dict[str, str],
    model: str | None = None,
    engine: str = "claude",
) -> int:
    """Read template, substitute variables, and run the LLM CLI. Returns exit code."""
    if model is None:
        model = _default_model_for_engine(engine)
    content = Path(template_path).read_text()
    order = substitute_vars(content, variables)
    cmd = _build_command(engine, model)

    print(
        f"[conditional_step] start engine={engine} model={model} template={template_path}",
        file=sys.stderr,
    )
    t0 = time.monotonic()

    proc = subprocess.run(
        cmd,
        input=order,
        text=True,
    )

    elapsed = time.monotonic() - t0
    print(
        f"[conditional_step] done exit_code={proc.returncode} elapsed={elapsed:.1f}s",
        file=sys.stderr,
    )

    return proc.returncode


def main() -> None:
    parser = argparse.ArgumentParser(description="Run conditional LLM step")
    parser.add_argument(
        "--engine",
        choices=("claude", "cursor", "codex"),
        default="claude",
        help="LLM CLI engine (default: claude)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model ID (default: engine-specific default from ENGINE_SPECS)",
    )
    parser.add_argument("template_path")
    parser.add_argument("variables", nargs="*", help="KEY=VALUE pairs")
    args = parser.parse_args()

    variables: dict[str, str] = {}
    for var in args.variables:
        if "=" in var:
            key, _, value = var.partition("=")
            variables[key] = value

    model = args.model if args.model is not None else _default_model_for_engine(args.engine)

    sys.exit(
        run_with_template(
            args.template_path,
            variables,
            model=model,
            engine=args.engine,
        )
    )


if __name__ == "__main__":
    main()
