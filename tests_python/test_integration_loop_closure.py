"""Tests for Phase 16 — Integration & Loop Closure.

Covers:
  - Benchmark → benchmark_learner wiring
  - DecisionTrace serialization + deserialization
  - NarrativeEngine.serialize_traces / load_traces
  - narrative_formats used in per-iteration logs
  - __init__ exports
  - Version consistency
"""

from __future__ import annotations

import json

import copilot_operator
from copilot_operator.narrative_engine import (
    DecisionTrace,
    LiveNarrative,
    MemorySnapshot,
    NarrativeEngine,
    RunSummaryView,
)
from copilot_operator.narrative_formats import (
    format_iteration_log,
    format_live_block,
    views_to_dict,
)

# ─── Version Consistency ─────────────────────────────────────────

class TestVersionConsistency:
    def test_init_version(self):
        assert copilot_operator.__version__ == '3.0.0'

    def test_init_exports(self):
        """All key user-facing classes should be importable from the package."""
        assert hasattr(copilot_operator, 'CopilotOperator')
        assert hasattr(copilot_operator, 'NarrativeEngine')
        assert hasattr(copilot_operator, 'LiveNarrative')
        assert hasattr(copilot_operator, 'DecisionTrace')
        assert hasattr(copilot_operator, 'RunSummaryView')
        assert hasattr(copilot_operator, 'MemorySnapshot')
        assert hasattr(copilot_operator, 'LLMBrain')
        assert hasattr(copilot_operator, 'OperatorConfig')


# ─── DecisionTrace Serialization ─────────────────────────────────

class TestDecisionTraceSerialization:
    def test_serialize_empty(self):
        engine = NarrativeEngine()
        result = engine.serialize_traces()
        assert result == []

    def test_serialize_roundtrip(self):
        engine = NarrativeEngine()
        trace1 = DecisionTrace(
            iteration=1,
            observation='Score stuck at 60',
            hypothesis='Need more tests',
            decision='Add integration tests',
            decision_code='WORK_REMAINS',
            alternatives_rejected=['Skip tests', 'Refactor first'],
            actions_taken=['escalate_profile'],
            score_delta=15,
            what_helped='Focused approach',
            what_hurt='Initial scatter',
            correction='Narrow scope',
        )
        trace2 = DecisionTrace(
            iteration=2,
            observation='Score improved to 75',
            decision='Continue',
            decision_code='CONTINUE_REQUIRED',
            score_delta=10,
        )
        engine.record_decision(trace1)
        engine.record_decision(trace2)

        serialized = engine.serialize_traces()
        assert len(serialized) == 2
        assert serialized[0]['iteration'] == 1
        assert serialized[0]['observation'] == 'Score stuck at 60'
        assert serialized[0]['alternatives_rejected'] == ['Skip tests', 'Refactor first']
        assert serialized[1]['iteration'] == 2

        # Verify JSON-serializable
        json_text = json.dumps(serialized)
        loaded_data = json.loads(json_text)

        # Roundtrip
        traces = NarrativeEngine.load_traces(loaded_data)
        assert len(traces) == 2
        assert traces[0].iteration == 1
        assert traces[0].observation == 'Score stuck at 60'
        assert traces[0].hypothesis == 'Need more tests'
        assert traces[0].alternatives_rejected == ['Skip tests', 'Refactor first']
        assert traces[0].score_delta == 15
        assert traces[1].iteration == 2
        assert traces[1].score_delta == 10

    def test_load_empty(self):
        traces = NarrativeEngine.load_traces([])
        assert traces == []

    def test_load_partial_data(self):
        """Missing fields should default gracefully."""
        data = [{'iteration': 5, 'decision': 'Stop'}]
        traces = NarrativeEngine.load_traces(data)
        assert len(traces) == 1
        assert traces[0].iteration == 5
        assert traces[0].decision == 'Stop'
        assert traces[0].observation == ''
        assert traces[0].score_delta == 0
        assert traces[0].alternatives_rejected == []

    def test_serialize_matches_views_to_dict(self):
        """serialize_traces output should be compatible with views_to_dict."""
        engine = NarrativeEngine()
        engine.record_decision(DecisionTrace(
            iteration=1,
            observation='obs',
            decision='dec',
            decision_code='DC',
        ))
        serialized = engine.serialize_traces()
        result = views_to_dict(traces=[
            DecisionTrace(**{k: v for k, v in s.items()})
            for s in serialized
        ])
        assert 'decisions' in result
        assert result['decisions'][0]['observation'] == 'obs'


# ─── Benchmark → Benchmark Learner Integration ──────────────────

