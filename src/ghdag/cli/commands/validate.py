"""ghdag validate command — validate workflow YAML files in a directory."""

from __future__ import annotations

import json
from pathlib import Path

from ghdag.core.exceptions import CliUsageError
from ghdag.workflow.loader import ValidationError, load_workflow_file


def cmd_validate(args) -> None:
    """Load every workflow YAML in workflows_dir and report validation errors."""
    workflows_dir = Path(args.workflows_dir).resolve()
    if not workflows_dir.exists():
        raise CliUsageError(
            f"directory not found: {workflows_dir}",
            code="WORKFLOWS_DIR_NOT_FOUND",
            hint="pass the path to a directory containing workflow YAML files",
        )

    paths = sorted(workflows_dir.glob("*.yml")) + sorted(workflows_dir.glob("*.yaml"))
    errors: list[dict[str, object]] = []
    workflow_names: list[str] = []

    for path in paths:
        rel = path.relative_to(workflows_dir)
        try:
            wf = load_workflow_file(path)
            workflow_names.append(wf.name)
        except ValidationError as exc:
            entry: dict[str, object] = {
                "file": str(rel),
                "code": exc.code,
                "message": exc.message,
            }
            if exc.hint is not None:
                entry["hint"] = exc.hint
            errors.append(entry)

    n_files = len(paths)
    n_errors = len(errors)
    valid = n_errors == 0

    if args.output_json:
        payload = {
            "valid": valid,
            "files": n_files,
            "workflows": workflow_names,
            "errors": errors,
        }
        print(json.dumps(payload, ensure_ascii=False))
    else:
        for err in errors:
            file_name = err["file"]
            code = err["code"]
            message = err["message"]
            print(f"{file_name}: error[{code}]: {message}")
            hint = err.get("hint")
            if hint:
                print(f"  hint: {hint}")
        print(f"validated {n_files} file(s), {n_errors} error(s)")

    if n_errors > 0:
        raise SystemExit(3)
