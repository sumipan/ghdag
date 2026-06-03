"""Issue body の H2 セクションを決定論的に編集するユーティリティ。"""

from __future__ import annotations

from collections.abc import Iterable


def split_h2_sections(body: str) -> list[tuple[str, str]]:
    """body を H2 見出し単位で分割する。"""
    lines = body.splitlines()
    sections: list[tuple[str, str]] = []
    n = len(lines)
    i = 0

    while i < n:
        line = lines[i]
        if line.startswith("## ") and not line.startswith("###"):
            heading = line
            start = i
            i += 1
            while i < n:
                nxt = lines[i]
                if nxt.startswith("## ") and not nxt.startswith("###"):
                    break
                i += 1
            section = "\n".join(lines[start:i])
            if i < n or body.endswith("\n"):
                section += "\n"
            sections.append((heading, section))
        else:
            i += 1

    return sections


def count_heading(body: str, heading: str) -> int:
    """指定 H2 見出しの出現回数を返す。"""
    h2 = f"## {heading}"
    return sum(1 for section_heading, _ in split_h2_sections(body) if section_heading == h2)


def _extract_section_body(section: str) -> str:
    parts = section.split("\n", 1)
    if len(parts) == 1:
        return ""
    return parts[1]


def _get_unique_section(body: str, heading: str) -> str | None:
    h2 = f"## {heading}"
    matched = [section for section_heading, section in split_h2_sections(body) if section_heading == h2]
    if len(matched) > 1:
        raise ValueError(f"Duplicate heading: '{h2}' appears {len(matched)} times")
    return matched[0] if matched else None


def get_section(body: str, heading: str) -> str | None:
    """指定 H2 見出しのセクション本文（見出し行を除く）を返す。"""
    section = _get_unique_section(body, heading)
    if section is None:
        return None
    return _extract_section_body(section)


def _normalize_content(content: str) -> str:
    return content if content.endswith("\n") else f"{content}\n"


def upsert_section(body: str, heading: str, content: str) -> str:
    """セクションを置換または末尾に追加し、重複見出しは拒否する。"""
    h2 = f"## {heading}"
    target = _get_unique_section(body, heading)
    normalized_content = _normalize_content(content)
    new_section = f"{h2}\n{normalized_content}"

    if target is not None:
        sections = split_h2_sections(body)
        rebuilt: list[str] = []
        for section_heading, section in sections:
            if section_heading == h2:
                rebuilt.append(new_section.rstrip("\n"))
            else:
                rebuilt.append(section.rstrip("\n"))
        return "\n".join(rebuilt)

    trimmed = body.rstrip()
    if not trimmed:
        return new_section.rstrip("\n")
    return f"{trimmed}\n\n{new_section.rstrip()}"


def _iter_h4_sections(content: str) -> Iterable[tuple[str, str]]:
    lines = content.splitlines()
    n = len(lines)
    i = 0
    while i < n:
        line = lines[i]
        if line.startswith("#### "):
            heading = line
            start = i + 1
            i += 1
            while i < n and not lines[i].startswith("#### "):
                i += 1
            body_part = "\n".join(lines[start:i])
            if body_part:
                body_part += "\n"
            yield heading, body_part
        else:
            i += 1


def filter_section_by_paths(section_content: str, sub_paths: list[str]) -> list[str]:
    """行フィルタリングアルゴリズム（R3・R4 共通）。"""
    lines = section_content.splitlines()
    groups: list[tuple[str | None, list[str]]] = []
    cur_h3: str | None = None
    cur_lines: list[str] = []

    for line in lines:
        if line.startswith("### "):
            groups.append((cur_h3, cur_lines))
            cur_h3 = line
            cur_lines = []
        else:
            cur_lines.append(line)
    groups.append((cur_h3, cur_lines))

    result: list[str] = []
    for h3, glines in groups:
        filtered = [
            line for line in glines
            if not line.startswith("- ") or any(
                p in line or p.rsplit("/", 1)[-1] in line
                for p in sub_paths
            )
        ]
        has_list = any(line.startswith("- ") for line in filtered)
        if h3 is None:
            result.extend(filtered)
        elif has_list:
            result.append(h3)
            result.extend(filtered)
        else:
            result.extend(line for line in filtered if line.strip())

    if not any(line.startswith("- ") for line in result):
        return []

    return result


def get_subsections(body: str, parent_heading: str, prefix: str) -> list[tuple[str, str]]:
    """親 H2 セクション内で prefix に一致する H4 サブセクションを返す。"""
    parent_content = get_section(body, parent_heading)
    if parent_content is None:
        return []

    return [
        (heading, subsection_content)
        for heading, subsection_content in _iter_h4_sections(parent_content)
        if heading.startswith(prefix)
    ]
