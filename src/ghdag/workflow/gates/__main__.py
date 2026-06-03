from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

from ghdag.workflow.gates import GATE_REGISTRY


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="ghdag.workflow.gates",
        description="Run a gate rule against an issue body.",
    )
    parser.add_argument("--gate", required=True, help="Gate name (key in GATE_REGISTRY)")
    parser.add_argument("--body-file", required=True, help="Path to issue body text file")
    parser.add_argument("--labels-file", default=None, help="Path to newline-separated labels file")
    args = parser.parse_args()

    if args.gate not in GATE_REGISTRY:
        print(f"Error: gate '{args.gate}' is not registered in GATE_REGISTRY", file=sys.stderr)
        sys.exit(1)

    body_path = Path(args.body_file)
    if not body_path.exists():
        print(f"Error: body file '{args.body_file}' does not exist", file=sys.stderr)
        sys.exit(1)

    body = body_path.read_text(encoding="utf-8")

    labels: list[str] = []
    if args.labels_file is not None:
        labels_path = Path(args.labels_file)
        if labels_path.exists():
            labels = [line.strip() for line in labels_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    rule_cls = GATE_REGISTRY[args.gate]
    rule = rule_cls()
    violations = rule.check(body, labels)

    print(json.dumps([dataclasses.asdict(v) for v in violations]))


if __name__ == "__main__":
    main()
