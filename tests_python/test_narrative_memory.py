"""Tests for narrative.py, mission_memory.py, and memory_promotion.py — the
Run Narrative, Mission Memory, and Memory Promotion modules."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

# ---------------------------------------------------------------------------
# Narrative tests
# ---------------------------------------------------------------------------


class TestLiveStatus(unittest.TestCase):
    def test_render_empty(self):
        from copilot_operator.narrative import LiveStatus
        ls = LiveStatus()
        text = ls.render()
        self.assertIn('[0/0]', text)

    def test_render_with_data(self):
        from copilot_operator.narrative import LiveStatus
        ls = LiveStatus(iteration=3, max_iterations=6, milestone='M1', task='T1',
                        last_action='CONTINUE', last_validation='2 pass / 0 fail',
                        score=85, next_action='Fix test')
        text = ls.render()
        self.assertIn('[3/6]', text)
        self.assertIn('M1', text)
        self.assertIn('Score: 85', text)

    def test_build_live_status_empty_runtime(self):
        from copilot_operator.narrative import build_live_status
        ls = build_live_status({}, iteration=1, max_iterations=6)
        self.assertEqual(ls.iteration, 1)
        self.assertEqual(ls.max_iterations, 6)

    def test_build_live_status_with_history(self):
        from copilot_operator.narrative import build_live_status
        runtime = {
            'history': [{
                'score': 90,
                'currentMilestoneId': 'M1',
                'currentTaskId': 'T1',
                'decisionCode': 'STOP_GATE_PASSED',
                'validation_after': [{'name': 'tests', 'status': 'pass'}],
            }],
            'pendingDecision': {'nextPrompt': 'Fix the remaining lint errors'},
        }
        ls = build_live_status(runtime, iteration=2, max_iterations=6)
        self.assertEqual(ls.score, 90)
        self.assertIn('M1', ls.milestone)
        self.assertIn('1 pass', ls.last_validation)


class TestDoneExplanation(unittest.TestCase):
    def test_build_complete(self):
        from copilot_operator.narrative import build_done_explanation
        runtime = {
            'history': [{
                'score': 95,
                'status': 'done',
                'blockers': [],
                'validation_after': [
                    {'name': 'tests', 'status': 'pass'},
                    {'name': 'lint', 'status': 'pass'},
                ],
            }],
            'allChangedFiles': ['src/main.py', 'tests/test_main.py'],
        }
        result = {'status': 'complete', 'reason': 'Done', 'reasonCode': 'STOP_GATE_PASSED', 'score': 95}
        de = build_done_explanation('Fix auth bug', runtime, result, config_target_score=85)
        self.assertEqual(de.outcome, 'complete')
        self.assertTrue(any('Score 95 >= target 85' in c for c in de.conditions_met))
        self.assertTrue(any('tests' in c for c in de.conditions_met))
        self.assertTrue(any('2 file' in e for e in de.evidence))

    def test_build_blocked(self):
        from copilot_operator.narrative import build_done_explanation
        runtime = {
            'history': [{
                'score': 40,
                'status': 'working',
                'blockers': [{'severity': 'critical', 'item': 'test crash'}],
                'validation_after': [{'name': 'tests', 'status': 'fail'}],
            }],
            'allChangedFiles': [],
        }
        result = {'status': 'blocked', 'reason': 'Max iterations', 'reasonCode': 'MAX_ITERATIONS_REACHED'}
        de = build_done_explanation('Add pagination', runtime, result, config_target_score=85)
        self.assertEqual(de.outcome, 'blocked')
        self.assertTrue(any('Score 40 < target 85' in c for c in de.conditions_unmet))
        self.assertTrue(len(de.risks) > 0)

    def test_render_output(self):
        from copilot_operator.narrative import DoneExplanation
        de = DoneExplanation(
            goal='Fix bug', outcome='complete', stop_reason='Done',
            stop_reason_code='STOP_GATE_PASSED',
            conditions_met=['Tests pass'], evidence=['3 files changed'],
            unchecked=['Security scan'], risks=['Large diff'],
        )
        text = de.render()
        self.assertIn('## Done Explanation', text)
        self.assertIn('Tests pass', text)
        self.assertIn('Security scan', text)
        self.assertIn('Large diff', text)


class TestRunNarrative(unittest.TestCase):
    def test_build_single_iteration(self):
        from copilot_operator.narrative import build_run_narrative
        runtime = {
            'history': [{
                'iteration': 1, 'score': 95, 'status': 'done',
                'summary': 'Fixed the auth bug',
                'decisionCode': 'STOP_GATE_PASSED',
                'blockers': [],
                'validation_after': [{'name': 'tests', 'status': 'pass'}],
            }],
            'allChangedFiles': ['auth.py'],
        }
        result = {'status': 'complete', 'reason': 'Done', 'reasonCode': 'STOP_GATE_PASSED', 'score': 95}
        narrative = build_run_narrative('Fix auth bug', runtime, result, config_target_score=85)
        self.assertEqual(narrative.outcome, 'complete')
        self.assertIn('single iteration', narrative.summary)
        self.assertIn('auth.py', narrative.files_changed)
        self.assertIsNotNone(narrative.done_explanation)

    def test_build_multi_iteration(self):
        from copilot_operator.narrative import build_run_narrative
        runtime = {
            'history': [
                {'iteration': 1, 'score': 40, 'status': 'working', 'summary': 'First attempt',
                 'decisionCode': 'WORK_REMAINS', 'blockers': [],
                 'validation_after': [{'name': 'tests', 'status': 'fail'}]},
                {'iteration': 2, 'score': 80, 'status': 'working', 'summary': 'Getting closer',
                 'decisionCode': 'WORK_REMAINS', 'blockers': [],
                 'validation_after': [{'name': 'tests', 'status': 'pass'}]},
                {'iteration': 3, 'score': 95, 'status': 'done', 'summary': 'Fixed everything',
                 'decisionCode': 'STOP_GATE_PASSED', 'blockers': [],
                 'validation_after': [{'name': 'tests', 'status': 'pass'}, {'name': 'lint', 'status': 'pass'}]},
            ],
            'allChangedFiles': ['src/main.py', 'tests/test_main.py'],
        }
        result = {'status': 'complete', 'reason': 'Done', 'reasonCode': 'STOP_GATE_PASSED', 'score': 95}
        narrative = build_run_narrative('Add feature', runtime, result)
        self.assertEqual(narrative.iterations, 3)
        self.assertIn('3 iterations', narrative.summary)
        self.assertIn('improved', narrative.summary)
        self.assertEqual(len(narrative.iteration_arc), 3)

    def test_render_narrative(self):
        from copilot_operator.narrative import build_run_narrative
        runtime = {
            'history': [{'iteration': 1, 'score': 90, 'status': 'done', 'summary': 'Done',
                         'decisionCode': 'STOP_GATE_PASSED', 'blockers': [],
                         'validation_after': []}],
            'allChangedFiles': [],
        }
        result = {'status': 'complete', 'reasonCode': 'STOP_GATE_PASSED', 'score': 90}
        narrative = build_run_narrative('Test goal', runtime, result)
        text = narrative.render()
        self.assertIn('# Run Narrative', text)
        self.assertIn('Test goal', text)

    def test_write_narrative_file(self):
        from copilot_operator.narrative import RunNarrative, write_narrative_file
        with tempfile.TemporaryDirectory() as tmp:
            narrative = RunNarrative(goal='Test', outcome='complete', summary='Done', iterations=1, score=90)
            path = write_narrative_file(tmp, 'run-123', narrative)
            self.assertTrue(Path(str(path)).exists())
            content = Path(str(path)).read_text(encoding='utf-8')
            self.assertIn('Test', content)

    def test_write_done_explanation_file(self):
        from copilot_operator.narrative import DoneExplanation, write_done_explanation_file
        with tempfile.TemporaryDirectory() as tmp:
            de = DoneExplanation(goal='Test', outcome='complete')
            path = write_done_explanation_file(tmp, 'run-123', de)
            self.assertTrue(Path(str(path)).exists())


class TestDescribeIteration(unittest.TestCase):
    def test_basic(self):
        from copilot_operator.narrative import _describe_iteration
        record = {
            'score': 85, 'status': 'done', 'decisionCode': 'STOP_GATE_PASSED',
            'summary': 'Fixed the bug',
            'validation_after': [{'name': 'tests', 'status': 'pass'}],
        }
        text = _describe_iteration(1, record)
        self.assertIn('#1', text)
        self.assertIn('score=85', text)
        self.assertIn('STOP_GATE_PASSED', text)

    def test_empty_record(self):
        from copilot_operator.narrative import _describe_iteration
        text = _describe_iteration(1, {})
        self.assertIn('#1', text)


# ---------------------------------------------------------------------------
# Mission Memory tests
# ---------------------------------------------------------------------------


class TestMissionMemory(unittest.TestCase):
    def test_load_empty(self):
        from copilot_operator.mission_memory import load_mission
        with tempfile.TemporaryDirectory() as tmp:
            m = load_mission(tmp)
            self.assertEqual(m.project_name, '')
            self.assertEqual(m.objectives, [])

    def test_save_and_load(self):
        from copilot_operator.mission_memory import Mission, MissionObjective, load_mission, save_mission
        with tempfile.TemporaryDirectory() as tmp:
            m = Mission(
                project_name='loopilot',
                direction='Build autonomous Copilot operator',
                current_phase='Phase 10',
                active_goals=['Implement narrative layer'],
                priorities=['Runtime safety', 'Observability'],
                objectives=[MissionObjective(
                    id='OBJ-1', description='Build narrative module',
                    status='active', success_criteria=['Tests pass'],
                )],
            )
            save_mission(tmp, m)
            loaded = load_mission(tmp)
            self.assertEqual(loaded.project_name, 'loopilot')
            self.assertEqual(loaded.direction, 'Build autonomous Copilot operator')
            self.assertEqual(len(loaded.objectives), 1)
            self.assertEqual(loaded.objectives[0].id, 'OBJ-1')
            self.assertNotEqual(loaded.updated_at, '')

    def test_render(self):
        from copilot_operator.mission_memory import Mission
        m = Mission(
            project_name='test', direction='Go north',
            active_goals=['Goal A'], priorities=['P1'],
        )
        text = m.render()
        self.assertIn('test', text)
        self.assertIn('Go north', text)
        self.assertIn('Goal A', text)

    def test_render_empty(self):
        from copilot_operator.mission_memory import Mission
        m = Mission()
        text = m.render()
        self.assertIn('Mission Context', text)

    def test_update_from_run_complete(self):
        from copilot_operator.mission_memory import Mission, MissionObjective, update_mission_from_run
        m = Mission(objectives=[
            MissionObjective(id='OBJ-1', description='Fix auth bug', status='active'),
        ])
        result = {'status': 'complete', 'reasonCode': 'STOP_GATE_PASSED'}
        update_mission_from_run(m, 'Fix auth bug in login module', result)
        self.assertEqual(m.objectives[0].status, 'done')
        self.assertIn('OBJ-1', m.completed_objectives)

    def test_update_from_run_blocked(self):
        from copilot_operator.mission_memory import Mission, update_mission_from_run
        m = Mission()
        result = {'status': 'blocked', 'reasonCode': 'MAX_ITERATIONS_REACHED'}
        update_mission_from_run(m, 'Complex refactor', result)
        self.assertTrue(any('MAX_ITERATIONS_REACHED' in ls for ls in m.lessons_learned))

    def test_lesson_trimming(self):
        from copilot_operator.mission_memory import Mission, update_mission_from_run
        m = Mission(lessons_learned=[f'lesson-{i}' for i in range(25)])
        result = {'status': 'complete', 'reasonCode': 'DONE'}
        update_mission_from_run(m, 'New goal', result, lesson='new lesson')
        self.assertLessEqual(len(m.lessons_learned), 20)
        self.assertIn('new lesson', m.lessons_learned)

    def test_render_for_prompt_empty(self):
        from copilot_operator.mission_memory import Mission, render_mission_for_prompt
        m = Mission()
        text = render_mission_for_prompt(m)
        self.assertEqual(text, '')

    def test_render_for_prompt_with_data(self):
        from copilot_operator.mission_memory import Mission, render_mission_for_prompt
        m = Mission(direction='North', active_goals=['Goal A'])
        text = render_mission_for_prompt(m)
        self.assertIn('North', text)
        self.assertIn('Goal A', text)


# ---------------------------------------------------------------------------
# Memory Promotion tests
# ---------------------------------------------------------------------------


class TestPromotionThresholds(unittest.TestCase):
    def test_defaults(self):
        from copilot_operator.memory_promotion import DEFAULT_THRESHOLDS
        self.assertEqual(DEFAULT_THRESHOLDS.trap_repeat_threshold, 2)
        self.assertEqual(DEFAULT_THRESHOLDS.max_project_lessons, 30)


class TestEvaluatePromotions(unittest.TestCase):
    def test_no_promotions_clean_run(self):
        from copilot_operator.memory_promotion import evaluate_promotions
        history = [{'score': 90, 'blockers': [], 'validation_after': [{'name': 'tests', 'status': 'pass'}]}]
        result = {'status': 'complete', 'reasonCode': 'STOP_GATE_PASSED'}
        pr = evaluate_promotions(history, result, [], [])
        self.assertEqual(len(pr.promoted), 0)

    def test_trap_promotion(self):
        from copilot_operator.memory_promotion import evaluate_promotions
        history = [{'blockers': [{'item': 'timeout_in_ci', 'severity': 'high'}], 'validation_after': []}]
        result = {'status': 'blocked', 'reasonCode': 'MAX_ITERATIONS_REACHED'}
        existing_rules = [{'trigger': 'timeout_in_ci', 'hit_count': 2}]
        pr = evaluate_promotions(history, result, existing_rules, [])
        trap_promotions = [p for p in pr.promoted if p.category == 'trap']
        self.assertTrue(len(trap_promotions) > 0)

    def test_validation_promotion(self):
        from copilot_operator.memory_promotion import evaluate_promotions
        history = [{'blockers': [], 'validation_after': [{'name': 'lint', 'status': 'fail'}]}]
        result = {'status': 'blocked', 'reasonCode': 'X'}
        existing_rules = [{'trigger': 'lint', 'hit_count': 2}]
        pr = evaluate_promotions(history, result, existing_rules, [])
        val_promos = [p for p in pr.promoted if p.category == 'validation_fact']
        self.assertTrue(len(val_promos) > 0)

    def test_strategy_cross_repo_promotion(self):
        from copilot_operator.memory_promotion import evaluate_promotions
        history = [{'blockers': [], 'validation_after': []}]
        result = {'status': 'complete', 'reasonCode': 'DONE'}
        existing_rules = [{
            'trigger': 'scope_check', 'guardrail': 'Always check scope first',
            'hit_count': 5, 'success_after_hit': 4, 'confidence': 0.9,
        }]
        pr = evaluate_promotions(history, result, existing_rules, [])
        cross_repo = [p for p in pr.promoted if p.target_layer == 'cross_repo']
        self.assertTrue(len(cross_repo) > 0)

    def test_lesson_promotion_to_mission(self):
        from copilot_operator.memory_promotion import evaluate_promotions
        history = [{'blockers': [], 'validation_after': []}]
        result = {'status': 'blocked', 'reasonCode': 'MAX_ITERATIONS_REACHED'}
        existing_lessons = ['MAX_ITERATIONS_REACHED in task X', 'MAX_ITERATIONS_REACHED in task Y']
        pr = evaluate_promotions(history, result, [], existing_lessons)
        mission_promos = [p for p in pr.promoted if p.target_layer == 'mission']
        self.assertTrue(len(mission_promos) > 0)

    def test_demotion_trimming(self):
        from copilot_operator.memory_promotion import PromotionThresholds, evaluate_promotions
        history = [{'blockers': [], 'validation_after': []}]
        result = {'status': 'complete'}
        th = PromotionThresholds(max_project_lessons=5)
        existing_lessons = [f'Lesson {i}' for i in range(10)]
        pr = evaluate_promotions(history, result, [], existing_lessons, thresholds=th)
        self.assertEqual(len(pr.demoted), 5)


class TestApplyPromotions(unittest.TestCase):
    def test_apply_to_project(self):
        from copilot_operator.memory_promotion import PromotionCandidate, PromotionResult, apply_promotions
        pr = PromotionResult(promoted=[
            PromotionCandidate(fact='New trap', source_layer='task', target_layer='project', category='trap'),
        ])
        brain = []
        mission = []
        shared = []
        counts = apply_promotions(pr, brain, mission, shared)
        self.assertEqual(counts['promoted_to_project'], 1)
        self.assertEqual(len(brain), 1)

    def test_apply_to_mission(self):
        from copilot_operator.memory_promotion import PromotionCandidate, PromotionResult, apply_promotions
        pr = PromotionResult(promoted=[
            PromotionCandidate(fact='Recurring blocker', source_layer='project', target_layer='mission', category='lesson'),
        ])
        brain = []
        mission = []
        shared = []
        counts = apply_promotions(pr, brain, mission, shared)
        self.assertEqual(counts['promoted_to_mission'], 1)
        self.assertIn('Recurring blocker', mission)

    def test_apply_deduplication(self):
        from copilot_operator.memory_promotion import PromotionCandidate, PromotionResult, apply_promotions
        pr = PromotionResult(promoted=[
            PromotionCandidate(fact='Existing', source_layer='task', target_layer='project', category='trap'),
        ])
        brain = [{'detail': 'Existing', 'category': 'trap'}]
        counts = apply_promotions(pr, brain, [], [])
        self.assertEqual(counts['promoted_to_project'], 0)
        self.assertEqual(len(brain), 1)  # No duplicate

    def test_apply_demotion(self):
        from copilot_operator.memory_promotion import PromotionResult, apply_promotions
        pr = PromotionResult(demoted=['old lesson'])
        brain = [{'detail': 'old lesson', 'category': 'trap'}, {'detail': 'keep', 'category': 'x'}]
        counts = apply_promotions(pr, brain, [], [])
        self.assertEqual(counts['demoted'], 1)
        self.assertEqual(len(brain), 1)


class TestRunPromotionCycle(unittest.TestCase):
    def test_full_cycle(self):
        from copilot_operator.memory_promotion import run_promotion_cycle
        history = [{'blockers': [{'item': 'timeout', 'severity': 'high'}], 'validation_after': []}]
        result = {'status': 'blocked', 'reasonCode': 'X'}
        rules = [{'trigger': 'timeout', 'hit_count': 3, 'success_after_hit': 0, 'confidence': 0.5}]
        brain = []
        mission_lessons = []
        shared = []
        counts = run_promotion_cycle(history, result, rules, brain, mission_lessons, shared)
        self.assertIsInstance(counts, dict)
        self.assertIn('promoted_to_project', counts)


if __name__ == '__main__':
    unittest.main()
