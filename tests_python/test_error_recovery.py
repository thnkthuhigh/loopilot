"""Tests for Phase 3: Error Recovery Nâng Cao."""
from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

from copilot_operator.operator import CopilotOperator, Decision, _is_retryable
from copilot_operator.vscode_chat import VSCodeChatError, VSCodeChatRetryableError


def _make_operator(max_error_retries: int = 3, backoff_base: float = 2.0, max_iterations: int = 5) -> CopilotOperator:
    """Create a CopilotOperator without triggering __init__ side effects."""
    config = MagicMock()
    config.max_iterations = max_iterations
    config.max_error_retries = max_error_retries
    config.error_backoff_base_seconds = backoff_base
    config.workspace = MagicMock()
    config.state_file = MagicMock()
    config.state_file.exists.return_value = False
    config.log_dir = MagicMock()
    config.memory_file = MagicMock()
    config.summary_file = MagicMock()
    config.llm_config_raw = {}

    op = CopilotOperator.__new__(CopilotOperator)
    op.config = config
    op.dry_run = False
    op.live = False
    op._llm_brain = None
    op._write_runtime = MagicMock()
    op._now = lambda: '2026-01-01T00:00:00Z'

    # Fake _prepare_run to skip real init
    initial_decision = Decision(action='continue', reason='Initial', next_prompt='test goal', reason_code='INITIAL')
    op.runtime = {
        'status': 'running', 'history': [], 'plan': {},
        'goal': 'test goal', 'runId': 'test-run',
    }
    op._prepare_run = MagicMock(return_value=('test goal', initial_decision, 1))
    # Mock ensure_workspace_storage
    return op


class TestErrorClassification(unittest.TestCase):
    """T3.2: error type classification."""

    def test_retryable_error_is_retryable(self):
        exc = VSCodeChatRetryableError('timeout')
        self.assertTrue(_is_retryable(exc))

    def test_vscode_chat_error_is_not_retryable(self):
        exc = VSCodeChatError('code.cmd not found')
        self.assertFalse(_is_retryable(exc))

    def test_file_not_found_is_fatal(self):
        exc = FileNotFoundError('missing.py')
        self.assertFalse(_is_retryable(exc))

    def test_permission_error_is_fatal(self):
        exc = PermissionError('access denied')
        self.assertFalse(_is_retryable(exc))

    def test_json_decode_error_is_retryable(self):
        exc = json.JSONDecodeError('bad json', '', 0)
        self.assertTrue(_is_retryable(exc))

    def test_value_error_is_retryable(self):
        exc = ValueError('unexpected value')
        self.assertTrue(_is_retryable(exc))

    def test_key_error_is_retryable(self):
        exc = KeyError('missing key')
        self.assertTrue(_is_retryable(exc))

    def test_runtime_error_is_retryable(self):
        exc = RuntimeError('general error')
        self.assertTrue(_is_retryable(exc))


class TestBackoffDelay(unittest.TestCase):
    """T3.1: backoff delay between retries."""

    @patch('copilot_operator.operator.time')
    @patch('copilot_operator.operator.ensure_workspace_storage')
    def test_backoff_delay_between_retries(self, mock_storage, mock_time):
        """Trigger 2 retryable errors, verify sleep called with increasing delays."""
        mock_storage.return_value = MagicMock()
        mock_storage.return_value.__truediv__ = lambda self, x: MagicMock()

        op = _make_operator()

        call_count = 0
        def mock_run_iteration(iteration, goal, decision, chat_dir):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise VSCodeChatRetryableError(f'Error {call_count}')
            op.runtime['status'] = 'complete'
            op.runtime['_last_decision'] = Decision(action='done', reason='done', reason_code='DONE')
            return {'status': 'complete'}

        op._run_iteration = mock_run_iteration

        result = op.run('test goal')
        self.assertEqual(result['status'], 'complete')

        # Verify sleep was called with backoff delays
        sleep_calls = mock_time.sleep.call_args_list
        self.assertEqual(len(sleep_calls), 2)
        # 2^1 = 2, 2^2 = 4
        self.assertAlmostEqual(sleep_calls[0][0][0], 2.0)
        self.assertAlmostEqual(sleep_calls[1][0][0], 4.0)


class TestFatalErrorStopsImmediately(unittest.TestCase):
    """T3.2: fatal error stops run after 1 occurrence."""

    @patch('copilot_operator.operator.ensure_workspace_storage')
    def test_fatal_error_stops_after_one(self, mock_storage):
        mock_storage.return_value = MagicMock()
        mock_storage.return_value.__truediv__ = lambda self, x: MagicMock()

        op = _make_operator()

        iteration_count = 0
        def mock_run_iteration(iteration, goal, decision, chat_dir):
            nonlocal iteration_count
            iteration_count += 1
            raise FileNotFoundError('critical binary missing')

        op._run_iteration = mock_run_iteration

        result = op.run('test goal')
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['reasonCode'], 'FATAL_ERROR')
        self.assertFalse(result['retryable'])
        # Only 1 attempt — fatal stops immediately
        self.assertEqual(iteration_count, 1)


class TestErrorTypeInState(unittest.TestCase):
    """T3.4: error type and retryable flag logged in state."""

    @patch('copilot_operator.operator.ensure_workspace_storage')
    def test_error_logged_with_type_and_retryable(self, mock_storage):
        mock_storage.return_value = MagicMock()
        mock_storage.return_value.__truediv__ = lambda self, x: MagicMock()

        op = _make_operator()

        def mock_run_iteration(iteration, goal, decision, chat_dir):
            raise FileNotFoundError('missing binary')

        op._run_iteration = mock_run_iteration

        op.run('test goal')
        errors = op.runtime.get('errors', [])
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]['type'], 'FileNotFoundError')
        self.assertFalse(errors[0]['retryable'])


class TestSessionResetHint(unittest.TestCase):
    """T3.5: session reset hint after consecutive errors."""

    @patch('copilot_operator.operator.time')
    @patch('copilot_operator.operator.ensure_workspace_storage')
    def test_session_reset_hint_after_two_errors(self, mock_storage, mock_time):
        mock_storage.return_value = MagicMock()
        mock_storage.return_value.__truediv__ = lambda self, x: MagicMock()

        op = _make_operator(max_error_retries=4)

        call_count = 0
        captured_decisions = []
        def mock_run_iteration(iteration, goal, decision, chat_dir):
            nonlocal call_count
            call_count += 1
            captured_decisions.append(decision)
            if call_count <= 3:
                raise VSCodeChatRetryableError(f'Error {call_count}')
            op.runtime['status'] = 'complete'
            op.runtime['_last_decision'] = Decision(action='done', reason='done', reason_code='DONE')
            return {'status': 'complete'}

        op._run_iteration = mock_run_iteration

        op.run('test goal')

        # After 2+ errors, the next prompt should contain session reset hint
        # captured_decisions[2] should have hint (decision after error 2, count=2)
        # captured_decisions[3] should have hint (decision after error 3, count=3)
        self.assertIn('Start fresh analysis', captured_decisions[2].next_prompt)
        self.assertIn('Start fresh analysis', captured_decisions[3].next_prompt)
        # First retry should NOT have hint (count=1 < 2)
        self.assertNotIn('Start fresh analysis', captured_decisions[1].next_prompt)


if __name__ == '__main__':
    unittest.main()
