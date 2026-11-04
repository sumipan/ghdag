"""Tests for ghdag.workflow.schema StepConfig."""

from __future__ import annotations

import pytest

from ghdag.workflow.schema import StepConfig


class TestAC1AgentFieldRemoved:

    def test_step_config_engine_keyword_works(self):
        """StepConfig(engine='cursor') → engine is 'cursor'"""
        step = StepConfig(template='t', model='m', engine='cursor')
        assert step.engine == 'cursor'

    def test_step_config_default_engine_is_claude(self):
        """StepConfig() → engine is 'claude' (default)"""
        step = StepConfig(template='t', model='m')
        assert step.engine == 'claude'

    def test_step_config_agent_keyword_raises_type_error(self):
        """StepConfig(agent='cursor') → TypeError (agent field removed)"""
        with pytest.raises(TypeError):
            StepConfig(template='t', model='m', agent='cursor')

