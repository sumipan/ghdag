"""Runtime template expand + bash runner for ``render: live`` shell steps.

Usage:
    python -m ghdag.workflow.render <template_path> [key=value ...]

Exit codes:
    0 — bash completed successfully
    2 — undefined template variable / bad args / missing template
    other — bash process exit code
"""

from __future__ import annotations

import hashlib
import os
import shlex
import string
import subprocess
import sys
import tempfile
from pathlib import Path


def parse_context(args: list[str]) -> dict[str, str]:
    context: dict[str, str] = {}
    for arg in args:
        if "=" not in arg:
            raise ValueError(f"key=value 形式ではありません: {arg!r}")
        key, value = arg.split("=", 1)
        context[key] = value
    return context


def render_template(template_path: Path, context: dict[str, str]) -> tuple[str, str]:
    """Expand template with string.Template.substitute; return (body, sha12)."""
    if not template_path.exists():
        raise FileNotFoundError(f"テンプレートファイルが見つかりません: {template_path}")
    text = template_path.read_text(encoding="utf-8")
    tmpl = string.Template(text)
    missing = sorted(set(tmpl.get_identifiers()) - set(context))
    if missing:
        raise KeyError(
            f"テンプレート展開エラー ({template_path}): 未定義変数: {missing}, "
            f"利用可能なキー: {sorted(context)}"
        )
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return tmpl.substitute(context), digest


def build_live_trampoline(template_path: Path | str, context: dict[str, str]) -> str:
    """Build a one-line bash trampoline that re-renders at execution time."""
    path = str(Path(template_path))
    parts = ["python", "-m", "ghdag.workflow.render", path]
    for key, value in context.items():
        parts.append(f"{key}={value}")
    return " ".join(shlex.quote(p) for p in parts)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(__doc__, file=sys.stderr)
        return 2

    template_path = Path(args[0])
    try:
        context = parse_context(args[1:])
        body, digest = render_template(template_path, context)
    except (ValueError, KeyError, FileNotFoundError) as exc:
        print(f"[ghdag.workflow.render] ERROR: {exc}", file=sys.stderr)
        return 2

    print(
        f"[ghdag.workflow.render] live-render template={template_path} template_sha={digest}",
        file=sys.stderr,
    )
    fd, path = tempfile.mkstemp(prefix="ghdag-render-", suffix=".sh")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(body)
        proc = subprocess.run(["bash", "-o", "pipefail", path])
        return int(proc.returncode)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
