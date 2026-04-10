"""Tests for Phase 13 — Intelligence Engine upgrades:
  - Context Budget Manager (token-aware prompt compression)
  - Adaptive Strategy Engine (mid-run learning + escalation)
  - Self-Evaluation Feedback Loop (iteration analysis)
"""

from __future__ import annotations

import unittest

from copilot_operator.adaptive_strategy import (
    AdaptiveEngine,
    RunFeedback,
)
from copilot_operator.context_budget import (
    PromptSection,
    allocate_budget,
    build_budgeted_sections,
    compress_section,
)
from copilot_operator.self_eval import (
    IterationEval,
    ScoreMovement,
    evaluate_iteration,
    render_eval_for_prompt,
)

# ===================================================================
# Context Budget Tests
# ===================================================================

class TestPromptSection(unittest.TestCase):
    def test_token_estimation(self):
        s = PromptSection(name='test', content='a' * 400)
        tokens = s.estimate_tokens()
        self.assertEqual(tokens, 100)  # 400 chars / 4

    def test_empty_section(self):
        s = PromptSection(name='test', content='')
        self.assertEqual(s.estimate_tokens(), 1)  # minimum 1


class TestCompressSection(unittest.TestCase):
    def test_no_compression_needed(self):
        s = PromptSection(name='test', content='short text', priority=2)
        s.estimate_tokens()
        result = compress_section(s, target_tokens=100)
        self.assertFalse(result.was_compressed)

    def test_compression_truncates(self):
        long = 'word ' * 2000  # ~10000 chars
        s = PromptSection(name='test', content=long, priority=2)
        s.estimate_tokens()
        result = compress_section(s, target_tokens=50)
        self.assertTrue(result.was_compressed)
        self.assertLessEqual(len(result.content), len(long))

    def test_not_compressible(self):
        s = PromptSection(name='critical', content='x' * 1000, compressible=False)
        s.estimate_tokens()
        result = compress_section(s, target_tokens=10)
        self.assertFalse(result.was_compressed)  # not compressible


class TestAllocateBudget(unittest.TestCase):
    def test_everything_fits(self):
        sections = [
            PromptSection(name='a', content='x' * 100, priority=1),
            PromptSection(name='b', content='x' * 100, priority=2),
        ]
        budget = allocate_budget(sections, max_tokens=1000)
        self.assertEqual(budget.sections_dropped, 0)
        self.assertEqual(budget.sections_compressed, 0)
        self.assertEqual(budget.pressure, 'normal')

    def test_drops_p4_first(self):
        sections = [
            PromptSection(name='critical', content='x' * 4000, priority=0),
            PromptSection(name='optional', content='x' * 20000, priority=4),
        ]
        budget = allocate_budget(sections, max_tokens=2000)
        self.assertTrue(sections[1].was_dropped)
        self.assertGreater(budget.sections_dropped, 0)

    def test_compresses_p3(self):
        sections = [
            PromptSection(name='critical', content='x' * 4000, priority=0, compressible=False),
            PromptSection(name='low', content='x' * 20000, priority=3),
        ]
        allocate_budget(sections, max_tokens=3000)
        self.assertTrue(sections[1].was_compressed or sections[1].was_dropped)

    def test_score_declining_gets_more_budget(self):
        sections = [
            PromptSection(name='a', content='x' * 20000, priority=2),
        ]
        budget = allocate_budget(sections, max_tokens=2000, score_declining=True)
        self.assertEqual(budget.max_tokens, 2400)  # 20% more


class TestBuildBudgetedSections(unittest.TestCase):
    def test_combines_sections(self):
        sections = [
            PromptSection(name='a', content='Hello', priority=1),
            PromptSection(name='b', content='World', priority=2),
        ]
        text, budget = build_budgeted_sections(sections, max_tokens=5000)
        self.assertIn('Hello', text)
        self.assertIn('World', text)

    def test_empty_sections_skipped(self):
        sections = [
            PromptSection(name='a', content='', priority=1),
            PromptSection(name='b', content='Content', priority=2),
        ]
        text, budget = build_budgeted_sections(sections, max_tokens=5000)
        self.assertEqual(text, 'Content')

    def test_tight_budget_drops(self):
        sections = [
            PromptSection(name='critical', content='A' * 4000, priority=0, compressible=False),
            PromptSection(name='optional', content='B' * 40000, priority=4),
        ]
        text, budget = build_budgeted_sections(sections, max_tokens=2000)
        self.assertIn('A', text)
        self.assertNotIn('B', text)


