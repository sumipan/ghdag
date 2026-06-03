from __future__ import annotations

import re


def strip_code_regions(body: str) -> str:
    """フェンスコードブロックとインラインコードスパンを除去して返す。"""
    body = re.sub(r"```[\s\S]*?```", "", body)
    body = re.sub(r"`[^`\n]+`", "", body)
    return body
