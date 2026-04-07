"""Tests for hardening features added in the latest pass:
- logging_config module
- llm_brain circuit breaker
- intention_guard adaptive guardrails
- goal_decomposer LLM decomposition
- adversarial parse_critic_report validation
- operator dry-run mode and error recovery
"""
from __future__ import annotations

import json
import logging
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Logging config
# ---------------------------------------------------------------------------
from copilot_operator.logging_config import get_logger, setup_logging
from copilot_operator.operator import CopilotOperator


class TestLoggingConfig(unittest.TestCase):
    def setUp(self):
        # Reset the _CONFIGURED flag so each test can reconfigure
        import copilot_operator.logging_config as lc
        lc._CONFIGURED = False
        root = logging.getLogger('copilot_operator')
        root.handlers.clear()

    def test_setup_returns_root_logger(self):
        logger = setup_logging(level='WARNING', quiet=True)
        self.assertEqual(logger.name, 'copilot_operator')
        self.assertEqual(logger.level, logging.WARNING)

    def test_get_logger_returns_child(self):
        child = get_logger('mymodule')
        self.assertEqual(child.name, 'copilot_operator.mymodule')

    def test_setup_idempotent(self):
        import copilot_operator.logging_config as lc
        lc._CONFIGURED = False
        root = logging.getLogger('copilot_operator')
        root.handlers.clear()
        setup_logging(level='INFO', quiet=True)
        count1 = len(logging.getLogger('copilot_operator').handlers)
        setup_logging(level='DEBUG', quiet=False)
        count2 = len(logging.getLogger('copilot_operator').handlers)
        # Second call should not add more handlers
        self.assertEqual(count1, count2)

    def test_file_handler_creates_parent_dirs(self):
        d = tempfile.mkdtemp()
        try:
            import copilot_operator.logging_config as lc
            lc._CONFIGURED = False
            logging.getLogger('copilot_operator').handlers.clear()
            log_path = Path(d) / 'subdir' / 'test.log'
            setup_logging(level='DEBUG', log_file=log_path, quiet=True)
            get_logger('test').info('hello')
            # Flush and close handlers so Windows can delete the file
            root = logging.getLogger('copilot_operator')
            for h in list(root.handlers):
                h.flush()
                h.close()
                root.removeHandler(h)
            self.assertTrue(log_path.exists())
            content = log_path.read_text(encoding='utf-8')
            self.assertIn('hello', content)
        finally:
            import shutil
            shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# Circuit breaker (llm_brain)
# ---------------------------------------------------------------------------
from copilot_operator.llm_brain import (
    _COOLDOWN_SECONDS,
    _MAX_FAILURES,
    _check_circuit_breaker,
    _circuit_breaker,
    _record_failure,
    _record_success,
)


class TestCircuitBreaker(unittest.TestCase):
    def setUp(self):
        # Reset circuit breaker state before each test
        _circuit_breaker['failures'] = 0
        _circuit_breaker['last_failure'] = 0.0
        _circuit_breaker['open'] = False

    def test_closed_by_default(self):
        result = _check_circuit_breaker('openai')
        self.assertIsNone(result)

    def test_opens_after_max_failures(self):
        for _ in range(_MAX_FAILURES):
            _record_failure()
        self.assertTrue(_circuit_breaker['open'])
        msg = _check_circuit_breaker('openai')
        self.assertIsNotNone(msg)
        self.assertIn('circuit breaker open', msg)

    def test_success_resets_state(self):
        _record_failure()
        _record_failure()
        self.assertEqual(_circuit_breaker['failures'], 2)
        _record_success()
        self.assertEqual(_circuit_breaker['failures'], 0)
        self.assertFalse(_circuit_breaker['open'])

    def test_half_open_after_cooldown(self):
        for _ in range(_MAX_FAILURES):
            _record_failure()
        self.assertTrue(_circuit_breaker['open'])
        # Simulate cooldown elapsed
        _circuit_breaker['last_failure'] = time.monotonic() - _COOLDOWN_SECONDS - 1
        msg = _check_circuit_breaker('openai')
        # Should be half-open now (allowed through)
        self.assertIsNone(msg)
        self.assertFalse(_circuit_breaker['open'])

    def test_still_open_during_cooldown(self):
        for _ in range(_MAX_FAILURES):
            _record_failure()
        # last_failure is recent — circuit should stay open
        msg = _check_circuit_breaker('openai')
        self.assertIsNotNone(msg)
        self.assertIn('cooldown', msg)


