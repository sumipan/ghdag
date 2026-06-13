"""Tests for ghdag tools list subcommand."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ghdag.tool.schema import FallbackEntry


def _write_tool(
    path: Path,
    name: str,
    *,
    filename: str | None = None,
    fallback: list[FallbackEntry] | None = None,
) -> Path:
    fname = filename or f"{name}.py"
    tool_path = path / fname
    fallback_lines = ""
    if fallback:
        entries = ", ".join(
            f'FallbackEntry(engine="{fb.engine}", model="{fb.model}")'
            for fb in fallback
        )
        fallback_lines = f", fallback=[{entries}]"
    tool_path.write_text(
        f'''\
from ghdag.tool.schema import ToolDef, FallbackEntry

tool = ToolDef(name="{name}", engine="claude-code", model="claude-opus-4-7"{fallback_lines})
''',
        encoding="utf-8",
    )
    return tool_path


class TestToolsListJson:
    def test_json_output(self, tmp_path, capsys):
        from ghdag.cli import main

        _write_tool(tmp_path, "fs_read")
        _write_tool(tmp_path, "http_get")

        main(["tools", "list", "--json", "--path", str(tmp_path)])

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "tools" in data
        assert len(data["tools"]) == 2
        names = {t["name"] for t in data["tools"]}
        assert names == {"fs_read", "http_get"}
        for tool in data["tools"]:
            assert tool["engine"] == "claude-code"
            assert tool["model"] == "claude-opus-4-7"
            assert tool["fallback"] == []

    def test_json_with_fallback(self, tmp_path, capsys):
        from ghdag.cli import main

        _write_tool(
            tmp_path,
            "fs_read",
            fallback=[FallbackEntry(engine="claude-code", model="claude-sonnet-4-6")],
        )

        main(["tools", "list", "--json", "--path", str(tmp_path)])

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert len(data["tools"]) == 1
        fb = data["tools"][0]["fallback"]
        assert fb == [{"engine": "claude-code", "model": "claude-sonnet-4-6"}]

    def test_empty_directory_json(self, tmp_path, capsys):
        from ghdag.cli import main

        main(["tools", "list", "--json", "--path", str(tmp_path)])

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data == {"tools": []}


class TestToolsListText:
    def test_text_output(self, tmp_path, capsys):
        from ghdag.cli import main

        _write_tool(tmp_path, "fs_read")
        _write_tool(tmp_path, "http_get")

        main(["tools", "list", "--path", str(tmp_path)])

        captured = capsys.readouterr()
        lines = captured.out.strip().split("\n")
        assert lines == [
            "fs_read: claude-code/claude-opus-4-7",
            "http_get: claude-code/claude-opus-4-7",
        ]

    def test_empty_directory_text(self, tmp_path, capsys):
        from ghdag.cli import main

        main(["tools", "list", "--path", str(tmp_path)])

        captured = capsys.readouterr()
        assert captured.out == ""


class TestToolsListErrors:
    def test_directory_not_found(self, tmp_path, capsys):
        from ghdag.cli import main

        missing = tmp_path / "missing"
        with pytest.raises(SystemExit) as exc_info:
            main(["tools", "list", "--path", str(missing)])
        assert exc_info.value.code == 1

        captured = capsys.readouterr()
        assert f"error: Directory not found: {missing}" in captured.err

    def test_invalid_tool_definition(self, tmp_path, capsys):
        from ghdag.cli import main

        (tmp_path / "bad.py").write_text('tool = "not a ToolDef"\n', encoding="utf-8")
        with pytest.raises(SystemExit) as exc_info:
            main(["tools", "list", "--path", str(tmp_path)])
        assert exc_info.value.code == 1

        captured = capsys.readouterr()
        assert "error:" in captured.err
        assert "must be a ToolDef instance" in captured.err

    def test_missing_path_argument(self, capsys):
        from ghdag.cli import main

        with pytest.raises(SystemExit):
            main(["tools", "list"])

        captured = capsys.readouterr()
        assert "--path" in captured.err
