import re
from unittest.mock import MagicMock, patch

from ghdag.metrics.parsers import parse_token_count
from ghdag.workflow.conditional_step import run_with_template, substitute_vars


def test_substitute_vars_basic():
    template = "Issue number: ${issue_number}"
    result = substitute_vars(template, {"issue_number": "42"})
    assert result == "Issue number: 42"


def test_substitute_vars_multiple():
    template = "${a} and ${b}"
    result = substitute_vars(template, {"a": "hello", "b": "world"})
    assert result == "hello and world"


def test_substitute_vars_double_dollar_unescaped():
    template = "$$VAR and ${key}"
    result = substitute_vars(template, {"key": "value"})
    assert result == "$VAR and value"


def test_substitute_vars_unknown_key_preserved():
    template = "${unknown} ${known}"
    result = substitute_vars(template, {"known": "x"})
    assert result == "${unknown} x"


def test_substitute_vars_empty_variables():
    template = "no vars here"
    assert substitute_vars(template, {}) == "no vars here"


def test_substitute_vars_double_dollar_only():
    template = "cd $$WORK_DIR && ls"
    result = substitute_vars(template, {})
    assert result == "cd $WORK_DIR && ls"


def test_run_with_template_calls_claude(tmp_path):
    template_file = tmp_path / "test.md"
    template_file.write_text("Hello ${name}")

    with patch("ghdag.workflow.conditional_step.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        code = run_with_template(str(template_file), {"name": "World"})

    assert code == 0
    call_args = mock_run.call_args
    cmd = call_args[0][0]
    assert "claude" in cmd
    assert call_args[1]["input"] == "Hello World"


def test_run_with_template_passes_model(tmp_path):
    template_file = tmp_path / "test.md"
    template_file.write_text("order")

    with patch("ghdag.workflow.conditional_step.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        run_with_template(str(template_file), {}, model="claude-opus-4-6")

    cmd = mock_run.call_args[0][0]
    assert "--model" in cmd
    model_idx = cmd.index("--model")
    assert cmd[model_idx + 1] == "claude-opus-4-6"


def test_run_with_template_uses_dangerously_skip_permissions(tmp_path):
    template_file = tmp_path / "test.md"
    template_file.write_text("order")

    with patch("ghdag.workflow.conditional_step.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        run_with_template(str(template_file), {})

    cmd = mock_run.call_args[0][0]
    assert "--dangerously-skip-permissions" in cmd
    assert "--force" not in cmd


def test_run_with_template_returns_exit_code(tmp_path):
    template_file = tmp_path / "test.md"
    template_file.write_text("order")

    with patch("ghdag.workflow.conditional_step.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1)
        code = run_with_template(str(template_file), {})

    assert code == 1


def test_run_with_template_cursor_engine(tmp_path):
    template_file = tmp_path / "test.md"
    template_file.write_text("Cursor order ${key}")

    with patch("ghdag.workflow.conditional_step.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        code = run_with_template(
            str(template_file),
            {"key": "ok"},
            model="auto",
            engine="cursor",
        )

    assert code == 0
    cmd = mock_run.call_args[0][0]
    assert cmd[:3] == ["cursor", "agent", "-p"]
    assert "--force" in cmd
    assert "--model" in cmd
    assert cmd[cmd.index("--model") + 1] == "auto"
    assert "--dangerously-skip-permissions" not in cmd
    assert mock_run.call_args[1]["input"] == "Cursor order ok"


def test_run_with_template_default_engine_is_claude(tmp_path):
    template_file = tmp_path / "test.md"
    template_file.write_text("order")

    with patch("ghdag.workflow.conditional_step.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        run_with_template(str(template_file), {})

    cmd = mock_run.call_args[0][0]
    assert cmd[0] == "claude"


def test_run_with_template_emits_diagnostic_logs(tmp_path, capsys):
    template_file = tmp_path / "order.md"
    template_file.write_text("order")

    with patch("ghdag.workflow.conditional_step.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1)
        run_with_template(
            str(template_file),
            {},
            model="claude-opus-4-6",
            engine="claude",
        )

    stderr = capsys.readouterr().err
    assert re.search(
        rf"\[conditional_step\] start engine=claude model=claude-opus-4-6 template={re.escape(str(template_file))}",
        stderr,
    )
    assert re.search(
        r"\[conditional_step\] done exit_code=1 elapsed=\d+\.\d+s",
        stderr,
    )


def test_diagnostic_logs_do_not_interfere_with_parse_token_count():
    stderr = (
        "[conditional_step] start engine=claude model=claude-sonnet-4-6 template=/tmp/order.md\n"
        "Total tokens: 5678\n"
        "[conditional_step] done exit_code=0 elapsed=12.3s\n"
    )
    assert parse_token_count("claude", stderr) == 5678
