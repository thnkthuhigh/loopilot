"""Tests for new modules: reasoning, brain, goal_decomposer, scheduler, repo_ops."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from copilot_operator.brain import (
    ProjectInsights,
    RunDigest,
    analyse_runs,
    extract_run_digest,
    render_insights_for_memory,
    render_insights_for_prompt,
)
from copilot_operator.goal_decomposer import (
    build_replan_prompt,
    classify_goal,
    decompose_goal,
    should_replan,
)
from copilot_operator.reasoning import (
    Diagnosis,
    LoopSignal,
    analyse_score_trend,
    detect_loops,
    diagnose,
    format_diagnosis_for_prompt,
    recommend_strategy,
)
from copilot_operator.repo_ops import (
    GitStatus,
    _sanitise_branch_name,
    is_git_repo,
    pre_run_safety_check,
    render_git_status,
)
from copilot_operator.scheduler import (
    create_scheduler_plan,
    get_next_runnable_slot,
    is_plan_complete,
    mark_slot_complete,
    mark_slot_running,
    render_scheduler_plan,
    update_plan_status,
)

# ---------------------------------------------------------------------------
# Reasoning tests
# ---------------------------------------------------------------------------

class ScoreTrendTests(unittest.TestCase):
    def test_improving_trend(self) -> None:
        history = [{'score': 40}, {'score': 55}, {'score': 70}, {'score': 85}]
        trend = analyse_score_trend(history)
        self.assertEqual(trend.direction, 'improving')
        self.assertEqual(trend.delta, 45)
        self.assertEqual(trend.values, [40, 55, 70, 85])

    def test_declining_trend(self) -> None:
        history = [{'score': 80}, {'score': 70}, {'score': 60}]
        trend = analyse_score_trend(history)
        self.assertEqual(trend.direction, 'declining')
        self.assertLess(trend.delta, 0)

    def test_stagnant_trend(self) -> None:
        history = [{'score': 60}, {'score': 60}, {'score': 60}]
        trend = analyse_score_trend(history)
        self.assertEqual(trend.direction, 'stagnant')
        self.assertEqual(trend.delta, 0)

    def test_oscillating_trend(self) -> None:
        history = [{'score': 60}, {'score': 70}, {'score': 55}, {'score': 68}]
        trend = analyse_score_trend(history)
        self.assertEqual(trend.direction, 'oscillating')

    def test_single_score_is_unknown(self) -> None:
        history = [{'score': 50}]
        trend = analyse_score_trend(history)
        self.assertEqual(trend.direction, 'unknown')

    def test_no_scores_is_unknown(self) -> None:
        history = [{'status': 'continue'}]
        trend = analyse_score_trend(history)
        self.assertEqual(trend.direction, 'unknown')


class LoopDetectionTests(unittest.TestCase):
    def test_score_plateau_detected(self) -> None:
        history = [{'score': 70}, {'score': 70}, {'score': 70}]
        loops = detect_loops(history)
        kinds = [sig.kind for sig in loops if sig.detected]
        self.assertIn('score_plateau', kinds)

    def test_summary_repeat_detected(self) -> None:
        history = [
            {'summary': 'Fixed lint issues'},
            {'summary': 'Fixed lint issues'},
            {'summary': 'Fixed lint issues'},
        ]
        loops = detect_loops(history)
        kinds = [sig.kind for sig in loops if sig.detected]
        self.assertIn('summary_repeat', kinds)

    def test_baton_repeat_detected(self) -> None:
        history = [
            {'decisionNextPrompt': 'Fix the failing test.'},
            {'decisionNextPrompt': 'Fix the failing test.'},
        ]
        loops = detect_loops(history)
        kinds = [sig.kind for sig in loops if sig.detected]
        self.assertIn('baton_repeat', kinds)

    def test_no_loops_on_diverse_history(self) -> None:
        history = [
            {'score': 50, 'summary': 'Started work', 'decisionNextPrompt': 'Fix A'},
            {'score': 65, 'summary': 'Fixed bug', 'decisionNextPrompt': 'Fix B'},
            {'score': 80, 'summary': 'Added tests', 'decisionNextPrompt': 'Polish'},
        ]
        loops = detect_loops(history)
        detected = [sig for sig in loops if sig.detected]
        self.assertEqual(len(detected), 0)


class DiagnosisTests(unittest.TestCase):
    def test_full_diagnosis_with_stuck_loop(self) -> None:
        history = [
            {'score': 60, 'summary': 'Same thing', 'decisionCode': 'WORK_REMAINS'},
            {'score': 60, 'summary': 'Same thing', 'decisionCode': 'WORK_REMAINS'},
            {'score': 60, 'summary': 'Same thing', 'decisionCode': 'WORK_REMAINS'},
        ]
        dx = diagnose(history, 'default', 85, 3, 6)
        self.assertIn(dx.risk_level, ('high', 'critical'))
        self.assertTrue(any(sig.detected for sig in dx.loops))
        self.assertTrue(len(dx.hints) > 0)

    def test_low_risk_on_healthy_progress(self) -> None:
        history = [
            {'score': 50, 'summary': 'Started'},
            {'score': 70, 'summary': 'Made progress'},
        ]
        dx = diagnose(history, 'default', 85, 2, 6)
        self.assertEqual(dx.risk_level, 'low')

    def test_format_diagnosis_empty_for_low_risk(self) -> None:
        dx = Diagnosis(risk_level='low')
        self.assertEqual(format_diagnosis_for_prompt(dx), '')


class StrategyTests(unittest.TestCase):
    def test_narrow_scope_when_declining_near_end(self) -> None:
        history = [{'score': 70}, {'score': 60}, {'score': 50}]
        hints = recommend_strategy(history, 'default', 85, iteration=5, max_iterations=6)
        actions = [h.action for h in hints]
        self.assertIn('narrow_scope', actions)

    def test_switch_baton_on_plateau(self) -> None:
        history = [{'score': 60}, {'score': 60}, {'score': 60}]
        hints = recommend_strategy(history, 'default', 85, iteration=3, max_iterations=6)
        actions = [h.action for h in hints]
        self.assertIn('switch_baton', actions)


# ---------------------------------------------------------------------------
# Brain / Project Insights tests
# ---------------------------------------------------------------------------

class BrainTests(unittest.TestCase):
    def test_extract_run_digest(self) -> None:
        state = {
            'runId': 'run-1',
            'goal': 'Fix bug',
            'goalProfile': 'bug',
            'status': 'complete',
            'finalReasonCode': 'STOP_GATE_PASSED',
            'startedAt': '2026-04-06T10:00:00+00:00',
            'finishedAt': '2026-04-06T10:05:00+00:00',
            'history': [
                {'score': 70, 'blockers': [], 'validation_after': []},
                {'score': 90, 'blockers': [], 'validation_after': [{'name': 'tests', 'status': 'pass'}]},
            ],
            'plan': {
                'milestones': [
                    {'id': 'm1', 'status': 'done', 'tasks': [{'id': 'm1.t1', 'status': 'done'}]},
                ],
            },
        }
        digest = extract_run_digest(state)
        self.assertEqual(digest.run_id, 'run-1')
        self.assertEqual(digest.status, 'complete')
        self.assertEqual(digest.iterations, 2)
        self.assertEqual(digest.final_score, 90)
        self.assertEqual(digest.task_done_count, 1)
        self.assertGreater(digest.duration_seconds, 0)

    def test_analyse_runs_computes_insights(self) -> None:
        digests = [
            RunDigest(run_id='r1', status='complete', goal_profile='bug', iterations=2, final_score=90),
            RunDigest(run_id='r2', status='blocked', goal_profile='bug', iterations=6, final_score=50, blocker_items=['flaky test']),
            RunDigest(run_id='r3', status='complete', goal_profile='feature', iterations=3, final_score=85),
        ]
        insights = analyse_runs(digests)
        self.assertEqual(insights.total_runs, 3)
        self.assertAlmostEqual(insights.success_rate, 2 / 3, places=2)
        self.assertAlmostEqual(insights.avg_score, 75.0, places=0)
        self.assertIn('flaky test', insights.common_blockers)

    def test_render_insights_for_memory_non_empty(self) -> None:
        insights = ProjectInsights(
            total_runs=5,
            success_rate=0.8,
            avg_iterations=3.0,
            avg_score=85.0,
        )
        text = render_insights_for_memory(insights)
        self.assertIn('Total runs analysed: 5', text)
        self.assertIn('80%', text)

    def test_render_insights_empty_for_zero_runs(self) -> None:
        text = render_insights_for_memory(ProjectInsights())
        self.assertEqual(text, '')

    def test_render_insights_for_prompt(self) -> None:
        insights = ProjectInsights(
            total_runs=3,
            success_rate=0.67,
            avg_score=72.0,
            common_blockers=['flaky test'],
        )
        text = render_insights_for_prompt(insights)
        self.assertIn('3 prior runs', text)
        self.assertIn('flaky test', text)


# ---------------------------------------------------------------------------
# Goal decomposer tests
# ---------------------------------------------------------------------------

class GoalClassifyTests(unittest.TestCase):
    def test_classify_bug_goal(self) -> None:
        self.assertEqual(classify_goal('Fix the login regression'), 'bug')

    def test_classify_feature_goal(self) -> None:
        self.assertEqual(classify_goal('Implement dark mode support'), 'feature')

    def test_classify_refactor_goal(self) -> None:
        self.assertEqual(classify_goal('Refactor the database layer'), 'refactor')

    def test_classify_audit_goal(self) -> None:
        self.assertEqual(classify_goal('Audit security of the API'), 'audit')

    def test_classify_docs_goal(self) -> None:
        self.assertEqual(classify_goal('Document the API endpoints'), 'docs')

    def test_classify_generic_goal(self) -> None:
        self.assertEqual(classify_goal('Make things better'), 'default')


class GoalDecomposeTests(unittest.TestCase):
    def test_decompose_bug_has_three_milestones(self) -> None:
        plan = decompose_goal('Fix the login crash', 'bug')
        self.assertEqual(len(plan['milestones']), 3)
        self.assertEqual(plan['milestones'][0]['status'], 'in_progress')
        self.assertIn('Reproduce', plan['milestones'][0]['title'])

    def test_decompose_feature_has_tasks(self) -> None:
        plan = decompose_goal('Add export to CSV', 'feature')
        self.assertTrue(len(plan['milestones']) >= 2)
        first_milestone = plan['milestones'][0]
        self.assertTrue(len(first_milestone.get('tasks', [])) >= 1)

    def test_decompose_default_fallback(self) -> None:
        plan = decompose_goal('Do something', 'default')
        self.assertTrue(len(plan['milestones']) >= 1)
        self.assertEqual(plan['currentMilestoneId'], 'm1')


class ReplanTests(unittest.TestCase):
    def test_should_replan_on_high_risk(self) -> None:
        dx = Diagnosis(
            risk_level='high',
            loops=[LoopSignal(detected=True, kind='score_plateau', detail='stuck')],
        )
        self.assertTrue(should_replan(dx))

    def test_should_not_replan_on_low_risk(self) -> None:
        dx = Diagnosis(risk_level='low', loops=[])
        self.assertFalse(should_replan(dx))

    def test_build_replan_prompt_contains_goal(self) -> None:
        dx = Diagnosis(
            risk_level='high',
            loops=[LoopSignal(detected=True, kind='score_plateau', detail='Score stuck at 60 for 3 iterations.')],
            hints=[],
        )
        prompt = build_replan_prompt(dx, None, 'Fix the auth bug')
        self.assertIn('Fix the auth bug', prompt)
        self.assertIn('Score stuck at 60', prompt)
        self.assertIn('MUST change your approach', prompt)


# ---------------------------------------------------------------------------
# Scheduler tests
# ---------------------------------------------------------------------------

class SchedulerTests(unittest.TestCase):
    def test_create_simple_plan(self) -> None:
        plan = create_scheduler_plan('Fix the bug', roles=['coder'])
        self.assertEqual(len(plan.slots), 1)
        self.assertEqual(plan.slots[0].role, 'coder')
        self.assertEqual(plan.status, 'pending')

    def test_create_multi_role_plan(self) -> None:
        plan = create_scheduler_plan('Implement feature', roles=['coder', 'reviewer', 'fixer'])
        self.assertEqual(len(plan.slots), 3)
        roles = [s.role for s in plan.slots]
        self.assertIn('coder', roles)
        self.assertIn('reviewer', roles)

    def test_dependency_order(self) -> None:
        plan = create_scheduler_plan('Ship feature', roles=['coder', 'reviewer', 'fixer'])
        # Coder should be before reviewer, reviewer before fixer
        roles = [s.role for s in plan.slots]
        self.assertLess(roles.index('coder'), roles.index('reviewer'))
        self.assertLess(roles.index('reviewer'), roles.index('fixer'))

    def test_get_next_runnable_slot(self) -> None:
        plan = create_scheduler_plan('Ship it', roles=['coder', 'reviewer'])
        slot = get_next_runnable_slot(plan)
        self.assertIsNotNone(slot)
        self.assertEqual(slot.role, 'coder')

    def test_reviewer_blocked_until_coder_completes(self) -> None:
        plan = create_scheduler_plan('Ship it', roles=['coder', 'reviewer'])
        # Without completing coder, next should be coder (reviewer is blocked by dependency)
        slot = get_next_runnable_slot(plan)
        self.assertEqual(slot.role, 'coder')

    def test_slot_lifecycle(self) -> None:
        plan = create_scheduler_plan('Ship it', roles=['coder', 'reviewer'])
        coder_slot = next(s for s in plan.slots if s.role == 'coder')
        mark_slot_running(coder_slot, 'run-1')
        self.assertEqual(coder_slot.status, 'running')
        mark_slot_complete(coder_slot, {'status': 'complete', 'score': 90, 'reasonCode': 'STOP_GATE_PASSED'})
        self.assertEqual(coder_slot.status, 'complete')
        self.assertEqual(coder_slot.final_score, 90)
        # Now reviewer should be next
        next_slot = get_next_runnable_slot(plan)
        self.assertIsNotNone(next_slot)
        self.assertEqual(next_slot.role, 'reviewer')

    def test_plan_complete(self) -> None:
        plan = create_scheduler_plan('Ship it', roles=['coder'])
        self.assertFalse(is_plan_complete(plan))
        mark_slot_complete(plan.slots[0], {'status': 'complete'})
        self.assertTrue(is_plan_complete(plan))
        update_plan_status(plan)
        self.assertEqual(plan.status, 'complete')

    def test_render_scheduler_plan(self) -> None:
        plan = create_scheduler_plan('Fix the bug', roles=['coder', 'reviewer'])
        text = render_scheduler_plan(plan)
        self.assertIn('coder', text)
        self.assertIn('reviewer', text)
        self.assertIn('pending', text)


# ---------------------------------------------------------------------------
# Repo ops tests
# ---------------------------------------------------------------------------

class RepoOpsTests(unittest.TestCase):
    def test_non_git_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            self.assertFalse(is_git_repo(workspace))
            safety = pre_run_safety_check(workspace)
            self.assertTrue(safety['safe'])

    def test_sanitise_branch_name_valid(self) -> None:
        self.assertEqual(_sanitise_branch_name('feature/my-branch'), 'feature/my-branch')

    def test_sanitise_branch_name_invalid(self) -> None:
        with self.assertRaises(ValueError):
            _sanitise_branch_name('')
        with self.assertRaises(ValueError):
            _sanitise_branch_name('--bad-name')
        with self.assertRaises(ValueError):
            _sanitise_branch_name('branch..lock')

    def test_render_git_status_clean(self) -> None:
        status = GitStatus(branch='main', is_clean=True, has_remote=True)
        text = render_git_status(status)
        self.assertIn('main', text)
        self.assertIn('clean', text)

    def test_render_git_status_dirty(self) -> None:
        status = GitStatus(
            branch='feature/x',
            is_clean=False,
            staged_files=['a.py'],
            unstaged_files=['b.py', 'c.py'],
            untracked_files=['d.py'],
        )
        text = render_git_status(status)
        self.assertIn('Staged: 1', text)
        self.assertIn('Unstaged: 2', text)
        self.assertIn('Untracked: 1', text)


# ---------------------------------------------------------------------------
# Integration: operator uses new modules
# ---------------------------------------------------------------------------

class OperatorIntelligenceIntegrationTests(unittest.TestCase):
    def test_operator_decide_triggers_replan_on_stuck_loop(self) -> None:
        from copilot_operator.config import OperatorConfig
        from copilot_operator.operator import CopilotOperator
        from copilot_operator.prompts import parse_operator_state

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            config = OperatorConfig(
                workspace=workspace,
                target_score=85,
                max_iterations=6,
                goal_profile='default',
                validation=[],
                memory_file=workspace / '.copilot-operator' / 'memory.md',
                state_file=workspace / '.copilot-operator' / 'state.json',
                summary_file=workspace / '.copilot-operator' / 'session-summary.json',
                log_dir=workspace / '.copilot-operator' / 'logs',
            )
            op = CopilotOperator(config)
            # Simulate a stuck loop in runtime history
            op.runtime = {
                'goal': 'Fix something',
                'plan': {
                    'currentMilestoneId': 'm1',
                    'currentTaskId': 'm1.t1',
                    'milestones': [{'id': 'm1', 'title': 'Fix', 'status': 'in_progress',
                                   'tasks': [{'id': 'm1.t1', 'title': 'Do it', 'status': 'in_progress'}]}],
                },
                'history': [
                    {'score': 60, 'summary': 'Same thing', 'decisionCode': 'WORK_REMAINS', 'decisionNextPrompt': 'Fix it'},
                    {'score': 60, 'summary': 'Same thing', 'decisionCode': 'WORK_REMAINS', 'decisionNextPrompt': 'Fix it'},
                    {'score': 60, 'summary': 'Same thing', 'decisionCode': 'WORK_REMAINS', 'decisionNextPrompt': 'Fix it'},
                ],
            }
            assessment = parse_operator_state(
                'ok\n<OPERATOR_STATE>{"status":"continue","score":60,"summary":"Same thing","next_prompt":"Continue","tests":"not_run","lint":"not_run","blockers":[],"needs_continue":false,"done_reason":""}</OPERATOR_STATE>'
            )
            decision = op._decide(assessment, [], iteration=4)
            self.assertEqual(decision.action, 'continue')
            self.assertEqual(decision.reason_code, 'REPLAN_TRIGGERED')
            self.assertIn('MUST change your approach', decision.next_prompt)

    def test_operator_fresh_run_uses_smart_plan(self) -> None:
        from copilot_operator.config import OperatorConfig
        from copilot_operator.operator import CopilotOperator

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            config = OperatorConfig(
                workspace=workspace,
                target_score=85,
                max_iterations=6,
                goal_profile='bug',
                validation=[],
                memory_file=workspace / '.copilot-operator' / 'memory.md',
                state_file=workspace / '.copilot-operator' / 'state.json',
                summary_file=workspace / '.copilot-operator' / 'session-summary.json',
                log_dir=workspace / '.copilot-operator' / 'logs',
            )
            op = CopilotOperator(config)
            goal, decision, iteration = op._prepare_fresh_run('Fix the login crash')
            # Smart plan should have multiple milestones for a bug profile
            plan = op.runtime['plan']
            self.assertGreater(len(plan['milestones']), 1)
            self.assertIn('Reproduce', plan['milestones'][0]['title'])
            # Should have inferred profile
            self.assertEqual(op.runtime.get('inferredProfile'), 'bug')


if __name__ == '__main__':
    unittest.main()
