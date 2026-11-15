"""G7: parse_engine_model / parse_token_count unit tests."""

from __future__ import annotations

import pytest

from ghdag.metrics.parsers import parse_engine_model, parse_token_count


@pytest.mark.parametrize("command,expected", [
    ('claude -p "hello" --model claude-opus-4-6', ("claude", "claude-opus-4-6")),
    ('claude -p "hello"', ("claude", None)),
    ('gemini --model gemini-3.1-flash -p "hello"', ("gemini", "gemini-3.1-flash")),
    # cursor CLI was renamed to agent, so a cursor command yields (None, None)
    ('cursor --model claude-sonnet-4-6 -p "hello"', (None, None)),
])
def test_parse_engine_model_normal(command, expected):
    assert parse_engine_model(command) == expected


@pytest.mark.parametrize("command,expected", [
    # bash became the shell CLI, so it returns ("shell", None)
    ('bash -c "echo test"', ("shell", None)),
    ('python script.py', (None, None)),
    ('', (None, None)),
    ('claude --model', ("claude", None)),
    ("claude 'unclosed quote", (None, None)),
])
def test_parse_engine_model_edge_cases(command, expected):
    assert parse_engine_model(command) == expected


# ---------------------------------------------------------------------------
# #982 fix: pipe / agent / shell command forms
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("engine,stderr_text,expected", [
    ("claude", "...\nTotal tokens: 5678\n...", 5678),
    ("claude", '..."input_tokens": 1000... "output_tokens": 500...', 1500),
    ("claude", '..."input_tokens": 800...', 800),
    ("claude", '..."output_tokens": 300...', 300),
])
def test_parse_token_count_normal(engine, stderr_text, expected):
    assert parse_token_count(engine, stderr_text) == expected


@pytest.mark.parametrize("engine,stderr_text,expected", [
    ("claude", "stderr with no token info", None),
    ("gemini", "Total tokens: 999", None),
    ("cursor", "anything", None),
    (None, "Total tokens: 999", None),
])
def test_parse_token_count_no_match(engine, stderr_text, expected):
    assert parse_token_count(engine, stderr_text) == expected
