"""Tests for ghdag.dag.models — Task model field defaults (M1〜M3)."""

from ghdag.dag.models import Task


class TestTaskDefaults:
    """Task モデルのデフォルト値テスト"""

    def test_m1_result_path_default_none(self):
        """M1: result_path のデフォルト値が None"""
        task = Task(uuid="a", command="echo hi")
        assert task.result_path is None

    def test_m2_idempotency_key_default_none(self):
        """M2: idempotency_key のデフォルト値が None"""
        task = Task(uuid="a", command="echo hi")
        assert task.idempotency_key is None

    def test_m3_existing_field_defaults_unchanged(self):
        """M3: 既存フィールドのデフォルト値が変わらない"""
        task = Task(uuid="a", command="echo hi")
        assert task.depends == []
        assert task.retry == 0
        assert task.annotations == {}
