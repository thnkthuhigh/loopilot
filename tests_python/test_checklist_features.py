"""Tests for checklist completion features: SLA config, history compaction,
acceptance evaluation, changelog, scheduler enhancements, blocked alert,
dashboard extras, quality rubric, and done guards.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# 1. SLA config fields
# ---------------------------------------------------------------------------

class TestSLAConfig(unittest.TestCase):
    def test_sla_fields_exist(self):
        from copilot_operator.config import OperatorConfig
        config = OperatorConfig(workspace=Path('/tmp/test'))
        self.assertEqual(config.sla_max_blocked_seconds, 0)
        self.assertEqual(config.sla_max_cost_per_hour_usd, 0.0)

    def test_output_expectation_fields_exist(self):
        from copilot_operator.config import OperatorConfig
        config = OperatorConfig(workspace=Path('/tmp/test'))
        self.assertFalse(config.expect_tests_added)
        self.assertFalse(config.expect_docs_updated)
        self.assertEqual(config.expect_max_files_changed, 0)


# ---------------------------------------------------------------------------
# 2. History compaction
# ---------------------------------------------------------------------------

class TestHistoryCompaction(unittest.TestCase):
    def _make_operator(self):
        from copilot_operator.operator import CopilotOperator
        config = MagicMock()
        config.workspace = Path('/tmp/test')
        config.memory_file = Path('/tmp/test/.copilot-operator/memory.md')
        config.state_file = Path('/tmp/test/.copilot-operator/state.json')
        config.summary_file = Path('/tmp/test/.copilot-operator/session-summary.json')
        config.log_dir = Path('/tmp/test/.copilot-operator/logs')
        config.max_iterations = 6
        config.max_error_retries = 3
        config.error_backoff_base_seconds = 2.0
        config.goal_profile = 'default'
        config.mode = 'agent'
        config.repo_profile = MagicMock()
        config.repo_profile.project_memory_files = []
        config.workspace_insight = MagicMock()
        config.llm_config_raw = {}
        op = CopilotOperator.__new__(CopilotOperator)
        op.config = config
        op.runtime = {'history': []}
        op.dry_run = True
        op.live = False
        op._llm_brain = None
        return op

    def test_compact_keeps_recent(self):
        op = self._make_operator()
        for i in range(8):
            op.runtime['history'].append({
                'iteration': i + 1,
                'score': 70 + i,
                'status': 'continue',
                'decisionCode': 'WORK_REMAINS',
                'summary': f'Did work iteration {i + 1}',
                'tests': 'pass',
                'lint': 'pass',
                'blockers': [],
                'changedFiles': [f'file{i}.py'],
                'validation_after': [{'name': 'tests', 'status': 'pass'}],
            })
        op._compact_history(keep_full=5)
        history = op.runtime['history']
        self.assertEqual(len(history), 8)
        # First 3 should be compacted (only essential keys)
        for i in range(3):
            self.assertNotIn('changedFiles', history[i])
            self.assertNotIn('validation_after', history[i])
            self.assertIn('iteration', history[i])
            self.assertIn('score', history[i])
        # Last 5 should be intact
        for i in range(3, 8):
            self.assertIn('changedFiles', history[i])
            self.assertIn('validation_after', history[i])

    def test_compact_noop_small_history(self):
        op = self._make_operator()
        op.runtime['history'] = [{'iteration': 1, 'score': 80, 'changedFiles': ['f.py']}]
        op._compact_history(keep_full=5)
        self.assertIn('changedFiles', op.runtime['history'][0])


# ---------------------------------------------------------------------------
# 3. Smart milestone evaluation + gate picking
# ---------------------------------------------------------------------------

class TestAcceptanceEvaluation(unittest.TestCase):
    def test_all_acceptance_met(self):
        from copilot_operator.planner import evaluate_acceptance
        met, unmet = evaluate_acceptance(
            ['Tests pass', 'Lint clean'],
            validation_results=[
                {'name': 'tests', 'status': 'pass'},
                {'name': 'lint', 'status': 'pass'},
            ],
        )
        self.assertEqual(len(met), 2)
        self.assertEqual(len(unmet), 0)

    def test_acceptance_unmet(self):
        from copilot_operator.planner import evaluate_acceptance
        met, unmet = evaluate_acceptance(
            ['Tests pass', 'Lint clean'],
            validation_results=[
                {'name': 'tests', 'status': 'pass'},
                {'name': 'lint', 'status': 'fail'},
            ],
        )
        self.assertEqual(len(met), 1)
        self.assertEqual(len(unmet), 1)

    def test_score_based_acceptance(self):
        from copilot_operator.planner import evaluate_acceptance
        met, unmet = evaluate_acceptance(
            ['Score meets target'],
            score=90,
            target_score=85,
        )
        self.assertEqual(len(met), 1)
        self.assertEqual(len(unmet), 0)

    def test_score_below_target(self):
        from copilot_operator.planner import evaluate_acceptance
        met, unmet = evaluate_acceptance(
            ['Score meets target'],
            score=70,
            target_score=85,
        )
        self.assertEqual(len(met), 0)
        self.assertEqual(len(unmet), 1)

    def test_empty_acceptance(self):
        from copilot_operator.planner import evaluate_acceptance
        met, unmet = evaluate_acceptance([])
        self.assertEqual(met, [])
        self.assertEqual(unmet, [])


class TestPickMilestoneByGate(unittest.TestCase):
    def test_skips_met_milestones(self):
        from copilot_operator.planner import pick_milestone_by_gate
        milestones = [
            {'id': 'm1', 'status': 'in_progress', 'acceptance': ['Tests pass']},
            {'id': 'm2', 'status': 'pending', 'acceptance': []},
        ]
        result = pick_milestone_by_gate(
            milestones,
            validation_results=[{'name': 'tests', 'status': 'pass'}],
            score=90,
        )
        self.assertEqual(result, 'm2')
        self.assertEqual(milestones[0]['status'], 'done')

    def test_stays_on_unmet_milestone(self):
        from copilot_operator.planner import pick_milestone_by_gate
        milestones = [
            {'id': 'm1', 'status': 'in_progress', 'acceptance': ['Tests pass']},
            {'id': 'm2', 'status': 'pending', 'acceptance': []},
        ]
        result = pick_milestone_by_gate(
            milestones,
            validation_results=[{'name': 'tests', 'status': 'fail'}],
        )
        self.assertEqual(result, 'm1')


# ---------------------------------------------------------------------------
# 4. Changelog generation
# ---------------------------------------------------------------------------

class TestChangelogGeneration(unittest.TestCase):
    def test_generate_changelog_no_git(self):
        from copilot_operator.repo_ops import generate_changelog
        with tempfile.TemporaryDirectory() as d:
            result = generate_changelog(Path(d))
            self.assertEqual(result, '')

    def test_generate_changelog_with_git(self):
        import subprocess

        from copilot_operator.repo_ops import generate_changelog
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)
            subprocess.run(['git', 'init'], cwd=str(ws), capture_output=True, check=True)
            subprocess.run(['git', 'config', 'user.email', 'test@test.com'], cwd=str(ws), capture_output=True)
            subprocess.run(['git', 'config', 'user.name', 'Test'], cwd=str(ws), capture_output=True)
            (ws / 'a.py').write_text('a = 1\n')
            subprocess.run(['git', 'add', '.'], cwd=str(ws), capture_output=True)
            subprocess.run(['git', 'commit', '-m', 'feat: initial'], cwd=str(ws), capture_output=True)
            (ws / 'b.py').write_text('b = 2\n')
            subprocess.run(['git', 'add', '.'], cwd=str(ws), capture_output=True)
            subprocess.run(['git', 'commit', '-m', 'fix: thing'], cwd=str(ws), capture_output=True)
            result = generate_changelog(ws)
            self.assertIn('fix: thing', result)


# ---------------------------------------------------------------------------
# 5. Scheduler: file locks, baton merge, shared gate
# ---------------------------------------------------------------------------

class TestFileConflictTracker(unittest.TestCase):
    def test_claim_files(self):
        from copilot_operator.scheduler import FileConflictTracker
        tracker = FileConflictTracker()
        conflicts = tracker.claim_files('slot-1', 'coder', ['a.py', 'b.py'])
        self.assertEqual(conflicts, [])

    def test_conflict_detection(self):
        from copilot_operator.scheduler import FileConflictTracker
        tracker = FileConflictTracker()
        tracker.claim_files('slot-1', 'coder', ['a.py', 'b.py'])
        conflicts = tracker.claim_files('slot-2', 'reviewer', ['b.py', 'c.py'])
        self.assertEqual(len(conflicts), 1)
        self.assertIn('b.py', conflicts[0])

    def test_release_files(self):
        from copilot_operator.scheduler import FileConflictTracker
        tracker = FileConflictTracker()
        tracker.claim_files('slot-1', 'coder', ['a.py'])
        tracker.release_files('slot-1')
        conflicts = tracker.claim_files('slot-2', 'reviewer', ['a.py'])
        self.assertEqual(conflicts, [])

    def test_has_conflict(self):
        from copilot_operator.scheduler import FileConflictTracker
        tracker = FileConflictTracker()
        tracker.claim_files('slot-1', 'coder', ['a.py'])
        self.assertTrue(tracker.has_conflict('slot-2', ['a.py']))
        self.assertFalse(tracker.has_conflict('slot-1', ['a.py']))
        self.assertFalse(tracker.has_conflict('slot-2', ['c.py']))


class TestBatonMerge(unittest.TestCase):
    def test_merge_completed_slots(self):
        from copilot_operator.scheduler import SessionSlot, merge_batons
        slots = [
            SessionSlot(slot_id='s1', role='coder', status='complete',
                        final_score=90, result={'reason': 'Implemented feature X'}),
            SessionSlot(slot_id='s2', role='reviewer', status='complete',
                        final_score=85, result={'reason': 'Found 2 issues'}),
            SessionSlot(slot_id='s3', role='tester', status='pending'),
        ]
        merged = merge_batons(slots)
        self.assertIn('Coder Session', merged)
        self.assertIn('Reviewer Session', merged)
        self.assertNotIn('Tester', merged)
        self.assertIn('90', merged)

    def test_merge_empty_slots(self):
        from copilot_operator.scheduler import merge_batons
        result = merge_batons([])
        self.assertIn('Combined Session Context', result)


class TestSharedStopGate(unittest.TestCase):
    def test_all_passed(self):
        from copilot_operator.scheduler import SchedulerPlan, SessionSlot, evaluate_shared_gate
        plan = SchedulerPlan(
            plan_id='test',
            slots=[
                SessionSlot(slot_id='s1', role='coder', status='complete', final_score=90),
                SessionSlot(slot_id='s2', role='tester', status='complete', final_score=88),
            ],
        )
        result = evaluate_shared_gate(plan, target_score=85)
        self.assertTrue(result['passed'])

    def test_one_blocked(self):
        from copilot_operator.scheduler import SchedulerPlan, SessionSlot, evaluate_shared_gate
        plan = SchedulerPlan(
            plan_id='test',
            slots=[
                SessionSlot(slot_id='s1', role='coder', status='complete', final_score=90),
                SessionSlot(slot_id='s2', role='tester', status='blocked'),
            ],
        )
        result = evaluate_shared_gate(plan, target_score=85)
        self.assertFalse(result['passed'])

    def test_still_running(self):
        from copilot_operator.scheduler import SchedulerPlan, SessionSlot, evaluate_shared_gate
        plan = SchedulerPlan(
            plan_id='test',
            slots=[
                SessionSlot(slot_id='s1', role='coder', status='complete', final_score=90),
                SessionSlot(slot_id='s2', role='tester', status='running'),
            ],
        )
        result = evaluate_shared_gate(plan, target_score=85)
        self.assertFalse(result['passed'])


class TestSchedulerPersistence(unittest.TestCase):
    def test_save_and_load(self):
        from copilot_operator.scheduler import (
            SchedulerPlan,
            SessionSlot,
            load_scheduler_plan,
            save_scheduler_plan,
        )
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'plan.json'
            plan = SchedulerPlan(
                plan_id='test-plan',
                base_goal='Fix the bug',
                slots=[SessionSlot(slot_id='s1', role='coder', status='complete', final_score=95)],
                execution_order=['s1'],
                status='complete',
            )
            save_scheduler_plan(plan, path)
            loaded = load_scheduler_plan(path)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.plan_id, 'test-plan')
            self.assertEqual(loaded.slots[0].final_score, 95)

    def test_load_missing_file(self):
        from copilot_operator.scheduler import load_scheduler_plan
        result = load_scheduler_plan(Path('/nonexistent/plan.json'))
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# 6. Blocked alert
# ---------------------------------------------------------------------------

class TestBlockedAlert(unittest.TestCase):
    @patch('copilot_operator.github_integration._api_request')
    def test_post_blocked_alert(self, mock_api):
        from copilot_operator.github_integration import GitHubConfig, post_blocked_alert
        mock_api.return_value = {'id': 1}
        config = GitHubConfig(owner='test', repo='test', token='fake')
        post_blocked_alert(config, 42, 'Tests failing', run_id='abc123', elapsed_seconds=300)
        # Should have made at least 2 calls: comment + label operations
        self.assertGreaterEqual(mock_api.call_count, 2)


# ---------------------------------------------------------------------------
# 7. Session cleanup
# ---------------------------------------------------------------------------

class TestSessionCleanup(unittest.TestCase):
    def test_cleanup_old_sessions(self):
        import time

        from copilot_operator.bootstrap import cleanup_old_sessions
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)
            op_dir = ws / '.copilot-operator'
            op_dir.mkdir()
            # Create old file
            old_file = op_dir / 'current-prompt.md'
            old_file.write_text('old prompt')
            # Make file appear old
            import os
            old_time = time.time() - (8 * 86400)
            os.utime(str(old_file), (old_time, old_time))
            result = cleanup_old_sessions(ws, max_age_days=7)
            self.assertEqual(len(result['removed']), 1)

    def test_cleanup_dry_run(self):
        import os
        import time

        from copilot_operator.bootstrap import cleanup_old_sessions
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)
            op_dir = ws / '.copilot-operator'
            op_dir.mkdir()
            old_file = op_dir / 'current-prompt.md'
            old_file.write_text('old prompt')
            old_time = time.time() - (8 * 86400)
            os.utime(str(old_file), (old_time, old_time))
            result = cleanup_old_sessions(ws, max_age_days=7, dry_run=True)
            self.assertEqual(len(result['skipped']), 1)
            self.assertTrue(old_file.exists())


# ---------------------------------------------------------------------------
# 8. Dashboard extras
# ---------------------------------------------------------------------------

class TestDashboardExtras(unittest.TestCase):
    def test_render_blocker_trend(self):
        from copilot_operator.dashboard import render_blocker_trend
        with tempfile.TemporaryDirectory() as d:
            state_file = Path(d) / 'state.json'
            state_file.write_text(json.dumps({
                'history': [
                    {'iteration': 1, 'blockers': [{'severity': 'high', 'item': 'err'}]},
                    {'iteration': 2, 'blockers': []},
                    {'iteration': 3, 'blockers': [
                        {'severity': 'critical', 'item': 'crash'},
                        {'severity': 'high', 'item': 'fail'},
                    ]},
                ]
            }))
            result = render_blocker_trend(state_file)
            self.assertIn('Blocker Trend', result)
            self.assertIn('iter 1', result)

    def test_render_blocker_trend_no_file(self):
        from copilot_operator.dashboard import render_blocker_trend
        result = render_blocker_trend(Path('/nonexistent/state.json'))
        self.assertIn('No state file', result)

    def test_render_repo_health(self):
        from copilot_operator.dashboard import render_repo_health
        with tempfile.TemporaryDirectory() as d:
            log_dir = Path(d)
            run_dir = log_dir / 'run-1'
            run_dir.mkdir()
            # Create a decision file
            decision = {'score': 92, 'action': 'stop'}
            (run_dir / 'iteration-01-decision.json').write_text(json.dumps(decision))
            result = render_repo_health(log_dir)
            self.assertIn('Repo Health', result)
            self.assertIn('92', result)

    def test_render_repo_health_no_dir(self):
        from copilot_operator.dashboard import render_repo_health
        result = render_repo_health(Path('/nonexistent/logs'))
        self.assertIn('No log directory', result)

    def test_cost_breakdown_display(self):
        from copilot_operator.dashboard import DashboardSnapshot, render_dashboard
        snap = DashboardSnapshot(
            status='running',
            goal='Test',
            iteration=3,
            score=85,
            llm_cost={'calls': 3, 'totalTokens': 5000, 'estimatedUsd': 0.05,
                       'promptTokens': 3000, 'completionTokens': 2000},
        )
        output = render_dashboard(snap)
        self.assertIn('Prompt: 3,000', output)
        self.assertIn('Completion: 2,000', output)
        self.assertIn('Cost/iteration', output)


# ---------------------------------------------------------------------------
# 9. Benchmark: quality rubric + done guards
# ---------------------------------------------------------------------------

class TestQualityRubric(unittest.TestCase):
    def test_get_rubric_known_profile(self):
        from copilot_operator.benchmark import get_rubric
        rubric = get_rubric('bug')
        self.assertTrue(len(rubric) > 0)
        self.assertTrue(any('test' in r.lower() for r in rubric))

    def test_get_rubric_unknown_defaults_to_feature(self):
        from copilot_operator.benchmark import get_rubric
        rubric = get_rubric('nonexistent_profile')
        self.assertEqual(rubric, get_rubric('feature'))

    def test_all_profiles_have_rubric(self):
        from copilot_operator.benchmark import QUALITY_RUBRIC
        for profile in ('bug', 'feature', 'refactor', 'audit', 'docs'):
            self.assertIn(profile, QUALITY_RUBRIC)
            self.assertTrue(len(QUALITY_RUBRIC[profile]) > 0)


class TestDoneGuards(unittest.TestCase):
    def test_tests_added_passes(self):
        from copilot_operator.benchmark import evaluate_done_guards
        result = {'allChangedFiles': ['src/main.py', 'tests/test_main.py']}
        violations = evaluate_done_guards(result)
        test_guard_violations = [v for v in violations if v['guard'] == 'tests_added']
        self.assertEqual(len(test_guard_violations), 0)

    def test_tests_added_fails(self):
        from copilot_operator.benchmark import evaluate_done_guards
        result = {'allChangedFiles': ['src/main.py', 'src/utils.py']}
        violations = evaluate_done_guards(result)
        test_guard_violations = [v for v in violations if v['guard'] == 'tests_added']
        self.assertEqual(len(test_guard_violations), 1)

    def test_max_files_guard(self):
        from copilot_operator.benchmark import evaluate_done_guards
        result = {'allChangedFiles': [f'file{i}.py' for i in range(20)]}
        violations = evaluate_done_guards(result, max_files=10)
        max_violations = [v for v in violations if v['guard'] == 'max_files']
        self.assertEqual(len(max_violations), 1)

    def test_max_files_zero_means_unlimited(self):
        from copilot_operator.benchmark import evaluate_done_guards
        result = {'allChangedFiles': [f'file{i}.py' for i in range(100)]}
        violations = evaluate_done_guards(result, max_files=0)
        max_violations = [v for v in violations if v['guard'] == 'max_files']
        self.assertEqual(len(max_violations), 0)

    def test_lint_clean_passes(self):
        from copilot_operator.benchmark import evaluate_done_guards
        result = {'history': [{'lint': 'pass'}], 'allChangedFiles': ['tests/test_x.py']}
        violations = evaluate_done_guards(result)
        lint_violations = [v for v in violations if v['guard'] == 'lint_clean']
        self.assertEqual(len(lint_violations), 0)

    def test_lint_fail(self):
        from copilot_operator.benchmark import evaluate_done_guards
        result = {'history': [{'lint': 'fail'}], 'allChangedFiles': ['tests/test_x.py']}
        violations = evaluate_done_guards(result)
        lint_violations = [v for v in violations if v['guard'] == 'lint_clean']
        self.assertEqual(len(lint_violations), 1)


if __name__ == '__main__':
    unittest.main()