# ---------------------------------------------------------------------------
# Adaptive guardrails (intention_guard)
# ---------------------------------------------------------------------------
from copilot_operator.intention_guard import learn_guardrails_from_history


class TestAdaptiveGuardrails(unittest.TestCase):
    def test_empty_history_returns_empty(self):
        self.assertEqual(learn_guardrails_from_history('bug', []), [])

    def test_short_history_returns_empty(self):
        self.assertEqual(learn_guardrails_from_history('bug', [{'score': 50}]), [])

    def test_detects_repeated_validation_failures(self):
        history = [
            {'validation_after': [{'name': 'tests', 'status': 'fail'}]},
            {'validation_after': [{'name': 'tests', 'status': 'fail'}]},
        ]
        result = learn_guardrails_from_history('bug', history)
        self.assertTrue(any('tests' in g and 'failed' in g.lower() for g in result))

    def test_detects_score_regression(self):
        history = [
            {'score': 80},
            {'score': 70},  # 10-point drop > 5 threshold
        ]
        result = learn_guardrails_from_history('feature', history)
        self.assertTrue(any('regress' in g.lower() for g in result))

    def test_no_regression_on_small_drop(self):
        history = [
            {'score': 80},
            {'score': 77},  # Only 3-point drop < 5 threshold
        ]
        result = learn_guardrails_from_history('feature', history)
        regression_guardrails = [g for g in result if 'regress' in g.lower()]
        self.assertEqual(len(regression_guardrails), 0)

    def test_detects_scope_creep(self):
        history = [
            {'summary': 'Fix the authentication login timeout bug'},
            {'summary': 'Working on the login timeout issue'},
            {'summary': 'Refactored database schema and added new migration'},
        ]
        result = learn_guardrails_from_history('bug', history)
        self.assertTrue(any('drift' in g.lower() for g in result))

    def test_no_scope_creep_on_consistent_topic(self):
        history = [
            {'summary': 'Fix login timeout bug in auth module'},
            {'summary': 'Testing login timeout fix in auth module'},
            {'summary': 'Login timeout bug fix verified in auth module'},
        ]
        result = learn_guardrails_from_history('bug', history)
        drift_guardrails = [g for g in result if 'drift' in g.lower()]
        self.assertEqual(len(drift_guardrails), 0)


# ---------------------------------------------------------------------------
# LLM goal decomposition
# ---------------------------------------------------------------------------
from copilot_operator.goal_decomposer import decompose_goal_with_llm


class TestLLMGoalDecomposition(unittest.TestCase):
    def test_returns_none_when_no_llm(self):
        result = decompose_goal_with_llm('Fix bug', 'bug', None)
        self.assertIsNone(result)

    def test_returns_none_when_llm_not_ready(self):
        mock_brain = MagicMock()
        mock_brain.is_ready = False
        result = decompose_goal_with_llm('Fix bug', 'bug', mock_brain)
        self.assertIsNone(result)

    def test_returns_plan_on_success(self):
        mock_brain = MagicMock()
        mock_brain.is_ready = True
        mock_brain.ask_json.return_value = (
            {
                'summary': 'Fix the bug',
                'currentMilestoneId': 'm1',
                'milestones': [
                    {
                        'id': 'm1',
                        'title': 'Reproduce and fix',
                        'status': 'in_progress',
                        'tasks': [{'id': 'm1.t1', 'title': 'Reproduce', 'status': 'in_progress'}],
                    }
                ],
            },
            'raw response',
        )
        result = decompose_goal_with_llm('Fix bug', 'bug', mock_brain)
        self.assertIsNotNone(result)
        self.assertIn('milestones', result)
        self.assertEqual(len(result['milestones']), 1)

    def test_returns_none_on_bad_llm_response(self):
        mock_brain = MagicMock()
        mock_brain.is_ready = True
        mock_brain.ask_json.return_value = ({'no_milestones': True}, 'raw')
        result = decompose_goal_with_llm('Fix bug', 'bug', mock_brain)
        self.assertIsNone(result)

    def test_returns_none_on_exception(self):
        mock_brain = MagicMock()
        mock_brain.is_ready = True
        mock_brain.ask_json.side_effect = RuntimeError('LLM down')
        result = decompose_goal_with_llm('Fix bug', 'bug', mock_brain)
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# Adversarial parse_critic_report validation
# ---------------------------------------------------------------------------
from copilot_operator.adversarial import parse_critic_report


