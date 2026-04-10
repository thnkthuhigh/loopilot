"""Tests for the AI Narrative UI — Phase 15.

Covers:
  - narrative_engine.py: 4 view dataclasses + NarrativeEngine aggregator
  - narrative_formats.py: fixed-structure formatters + dict export
  - CLI explain command integration
"""

from __future__ import annotations

from copilot_operator.narrative_engine import (
    DecisionTrace,
    LiveNarrative,
    MemorySnapshot,
    NarrativeEngine,
    RunSummaryView,
)
from copilot_operator.narrative_formats import (
    format_decision_block,
    format_iteration_log,
    format_live_block,
    format_memory_block,
    format_summary_block,
    views_to_dict,
)

# ─── LiveNarrative ────────────────────────────────────────────────

class TestLiveNarrative:
    def test_defaults(self):
        live = LiveNarrative()
        text = live.render()
        assert 'RUNNING' in text or 'initializing' in text

    def test_full_state(self):
        live = LiveNarrative(
            task='Fix auth module',
            milestone='M1 — Core',
            iteration=3,
            max_iterations=6,
            worker_status='running',
            score=70,
            target_score=85,
            strategy='aggressive',
            escalation_level=2,
            just_did='Added test file',
            next_action='Run lint',
            urgency='normal',
        )
        text = live.render()
        assert 'Fix auth module' in text
        assert 'M1 — Core' in text
        assert '3/6' in text
        assert '70/85' in text
        assert 'aggressive' in text
        assert 'Added test file' in text
        assert 'Run lint' in text

    def test_score_icons(self):
        # Green when score >= target
        live = LiveNarrative(score=90, target_score=85)
        assert '🟢' in live.render()

        # Yellow when score > 0 but below target
        live = LiveNarrative(score=50, target_score=85)
        assert '🟡' in live.render()

        # White when score == 0
        live = LiveNarrative(score=0, target_score=85)
        assert '⚪' in live.render()

    def test_urgency_display(self):
        live = LiveNarrative(urgency='critical', blocking_now=['Tests failing', 'Lint error'])
        text = live.render()
        assert '🚨' in text
        assert 'Tests failing' in text
        assert 'Lint error' in text

    def test_high_urgency(self):
        live = LiveNarrative(urgency='high')
        text = live.render()
        assert '⚠️' in text

    def test_default_strategy_hidden(self):
        live = LiveNarrative(strategy='default')
        text = live.render()
        assert 'Strategy' not in text


# ─── DecisionTrace ────────────────────────────────────────────────

class TestDecisionTrace:
    def test_minimal(self):
        trace = DecisionTrace(iteration=1)
        text = trace.render()
        assert 'Iteration 1' in text

    def test_full_trace(self):
        trace = DecisionTrace(
            iteration=3,
            observation='Score stuck at 60',
            hypothesis='Missing test coverage',
            decision='Add integration tests',
            decision_code='WORK_REMAINS',
            alternatives_rejected=['Skip tests', 'Refactor first'],
            actions_taken=['escalate_profile → aggressive'],
            score_delta=15,
            what_helped='Targeted test addition',
            what_hurt='Initial approach too broad',
            correction='Focus on specific module',
        )
        text = trace.render()
        assert '[OBSERVATION]' in text
        assert 'Score stuck at 60' in text
        assert '[HYPOTHESIS]' in text
        assert '[DECISION]' in text
        assert 'WORK_REMAINS' in text
        assert '[REJECTED]' in text
        assert 'Skip tests' in text
        assert '[ACTUATION]' in text
        assert '[SELF-EVAL]' in text
        assert '📈' in text
        assert '+15' in text
        assert '✅' in text
        assert '❌' in text
        assert '🔧' in text

    def test_negative_score_delta(self):
        trace = DecisionTrace(iteration=2, score_delta=-5)
        text = trace.render()
        assert '📉' in text
        assert '-5' in text

    def test_zero_score_delta_hidden(self):
        trace = DecisionTrace(iteration=2, score_delta=0)
        text = trace.render()
        assert 'SELF-EVAL' not in text


