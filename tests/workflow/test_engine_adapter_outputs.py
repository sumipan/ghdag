"""Tests for ghdag.workflow.engine adapter exec records."""

from ghdag.workflow.engine import get_adapter


class TestAdapterOutputs:

    def test_claude_adapter_build_exec_record(self):
        adapter = get_adapter('claude')
        record = adapter.build_exec_record(uuid='u1', order_path='queue/order.md', result_path='results/result.md', model='claude-sonnet-4-6', depends=[])
        assert record['uuid'] == 'u1'
        cmd = record['command']
        assert cmd == "claude -p --model 'claude-sonnet-4-6' --output-format stream-json --verbose --dangerously-skip-permissions < queue/order.md"

    def test_gemini_adapter_uses_double_dash_model(self):
        """get_adapter('gemini') uses --model, not -m"""
        adapter = get_adapter('gemini')
        record = adapter.build_exec_record(uuid='u1', order_path='queue/order.md', result_path='results/result.md', model='gemini-2.5-flash', depends=[])
        cmd = record['command']
        assert ' -m ' not in cmd
        assert "--model 'gemini-2.5-flash'" in cmd

    def test_cursor_adapter_p_force_adjacent(self):
        """get_adapter('cursor') output has adjacent -p --force"""
        adapter = get_adapter('cursor')
        record = adapter.build_exec_record(uuid='u1', order_path='queue/order.md', result_path='results/result.md', model='auto', depends=[])
        assert '-p --force' in record['command']

    def test_shell_adapter_bash_pipefail(self):
        adapter = get_adapter('shell')
        record = adapter.build_exec_record(uuid='u1', order_path='queue/order.sh', result_path='results/result.md', model=None, depends=[])
        assert record['command'] == 'bash -o pipefail queue/order.sh'