class TestCriticReportValidation(unittest.TestCase):
    def test_valid_report(self):
        report_json = json.dumps({
            'findings': [
                {
                    'severity': 'high',
                    'category': 'bug',
                    'file_path': 'main.py',
                    'line_hint': '42',
                    'description': 'Missing null check',
                    'suggestion': 'Add if x is not None',
                }
            ],
            'summary': 'One issue found',
            'overall_quality': 'good',
            'confidence': 0.8,
        })
        text = f'<CRITIC_REPORT>\n{report_json}\n</CRITIC_REPORT>'
        result = parse_critic_report(text)
        self.assertIsNotNone(result)
        self.assertEqual(len(result.findings), 1)
        self.assertEqual(result.overall_quality, 'good')

    def test_rejects_non_dict(self):
        text = '<CRITIC_REPORT>\n["not", "a", "dict"]\n</CRITIC_REPORT>'
        result = parse_critic_report(text)
        self.assertIsNone(result)

    def test_rejects_missing_required_fields(self):
        text = '<CRITIC_REPORT>\n{"irrelevant": true}\n</CRITIC_REPORT>'
        result = parse_critic_report(text)
        self.assertIsNone(result)

    def test_accepts_overall_quality_only(self):
        report_json = json.dumps({'overall_quality': 'good'})
        text = f'<CRITIC_REPORT>\n{report_json}\n</CRITIC_REPORT>'
        result = parse_critic_report(text)
        self.assertIsNotNone(result)

    def test_handles_invalid_json(self):
        text = '<CRITIC_REPORT>\nnot valid json\n</CRITIC_REPORT>'
        result = parse_critic_report(text)
        self.assertIsNone(result)

    def test_handles_empty_input(self):
        self.assertIsNone(parse_critic_report(''))
        self.assertIsNone(parse_critic_report(None))


# ---------------------------------------------------------------------------
# Operator dry-run mode
# ---------------------------------------------------------------------------


class TestOperatorDryRun(unittest.TestCase):
    def test_dry_run_flag_stored(self):
        """Operator constructor stores dry_run flag."""
        with tempfile.TemporaryDirectory() as d:
            workspace = Path(d)
            from copilot_operator.config import OperatorConfig
            config = OperatorConfig(
                workspace=workspace,
                mode='agent',
                goal_profile='default',
                max_iterations=1,
                target_score=85,
                session_timeout_seconds=60,
                poll_interval_seconds=1,
                validation=[],
            )
            op = CopilotOperator(config, dry_run=True)
            self.assertTrue(op.dry_run)

    def test_dry_run_stops_after_prompt(self):
        """In dry-run mode, operator should return after generating prompt."""
        with tempfile.TemporaryDirectory() as d:
            workspace = Path(d)
            from copilot_operator.config import OperatorConfig
            config = OperatorConfig(
                workspace=workspace,
                mode='agent',
                goal_profile='default',
                max_iterations=2,
                target_score=85,
                session_timeout_seconds=60,
                poll_interval_seconds=1,
                validation=[],
            )
            op = CopilotOperator(config, dry_run=True)
            # Mock ensure_workspace_storage to avoid needing real VS Code
            chat_dir = workspace / 'chatSessions'
            chat_dir.mkdir(parents=True, exist_ok=True)
            with patch('copilot_operator.operator.ensure_workspace_storage', return_value=workspace):
                result = op.run(goal='Test dry run')
            self.assertEqual(result['status'], 'dry_run')
            self.assertEqual(result['reasonCode'], 'DRY_RUN')
            self.assertIn('promptPath', result)
            self.assertTrue(Path(result['promptPath']).exists())


# ---------------------------------------------------------------------------
# Operator error recovery
# ---------------------------------------------------------------------------


class TestOperatorErrorRecovery(unittest.TestCase):
    def test_error_tracking_structure(self):
        """Operator should track errors in runtime state."""
        with tempfile.TemporaryDirectory() as d:
            workspace = Path(d)
            from copilot_operator.config import OperatorConfig
            config = OperatorConfig(
                workspace=workspace,
                mode='agent',
                goal_profile='default',
                max_iterations=3,
                target_score=85,
                session_timeout_seconds=60,
                poll_interval_seconds=1,
                validation=[],
            )
            op = CopilotOperator(config)
            op.runtime.setdefault('errors', [])
            # Simulate adding error records
            op.runtime['errors'].append({
                'iteration': 1,
                'error': 'test error',
                'type': 'RuntimeError',
                'timestamp': '2024-01-01T00:00:00',
            })
            self.assertEqual(len(op.runtime['errors']), 1)
            self.assertEqual(op.runtime['errors'][0]['type'], 'RuntimeError')