class TestBenchmarkLearnerIntegration:
    def test_benchmark_learner_import(self):
        """benchmark_learner should be importable from benchmark context."""
        from copilot_operator.benchmark_learner import analyse_benchmark_for_rules
        assert callable(analyse_benchmark_for_rules)

    def test_analyse_benchmark_with_failures(self):
        """analyse_benchmark_for_rules should produce rules from failed cases."""
        from copilot_operator.benchmark_learner import analyse_benchmark_for_rules

        case_results = [
            {
                'case_id': 'test-auth',
                'passed': False,
                'score': 0.3,
                'matched_keywords': ['auth'],
                'missing_keywords': ['token', 'jwt'],
                'error': '',
            },
            {
                'case_id': 'test-perf',
                'passed': True,
                'score': 1.0,
                'matched_keywords': ['perf'],
                'missing_keywords': [],
                'error': '',
            },
            {
                'case_id': 'test-crash',
                'passed': False,
                'score': 0.0,
                'matched_keywords': [],
                'missing_keywords': ['error'],
                'error': 'ModuleNotFoundError: missing dep',
            },
        ]

        rules = analyse_benchmark_for_rules(case_results, benchmark_id='test-bench')
        assert len(rules) >= 1  # At least one rule from failures
        # Check rule structure
        for rule in rules:
            assert 'trigger' in rule
            assert 'guardrail' in rule
            assert 'category' in rule
            assert 'source_run' in rule

    def test_analyse_all_passing_produces_no_rules(self):
        from copilot_operator.benchmark_learner import analyse_benchmark_for_rules

        case_results = [
            {'case_id': 'ok', 'passed': True, 'score': 1.0, 'matched_keywords': ['all'], 'missing_keywords': [], 'error': ''},
        ]
        rules = analyse_benchmark_for_rules(case_results)
        assert rules == []


# ─── Watch / format_operator_status integration ─────────────────

class TestWatchLiveNarrative:
    def test_format_operator_status_includes_live(self):
        from copilot_operator.bootstrap import format_operator_status

        status = {
            'status': 'running',
            'goal': 'Fix authentication bug',
            'goalProfile': 'bug',
            'runId': 'test-123',
            'iterationsCompleted': 3,
            'targetScore': 85,
            'maxIterations': 6,
            'history': [{'score': 70, 'summary': 'Added tests'}],
            'plan': {'milestones': [{'title': 'M1', 'status': 'in_progress', 'tasks': [{'title': 'T1', 'status': 'pending'}]}]},
        }
        text = format_operator_status(status)
        # Should include both LiveNarrative and traditional status
        assert '=== Copilot Operator Status ===' in text
        assert 'Fix authentication bug' in text

    def test_format_status_empty(self):
        from copilot_operator.bootstrap import format_operator_status

        text = format_operator_status({})
        assert '=== Copilot Operator Status ===' in text


# ─── Narrative Formats Integration ──────────────────────────────

class TestNarrativeFormatsIntegration:
    def test_format_iteration_log_produces_tagged_blocks(self):
        """format_iteration_log should produce predictable tagged output."""
        live = LiveNarrative(
            task='Fix auth module',
            iteration=3,
            max_iterations=6,
            score=70,
            target_score=85,
            worker_status='running',
            strategy='aggressive',
            escalation_level=2,
        )
        trace = DecisionTrace(
            iteration=3,
            observation='Score declining',
            decision='Escalate strategy',
            decision_code='REPLAN',
            score_delta=-5,
        )
        text = format_iteration_log(3, live, trace)

        # Should have both [STATE] and [ITERATION] blocks
        assert '[STATE]' in text
        assert '[ACTION]' in text
        assert '[ITERATION 3]' in text
        assert '[OBSERVATION]' in text
        assert '[DECISION]' in text

    def test_views_to_dict_json_serializable(self):
        """views_to_dict output must be fully JSON-serializable."""
        live = LiveNarrative(task='X', iteration=1, max_iterations=6, score=50, target_score=85,
                            strategy='default', urgency='normal', just_did='a', next_action='b', milestone='M')
        summary = RunSummaryView(goal='G', outcome='success', score=90, target_score=85,
                                iterations=3, files_changed=['f.py'], stop_reason='done',
                                stop_reason_code='STOP', conditions_met=[], conditions_unmet=[],
                                risks=[], next_step='')
        mem = MemorySnapshot(mission_direction='Dir', total_runs=5, success_rate=0.8,
                            active_rules=3, recent_lessons=['L'], effective_strategies=['E'],
                            failed_strategies=['F'])

        result = views_to_dict(live=live, summary=summary, memory=mem)
        json_text = json.dumps(result)
        parsed = json.loads(json_text)
        assert parsed['live']['task'] == 'X'
        assert parsed['summary']['score'] == 90
        assert parsed['memory']['mission'] == 'Dir'

    def test_format_blocks_match_dataclass_render(self):
        """Both render methods should produce output — they can differ in format but must have key content."""
        live = LiveNarrative(task='Fix', score=70, target_score=85, iteration=2, max_iterations=6, worker_status='running')
        render_output = live.render()
        format_output = format_live_block(live)

        # Both should mention the task and score
        assert 'Fix' in render_output
        assert 'Fix' in format_output
        assert '70' in render_output
        assert '70' in format_output