# ─── RunSummaryView ───────────────────────────────────────────────

class TestRunSummaryView:
    def test_success(self):
        summary = RunSummaryView(
            goal='Fix login bug',
            goal_profile='bug',
            outcome='success',
            score=92,
            target_score=85,
            iterations=4,
            files_changed=['src/auth.py', 'tests/test_auth.py'],
            tests_passed=3,
            tests_failed=0,
            lint_status='pass',
            stop_reason='All conditions met',
            stop_reason_code='STOP_GATE_PASSED',
            conditions_met=['Tests pass', 'Score >= 85'],
            conditions_unmet=[],
        )
        text = summary.render()
        assert '✅' in text
        assert 'Fix login bug' in text
        assert '92/85' in text
        assert 'src/auth.py' in text
        assert '3 passed' in text
        assert 'STOP_GATE_PASSED' in text

    def test_partial(self):
        summary = RunSummaryView(outcome='partial', goal='Improve perf')
        text = summary.render()
        assert '🟡' in text

    def test_fail(self):
        summary = RunSummaryView(outcome='fail', goal='Fix crash')
        text = summary.render()
        assert '❌' in text

    def test_blocked(self):
        summary = RunSummaryView(outcome='blocked', goal='Deploy')
        text = summary.render()
        assert '🔴' in text

    def test_risks_and_next(self):
        summary = RunSummaryView(
            goal='Migrate DB',
            outcome='success',
            risks=['Data loss risk', 'Schema mismatch'],
            next_step='Run migration on staging',
            commitments_owed=['Update docs', 'Notify team'],
        )
        text = summary.render()
        assert '⚠' in text
        assert 'Data loss risk' in text
        assert 'Run migration on staging' in text
        assert '📌' in text
        assert 'Update docs' in text

    def test_many_files_truncated(self):
        files = [f'file_{i}.py' for i in range(10)]
        summary = RunSummaryView(goal='Big change', outcome='success', files_changed=files)
        text = summary.render()
        assert '+5 more' in text


# ─── MemorySnapshot ───────────────────────────────────────────────

class TestMemorySnapshot:
    def test_empty(self):
        mem = MemorySnapshot()
        text = mem.render()
        assert 'No memory data' in text

    def test_full_memory(self):
        mem = MemorySnapshot(
            mission_direction='Ship v3.0 with AI narrative',
            mission_phase='Phase 15',
            mission_priorities=['Narrative UI', 'Integration'],
            mission_constraints=['No breaking changes'],
            success_rate=0.85,
            total_runs=20,
            common_blockers=['Lint failures'],
            active_rules=12,
            top_rules=['[performance] Avoid N+1 queries', '[security] Validate input'],
            recent_lessons=['Test first approach works better'],
            effective_strategies=['aggressive', 'targeted'],
            failed_strategies=['brute-force'],
            top_intel_sources=['diagnosis', 'brain'],
            wasteful_sources=['archive'],
        )
        text = mem.render()
        assert '🎯' in text
        assert 'Ship v3.0' in text
        assert 'Phase 15' in text
        assert '📊' in text
        assert '85%' in text
        assert '📏' in text
        assert '12 active' in text
        assert '💡' in text
        assert 'Test first' in text
        assert '✅ Effective' in text
        assert '❌ Failed' in text
        assert '🔬' in text


# ─── NarrativeEngine ─────────────────────────────────────────────