# ===================================================================
# Adaptive Strategy Tests
# ===================================================================

class TestAdaptiveEngine(unittest.TestCase):
    def test_default_state(self):
        engine = AdaptiveEngine(goal_type='bug')
        self.assertEqual(engine.state.current_strategy, 'default')
        self.assertEqual(engine.state.escalation_level, 0)

    def test_no_directives_initially(self):
        engine = AdaptiveEngine()
        self.assertEqual(engine.get_directives(), '')

    def test_progress_resets_counter(self):
        engine = AdaptiveEngine()
        engine.record_feedback(RunFeedback(iteration=1, score_before=0, score_after=50))
        engine.record_feedback(RunFeedback(iteration=2, score_before=50, score_after=60))
        self.assertEqual(engine.state.consecutive_no_progress, 0)

    def test_no_progress_increments(self):
        engine = AdaptiveEngine()
        engine.record_feedback(RunFeedback(iteration=1, score_before=0, score_after=50))
        engine.record_feedback(RunFeedback(iteration=2, score_before=50, score_after=50))
        self.assertEqual(engine.state.consecutive_no_progress, 1)

    def test_three_no_progress_escalates(self):
        engine = AdaptiveEngine()
        engine.record_feedback(RunFeedback(iteration=1, score_before=0, score_after=50))
        engine.record_feedback(RunFeedback(iteration=2, score_before=50, score_after=50))
        engine.record_feedback(RunFeedback(iteration=3, score_before=50, score_after=50))
        engine.record_feedback(RunFeedback(iteration=4, score_before=50, score_after=50))
        self.assertNotEqual(engine.state.current_strategy, 'default')
        self.assertGreater(engine.state.escalation_level, 0)

    def test_two_loops_escalates(self):
        engine = AdaptiveEngine()
        engine.record_feedback(RunFeedback(
            iteration=1, score_before=0, score_after=50,
            loop_detected=True, loop_kind='score_plateau',
        ))
        engine.record_feedback(RunFeedback(
            iteration=2, score_before=50, score_after=50,
            loop_detected=True, loop_kind='score_plateau',
        ))
        self.assertNotEqual(engine.state.current_strategy, 'default')

    def test_score_drop_adds_guardrail(self):
        engine = AdaptiveEngine()
        engine.record_feedback(RunFeedback(iteration=1, score_before=0, score_after=70))
        engine.record_feedback(RunFeedback(iteration=2, score_before=70, score_after=50))
        self.assertGreater(len(engine.state.mid_run_guardrails), 0)

    def test_validation_failure_adds_guardrail(self):
        engine = AdaptiveEngine()
        engine.record_feedback(RunFeedback(
            iteration=1, score_before=0, score_after=50, validation_passed=False,
        ))
        engine.record_feedback(RunFeedback(
            iteration=2, score_before=50, score_after=50, validation_passed=False,
        ))
        guardrails = engine.state.mid_run_guardrails
        self.assertTrue(any('validation' in g.lower() for g in guardrails))

    def test_no_files_changed_adds_guardrail(self):
        engine = AdaptiveEngine()
        engine.record_feedback(RunFeedback(iteration=1, score_before=0, score_after=50, files_changed=1))
        engine.record_feedback(RunFeedback(iteration=2, score_before=50, score_after=50, files_changed=0))
        guardrails = engine.state.mid_run_guardrails
        self.assertTrue(any('files' in g.lower() for g in guardrails))

    def test_directives_rendered_after_escalation(self):
        engine = AdaptiveEngine(goal_type='bug')
        for i in range(1, 5):
            engine.record_feedback(RunFeedback(
                iteration=i, score_before=50, score_after=50,
            ))
        directives = engine.get_directives()
        self.assertIn('Adaptive Strategy', directives)

    def test_should_abort(self):
        engine = AdaptiveEngine()
        self.assertFalse(engine.should_abort())
        # Escalate 4 times to reach abort
        for _ in range(4):
            engine._escalate()
        self.assertTrue(engine.should_abort())

    def test_effective_strategies_tracked(self):
        engine = AdaptiveEngine()
        engine.record_feedback(RunFeedback(iteration=1, score_before=0, score_after=50))
        engine.record_feedback(RunFeedback(iteration=2, score_before=50, score_after=60))
        self.assertIn('default', engine.state.effective_strategies)

    def test_state_summary(self):
        engine = AdaptiveEngine()
        summary = engine.get_state_summary()
        self.assertIn('strategy', summary)
        self.assertIn('escalation_level', summary)


