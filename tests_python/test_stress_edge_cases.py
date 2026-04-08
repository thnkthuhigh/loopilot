"""Tests for Phase 6: E2E Stress & Edge Case Tests.

These test edge cases and error paths that can be exercised without a live VS Code instance.
P6.7: No git repo, P6.8: Corrupted state, plus various resilience checks.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from copilot_operator.bootstrap import initialize_workspace
from copilot_operator.cli import _run
from copilot_operator.operator import CopilotOperator, Decision

# --- Helpers ---

def _write_config(tmp: Path) -> Path:
    config = tmp / 'copilot-operator.yml'
    config.write_text(
        f"""workspace: {tmp}
mode: agent
maxIterations: 2
stateFile: {tmp / '.copilot-operator' / 'state.json'}
summaryFile: {tmp / '.copilot-operator' / 'session-summary.json'}
memoryFile: {tmp / '.copilot-operator' / 'memory.md'}
logDir: {tmp / '.copilot-operator' / 'logs'}
validation: []
""",
        encoding='utf-8',
    )
    return config


def _make_operator(max_iterations: int = 3) -> CopilotOperator:
    from copilot_operator.brain import ProjectInsights
    config = MagicMock()
    config.max_iterations = max_iterations
    config.max_error_retries = 3
    config.error_backoff_base_seconds = 2.0
    config.escalation_policy = 'block'
    config.large_diff_threshold = 200
    config.workspace = Path('/fake')
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
    op._project_insights = ProjectInsights()
    op._learned_rules = []
    op._shared_brain = None
    op._intention_goal_type = 'default'
    op._post_run_hooks = MagicMock()

    initial_decision = Decision(action='continue', reason='Initial', next_prompt='test', reason_code='INITIAL')
    op.runtime = {
        'status': 'running', 'history': [], 'plan': {},
        'goal': 'test', 'runId': 'test-run',
    }
    op._prepare_run = MagicMock(return_value=('test', initial_decision, 1))
    return op


# --- P6.7: No git repo (graceful handling) ---

class TestNoGitRepo(unittest.TestCase):
    """P6.7: init and run in a directory without .git should not crash."""

    def test_init_in_non_git_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            # No .git directory — init should still work
            result = initialize_workspace(tmp_path, force=True)
            self.assertIn('created', result)
            self.assertIn('workspace', result)
            # Config file should exist
            self.assertTrue((tmp_path / 'copilot-operator.yml').exists())

    def test_changed_files_tracking_no_git(self):
        """Changed files tracking should gracefully handle missing git."""
        op = _make_operator()
        # Simulate what happens when get_all_changed_files fails
        op.runtime['allChangedFiles'] = []
        # No crash
        self.assertEqual(op.runtime['allChangedFiles'], [])


# --- P6.8: Corrupted state.json ---

class TestCorruptedState(unittest.TestCase):
    """P6.8: corrupted state.json should not crash the run."""

    @patch('copilot_operator.cli.CopilotOperator')
    def test_corrupted_state_json_ignored(self, MockOp):
        """Auto-resume with corrupted state.json → graceful fallback to fresh start."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = _write_config(tmp_path)
            state_dir = tmp_path / '.copilot-operator'
            state_dir.mkdir(parents=True, exist_ok=True)
            state_file = state_dir / 'state.json'
            state_file.write_text('NOT VALID JSON {{{', encoding='utf-8')

            mock_instance = MockOp.return_value
            mock_instance.run.return_value = {'status': 'complete'}

            # Should not crash — corrupted JSON is caught
            _run(str(config_path), str(tmp_path), 'fix bug', None, None, None, None, False, dry_run=True)

            # Should have been called (fresh start since state is corrupted)
            mock_instance.run.assert_called_once()

    @patch('copilot_operator.cli.CopilotOperator')
    def test_empty_state_json_ignored(self, MockOp):
        """Empty state.json → treated as no state, fresh start."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = _write_config(tmp_path)
            state_dir = tmp_path / '.copilot-operator'
            state_dir.mkdir(parents=True, exist_ok=True)
            state_file = state_dir / 'state.json'
            state_file.write_text('', encoding='utf-8')

            mock_instance = MockOp.return_value
            mock_instance.run.return_value = {'status': 'complete'}

            _run(str(config_path), str(tmp_path), 'fix bug', None, None, None, None, False, dry_run=True)
            mock_instance.run.assert_called_once()


# --- Multiple error types in sequence ---

class TestMixedErrorSequence(unittest.TestCase):
    """Mixed retryable + fatal errors in sequence."""

    @patch('copilot_operator.operator.time')
    @patch('copilot_operator.operator.ensure_workspace_storage')
    def test_retryable_then_fatal(self, mock_storage, mock_time):
        """Retryable error followed by fatal should stop on fatal."""
        mock_storage.return_value = MagicMock()
        mock_storage.return_value.__truediv__ = lambda self, x: MagicMock()

        from copilot_operator.vscode_chat import VSCodeChatRetryableError

        op = _make_operator()
        call_count = 0
        def mock_iteration(iteration, goal, decision, chat_dir):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise VSCodeChatRetryableError('timeout')
            raise FileNotFoundError('binary gone')

        op._run_iteration = mock_iteration
        result = op.run('test')
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['reasonCode'], 'FATAL_ERROR')
        self.assertEqual(call_count, 2)


# --- Max iterations correctly enforced ---

class TestMaxIterationsEnforced(unittest.TestCase):

    @patch('copilot_operator.operator.ensure_workspace_storage')
    def test_stops_at_max_iterations(self, mock_storage):
        mock_storage.return_value = MagicMock()
        mock_storage.return_value.__truediv__ = lambda self, x: MagicMock()

        op = _make_operator(max_iterations=2)
        iterations_run = 0
        def mock_iteration(iteration, goal, decision, chat_dir):
            nonlocal iterations_run
            iterations_run += 1
            op.runtime['_last_decision'] = Decision(action='continue', reason='more', next_prompt='next', reason_code='WORK_REMAINS')
            return None  # continue

        op._run_iteration = mock_iteration
        result = op.run('test')
        self.assertEqual(iterations_run, 2)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['reasonCode'], 'MAX_ITERATIONS_REACHED')


# --- Resume limit ---

class TestResumeLimit(unittest.TestCase):

    def test_resume_limit_exceeded_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            state_dir = tmp_path / '.copilot-operator'
            state_dir.mkdir(parents=True, exist_ok=True)
            state_file = state_dir / 'state.json'
            state = {
                'status': 'blocked',
                'goal': 'test',
                'pendingDecision': {'action': 'continue', 'nextPrompt': 'retry'},
                'history': [{'iteration': 1}],
                'resumeCount': 11,
                'runId': 'test-run',
            }
            state_file.write_text(json.dumps(state), encoding='utf-8')

            config = MagicMock()
            config.state_file = state_file
            config.workspace = tmp_path
            config.workspace_insight = MagicMock()
            config.goal_profile = 'default'
            config.mode = 'agent'
            config.llm_config_raw = {}

            op = CopilotOperator.__new__(CopilotOperator)
            op.config = config
            op.dry_run = False
            op.live = False
            op._llm_brain = None
            op._write_runtime = MagicMock()
            op._now = lambda: '2026-01-01T00:00:00Z'
            op.runtime = {}

            with self.assertRaises(ValueError) as ctx:
                op._prepare_resume_run(None)
            self.assertIn('Resume limit', str(ctx.exception))


# --- Init with detect-hints flag combinations ---

class TestInitDetectHintsCombinations(unittest.TestCase):

    def test_init_force_plus_detect(self):
        """Init with force=True + detect_hints=True on Python project."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / 'pyproject.toml').write_text('[tool.pytest]', encoding='utf-8')
            (tmp_path / 'tests').mkdir()
            result = initialize_workspace(tmp_path, force=True, detect_hints=True)
            self.assertEqual(result['ecosystem'], 'python')
            self.assertIn('tests', result['hydratedValidation'])

    def test_init_no_detect_empty_dir(self):
        """Init with detect_hints=False → always unknown ecosystem."""
        with tempfile.TemporaryDirectory() as tmp:
            result = initialize_workspace(tmp, force=True, detect_hints=False)
            self.assertEqual(result['ecosystem'], 'unknown')


# --- Keyboard interrupt properly saved ---

class TestKeyboardInterrupt(unittest.TestCase):

    @patch('copilot_operator.operator.ensure_workspace_storage')
    def test_keyboard_interrupt_saves_state(self, mock_storage):
        mock_storage.return_value = MagicMock()
        mock_storage.return_value.__truediv__ = lambda self, x: MagicMock()

        op = _make_operator()
        def mock_iteration(iteration, goal, decision, chat_dir):
            raise KeyboardInterrupt()

        op._run_iteration = mock_iteration
        with self.assertRaises(KeyboardInterrupt):
            op.run('test')

        self.assertEqual(op.runtime['status'], 'interrupted')
        self.assertEqual(op.runtime['finalReasonCode'], 'INTERRUPTED')


if __name__ == '__main__':
    unittest.main()
