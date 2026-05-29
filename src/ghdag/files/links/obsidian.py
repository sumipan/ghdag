"""Obsidian wiki-link helpers for DAG artifact edges (pure functions, no I/O)."""

from __future__ import annotations

import re

_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(\|[^\]]+)?\]\]")


def job_footer(ts: str, uuid: str, engine: str) -> str:
    """Return footer wiki-links for a single job (order / result / done marker)."""
    order_fn = f"{ts}-{engine}-order-{uuid}.md"
    result_fn = f"{ts}-{engine}-result-{uuid}.md"
    return (
        "\n\n---\n\n"
        "## DAG（Obsidian）\n"
        f"- 完了マーカー: [[jobs/done/{uuid}]]\n"
        f"- この order: [[jobs/{order_fn}]]\n"
        f"- result: [[jobs/{result_fn}]]\n"
    )


def summary_footer(
    ts: str,
    summary_uuid: str,
    job_result_paths: list[str],
    slack_uuid: str | None = None,
) -> str:
    """Return footer wiki-links for a summary job."""
    lines = [
        "\n\n---\n\n",
        "## DAG（Obsidian）\n",
        f"- 完了マーカー: [[jobs/done/{summary_uuid}]]\n",
        f"- この order: [[jobs/{ts}-claude-order-{summary_uuid}.md]]\n",
        f"- result: [[jobs/{ts}-claude-result-{summary_uuid}.md]]\n",
        "- 先行ジョブの result:\n",
    ]
    for p in job_result_paths:
        name = p.replace("jobs/", "", 1) if p.startswith("jobs/") else p
        lines.append(f"  - [[jobs/{name}]]\n")
    if slack_uuid:
        lines.append(f"- Slack 返信ステップの完了マーカー: [[jobs/done/{slack_uuid}]]\n")
    return "".join(lines)


def rewrite_links(content: str, path_map: dict[str, str]) -> str:
    """Rewrite wiki-link paths using path_map; unmatched links are left unchanged."""
    if not path_map:
        return content

    def repl(match: re.Match[str]) -> str:
        path_part = match.group(1)
        display = match.group(2) or ""
        new_path = path_map.get(path_part)
        if new_path is None:
            return match.group(0)
        return f"[[{new_path}{display}]]"

    return _WIKILINK_RE.sub(repl, content)
