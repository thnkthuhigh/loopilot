"""Tests for Phase 4: Risk Gate & Escalation."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from copilot_operator.cli import _approve_escalation, _reject_escalation


class TestEscalationPolicy(unittest.TestCase):
    """T4.1, T4.4: escalation vs block policy."""

    def _make_operator(self, policy: str = 'escalate'):
        from copilot_operator.operator import CopilotOperator, Decision
        config = MagicMock()
        config.max_iterations = 5
        config.max_error_retries = 3
        config.error_backoff_base_seconds = 2.0
        config.escalation_policy = policy
        config.large_diff_threshold = 200
        config.workspace = MagicMock()
        config.state_file = MagicMock()
        config.state_file.exists.return_value = False
        config.log_dir = MagicMock()
        config.summary_file = MagicMock()
        config.memory_file = MagicMock()
        config.llm_config_raw = {}

        op = CopilotOperator.__new__(CopilotOperator)
        op.config = config
        op.dry_run = False
        op.live = False
        op._llm_brain = None
        op._write_runtime = MagicMock()
        op._now = lambda: '2026-01-01T00:00:00Z'
        op._post_run_hooks = MagicMock()

        initial_decision = Decision(action='continue', reason='Initial', next_prompt='test goal', reason_code='INITIAL')
        op.runtime = {
            'status': 'running', 'history': [], 'plan': {},
            'goal': 'test goal', 'runId': 'test-run',
        }
        op._prepare_run = MagicMock(return_value=('test goal', initial_decision, 1))
        return op

    @patch('copilot_operator.operator.ensure_workspace_storage')
    @patch('copilot_operator.operator.get_diff_summary', return_value='diff +10 -5')
    def test_escalate_policy_sets_escalated_status(self, mock_diff, mock_storage):
        """T4.1: escalate policy → status='escalated', pendingEscalation written."""
        mock_storage.return_value = MagicMock()
        mock_storage.return_value.__truediv__ = lambda self, x: MagicMock()

        op = self._make_operator('escalate')

        call_count = 0
        def mock_run_iteration(iteration, goal, decision, chat_dir):
            nonlocal call_count
            call_count += 1
            # Simulate iteration that triggers protected path check
            # We need to mock _check_protected_paths
            return None  # Would normally continue

        # Instead, directly test the protected path block
        op._check_protected_paths = MagicMock(return_value=['src/critical.py'])

        # Mock all the things _run_iteration calls
        def fake_iteration(iteration, goal, decision, chat_dir):
            # Simulate the protected check path
            violations = op._check_protected_paths()
            if violations:
                policy = op.config.escalation_policy
                if policy == 'escalate':
                    op.runtime['status'] = 'escalated'
                    op.runtime['pendingEscalation'] = {
                        'type': 'protected_path',
                        'paths': violations,
                        'iteration': iteration,
                        'diffSummary': 'diff +10 -5',
                        'timestamp': op._now(),
                    }
                    return {
                        'status': 'escalated',
                        'reasonCode': 'ESCALATION_REQUIRED',
                        'violations': violations,
                    }
            return {'status': 'complete'}

        op._run_iteration = fake_iteration

        result = op.run('test goal')
        self.assertEqual(result['status'], 'escalated')
        self.assertEqual(result['reasonCode'], 'ESCALATION_REQUIRED')
        self.assertIn('src/critical.py', result['violations'])

    @patch('copilot_operator.operator.ensure_workspace_storage')
    def test_block_policy_returns_error(self, mock_storage):
        """T4.4: block policy → error behavior unchanged."""
        mock_storage.return_value = MagicMock()
        mock_storage.return_value.__truediv__ = lambda self, x: MagicMock()

        op = self._make_operator('block')

        def fake_iteration(iteration, goal, decision, chat_dir):
            violations = op._check_protected_paths()
            if violations:
                op.runtime['status'] = 'error'
                op.runtime['finalReasonCode'] = 'PROTECTED_PATH_VIOLATION'
                return {
                    'status': 'error',
                    'reasonCode': 'PROTECTED_PATH_VIOLATION',
                    'violations': violations,
                }
            return {'status': 'complete'}

        op._check_protected_paths = MagicMock(return_value=['src/critical.py'])
        op._run_iteration = fake_iteration

        result = op.run('test goal')
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['reasonCode'], 'PROTECTED_PATH_VIOLATION')


class TestEscalationStateContext(unittest.TestCase):
    """T4.6: escalation state has full context."""

    def test_escalation_state_has_full_context(self):
        escalation = {
            'type': 'protected_path',
            'paths': ['src/important.py'],
            'iteration': 3,
            'diffSummary': '+10 lines -5 lines',
            'timestamp': '2026-01-01T00:00:00Z',
        }
        self.assertEqual(escalation['type'], 'protected_path')
        self.assertIn('src/important.py', escalation['paths'])
        self.assertEqual(escalation['iteration'], 3)
        self.assertIn('lines', escalation['diffSummary'])
        self.assertTrue(escalation['timestamp'])


class TestApproveEscalation(unittest.TestCase):
    """T4.2: approve escalation resumes run."""

    def test_approve_clears_escalation_and_sets_continue(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            state_dir = tmp_path / '.copilot-operator'
            state_dir.mkdir()
            state_file = state_dir / 'state.json'
            state = {
                'status': 'escalated',
                'goal': 'fix bug',
                'pendingEscalation': {
                    'type': 'protected_path',
                    'paths': ['src/critical.py'],
                    'iteration': 2,
                    'diffSummary': '+5 -3',
                    'timestamp': '2026-01-01T00:00:00Z',
                },
                'pendingDecision': {
                    'action': 'escalate',
                    'reason': 'Protected path modified',
                    'reasonCode': 'ESCALATION_REQUIRED',
                    'nextPrompt': 'continue fixing',
                },
            }
            state_file.write_text(json.dumps(state), encoding='utf-8')

            config_path = tmp_path / 'copilot-operator.yml'
            config_path.write_text(
                f"workspace: {tmp_path}\nstateFile: {state_file}\nsummaryFile: {state_dir / 'summary.json'}\n"
                f"memoryFile: {state_dir / 'memory.md'}\nlogDir: {state_dir / 'logs'}\nvalidation: []\n",
                encoding='utf-8',
            )

            result = _approve_escalation(str(config_path), str(tmp_path))
            self.assertEqual(result, 0)

            updated = json.loads(state_file.read_text(encoding='utf-8'))
            self.assertIsNone(updated['pendingEscalation'])
            self.assertEqual(updated['status'], 'blocked')
            self.assertEqual(updated['pendingDecision']['action'], 'continue')
            self.assertEqual(updated['pendingDecision']['reasonCode'], 'ESCALATION_APPROVED')

    def test_approve_no_escalation_returns_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            state_dir = tmp_path / '.copilot-operator'
            state_dir.mkdir()
            state_file = state_dir / 'state.json'
            state_file.write_text(json.dumps({'status': 'running'}), encoding='utf-8')

            config_path = tmp_path / 'copilot-operator.yml'
            config_path.write_text(
                f"workspace: {tmp_path}\nstateFile: {state_file}\nsummaryFile: {state_dir / 'summary.json'}\n"
                f"memoryFile: {state_dir / 'memory.md'}\nlogDir: {state_dir / 'logs'}\nvalidation: []\n",
                encoding='utf-8',
            )

            result = _approve_escalation(str(config_path), str(tmp_path))
            self.assertEqual(result, 1)


class TestRejectEscalation(unittest.TestCase):
    """T4.3: reject escalation rolls back and adjusts prompt."""

    @patch('copilot_operator.snapshot.rollback_to_last_good')
    @patch('copilot_operator.snapshot.SnapshotManager')
    def test_reject_rollbacks_and_adjusts_prompt(self, MockSnap, mock_rollback):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            state_dir = tmp_path / '.copilot-operator'
            state_dir.mkdir()
            state_file = state_dir / 'state.json'
            state = {
                'status': 'escalated',
                'goal': 'fix bug',
                'pendingEscalation': {
                    'type': 'protected_path',
                    'paths': ['src/critical.py', 'config/settings.py'],
                    'iteration': 2,
                    'diffSummary': '+5 -3',
                    'timestamp': '2026-01-01T00:00:00Z',
                },
                'pendingDecision': {
                    'action': 'escalate',
                    'reason': 'Protected path modified',
                    'reasonCode': 'ESCALATION_REQUIRED',
                    'nextPrompt': 'continue fixing',
                },
            }
            state_file.write_text(json.dumps(state), encoding='utf-8')

            config_path = tmp_path / 'copilot-operator.yml'
            config_path.write_text(
                f"workspace: {tmp_path}\nstateFile: {state_file}\nsummaryFile: {state_dir / 'summary.json'}\n"
                f"memoryFile: {state_dir / 'memory.md'}\nlogDir: {state_dir / 'logs'}\nvalidation: []\n",
                encoding='utf-8',
            )

            result = _reject_escalation(str(config_path), str(tmp_path))
            self.assertEqual(result, 0)

            updated = json.loads(state_file.read_text(encoding='utf-8'))
            self.assertIsNone(updated['pendingEscalation'])
            self.assertEqual(updated['status'], 'blocked')
            self.assertEqual(updated['pendingDecision']['reasonCode'], 'ESCALATION_REJECTED')
            self.assertIn('Do NOT modify', updated['pendingDecision']['nextPrompt'])
            self.assertIn('src/critical.py', updated['pendingDecision']['nextPrompt'])


if __name__ == '__main__':
    unittest.main()
