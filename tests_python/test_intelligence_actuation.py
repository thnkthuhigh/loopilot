"""Tests for Phase 14 — Intelligence Actuation:
  - Hint Actuator (execute reasoning recommendations)
  - Benchmark Learner (extract rules from failures)
  - Intelligence Telemetry (track injection effectiveness)
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from copilot_operator.benchmark_learner import (
    BenchmarkLesson,
    analyse_benchmark_for_rules,
    render_benchmark_lessons,
)
from copilot_operator.hint_actuator import (
    ActuationResult,
    actuate_hints,
)
from copilot_operator.intelligence_telemetry import (
    InjectionRecord,
    IntelligenceReport,
    TelemetryAggregator,
)
from copilot_operator.reasoning import StrategyHint


# ===================================================================
# Hint Actuator Tests
# ===================================================================

def _make_config(**overrides):
    defaults = {
        'goal_profile': 'default',
        'max_iterations': 6,
        'expect_max_files_changed': 20,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestActuateHints(unittest.TestCase):
    def test_no_action(self):
        hints = [StrategyHint(action='none')]
        config = _make_config()
        runtime: dict = {}
        result = actuate_hints(hints, config, runtime, 3, 6)
        self.assertEqual(result.actions_taken, [])
        self.assertFalse(result.aborted)

    def test_escalate_profile(self):
        hints = [StrategyHint(action='escalate_profile', suggested_profile='bug')]
        config = _make_config()
        runtime: dict = {}
        result = actuate_hints(hints, config, runtime, 3, 6)
        self.assertEqual(config.goal_profile, 'bug')
        self.assertEqual(runtime['goalProfile'], 'bug')
        self.assertIn('escalate_profile', result.actions_taken[0])
        self.assertIn('default', result.config_changes['goal_profile']['old'])

    def test_escalate_profile_invalid(self):
        hints = [StrategyHint(action='escalate_profile', suggested_profile='nonexistent')]
        config = _make_config()
        runtime: dict = {}
        result = actuate_hints(hints, config, runtime, 3, 6)
        self.assertEqual(config.goal_profile, 'default')  # unchanged
        self.assertEqual(result.actions_taken, [])

    def test_escalate_profile_same(self):
        hints = [StrategyHint(action='escalate_profile', suggested_profile='default')]
        config = _make_config()
        runtime: dict = {}
        result = actuate_hints(hints, config, runtime, 3, 6)
        self.assertEqual(result.actions_taken, [])  # no change

    def test_narrow_scope(self):
        hints = [StrategyHint(action='narrow_scope')]
        config = _make_config(expect_max_files_changed=20)
        runtime: dict = {}
        result = actuate_hints(hints, config, runtime, 3, 6)
        self.assertEqual(config.expect_max_files_changed, 10)
        self.assertTrue(runtime.get('_scope_narrowed'))
        self.assertIn('narrow_scope', result.actions_taken[0])

    def test_narrow_scope_floor(self):
        hints = [StrategyHint(action='narrow_scope')]
        config = _make_config(expect_max_files_changed=4)
        runtime: dict = {}
        result = actuate_hints(hints, config, runtime, 3, 6)
        self.assertEqual(config.expect_max_files_changed, 3)  # floor at 3

    def test_increase_iterations(self):
        hints = [StrategyHint(action='increase_iterations')]
        config = _make_config(max_iterations=6)
        runtime: dict = {}
        result = actuate_hints(hints, config, runtime, iteration=5, max_iterations=6)
        self.assertEqual(config.max_iterations, 8)
        self.assertIn('increase_iterations', result.actions_taken[0])

    def test_increase_iterations_cap(self):
        hints = [StrategyHint(action='increase_iterations')]
        config = _make_config(max_iterations=12)
        runtime: dict = {}
        result = actuate_hints(hints, config, runtime, iteration=11, max_iterations=12)
        self.assertEqual(config.max_iterations, 12)  # already at cap
        self.assertEqual(result.actions_taken, [])

    def test_increase_iterations_not_near_limit(self):
        hints = [StrategyHint(action='increase_iterations')]
        config = _make_config(max_iterations=6)
        runtime: dict = {}
        result = actuate_hints(hints, config, runtime, iteration=2, max_iterations=6)
        self.assertEqual(config.max_iterations, 6)  # not near limit
        self.assertEqual(result.actions_taken, [])

    def test_switch_baton(self):
        hints = [StrategyHint(action='switch_baton', suggested_prompt='Try bisecting')]
        config = _make_config()
        runtime: dict = {}
        result = actuate_hints(hints, config, runtime, 3, 6)
        self.assertEqual(runtime['_baton_override'], 'Try bisecting')
        self.assertIn('switch_baton', result.actions_taken[0])

    def test_abort(self):
        hints = [StrategyHint(action='abort', reason='Too many failures')]
        config = _make_config()
        runtime: dict = {}
        result = actuate_hints(hints, config, runtime, 3, 6)
        self.assertTrue(result.aborted)
        self.assertTrue(runtime['_abort_requested'])
        self.assertIn('abort', result.actions_taken[0])

    def test_multiple_hints(self):
        hints = [
            StrategyHint(action='escalate_profile', suggested_profile='audit'),
            StrategyHint(action='narrow_scope'),
        ]
        config = _make_config()
        runtime: dict = {}
        result = actuate_hints(hints, config, runtime, 3, 6)
        self.assertEqual(config.goal_profile, 'audit')
        self.assertEqual(len(result.actions_taken), 2)

    def test_profile_history_tracked(self):
        hints = [StrategyHint(action='escalate_profile', suggested_profile='bug')]
        config = _make_config()
        runtime: dict = {}
        actuate_hints(hints, config, runtime, 3, 6)
        self.assertIn('default', runtime['_profile_history'])

    def test_unknown_action(self):
        hints = [StrategyHint(action='fly_to_moon')]
        config = _make_config()
        runtime: dict = {}
        result = actuate_hints(hints, config, runtime, 3, 6)
        self.assertEqual(result.actions_taken, [])


# ===================================================================
# Benchmark Learner Tests
# ===================================================================

class TestAnalyseBenchmarkForRules(unittest.TestCase):
    def test_no_failures(self):
        cases = [{'case_id': 'c1', 'passed': True, 'score': 1.0}]
        rules = analyse_benchmark_for_rules(cases)
        self.assertEqual(rules, [])

    def test_missing_keywords(self):
        cases = [{'case_id': 'c1', 'passed': False, 'score': 0.5,
                  'missing_keywords': ['error_handling', 'validation']}]
        rules = analyse_benchmark_for_rules(cases)
        self.assertEqual(len(rules), 1)
        self.assertIn('error_handling', rules[0]['trigger'])
        self.assertIn('error_handling', rules[0]['guardrail'])
        self.assertEqual(rules[0]['category'], 'scope_creep')

    def test_low_score(self):
        cases = [{'case_id': 'c2', 'passed': False, 'score': 0.3,
                  'missing_keywords': []}]
        rules = analyse_benchmark_for_rules(cases)
        self.assertEqual(len(rules), 1)
        self.assertIn('low_score', rules[0]['trigger'])
        self.assertEqual(rules[0]['category'], 'test_regression')

    def test_error_case(self):
        cases = [{'case_id': 'c3', 'passed': False, 'score': 0.0,
                  'error': 'TimeoutError: test exceeded 30s'}]
        rules = analyse_benchmark_for_rules(cases)
        self.assertTrue(any('TimeoutError' in r['trigger'] for r in rules))
        self.assertTrue(any(r['category'] == 'blocker_pattern' for r in rules))

    def test_dedup_existing_triggers(self):
        cases = [{'case_id': 'c1', 'passed': False, 'score': 0.5,
                  'missing_keywords': ['auth']}]
        existing = {'benchmark_miss:auth'}
        rules = analyse_benchmark_for_rules(cases, existing_rule_triggers=existing)
        self.assertEqual(rules, [])

    def test_multiple_failures(self):
        cases = [
            {'case_id': 'c1', 'passed': False, 'score': 0.5, 'missing_keywords': ['auth']},
            {'case_id': 'c2', 'passed': False, 'score': 0.3, 'missing_keywords': []},
            {'case_id': 'c3', 'passed': True, 'score': 1.0},
        ]
        rules = analyse_benchmark_for_rules(cases)
        self.assertEqual(len(rules), 2)  # c1 + c2, c3 passes

    def test_rule_structure(self):
        cases = [{'case_id': 'c1', 'passed': False, 'score': 0.4,
                  'missing_keywords': ['test']}]
        rules = analyse_benchmark_for_rules(cases, benchmark_id='bench-001')
        self.assertEqual(len(rules), 1)
        r = rules[0]
        self.assertTrue(r['rule_id'].startswith('bench_'))
        self.assertEqual(r['source_run'], 'bench-001')
        self.assertEqual(r['confidence'], 0.5)
        self.assertTrue(r['created_at'])  # non-empty timestamp


class TestRenderBenchmarkLessons(unittest.TestCase):
    def test_all_pass(self):
        cases = [{'case_id': 'c1', 'passed': True, 'score': 1.0}]
        self.assertEqual(render_benchmark_lessons(cases), '')

    def test_failures_rendered(self):
        cases = [
            {'case_id': 'c1', 'passed': False, 'score': 0.5, 'missing_keywords': ['auth']},
            {'case_id': 'c2', 'passed': False, 'score': 0.3, 'error': 'Timeout'},
        ]
        text = render_benchmark_lessons(cases)
        self.assertIn('Benchmark Failures', text)
        self.assertIn('c1', text)
        self.assertIn('c2', text)
        self.assertIn('auth', text)

    def test_edge_case_high_score_not_passed(self):
        cases = [{'case_id': 'c1', 'passed': False, 'score': 0.7}]
        text = render_benchmark_lessons(cases)
        self.assertIn('c1', text)


# ===================================================================
# Intelligence Telemetry Tests
# ===================================================================

class TestInjectionRecord(unittest.TestCase):
    def test_score_delta(self):
        r = InjectionRecord(score_before=50, score_after=70)
        self.assertEqual(r.score_delta, 20)
        self.assertTrue(r.helped)

    def test_regression(self):
        r = InjectionRecord(score_before=70, score_after=50)
        self.assertEqual(r.score_delta, -20)
        self.assertFalse(r.helped)

    def test_neutral(self):
        r = InjectionRecord(score_before=50, score_after=50)
        self.assertEqual(r.score_delta, 0)
        self.assertFalse(r.helped)


class TestTelemetryAggregator(unittest.TestCase):
    def test_empty_report(self):
        agg = TelemetryAggregator()
        report = agg.build_report()
        self.assertEqual(report.total_iterations, 0)
        self.assertEqual(report.source_stats, {})

    def test_record_and_report(self):
        agg = TelemetryAggregator()
        sections = [
            {'name': 'brain', 'tokens_estimated': 100, 'was_compressed': False, 'was_dropped': False},
            {'name': 'archive', 'tokens_estimated': 200, 'was_compressed': True, 'was_dropped': False},
        ]
        agg.record_iteration(sections, score_before=50, score_after=70, iteration=1)
        report = agg.build_report()
        self.assertEqual(report.total_iterations, 1)
        self.assertIn('brain', report.source_stats)
        self.assertIn('archive', report.source_stats)
        self.assertEqual(report.source_stats['brain'].times_helped, 1)

    def test_wasteful_detection(self):
        agg = TelemetryAggregator()
        for i in range(1, 4):
            sections = [
                {'name': 'useless', 'tokens_estimated': 500, 'was_compressed': False, 'was_dropped': False},
            ]
            agg.record_iteration(sections, score_before=50, score_after=50, iteration=i)
        report = agg.build_report()
        self.assertIn('useless', report.wasteful_sources)

    def test_top_source_detection(self):
        agg = TelemetryAggregator()
        for i in range(1, 4):
            sections = [
                {'name': 'helpful', 'tokens_estimated': 100, 'was_compressed': False, 'was_dropped': False},
            ]
            agg.record_iteration(sections, score_before=50, score_after=60, iteration=i)
        report = agg.build_report()
        self.assertIn('helpful', report.top_sources)

    def test_priority_adjustment_boost(self):
        agg = TelemetryAggregator()
        for i in range(1, 4):
            agg.record_iteration(
                [{'name': 'good', 'tokens_estimated': 50, 'was_compressed': False, 'was_dropped': False}],
                score_before=50, score_after=60, iteration=i,
            )
        self.assertEqual(agg.get_priority_adjustment('good'), -1)  # boost

    def test_priority_adjustment_demote(self):
        agg = TelemetryAggregator()
        for i in range(1, 4):
            agg.record_iteration(
                [{'name': 'bad', 'tokens_estimated': 50, 'was_compressed': False, 'was_dropped': False}],
                score_before=50, score_after=50, iteration=i,
            )
        self.assertEqual(agg.get_priority_adjustment('bad'), 1)  # demote

    def test_priority_adjustment_insufficient_data(self):
        agg = TelemetryAggregator()
        agg.record_iteration(
            [{'name': 'new', 'tokens_estimated': 50, 'was_compressed': False, 'was_dropped': False}],
            score_before=50, score_after=60, iteration=1,
        )
        self.assertEqual(agg.get_priority_adjustment('new'), 0)  # not enough data

    def test_dropped_counted_separately(self):
        agg = TelemetryAggregator()
        sections = [
            {'name': 'dropped_source', 'tokens_estimated': 200, 'was_compressed': False, 'was_dropped': True},
        ]
        agg.record_iteration(sections, score_before=50, score_after=60, iteration=1)
        report = agg.build_report()
        st = report.source_stats['dropped_source']
        self.assertEqual(st.times_dropped, 1)
        # Dropped sources don't count as helped/hurt
        self.assertEqual(st.times_helped, 0)

    def test_save_load(self):
        agg = TelemetryAggregator()
        agg.record_iteration(
            [{'name': 'test', 'tokens_estimated': 100, 'was_compressed': False, 'was_dropped': False}],
            score_before=50, score_after=60, iteration=1,
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'telemetry.json'
            agg.save(path)
            self.assertTrue(path.exists())

            loaded = TelemetryAggregator.load(path)
            report = loaded.build_report()
            self.assertEqual(report.total_iterations, 1)
            self.assertIn('test', report.source_stats)

    def test_load_missing_file(self):
        agg = TelemetryAggregator.load(Path('/nonexistent/telemetry.json'))
        self.assertEqual(agg.build_report().total_iterations, 0)


class TestIntelligenceReport(unittest.TestCase):
    def test_render_empty(self):
        report = IntelligenceReport()
        text = report.render_text()
        self.assertIn('No intelligence telemetry', text)

    def test_render_with_data(self):
        agg = TelemetryAggregator()
        for i in range(1, 4):
            agg.record_iteration(
                [{'name': 'brain', 'tokens_estimated': 100, 'was_compressed': False, 'was_dropped': False}],
                score_before=50, score_after=60, iteration=i,
            )
        report = agg.build_report()
        text = report.render_text()
        self.assertIn('Intelligence Effectiveness', text)
        self.assertIn('brain', text)
        self.assertIn('100%', text)  # 3/3 helped


if __name__ == '__main__':
    unittest.main()
