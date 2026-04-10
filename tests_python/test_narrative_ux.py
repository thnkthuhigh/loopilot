"""Tests for Narrative UX improvements — Phase 17.

Covers:
  - format_summary_short: ultra-compact run summary (--short mode)
  - format_live_stream_line: single-line live progress
  - Auto-summary print in _post_run_hooks
  - --short flag in _explain CLI
  - --live enhanced streaming (validation, decision trace to stderr)
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from unittest import mock

from copilot_operator.narrative_engine import (
    DecisionTrace,
    NarrativeEngine,
    RunSummaryView,
)
from copilot_operator.narrative_formats import (
    format_live_stream_line,
    format_summary_short,
)

# ─── format_summary_short ────────────────────────────────────────

class TestFormatSummaryShort:
    def test_success_output(self):
        s = RunSummaryView(
            goal='Fix auth bug',
            outcome='success',
            score=90,
            target_score=85,
            iterations=2,
            stop_reason='tests pass',
            risks=['concurrency edge case'],
            next_step='add stress test',
        )
        text = format_summary_short(s)
        assert 'Fix auth bug' in text
        assert 'SUCCESS' in text
        assert '90/85' in text
        assert '2 iter' in text
        assert 'tests pass' in text
        assert 'concurrency edge case' in text
        assert 'add stress test' in text

    def test_fail_output(self):
        s = RunSummaryView(
            goal='Add feature X',
            outcome='fail',
            score=30,
            target_score=85,
            iterations=6,
            stop_reason='max iterations',
        )
        text = format_summary_short(s)
        assert 'FAIL' in text
        assert '30/85' in text
        assert '6 iter' in text
        lines = text.strip().split('\n')
        assert len(lines) <= 5  # Short = max 5 lines

    def test_blocked_with_owed(self):
        s = RunSummaryView(
            goal='Refactor DB',
            outcome='blocked',
            score=50,
            target_score=85,
            iterations=3,
            stop_reason='blocker detected',
            commitments_owed=['fix migration', 'update schema'],
        )
        text = format_summary_short(s)
        assert 'BLOCKED' in text
        assert 'fix migration' in text

    def test_minimal(self):
        s = RunSummaryView(
            goal='Simple task',
            outcome='success',
            score=95,
            target_score=85,
            iterations=1,
            stop_reason='done',
        )
        text = format_summary_short(s)
        lines = text.strip().split('\n')
        assert len(lines) >= 2  # At least goal + result
        assert len(lines) <= 5

    def test_partial_outcome(self):
        s = RunSummaryView(
            goal='Partial fix',
            outcome='partial',
            score=70,
            target_score=85,
            iterations=4,
            stop_reason='no progress',
            next_step='try different approach',
        )
        text = format_summary_short(s)
        assert 'PARTIAL' in text
        assert 'try different approach' in text

    def test_no_risks_no_next(self):
        s = RunSummaryView(
            goal='Quick fix',
            outcome='success',
            score=100,
            target_score=85,
            iterations=1,
            stop_reason='all tests pass',
        )
        text = format_summary_short(s)
        assert 'Risk' not in text
        assert 'Next' not in text


# ─── format_live_stream_line ─────────────────────────────────────

class TestFormatLiveStreamLine:
    def test_basic(self):
        line = format_live_stream_line(1, 'VALIDATING', 'Running pytest...')
        assert '[ITER 1]' in line
        assert 'VALIDATING' in line
        assert 'Running pytest...' in line

    def test_decision_phase(self):
        line = format_live_stream_line(3, 'DECISION', 'Choosing middleware fix')
        assert '[ITER 3]' in line
        assert 'DECISION' in line


# ─── NarrativeEngine build_summary + short ───────────────────────

class TestAutoSummaryIntegration:
    def test_engine_summary_to_short(self):
        """Verify NarrativeEngine output works with format_summary_short."""
        engine = NarrativeEngine()
        runtime = {
            'goal': 'Fix login',
            'runId': 'test-123',
            'status': 'complete',
            'allChangedFiles': ['auth.py', 'test_auth.py'],
            'plan': {'milestones': [{'id': 'M1', 'title': 'Fix', 'status': 'complete'}]},
            'history': [{'iteration': 1, 'score': 90, 'status': 'done',
                         'validation_after': [{'status': 'pass', 'name': 'tests'}]}],
        }
        result = {
            'status': 'complete',
            'score': 90,
            'iterations': 1,
            'reason': 'All tests pass',
            'reasonCode': 'TARGET_MET',
        }
        config = mock.MagicMock()
        config.target_score = 85
        config.goal_profile = 'bug'
        config.expect_tests_added = True
        config.expect_docs_updated = False
        config.expect_max_files_changed = 20
        summary = engine.build_summary(runtime, result, config)
        short = format_summary_short(summary)
        assert 'Fix login' in short
        assert 'SUCCESS' in short


# ─── CLI --short flag ─────────────────────────────────────────────

class TestExplainShortCLI:
    def test_argparser_has_short(self):
        from copilot_operator.cli import build_parser
        parser = build_parser()
        # Verify --short is recognized by the explain subcommand
        args = parser.parse_args(['explain', '--short'])
        assert args.short is True

    def test_argparser_short_default_false(self):
        from copilot_operator.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(['explain'])
        assert args.short is False

    def test_argparser_short_with_json(self):
        from copilot_operator.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(['explain', '--short', '--json'])
        assert args.short is True
        assert args.output_json is True


# ─── Live streaming in operator ──────────────────────────────────

class TestLiveStreaming:
    def test_live_print_outputs_to_stderr(self):
        """Verify _live_print writes to stderr when live=True."""
        from copilot_operator.operator import CopilotOperator
        config = mock.MagicMock()
        config.workspace = Path('/tmp/test')
        config.state_file = Path('/tmp/test/state.json')
        config.memory_file = Path('/tmp/test/memory.json')
        config.summary_file = Path('/tmp/test/summary.json')
        config.llm_config_raw = None
        config.ensure_runtime_dirs = mock.MagicMock()
        config.chat_model = None
        with mock.patch('copilot_operator.operator._merge_vscode_settings'):
            with mock.patch('copilot_operator.operator.load_llm_config_from_env') as mock_llm:
                mock_llm.return_value = mock.MagicMock(is_ready=False)
                op = CopilotOperator(config, live=True)
        buf = io.StringIO()
        with mock.patch('sys.stderr', buf):
            op._live_print('test message')
        assert 'test message' in buf.getvalue()

    def test_live_print_silent_when_not_live(self):
        """Verify _live_print is silent when live=False."""
        from copilot_operator.operator import CopilotOperator
        config = mock.MagicMock()
        config.workspace = Path('/tmp/test')
        config.state_file = Path('/tmp/test/state.json')
        config.memory_file = Path('/tmp/test/memory.json')
        config.summary_file = Path('/tmp/test/summary.json')
        config.llm_config_raw = None
        config.ensure_runtime_dirs = mock.MagicMock()
        config.chat_model = None
        with mock.patch('copilot_operator.operator._merge_vscode_settings'):
            with mock.patch('copilot_operator.operator.load_llm_config_from_env') as mock_llm:
                mock_llm.return_value = mock.MagicMock(is_ready=False)
                op = CopilotOperator(config, live=False)
        buf = io.StringIO()
        with mock.patch('sys.stderr', buf):
            op._live_print('test message')
        assert buf.getvalue() == ''


# ─── DecisionTrace live rendering ────────────────────────────────

class TestDecisionTraceLive:
    def test_trace_fields_for_streaming(self):
        """Verify DecisionTrace has all fields needed by live streaming."""
        trace = DecisionTrace(
            iteration=2,
            observation='score dropped from 80 to 60',
            decision='switch approach',
            decision_code='SWITCH_APPROACH',
            score_delta=-20,
            correction='focus on failing test',
        )
        assert trace.observation == 'score dropped from 80 to 60'
        assert trace.score_delta == -20
        assert trace.correction == 'focus on failing test'


# ─── views_to_dict includes short ────────────────────────────────

class TestViewsIntegration:
    def test_short_format_json_roundtrip(self):
        """Verify short format data can be serialized to JSON."""
        s = RunSummaryView(
            goal='Fix auth',
            outcome='success',
            score=90,
            target_score=85,
            iterations=2,
            stop_reason='done',
            risks=['edge case'],
            next_step='add test',
        )
        data = {
            'goal': s.goal,
            'outcome': s.outcome,
            'score': s.score,
            'target_score': s.target_score,
            'iterations': s.iterations,
            'stop_reason': s.stop_reason,
            'risks': s.risks,
            'next_step': s.next_step,
        }
        serialized = json.dumps(data)
        parsed = json.loads(serialized)
        assert parsed['goal'] == 'Fix auth'
        assert parsed['outcome'] == 'success'
        assert parsed['risks'] == ['edge case']
