from unittest.mock import MagicMock, patch

from ghdag.workflow.conditional_step import run_with_template, substitute_vars


def test_substitute_vars_basic():
    template = 'Issue number: ${issue_number}'
    result = substitute_vars(template, {'issue_number': '42'})
    assert result == 'Issue number: 42'

def test_substitute_vars_multiple():
    template = '${a} and ${b}'
    result = substitute_vars(template, {'a': 'hello', 'b': 'world'})
    assert result == 'hello and world'

def test_substitute_vars_double_dollar_unescaped():
    template = '$$VAR and ${key}'
    result = substitute_vars(template, {'key': 'value'})
    assert result == '$VAR and value'

def test_substitute_vars_unknown_key_preserved():
    template = '${unknown} ${known}'
    result = substitute_vars(template, {'known': 'x'})
    assert result == '${unknown} x'

def test_substitute_vars_empty_variables():
    template = 'no vars here'
    assert substitute_vars(template, {}) == 'no vars here'

def test_substitute_vars_double_dollar_only():
    template = 'cd $$WORK_DIR && ls'
    result = substitute_vars(template, {})
    assert result == 'cd $WORK_DIR && ls'

def test_run_with_template_passes_model(tmp_path):
    template_file = tmp_path / 'test.md'
    template_file.write_text('order')
    with patch('ghdag.workflow.conditional_step.subprocess.run') as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        run_with_template(str(template_file), {}, model='claude-opus-4-6')
    cmd = mock_run.call_args[0][0]
    assert '--model' in cmd
    model_idx = cmd.index('--model')
    assert cmd[model_idx + 1] == 'claude-opus-4-6'

def test_run_with_template_returns_exit_code(tmp_path):
    template_file = tmp_path / 'test.md'
    template_file.write_text('order')
    with patch('ghdag.workflow.conditional_step.subprocess.run') as mock_run:
        mock_run.return_value = MagicMock(returncode=1)
        code = run_with_template(str(template_file), {})
    assert code == 1
