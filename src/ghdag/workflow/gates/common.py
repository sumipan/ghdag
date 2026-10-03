from __future__ import annotations

import re


def strip_code_regions(body: str) -> str:
    """Return the text with fenced code blocks and inline code spans removed."""
    body = re.sub(r"```[\s\S]*?```", "", body)
    body = re.sub(r"`[^`\n]+`", "", body)
    return body