class TestNarrativeEngine:
    def _make_runtime(self, **overrides):
        base = {
            'goal': 'Fix bug #42',
            'goalProfile': 'bug',
            'status': 'running',
            'plan': {'milestones': [{'title': 'M1', 'status': 'in_progress', 'tasks': [{'title': 'T1', 'status': 'pending'}]}]},
            'history': [{'score': 75, 'summary': 'Fixed auth', 'blockers': []}],
            'allChangedFiles': ['src/auth.py'],
        }
        base.update(overrides)
        return base

    def _make_config(self):
        class MockConfig:
            max_iterations = 6
            target_score = 85
        return MockConfig()

    def test_build_live(self):
        engine = NarrativeEngine()
        runtime = self._make_runtime()
        config = self._make_config()
        live = engine.build_live(runtime, config)
        assert live.task == 'T1'
        assert live.milestone == 'M1'
        assert live.iteration == 1
        assert live.score == 75
        assert live.target_score == 85

    def test_build_live_empty_plan(self):
        engine = NarrativeEngine()
        runtime = self._make_runtime(plan={}, goal='Do something special')
        config = self._make_config()
        live = engine.build_live(runtime, config)
        assert 'Do something' in live.task

    def test_build_decision_trace(self):
        engine = NarrativeEngine()

        class MockDiagnosis:
            score_trend = type('Trend', (), {'direction': 'declining', 'delta': -10})()
            loops = [type('Loop', (), {'detected': True, 'kind': 'score_stuck'})()]
            risk_level = 'high'
            hints = [type('Hint', (), {'action': 'escalate_profile', 'reason': 'Score stuck', 'suggested_profile': 'aggressive'})()]

        class MockEval:
            score = type('Score', (), {'delta': 15})()
            what_helped = 'Targeted approach'
            what_hurt = 'Too broad'
            correction = 'Focus more'

        class MockDecision:
            reason = 'Work remains'
            reason_code = 'WORK_REMAINS'

        trace = engine.build_decision_trace(
            iteration=2,
            diagnosis=MockDiagnosis(),
            eval_result=MockEval(),
            decision=MockDecision(),
        )
        assert trace.iteration == 2
        assert 'declining' in trace.observation
        assert 'Loop detected' in trace.observation
        assert trace.score_delta == 15
        assert trace.what_helped == 'Targeted approach'
        assert trace.decision_code == 'WORK_REMAINS'
        assert len(engine.get_decision_history()) == 1

    def test_build_summary(self):
        engine = NarrativeEngine()
        runtime = self._make_runtime()
        config = self._make_config()
        result = {'status': 'complete', 'reason': 'All done', 'reasonCode': 'STOP_GATE_PASSED'}
        summary = engine.build_summary(runtime, result, config)
        assert summary.outcome == 'success'
        assert summary.score == 75
        assert summary.goal == 'Fix bug #42'

    def test_build_summary_blocked(self):
        engine = NarrativeEngine()
        runtime = self._make_runtime()
        config = self._make_config()
        result = {'status': 'blocked', 'reason': 'Max iter', 'reasonCode': 'MAX_ITERATIONS_REACHED'}
        summary = engine.build_summary(runtime, result, config)
        assert summary.outcome == 'blocked'

    def test_build_memory(self):
        engine = NarrativeEngine()

        class MockMission:
            direction = 'Ship v3'
            current_phase = 'P15'
            priorities = ['UI']
            hard_constraints = ['No breaks']
            lessons_learned = ['Test first']

        mem = engine.build_memory(mission=MockMission())
        assert mem.mission_direction == 'Ship v3'
        assert mem.mission_phase == 'P15'
        assert len(mem.recent_lessons) == 1

    def test_record_decision(self):
        engine = NarrativeEngine()
        trace = DecisionTrace(iteration=1, decision='Continue')
        engine.record_decision(trace)
        assert len(engine.get_decision_history()) == 1

    def test_render_full_trace_empty(self):
        engine = NarrativeEngine()
        assert 'No decisions' in engine.render_full_trace()

    def test_render_full_trace(self):
        engine = NarrativeEngine()
        engine.record_decision(DecisionTrace(iteration=1, decision='Fix'))
        engine.record_decision(DecisionTrace(iteration=2, decision='Test'))
        text = engine.render_full_trace()
        assert 'Iteration 1' in text
        assert 'Iteration 2' in text

    def test_render_all_views(self):
        engine = NarrativeEngine()
        engine.record_decision(DecisionTrace(iteration=1, decision='Did something'))
        runtime = self._make_runtime()
        config = self._make_config()
        result = {'status': 'complete', 'reason': 'Done', 'reasonCode': 'STOP_GATE_PASSED'}
        text = engine.render_all_views(runtime, result, config)
        assert 'RUN SUMMARY' in text
        assert 'DECISION TRACE' in text


