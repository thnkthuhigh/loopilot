"""Tests for enhanced stop controller: cost/time ceiling, diff/response dedup."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from copilot_operator.config import OperatorConfig
from copilot_operator.operator import CopilotOperator, Decision
from copilot_operator.reasoning import (
    detect_loops,
    recommend_strategy,
)

# ---------------------------------------------------------------------------
# Reasoning: new loop signals
# ---------------------------------------------------------------------------

class TestDiffRepeatDetection(unittest.TestCase):
    """Detect when the same files are changed in consecutive iterations."""

    def test_same_changed_files_detected(self):
        history = [
            {'changedFiles': ['a.py', 'b.py'], 'summary': 'fix a', 'score': 70},
            {'changedFiles': ['a.py', 'b.py'], 'summary': 'fix b', 'score': 70},
            {'changedFiles': ['a.py', 'b.py'], 'summary': 'fix c', 'score': 70},
        ]
        signals = detect_loops(history)
        diff_signals = [s for s in signals if s.kind == 'diff_repeat']
        self.assertEqual(len(diff_signals), 1)
        self.assertTrue(diff_signals[0].detected)
        self.assertIn('a.py', diff_signals[0].detail)

    def test_different_changed_files_not_detected(self):
        history = [
            {'changedFiles': ['a.py'], 'summary': 'fix a', 'score': 70},
            {'changedFiles': ['b.py'], 'summary': 'fix b', 'score': 80},
            {'changedFiles': ['c.py'], 'summary': 'fix c', 'score': 90},
        ]
        signals = detect_loops(history)
        diff_signals = [s for s in signals if s.kind == 'diff_repeat']
        self.assertEqual(len(diff_signals), 0)


class TestResponseRepeatDetection(unittest.TestCase):
    """Detect near-identical response content."""

    def test_same_response_detected(self):
        history = [
            {'summary': 'Fixed the login', 'blockers': [{'item': 'test fails'}], 'score': 60},
            {'summary': 'Fixed the login', 'blockers': [{'item': 'test fails'}], 'score': 60},
            {'summary': 'Fixed the login', 'blockers': [{'item': 'test fails'}], 'score': 60},
        ]
        signals = detect_loops(history)
        resp_signals = [s for s in signals if s.kind == 'response_repeat']
        self.assertEqual(len(resp_signals), 1)
        self.assertTrue(resp_signals[0].detected)

    def test_different_responses_not_detected(self):
        history = [
            {'summary': 'Fixed auth module', 'blockers': [], 'score': 60},
            {'summary': 'Added test coverage', 'blockers': [], 'score': 75},
            {'summary': 'Refactored login', 'blockers': [], 'score': 90},
        ]
        signals = detect_loops(history)
        resp_signals = [s for s in signals if s.kind == 'response_repeat']
        self.assertEqual(len(resp_signals), 0)


class TestNewStrategyHints(unittest.TestCase):
    """Strategy hints for new loop types."""

    def test_diff_repeat_generates_switch_baton_hint(self):
        history = [
            {'changedFiles': ['x.py'], 'summary': 's', 'score': 50,
             'decisionNextPrompt': 'fix', 'decisionCode': 'WORK_REMAINS',
             'blockers': [], 'validation_after': []},
            {'changedFiles': ['x.py'], 'summary': 'ss', 'score': 50,
             'decisionNextPrompt': 'fix2', 'decisionCode': 'WORK_REMAINS',
             'blockers': [], 'validation_after': []},
        ]
        hints = recommend_strategy(history, 'default', 85, 3, 6)
        actions = [h.action for h in hints]
        self.assertIn('switch_baton', actions)

    def test_response_repeat_generates_escalate_hint(self):
        history = [
            {'summary': 'same text', 'blockers': [{'item': 'b'}], 'score': 50,
             'changedFiles': [], 'decisionNextPrompt': 'fix', 'decisionCode': 'WORK_REMAINS',
             'validation_after': []},
            {'summary': 'same text', 'blockers': [{'item': 'b'}], 'score': 50,
             'changedFiles': [], 'decisionNextPrompt': 'fix2', 'decisionCode': 'WORK_REMAINS',
             'validation_after': []},
        ]
        hints = recommend_strategy(history, 'default', 85, 3, 6)
        actions = [h.action for h in hints]
        self.assertIn('escalate_profile', actions)


# ---------------------------------------------------------------------------
# Config: new fields
# ---------------------------------------------------------------------------

class TestConfigCeilings(unittest.TestCase):
    """Config supports cost and time ceiling fields."""

    def test_default_cost_ceiling_is_zero(self):
        from pathlib import Path
        config = OperatorConfig(workspace=Path('.'))
        self.assertEqual(config.max_llm_cost_usd, 0.0)

    def test_default_time_ceiling_is_zero(self):
        from pathlib import Path
        config = OperatorConfig(workspace=Path('.'))
        self.assertEqual(config.max_task_seconds, 0)


# ---------------------------------------------------------------------------
# Operator: cost ceiling
# ---------------------------------------------------------------------------

def _make_operator_with_ceilings(
    max_llm_cost_usd: float = 0.0,
    max_task_seconds: int = 0,
    max_iterations: int = 5,
) -> CopilotOperator:
    from pathlib import Path
    config = OperatorConfig(
        workspace=Path('/fake/workspace'),
        max_iterations=max_iterations,
        max_llm_cost_usd=max_llm_cost_usd,
        max_task_seconds=max_task_seconds,
    )
    op = CopilotOperator.__new__(CopilotOperator)
    op.config = config
    op.dry_run = False
    op.live = False
    op._llm_brain = None
    op._write_runtime = MagicMock()
    op._now = lambda: '2026-01-01T00:00:00Z'
    op._post_run_hooks = MagicMock()
    op._worker = MagicMock()

    initial_decision = Decision(action='continue', reason='Initial', next_prompt='test', reason_code='INITIAL')
    op.runtime = {
        'status': 'running', 'history': [], 'plan': {},
        'goal': 'test goal', 'runId': 'test-run',
    }
    op._prepare_run = MagicMock(return_value=('test goal', initial_decision, 1))
    return op


class TestCostCeiling(unittest.TestCase):

    @patch('copilot_operator.operator.ensure_workspace_storage')
    @patch('copilot_operator.operator.cleanup_operator_stashes', return_value=0)
    def test_cost_ceiling_stops_run(self, _mock_cleanup, mock_storage):
        mock_storage.return_value = MagicMock()
        mock_storage.return_value.__truediv__ = lambda self, x: MagicMock()

        op = _make_operator_with_ceilings(max_llm_cost_usd=0.01)
        # Set up a real LLM brain mock with stats
        llm_brain = MagicMock()
        llm_brain.stats = {'estimated_cost_usd': 0.05, 'calls': 10, 'total_tokens': 5000}
        op._llm_brain = llm_brain

        result = op.run('test goal')
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['reasonCode'], 'COST_CEILING_REACHED')

    @patch('copilot_operator.operator.ensure_workspace_storage')
    @patch('copilot_operator.operator.cleanup_operator_stashes', return_value=0)
    def test_no_cost_ceiling_when_zero(self, _mock_cleanup, mock_storage):
        mock_storage.return_value = MagicMock()
        mock_storage.return_value.__truediv__ = lambda self, x: MagicMock()

        op = _make_operator_with_ceilings(max_llm_cost_usd=0.0)
        llm_brain = MagicMock()
        llm_brain.stats = {'estimated_cost_usd': 100.0}
        op._llm_brain = llm_brain

        call_count = 0
        def mock_iteration(iteration, goal, decision, chat_dir):
            nonlocal call_count
            call_count += 1
            return {'status': 'complete', 'score': 100}

        op._run_iteration = mock_iteration
        result = op.run('test goal')
        self.assertEqual(result['status'], 'complete')


# ---------------------------------------------------------------------------
# Memory layering
# ---------------------------------------------------------------------------

class TestMemoryLayers(unittest.TestCase):
    """Memory file has 4 distinct layers."""

    def _make_op_with_history(self):
        from pathlib import Path
        config = OperatorConfig(workspace=Path('/fake/workspace'))
        op = CopilotOperator.__new__(CopilotOperator)
        op.config = config
        op._llm_brain = None
        op._worker = MagicMock()
        op.runtime = {
            'status': 'running',
            'workspace': '/fake/workspace',
            'mode': 'agent',
            'goal': 'test goal',
            'goalProfile': 'default',
            'targetScore': 85,
            'runId': 'test-run',
            'logDir': '/logs',
            'plan': {},
            'history': [
                {'iteration': 1, 'score': 50, 'summary': 'attempt 1', 'status': 'in_progress',
                 'decisionCode': 'WORK_REMAINS', 'blockers': [], 'changedFiles': ['a.py'],
                 'tests': 'fail', 'lint': 'pass', 'validation_after': []},
                {'iteration': 2, 'score': 50, 'summary': 'attempt 2 same approach', 'status': 'in_progress',
                 'decisionCode': 'WORK_REMAINS', 'blockers': [], 'changedFiles': ['a.py'],
                 'tests': 'fail', 'lint': 'pass', 'validation_after': [],
                 'next_prompt': 'continue', 'decisionNextPrompt': 'try again'},
                {'iteration': 3, 'score': 80, 'summary': 'improved approach', 'status': 'in_progress',
                 'decisionCode': 'WORK_REMAINS', 'blockers': [],
                 'changedFiles': ['a.py', 'b.py'], 'tests': 'pass', 'lint': 'pass',
                 'validation_after': [], 'next_prompt': 'finish up',
                 'decisionNextPrompt': 'finalize'},
            ],
            'allChangedFiles': ['a.py', 'b.py'],
        }
        return op

    def test_memory_has_layer_1_repo_context(self):
        op = self._make_op_with_history()
        memory = op._render_memory()
        self.assertIn('Layer 1', memory)
        self.assertIn('Repo Context', memory)
        self.assertIn('Ecosystem', memory)

    def test_memory_has_layer_2_task_context(self):
        op = self._make_op_with_history()
        memory = op._render_memory()
        self.assertIn('Layer 2', memory)
        self.assertIn('Task Context', memory)
        self.assertIn('test goal', memory)

    def test_memory_has_layer_3_delta(self):
        op = self._make_op_with_history()
        memory = op._render_memory()
        self.assertIn('Layer 3', memory)
        self.assertIn('Last Iteration Delta', memory)
        # Should show iteration 3 (the last one)
        self.assertIn('improved approach', memory)

    def test_memory_has_layer_4_failure_lessons(self):
        op = self._make_op_with_history()
        memory = op._render_memory()
        self.assertIn('Layer 4', memory)
        self.assertIn('Failure Lessons', memory)
        # Iteration 2 had same score as iteration 1 (50→50), should appear
        self.assertIn('Iter 2', memory)

    def test_memory_score_trajectory(self):
        op = self._make_op_with_history()
        memory = op._render_memory()
        self.assertIn('Score trajectory', memory)


if __name__ == '__main__':
    unittest.main()
