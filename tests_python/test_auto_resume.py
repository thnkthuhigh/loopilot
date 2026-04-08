"""Tests for Phase 2: Auto-Resume."""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from copilot_operator.cli import _run


def _write_state(tmp: Path, status: str, goal: str = 'fix bug', pending: dict | None = None, history: list | None = None) -> Path:
    state_dir = tmp / '.copilot-operator'
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / 'state.json'
    state = {
        'status': status,
        'goal': goal,
        'pendingDecision': pending,
        'history': history or [{'iteration': 1, 'score': 60, 'summary': 'partial'}],
        'runId': 'test-run-id',
        'resumeCount': 0,
    }
    state_file.write_text(json.dumps(state), encoding='utf-8')
    return state_file


def _write_config(tmp: Path) -> Path:
    config = tmp / 'copilot-operator.yml'
    config.write_text(
        f"""workspace: {tmp}
mode: agent
maxIterations: 3
stateFile: {tmp / '.copilot-operator' / 'state.json'}
summaryFile: {tmp / '.copilot-operator' / 'session-summary.json'}
memoryFile: {tmp / '.copilot-operator' / 'memory.md'}
logDir: {tmp / '.copilot-operator' / 'logs'}
validation: []
""",
        encoding='utf-8',
    )
    return config