# ─── narrative_formats.py ────────────────────────────────────────

class TestFormatLiveBlock:
    def test_basic(self):
        live = LiveNarrative(
            task='Fix bug', iteration=2, max_iterations=6,
            score=60, target_score=85, worker_status='running',
        )
        text = format_live_block(live)
        assert '[STATE]' in text
        assert '[ACTION]' in text
        assert 'Fix bug' in text
        assert '2/6' in text

    def test_pressure_block(self):
        live = LiveNarrative(urgency='critical', blocking_now=['Test failure'])
        text = format_live_block(live)
        assert '[PRESSURE]' in text
        assert 'critical' in text
        assert 'Test failure' in text

    def test_milestone_shown(self):
        live = LiveNarrative(milestone='M1 Core')
        text = format_live_block(live)
        assert 'Milestone: M1 Core' in text

    def test_strategy_shown(self):
        live = LiveNarrative(strategy='aggressive', escalation_level=2)
        text = format_live_block(live)
        assert 'Strategy:  aggressive' in text


class TestFormatDecisionBlock:
    def test_full(self):
        trace = DecisionTrace(
            iteration=3,
            observation='Score declining',
            hypothesis='Wrong approach',
            decision='Switch strategy',
            decision_code='REPLAN',
            alternatives_rejected=['Retry', 'Abort'],
            actions_taken=['escalate_profile'],
            score_delta=10,
            what_helped='Focus',
            what_hurt='Scatter',
            correction='Narrow scope',
        )
        text = format_decision_block(trace)
        assert '[ITERATION 3]' in text
        assert '[OBSERVATION]' in text
        assert '[HYPOTHESIS]' in text
        assert '[DECISION]' in text
        assert '(REPLAN)' in text
        assert '[ALTERNATIVES_REJECTED]' in text
        assert '[ACTUATION]' in text
        assert '[SELF_EVAL]' in text
        assert 'Score: +10' in text


class TestFormatSummaryBlock:
    def test_full(self):
        summary = RunSummaryView(
            goal='Fix auth', goal_profile='bug', outcome='success',
            score=92, target_score=85, iterations=4,
            files_changed=['a.py', 'b.py'],
            tests_passed=5, tests_failed=0, lint_status='pass',
            stop_reason='All done', stop_reason_code='STOP_GATE_PASSED',
            conditions_met=['Tests pass'],
            conditions_unmet=['Docs missing'],
            risks=['Regression risk'],
            next_step='Deploy',
            commitments_owed=['Update changelog'],
        )
        text = format_summary_block(summary)
        assert '[GOAL]' in text
        assert '[OUTCOME]' in text
        assert '[CHANGES]' in text
        assert '[VALIDATION]' in text
        assert '[WHY_DONE]' in text
        assert '✅ Tests pass' in text
        assert '❌ Docs missing' in text
        assert '[RISKS]' in text
        assert '[NEXT]' in text
        assert '📌 Update changelog' in text


class TestFormatMemoryBlock:
    def test_empty(self):
        mem = MemorySnapshot()
        text = format_memory_block(mem)
        assert '[MEMORY]' in text

    def test_full(self):
        mem = MemorySnapshot(
            mission_direction='Ship v3', mission_phase='P15',
            mission_priorities=['Speed'],
            total_runs=10, success_rate=0.8,
            common_blockers=['lint'],
            active_rules=5, top_rules=['Rule 1'],
            recent_lessons=['Lesson A'],
            effective_strategies=['fast'], failed_strategies=['slow'],
            top_intel_sources=['brain'], wasteful_sources=['archive'],
        )
        text = format_memory_block(mem)
        assert '[MISSION]' in text
        assert '[PROJECT]' in text
        assert '[RULES]' in text
        assert '[LESSONS]' in text
        assert '[STRATEGIES]' in text
        assert '[TELEMETRY]' in text