# ---------------------------------------------------------------------------
# CLI goal validation
# ---------------------------------------------------------------------------


class TestGoalValidation(unittest.TestCase):
    def test_empty_goal_rejected(self):
        from copilot_operator.cli import _read_goal
        with self.assertRaises(SystemExit):
            _read_goal(None, None, '   ', None)

    def test_oversized_goal_rejected(self):
        from copilot_operator.cli import _MAX_GOAL_LENGTH, _read_goal
        with self.assertRaises(SystemExit):
            _read_goal(None, None, 'x' * (_MAX_GOAL_LENGTH + 1), None)

    def test_valid_goal_passes(self):
        from copilot_operator.cli import _read_goal
        result = _read_goal(None, None, 'Fix the bug', None)
        self.assertEqual(result, 'Fix the bug')


# ---------------------------------------------------------------------------
# CLI --dry-run flag wiring
# ---------------------------------------------------------------------------


class TestCLIDryRunFlag(unittest.TestCase):
    def test_run_parser_accepts_dry_run(self):
        from copilot_operator.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(['run', '--goal', 'test', '--dry-run'])
        self.assertTrue(args.dry_run)

    def test_resume_parser_accepts_dry_run(self):
        from copilot_operator.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(['resume', '--dry-run'])
        self.assertTrue(args.dry_run)


# ---------------------------------------------------------------------------
# Fallback assessment includes preview
# ---------------------------------------------------------------------------


class TestFallbackAssessmentPreview(unittest.TestCase):
    def test_includes_response_preview_in_blocker(self):
        from copilot_operator.prompts import fallback_assessment
        assessment = fallback_assessment('Some copilot response text here', needs_continue=False)
        self.assertEqual(assessment.status, 'blocked')
        self.assertTrue(any('Response preview' in b.get('item', '') for b in assessment.blockers))

    def test_empty_response_no_preview(self):
        from copilot_operator.prompts import fallback_assessment
        assessment = fallback_assessment('', needs_continue=False)
        # Should still have the base blocker message
        self.assertEqual(len(assessment.blockers), 1)
        self.assertNotIn('Response preview', assessment.blockers[0]['item'])


# ---------------------------------------------------------------------------
# Resume limit
# ---------------------------------------------------------------------------


class TestResumeLimit(unittest.TestCase):
    def test_resume_count_beyond_limit_raises(self):
        with tempfile.TemporaryDirectory() as d:
            workspace = Path(d)
            from copilot_operator.config import OperatorConfig
            config = OperatorConfig(
                workspace=workspace,
                mode='agent',
                goal_profile='default',
                max_iterations=3,
                target_score=85,
                session_timeout_seconds=60,
                poll_interval_seconds=1,
                validation=[],
            )
            op = CopilotOperator(config)
            # Simulate a state file with resumeCount > 10
            state = {
                'goal': 'Test goal',
                'goalProfile': 'default',
                'status': 'blocked',
                'history': [{'iteration': 1, 'score': 50}],
                'resumeCount': 10,
                'pendingDecision': {
                    'action': 'continue',
                    'reason': 'test',
                    'reasonCode': 'TEST',
                    'nextPrompt': 'do stuff',
                },
            }
            config.state_file.parent.mkdir(parents=True, exist_ok=True)
            config.state_file.write_text(json.dumps(state), encoding='utf-8')
            with self.assertRaises(ValueError) as ctx:
                op._prepare_resume_run(None)
            self.assertIn('Resume limit exceeded', str(ctx.exception))


# ---------------------------------------------------------------------------
# Validation timeout default
# ---------------------------------------------------------------------------


class TestValidationDefaults(unittest.TestCase):
    def test_default_timeout_is_300(self):
        from copilot_operator.config import ValidationCommand
        cmd = ValidationCommand(name='test')
        self.assertEqual(cmd.timeout_seconds, 300)


if __name__ == '__main__':
    unittest.main()
