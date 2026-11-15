"""Multi-engine DAG progress events (cursor / codex → jobs/events).

Uses slimmed real fixtures from tests/fixtures/ (Issue #2967).
"""

from __future__ import annotations

from ghdag.dag.task_launcher import _engine_emits_stream_events


class TestEngineEmitsStreamEvents:
    def test_claude_cursor_codex_emit(self):
        assert _engine_emits_stream_events("claude")
        assert _engine_emits_stream_events("cursor")
        assert _engine_emits_stream_events("codex")

    def test_gemini_shell_do_not_emit(self):
        assert not _engine_emits_stream_events("gemini")
        assert not _engine_emits_stream_events("shell")
        assert not _engine_emits_stream_events(None)
