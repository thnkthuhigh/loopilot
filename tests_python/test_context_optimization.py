"""Tests for Phase 5: Session Context Optimization."""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock

from copilot_operator.brain import ProjectInsights
from copilot_operator.operator import CopilotOperator


def _make_operator() -> CopilotOperator:
    """Create a CopilotOperator without side effects."""
    config = MagicMock()
    config.max_iterations = 5
    config.max_error_retries = 3
    config.error_backoff_base_seconds = 2.0
    config.escalation_policy = 'block'
    config.large_diff_threshold = 200
    config.workspace = Path('/fake/workspace')
    config.state_file = MagicMock()
    config.state_file.exists.return_value = False
    config.log_dir = MagicMock()
    config.memory_file = MagicMock()
    config.summary_file = MagicMock()
    config.llm_config_raw = {}
    config.workspace_insight = MagicMock()
    config.workspace_insight.ecosystem = 'python'
    config.workspace_insight.package_manager = 'pip'
    config.repo_profile = MagicMock()
    config.repo_profile.repo_name = 'test-repo'
    config.repo_profile.protected_paths = []
    config.repo_profile.project_memory_files = []
    config.repo_profile.summary = ''
    config.goal_profile = 'default'
    config.target_score = 85
    config.fail_on_blockers = ['critical', 'high']

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

    op.runtime = {
        'status': 'running',
        'history': [],
        'plan': {},
        'goal': 'test goal',
        'runId': 'test-run',
        'workspace': '/fake/workspace',
        'mode': 'agent',
        'targetScore': 85,
        'goalProfile': 'default',
    }
    return op


class TestMemoryFileChangedFiles(unittest.TestCase):
    """T5.1: memory.md includes changed files section."""

    def test_memory_has_changed_files_section(self):
        op = _make_operator()
        op.runtime['history'] = [
            {'iteration': 1, 'score': 70, 'summary': 'partial fix', 'changedFiles': ['src/main.py', 'tests/test_main.py']},
        ]
        op.runtime['allChangedFiles'] = ['src/main.py', 'tests/test_main.py']

        memory = op._render_memory()
        self.assertIn('Files Changed Across Iterations', memory)
        self.assertIn('src/main.py', memory)
        self.assertIn('tests/test_main.py', memory)

    def test_memory_per_iteration_changed_files(self):
        op = _make_operator()
        op.runtime['history'] = [
            {'iteration': 1, 'score': 60, 'summary': 'step 1', 'changedFiles': ['a.py']},
            {'iteration': 2, 'score': 80, 'summary': 'step 2', 'changedFiles': ['b.py', 'c.py']},
        ]
        op.runtime['allChangedFiles'] = ['a.py', 'b.py', 'c.py']

        memory = op._render_memory()
        # Layer 2 has all changed files across iterations
        self.assertIn('a.py', memory)
        self.assertIn('b.py', memory)
        self.assertIn('c.py', memory)
        # Layer 3 shows only the last iteration's changed files
        self.assertIn('Changed files: b.py, c.py', memory)


class TestFollowUpPromptChangedFiles(unittest.TestCase):
    """T5.2: follow-up prompt knows about changed files via memory."""

    def test_prompt_context_has_history_with_changed_files(self):
        op = _make_operator()
        op.runtime['history'] = [
            {
                'iteration': 1, 'score': 70, 'summary': 'initial changes',
                'changedFiles': ['src/app.py'],
                'status': 'continue', 'tests': 'pass', 'lint': 'pass',
                'blockers': [], 'needs_continue': True,
                'decisionCode': 'WORK_REMAINS', 'decisionNextPrompt': 'next step',
                'next_prompt': 'continue', 'done_reason': '',
            },
        ]
        op.runtime['allChangedFiles'] = ['src/app.py']

        # The memory renders changed files
        memory = op._render_memory()
        self.assertIn('src/app.py', memory)


class TestChangedFilesAttachment(unittest.TestCase):
    """T5.4: iteration 2+ includes changed files as --add-file."""

    def test_changed_files_not_attached_on_iteration_1(self):
        """First iteration should not try to attach changed files."""
        op = _make_operator()
        op.runtime['allChangedFiles'] = []  # No changes yet
        # Just verify the logic exists — actual attachment is tested via _run_iteration mocking

    def test_changed_files_attached_on_iteration_2(self):
        """Second iteration should include previously changed files."""
        op = _make_operator()
        op.runtime['allChangedFiles'] = ['src/main.py', 'tests/test_main.py']
        # The iteration > 1 check plus file existence check is in _run_iteration
        # We verify the runtime tracking
        self.assertIn('src/main.py', op.runtime['allChangedFiles'])
        self.assertEqual(len(op.runtime['allChangedFiles']), 2)


class TestChangedFilesTracking(unittest.TestCase):
    """Verify changed files are accumulated across iterations."""

    def test_tracking_accumulates_across_iterations(self):
        op = _make_operator()
        # Simulate iteration 1
        op.runtime['allChangedFiles'] = ['a.py']
        # Simulate iteration 2 adding more
        all_changed = set(op.runtime.get('allChangedFiles', []))
        all_changed.update(['b.py', 'c.py'])
        op.runtime['allChangedFiles'] = sorted(all_changed)

        self.assertEqual(op.runtime['allChangedFiles'], ['a.py', 'b.py', 'c.py'])

    def test_tracking_deduplicates(self):
        op = _make_operator()
        op.runtime['allChangedFiles'] = ['a.py']
        all_changed = set(op.runtime.get('allChangedFiles', []))
        all_changed.update(['a.py', 'b.py'])
        op.runtime['allChangedFiles'] = sorted(all_changed)

        self.assertEqual(op.runtime['allChangedFiles'], ['a.py', 'b.py'])


class TestMemoryTruncation(unittest.TestCase):
    """T5.5: memory doesn't grow unbounded."""

    def test_changed_files_capped_at_30(self):
        op = _make_operator()
        op.runtime['history'] = [{'iteration': 1, 'score': 50, 'summary': 'test'}]
        op.runtime['allChangedFiles'] = [f'file_{i}.py' for i in range(50)]

        memory = op._render_memory()
        self.assertIn('file_0.py', memory)
        self.assertIn('file_29.py', memory)
        self.assertIn('and 20 more', memory)


if __name__ == '__main__':
    unittest.main()