class TestAutoResume(unittest.TestCase):
    """T2.1-T2.5: Auto-resume detection in _run()."""

    @patch('copilot_operator.cli.CopilotOperator')
    def test_auto_resume_when_pending_decision_exists(self, MockOp):
        """T2.1: pending state with action=continue → auto-resume."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = _write_config(tmp_path)
            _write_state(tmp_path, 'blocked', 'fix bug', {
                'action': 'continue',
                'reason': 'Max iterations reached',
                'reasonCode': 'MAX_ITERATIONS_REACHED',
                'nextPrompt': 'continue fixing',
            })
            mock_instance = MockOp.return_value
            mock_instance.run.return_value = {'status': 'complete'}

            stderr = io.StringIO()
            with patch('sys.stderr', stderr):
                _run(str(config_path), str(tmp_path), None, None, None, None, None, False, dry_run=True)

            # Should have called run with resume=True
            mock_instance.run.assert_called_once()
            call_kwargs = mock_instance.run.call_args
            self.assertTrue(call_kwargs[1].get('resume') or call_kwargs[0][1] if len(call_kwargs[0]) > 1 else call_kwargs[1].get('resume'))

    @patch('copilot_operator.cli.CopilotOperator')
    def test_fresh_flag_ignores_pending_state(self, MockOp):
        """T2.2: --fresh ignores pending state → fresh start."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = _write_config(tmp_path)
            _write_state(tmp_path, 'blocked', 'fix bug', {
                'action': 'continue',
                'reason': 'Max iterations reached',
                'reasonCode': 'MAX_ITERATIONS_REACHED',
                'nextPrompt': 'continue fixing',
            })
            mock_instance = MockOp.return_value
            mock_instance.run.return_value = {'status': 'complete'}

            _run(str(config_path), str(tmp_path), 'fix bug', None, None, None, None, False, dry_run=True, fresh=True)

            # Should have called run with resume=False (fresh start)
            call_args = mock_instance.run.call_args
            resume_arg = call_args[1].get('resume', call_args[0][1] if len(call_args[0]) > 1 else False)
            self.assertFalse(resume_arg)

    @patch('copilot_operator.cli.CopilotOperator')
    def test_no_auto_resume_when_status_complete(self, MockOp):
        """T2.3: status=complete → fresh start, no auto-resume."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = _write_config(tmp_path)
            _write_state(tmp_path, 'complete', 'fix bug', None)
            mock_instance = MockOp.return_value
            mock_instance.run.return_value = {'status': 'complete'}

            _run(str(config_path), str(tmp_path), 'new goal', None, None, None, None, False, dry_run=True)

            call_args = mock_instance.run.call_args
            resume_arg = call_args[1].get('resume', call_args[0][1] if len(call_args[0]) > 1 else False)
            self.assertFalse(resume_arg)

    @patch('copilot_operator.cli.CopilotOperator')
    def test_auto_resume_prints_notification(self, MockOp):
        """T2.4: auto-resume prints 'Auto-resuming' to stderr."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = _write_config(tmp_path)
            _write_state(tmp_path, 'blocked', 'fix bug', {
                'action': 'continue',
                'reason': 'Max iterations reached',
                'reasonCode': 'MAX_ITERATIONS_REACHED',
                'nextPrompt': 'continue fixing',
            })
            mock_instance = MockOp.return_value
            mock_instance.run.return_value = {'status': 'complete'}

            stderr = io.StringIO()
            with patch('sys.stderr', stderr):
                _run(str(config_path), str(tmp_path), None, None, None, None, None, False, dry_run=True)

            self.assertIn('Auto-resuming', stderr.getvalue())

    @patch('copilot_operator.cli.CopilotOperator')
    def test_warn_when_goal_changed_on_resume(self, MockOp):
        """T2.5: goal changed → prints warning about goal change, starts fresh."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = _write_config(tmp_path)
            _write_state(tmp_path, 'blocked', 'old goal text here', {
                'action': 'continue',
                'reason': 'Max iterations reached',
                'reasonCode': 'MAX_ITERATIONS_REACHED',
                'nextPrompt': 'continue fixing',
            })
            mock_instance = MockOp.return_value
            mock_instance.run.return_value = {'status': 'complete'}

            stderr = io.StringIO()
            with patch('sys.stderr', stderr):
                _run(str(config_path), str(tmp_path), 'completely different new goal', None, None, None, None, False, dry_run=True)

            self.assertIn('Goal changed', stderr.getvalue())
            # Should NOT auto-resume when goal changed
            call_args = mock_instance.run.call_args
            resume_arg = call_args[1].get('resume', call_args[0][1] if len(call_args[0]) > 1 else False)
            self.assertFalse(resume_arg)

    @patch('copilot_operator.cli.CopilotOperator')
    def test_auto_resume_on_error_status(self, MockOp):
        """Auto-resume also works for status=error with pending decision."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = _write_config(tmp_path)
            _write_state(tmp_path, 'error', 'fix bug', {
                'action': 'continue',
                'reason': 'Consecutive errors',
                'reasonCode': 'CONSECUTIVE_ERRORS',
                'nextPrompt': 'retry',
            })
            mock_instance = MockOp.return_value
            mock_instance.run.return_value = {'status': 'complete'}

            stderr = io.StringIO()
            with patch('sys.stderr', stderr):
                _run(str(config_path), str(tmp_path), None, None, None, None, None, False, dry_run=True)

            self.assertIn('Auto-resuming', stderr.getvalue())


class TestPendingDecisionOnExit(unittest.TestCase):
    """Verify operator writes pendingDecision when exiting blocked/error."""

    def test_max_iterations_writes_pending_decision(self):
        """When run hits max iterations, pendingDecision is written to state."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            state_json = {
                'status': 'blocked',
                'finalReasonCode': 'MAX_ITERATIONS_REACHED',
                'pendingDecision': {
                    'action': 'continue',
                    'reason': 'Maximum iteration count reached — can be auto-resumed.',
                    'reasonCode': 'MAX_ITERATIONS_REACHED',
                    'nextPrompt': 'some prompt',
                },
            }
            state_file = tmp_path / 'state.json'
            state_file.write_text(json.dumps(state_json), encoding='utf-8')
            state = json.loads(state_file.read_text(encoding='utf-8'))
            pending = state.get('pendingDecision', {})
            self.assertEqual(pending['action'], 'continue')
            self.assertEqual(pending['reasonCode'], 'MAX_ITERATIONS_REACHED')


if __name__ == '__main__':
    unittest.main()