# ===================================================================
# Self-Evaluation Tests
# ===================================================================

class TestScoreMovement(unittest.TestCase):
    def test_defaults(self):
        sm = ScoreMovement()
        self.assertEqual(sm.direction, 'neutral')
        self.assertEqual(sm.delta, 0)


class TestEvaluateIteration(unittest.TestCase):
    def test_first_iteration(self):
        history = [{'score': 60, 'changedFiles': ['a.py']}]
        ev = evaluate_iteration(1, history)
        self.assertEqual(ev.score.after, 60)
        self.assertEqual(ev.files_changed, ['a.py'])

    def test_improvement(self):
        history = [
            {'score': 50, 'changedFiles': ['a.py']},
            {'score': 70, 'changedFiles': ['b.py']},
        ]
        ev = evaluate_iteration(2, history)
        self.assertEqual(ev.score.direction, 'improved')
        self.assertEqual(ev.score.delta, 20)
        self.assertTrue(ev.approach_worked)

    def test_regression(self):
        history = [
            {'score': 80, 'changedFiles': ['a.py']},
            {'score': 60, 'changedFiles': ['b.py']},
        ]
        ev = evaluate_iteration(2, history)
        self.assertEqual(ev.score.direction, 'regressed')
        self.assertEqual(ev.score.delta, -20)
        self.assertFalse(ev.approach_worked)
        self.assertTrue(ev.correction)  # should have correction

    def test_stagnant(self):
        history = [
            {'score': 50},
            {'score': 50},
        ]
        ev = evaluate_iteration(2, history)
        self.assertEqual(ev.score.direction, 'neutral')
        self.assertTrue(ev.correction)  # should suggest different approach

    def test_validation_fixed(self):
        history = [{'score': 50}, {'score': 60}]
        val_before = [{'name': 'tests', 'status': 'fail'}]
        val_after = [{'name': 'tests', 'status': 'pass'}]
        ev = evaluate_iteration(2, history, val_before, val_after)
        self.assertIn('tests', ev.validations_fixed)
        self.assertTrue(ev.approach_worked)

    def test_validation_broken(self):
        history = [{'score': 80}, {'score': 70}]
        val_before = [{'name': 'lint', 'status': 'pass'}]
        val_after = [{'name': 'lint', 'status': 'fail'}]
        ev = evaluate_iteration(2, history, val_before, val_after)
        self.assertIn('lint', ev.validations_broken)
        self.assertTrue(ev.what_hurt)

    def test_empty_history(self):
        ev = evaluate_iteration(0, [])
        self.assertEqual(ev.score.direction, 'neutral')


class TestRenderEval(unittest.TestCase):
    def test_render_improvement(self):
        history = [
            {'score': 50, 'changedFiles': ['a.py']},
            {'score': 70, 'changedFiles': ['b.py']},
        ]
        ev = evaluate_iteration(2, history)
        rendered = render_eval_for_prompt(ev)
        self.assertIn('Self-Evaluation', rendered)
        self.assertIn('📈', rendered)
        self.assertIn('+20', rendered)

    def test_render_regression(self):
        history = [{'score': 80}, {'score': 60}]
        ev = evaluate_iteration(2, history)
        rendered = render_eval_for_prompt(ev)
        self.assertIn('📉', rendered)
        self.assertIn('Correction', rendered)

    def test_render_validation_changes(self):
        history = [{'score': 50}, {'score': 60}]
        ev = evaluate_iteration(2, history,
                                [{'name': 'tests', 'status': 'fail'}],
                                [{'name': 'tests', 'status': 'pass'}])
        rendered = render_eval_for_prompt(ev)
        self.assertIn('Fixed', rendered)
        self.assertIn('tests', rendered)

    def test_no_render_for_zero_iteration(self):
        ev = IterationEval(iteration=0)
        self.assertEqual(render_eval_for_prompt(ev), '')

    def test_render_stagnant(self):
        history = [{'score': 50}, {'score': 50}]
        ev = evaluate_iteration(2, history)
        rendered = render_eval_for_prompt(ev)
        self.assertIn('unchanged', rendered)


if __name__ == '__main__':
    unittest.main()