class TestFormatIterationLog:
    def test_combined(self):
        live = LiveNarrative(task='Fix', iteration=1, max_iterations=6, score=50, target_score=85, worker_status='running')
        trace = DecisionTrace(iteration=1, decision='Continue')
        text = format_iteration_log(1, live, trace)
        assert '[STATE]' in text
        assert '[ITERATION 1]' in text

    def test_without_trace(self):
        live = LiveNarrative(task='Fix', worker_status='running')
        text = format_iteration_log(1, live)
        assert '[STATE]' in text
        assert '[ITERATION' not in text


class TestViewsToDict:
    def test_all_views(self):
        live = LiveNarrative(task='Fix', score=70, target_score=85, iteration=2, max_iterations=6, strategy='default', urgency='normal', just_did='a', next_action='b', milestone='M1')
        trace = DecisionTrace(iteration=1, observation='obs', hypothesis='hyp', decision='dec', decision_code='DC', alternatives_rejected=['x'], actions_taken=['y'], score_delta=5, what_helped='h', what_hurt='hu', correction='c')
        summary = RunSummaryView(goal='G', outcome='success', score=90, target_score=85, iterations=3, files_changed=['f.py'], stop_reason='done', stop_reason_code='STOP', conditions_met=['a'], conditions_unmet=['b'], risks=['r'], next_step='next')
        mem = MemorySnapshot(mission_direction='Dir', mission_phase='P1', mission_priorities=['p'], total_runs=5, success_rate=0.9, active_rules=3, recent_lessons=['l'], effective_strategies=['e'], failed_strategies=['f'])

        result = views_to_dict(live=live, traces=[trace], summary=summary, memory=mem)
        assert 'live' in result
        assert result['live']['task'] == 'Fix'
        assert 'decisions' in result
        assert result['decisions'][0]['iteration'] == 1
        assert 'summary' in result
        assert result['summary']['goal'] == 'G'
        assert 'memory' in result
        assert result['memory']['mission'] == 'Dir'

    def test_partial_views(self):
        result = views_to_dict(live=LiveNarrative(task='X'))
        assert 'live' in result
        assert 'decisions' not in result
        assert 'summary' not in result

    def test_empty(self):
        result = views_to_dict()
        assert result == {}


# ─── Edge cases and integration ───────────────────────────────────

class TestEdgeCases:
    def test_blocking_now_limited_to_3(self):
        live = LiveNarrative(
            urgency='critical',
            blocking_now=['a', 'b', 'c', 'd', 'e'],
        )
        text = live.render()
        assert text.count('🔴 Blocked:') == 3

    def test_conditions_unmet_rendering(self):
        summary = RunSummaryView(
            goal='Deploy', outcome='partial',
            conditions_unmet=['Tests', 'Lint', 'Coverage'],
        )
        text = summary.render()
        assert text.count('❌') >= 3

    def test_commitments_limited_to_3(self):
        summary = RunSummaryView(
            goal='Deploy', outcome='success',
            commitments_owed=['A', 'B', 'C', 'D'],
        )
        text = summary.render()
        assert text.count('📌') == 3

    def test_rules_limited_to_3(self):
        mem = MemorySnapshot(
            active_rules=10,
            top_rules=['R1', 'R2', 'R3', 'R4', 'R5'],
        )
        text = mem.render()
        assert text.count('•') == 3

    def test_narrative_engine_multiple_traces(self):
        engine = NarrativeEngine()
        for i in range(5):
            engine.record_decision(DecisionTrace(iteration=i + 1, decision=f'Action {i}'))
        assert len(engine.get_decision_history()) == 5
        text = engine.render_full_trace()
        assert 'Iteration 5' in text
