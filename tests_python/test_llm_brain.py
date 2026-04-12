"""Tests for LLM brain, config dotenv loading, CLI brain/version commands,
and public API surface.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# ---------------------------------------------------------------------------
# LLM Brain core
# ---------------------------------------------------------------------------
from copilot_operator.llm_brain import (
    LLMBrain,
    LLMConfig,
    LLMResponse,
    _extract_json,
    load_llm_config_from_dict,
    load_llm_config_from_env,
    render_brain_status,
)


class TestLLMConfig(unittest.TestCase):
    def test_default_not_ready(self):
        config = LLMConfig()
        self.assertFalse(config.is_ready)

    def test_configured_openai_ready(self):
        config = LLMConfig(provider='openai', api_key='sk-test', enabled=True)
        self.assertTrue(config.is_ready)

    def test_local_without_key_is_ready(self):
        config = LLMConfig(provider='local', enabled=True)
        self.assertTrue(config.is_ready)

    def test_unknown_provider_not_ready(self):
        config = LLMConfig(provider='unknown_xyz', api_key='key', enabled=True)
        self.assertFalse(config.is_ready)

    def test_disabled_is_not_ready(self):
        config = LLMConfig(provider='openai', api_key='sk-test', enabled=False)
        self.assertFalse(config.is_ready)


class TestLoadFromEnv(unittest.TestCase):
    def test_auto_detect_openai(self):
        with patch.dict('os.environ', {'OPENAI_API_KEY': 'sk-test123'}, clear=False):
            config = load_llm_config_from_env()
            self.assertEqual(config.provider, 'openai')
            self.assertTrue(config.enabled)
            self.assertEqual(config.api_key, 'sk-test123')

    def test_auto_detect_anthropic(self):
        with patch.dict('os.environ', {'ANTHROPIC_API_KEY': 'sk-ant-test'}, clear=False):
            config = load_llm_config_from_env()
            self.assertEqual(config.provider, 'anthropic')

    def test_explicit_provider(self):
        with patch.dict('os.environ', {'OPENAI_API_KEY': 'sk-test'}, clear=False):
            config = load_llm_config_from_env(provider='openai', model='gpt-4')
            self.assertEqual(config.model, 'gpt-4')

    def test_no_keys_returns_disabled(self):
        env = {k: '' for k in ['OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'GEMINI_API_KEY', 'XAI_API_KEY', 'LOCAL_LLM_API_KEY', 'COPILOT_OPERATOR_LLM_PROVIDER']}
        with patch.dict('os.environ', env, clear=False):
            config = load_llm_config_from_env()
            self.assertFalse(config.enabled)


class TestLoadFromDict(unittest.TestCase):
    def test_basic_dict(self):
        with patch.dict('os.environ', {'OPENAI_API_KEY': 'sk-yaml-test'}, clear=False):
            config = load_llm_config_from_dict({
                'provider': 'openai',
                'model': 'gpt-4o-mini',
                'maxTokens': 2048,
                'temperature': 0.5,
            })
            self.assertEqual(config.provider, 'openai')
            self.assertEqual(config.model, 'gpt-4o-mini')
            self.assertEqual(config.max_tokens, 2048)
            self.assertEqual(config.temperature, 0.5)
            self.assertEqual(config.api_key, 'sk-yaml-test')
            self.assertTrue(config.enabled)

    def test_empty_dict(self):
        config = load_llm_config_from_dict({})
        self.assertFalse(config.enabled)

    def test_key_never_from_dict(self):
        """API keys must come from env, NEVER from config dict."""
        config = load_llm_config_from_dict({
            'provider': 'openai',
            'api_key': 'SHOULD_NOT_BE_USED',
        })
        # The key should come from env, not from dict
        self.assertNotEqual(config.api_key, 'SHOULD_NOT_BE_USED')


class TestExtractJson(unittest.TestCase):
    def test_extract_from_code_fence(self):
        text = 'Here is the analysis:\n```json\n{"action": "retry", "reason": "test"}\n```'
        result = _extract_json(text)
        self.assertIsNotNone(result)
        self.assertEqual(result['action'], 'retry')

    def test_extract_raw_json(self):
        text = 'The answer is {"score": 85, "complete": true}'
        result = _extract_json(text)
        self.assertIsNotNone(result)
        self.assertEqual(result['score'], 85)

    def test_no_json_returns_none(self):
        self.assertIsNone(_extract_json('just plain text'))

    def test_extract_nested_json(self):
        text = '{"outer": {"inner": "value"}, "list": [1, 2]}'
        result = _extract_json(text)
        self.assertIsNotNone(result)
        self.assertEqual(result['outer']['inner'], 'value')


class TestLLMBrain(unittest.TestCase):
    def test_not_ready_returns_error(self):
        brain = LLMBrain(LLMConfig())
        self.assertFalse(brain.is_ready)

    def test_stats_tracking(self):
        brain = LLMBrain(LLMConfig(provider='openai', api_key='test', enabled=True))
        stats = brain.stats
        self.assertEqual(stats['provider'], 'openai')
        self.assertEqual(stats['calls'], 0)
        self.assertTrue(stats['ready'])

    def test_from_env_classmethod(self):
        with patch.dict('os.environ', {'OPENAI_API_KEY': 'sk-cls-test'}, clear=False):
            brain = LLMBrain.from_env(provider='openai')
            self.assertTrue(brain.is_ready)

    @patch('copilot_operator.llm_brain._call_llm')
    def test_ask_calls_llm(self, mock_call):
        mock_call.return_value = LLMResponse(content='Hello!', success=True, tokens_used=10)
        brain = LLMBrain(LLMConfig(provider='openai', api_key='test', enabled=True))
        response = brain.ask('Hi')
        self.assertTrue(response.success)
        self.assertEqual(response.content, 'Hello!')
        self.assertEqual(brain._call_count, 1)
        self.assertEqual(brain._total_tokens, 10)

    @patch('copilot_operator.llm_brain._call_llm')
    def test_ask_json_parses(self, mock_call):
        mock_call.return_value = LLMResponse(content='{"action": "fix", "reason": "bug"}', success=True)
        brain = LLMBrain(LLMConfig(provider='openai', api_key='test', enabled=True))
        result, response = brain.ask_json('What to do?')
        self.assertIsNotNone(result)
        self.assertEqual(result['action'], 'fix')

    @patch('copilot_operator.llm_brain._call_llm')
    def test_analyse_failure(self, mock_call):
        mock_call.return_value = LLMResponse(
            content='{"root_cause": "test gap", "strategy": "add tests", "suggested_prompt": "fix tests", "risk_level": "medium", "confidence": 0.8}',
            success=True,
        )
        brain = LLMBrain(LLMConfig(provider='openai', api_key='test', enabled=True))
        result, _ = brain.analyse_failure('Fix bug', 'Iter 1: failed', 'Test error')
        self.assertIsNotNone(result)
        self.assertEqual(result['root_cause'], 'test gap')


class TestRenderBrainStatus(unittest.TestCase):
    def test_disabled(self):
        text = render_brain_status(None)
        self.assertIn('disabled', text)

    def test_enabled(self):
        brain = LLMBrain(LLMConfig(provider='openai', model='gpt-4o', api_key='test', enabled=True))
        text = render_brain_status(brain)
        self.assertIn('openai', text)
        self.assertIn('gpt-4o', text)


# ---------------------------------------------------------------------------
# Config dotenv loading
# ---------------------------------------------------------------------------

from copilot_operator.config import _load_dotenv


class TestDotenvLoading(unittest.TestCase):
    def test_load_dotenv_basic(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / '.env'
            env_file.write_text('TEST_COPILOT_VAR=hello_world\n', encoding='utf-8')
            # Clear the var first
            with patch.dict('os.environ', {}, clear=False):
                os.environ.pop('TEST_COPILOT_VAR', None)
                _load_dotenv(Path(tmp))
                self.assertEqual(os.environ.get('TEST_COPILOT_VAR'), 'hello_world')
                os.environ.pop('TEST_COPILOT_VAR', None)

    def test_load_dotenv_skips_comments(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / '.env'
            env_file.write_text('# comment\nTEST_VAR2=value\n', encoding='utf-8')
            with patch.dict('os.environ', {}, clear=False):
                os.environ.pop('TEST_VAR2', None)
                _load_dotenv(Path(tmp))
                self.assertEqual(os.environ.get('TEST_VAR2'), 'value')
                os.environ.pop('TEST_VAR2', None)

    def test_load_dotenv_no_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / '.env'
            env_file.write_text('PATH=should_not_override\n', encoding='utf-8')
            original_path = os.environ.get('PATH', '')
            _load_dotenv(Path(tmp))
            self.assertEqual(os.environ.get('PATH'), original_path)

    def test_no_env_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            _load_dotenv(Path(tmp))  # Should not raise


# ---------------------------------------------------------------------------
# Deep Brain — autonomous thinking methods
# ---------------------------------------------------------------------------


class TestDeepBrainCompose(unittest.TestCase):
    """Tests for LLMBrain.compose_prompt()."""

    def _make_brain(self):
        config = LLMConfig(provider='xai', api_key='test', enabled=True)
        return LLMBrain(config)

    def test_compose_prompt_builds_prompt(self):
        brain = self._make_brain()
        compose_json = '{"thinking":"test analysis","strategy":"focus on tests","prompt":"Create unit tests for config.py","focus_files":["config.py"],"expected_outcome":"5 tests pass"}'
        resp = LLMResponse(content=compose_json, model='test', tokens_used=100, success=True)
        with patch.object(brain, 'ask', return_value=resp):
            result, response = brain.compose_prompt(
                goal='Add config module',
                iteration=2,
                history_summary='  Iter 1: score=70',
                validation_summary='  [PASS] lint',
                current_files='config.py\ntests/',
                score=70,
                blockers='',
                repo_context='Python project',
            )
        self.assertIsNotNone(result)
        self.assertEqual(result['strategy'], 'focus on tests')
        self.assertIn('config.py', result['prompt'])

    def test_compose_prompt_returns_none_on_failure(self):
        brain = self._make_brain()
        resp = LLMResponse(content='', model='test', tokens_used=0, success=False, error='timeout')
        with patch.object(brain, 'ask', return_value=resp):
            result, response = brain.compose_prompt(
                goal='test', iteration=1, history_summary='', validation_summary='',
                current_files='', score=None, blockers='', repo_context='',
            )
        self.assertIsNone(result)


class TestDeepBrainDecision(unittest.TestCase):
    """Tests for LLMBrain.make_decision()."""

    def _make_brain(self):
        config = LLMConfig(provider='xai', api_key='test', enabled=True)
        return LLMBrain(config)

    def test_make_decision_stop(self):
        brain = self._make_brain()
        decision_json = '{"thinking":"goal achieved","action":"stop","reason":"tests pass, score high","next_prompt":"","strategy_change":null,"confidence":0.95}'
        resp = LLMResponse(content=decision_json, model='test', tokens_used=80, success=True)
        with patch.object(brain, 'ask', return_value=resp):
            result, response = brain.make_decision(
                goal='Fix bug', iteration=2, max_iterations=5, score=95,
                target_score=85, summary='Bug fixed', history_summary='',
                validation_summary='[PASS] tests', blockers='', diff_summary='',
            )
        self.assertIsNotNone(result)
        self.assertEqual(result['action'], 'stop')
        self.assertGreaterEqual(result['confidence'], 0.9)

    def test_make_decision_continue(self):
        brain = self._make_brain()
        decision_json = '{"thinking":"tests failing","action":"continue","reason":"fix test","next_prompt":"Fix the failing test in test_foo.py","strategy_change":null,"confidence":0.88}'
        resp = LLMResponse(content=decision_json, model='test', tokens_used=80, success=True)
        with patch.object(brain, 'ask', return_value=resp):
            result, response = brain.make_decision(
                goal='Add feature', iteration=1, max_iterations=5, score=60,
                target_score=85, summary='WIP', history_summary='',
                validation_summary='[FAIL] tests', blockers='test failing', diff_summary='',
            )
        self.assertIsNotNone(result)
        self.assertEqual(result['action'], 'continue')
        self.assertIn('test_foo', result['next_prompt'])


class TestDeepBrainReview(unittest.TestCase):
    """Tests for LLMBrain.review_iteration()."""

    def _make_brain(self):
        config = LLMConfig(provider='xai', api_key='test', enabled=True)
        return LLMBrain(config)

    def test_review_good(self):
        brain = self._make_brain()
        review_json = '{"verdict":"good","issues":[],"on_track":true,"correction":null}'
        resp = LLMResponse(content=review_json, model='test', tokens_used=50, success=True)
        with patch.object(brain, 'ask', return_value=resp):
            result, response = brain.review_iteration(
                goal='Fix bug', diff_text='+def fix():\n+  return True',
                validation_summary='[PASS] tests', score=90, iteration=1,
            )
        self.assertIsNotNone(result)
        self.assertEqual(result['verdict'], 'good')
        self.assertTrue(result['on_track'])

    def test_review_bad_with_correction(self):
        brain = self._make_brain()
        review_json = '{"verdict":"concerning","issues":["hardcoded path","missing test"],"on_track":false,"correction":"Replace hardcoded path with os.path.join and add test"}'
        resp = LLMResponse(content=review_json, model='test', tokens_used=60, success=True)
        with patch.object(brain, 'ask', return_value=resp):
            result, response = brain.review_iteration(
                goal='Add config', diff_text='+PATH = "C:\\temp"',
                validation_summary='[PASS] lint', score=50, iteration=2,
            )
        self.assertIsNotNone(result)
        self.assertEqual(result['verdict'], 'concerning')
        self.assertFalse(result['on_track'])
        self.assertIn('hardcoded', result['correction'])


# ---------------------------------------------------------------------------
# Operator Deep Brain integration
# ---------------------------------------------------------------------------

from copilot_operator.operator import CopilotOperator, Decision


class TestOperatorDeepBrain(unittest.TestCase):
    """Tests for operator's deep brain integration methods."""

    def _make_operator(self):
        config = _make_test_config()
        op = CopilotOperator(config, dry_run=True)
        return op

    def test_format_history_for_brain(self):
        op = self._make_operator()
        history = [
            {'iteration': 1, 'score': 70, 'status': 'in_progress', 'summary': 'Started', 'blockers': []},
            {'iteration': 2, 'score': 85, 'status': 'done', 'summary': 'Finished', 'blockers': [{'item': 'test fail'}]},
        ]
        result = op._format_history_for_brain(history)
        self.assertIn('Iter 1', result)
        self.assertIn('score=70', result)
        self.assertIn('test fail', result)

    def test_format_history_empty(self):
        op = self._make_operator()
        self.assertEqual(op._format_history_for_brain([]), '')

    def test_format_validation_for_brain(self):
        op = self._make_operator()
        validations = [
            {'name': 'tests', 'status': 'pass'},
            {'name': 'lint', 'status': 'fail', 'output': 'error: unused import'},
        ]
        result = op._format_validation_for_brain(validations)
        self.assertIn('[PASS] tests', result)
        self.assertIn('[FAIL] lint', result)
        self.assertIn('unused import', result)

    def test_format_validation_empty(self):
        op = self._make_operator()
        self.assertEqual(op._format_validation_for_brain([]), 'No validations run.')

    def test_try_llm_compose_no_brain(self):
        """Without LLM brain, compose returns None (falls back to templates)."""
        op = self._make_operator()
        op._llm_brain = None
        result = op._try_llm_compose_prompt('goal', [], [], Decision('continue', 'test'), '')
        self.assertIsNone(result)

    def test_try_llm_decide_no_brain(self):
        """Without LLM brain, decision passes through unchanged."""
        op = self._make_operator()
        op._llm_brain = None
        rule_dec = Decision('continue', 'test', 'do stuff', 'WORK_REMAINS')
        from copilot_operator.prompts import Assessment
        assessment = Assessment(status='in_progress', score=70, summary='wip', blockers=[])
        result = op._try_llm_decide('goal', assessment, [], 1, rule_dec)
        self.assertEqual(result.action, 'continue')
        self.assertEqual(result.reason_code, 'WORK_REMAINS')

    def test_try_llm_decide_safety_not_overridden(self):
        """Hard safety stops (MAX_ITERATIONS) must NOT be overridden by brain."""
        op = self._make_operator()
        brain = LLMBrain(LLMConfig(provider='xai', api_key='test', enabled=True))
        op._llm_brain = brain
        safety_dec = Decision('stop', 'max iter', reason_code='MAX_ITERATIONS_REACHED')
        from copilot_operator.prompts import Assessment
        assessment = Assessment(status='in_progress', score=50, summary='wip', blockers=[])
        result = op._try_llm_decide('goal', assessment, [], 6, safety_dec)
        self.assertEqual(result.reason_code, 'MAX_ITERATIONS_REACHED')

    def test_try_llm_review_no_brain(self):
        """Without LLM brain, review returns empty string."""
        op = self._make_operator()
        op._llm_brain = None
        from copilot_operator.prompts import Assessment
        assessment = Assessment(status='done', score=90, summary='done', blockers=[])
        result = op._try_llm_review_iteration('goal', assessment, [], 1)
        self.assertEqual(result, '')

    def test_decide_blocked_with_workspace_changes(self):
        """When Copilot doesn't emit OPERATOR_STATE but made changes, prompt should say don't re-edit."""
        op = self._make_operator()
        from copilot_operator.prompts import Assessment
        assessment = Assessment(
            status='blocked', score=None, summary='response without operator state',
            blockers=[{'severity': 'high', 'item': 'no OPERATOR_STATE'}],
            done_reason='Missing OPERATOR_STATE block.',
        )
        # Mock get_diff_summary to simulate real workspace changes
        import copilot_operator.operator as op_mod
        original_fn = op_mod.get_diff_summary
        try:
            op_mod.get_diff_summary = lambda ws: ' 3 files changed, 42 insertions(+), 5 deletions(-)'
            decision = op._decide(assessment, [], 1)
            self.assertEqual(decision.action, 'continue')
            self.assertEqual(decision.reason_code, 'COPILOT_BLOCKED')
            self.assertIn('Do NOT modify any files', decision.next_prompt)
            self.assertIn('already made code changes', decision.next_prompt)
        finally:
            op_mod.get_diff_summary = original_fn

    def test_decide_blocked_no_workspace_changes(self):
        """When Copilot doesn't emit OPERATOR_STATE and no changes, use standard retry."""
        op = self._make_operator()
        from copilot_operator.prompts import Assessment
        assessment = Assessment(
            status='blocked', score=None, summary='empty',
            blockers=[{'severity': 'high', 'item': 'no OPERATOR_STATE'}],
            done_reason='Missing OPERATOR_STATE block.',
        )
        import copilot_operator.operator as op_mod
        original_fn = op_mod.get_diff_summary
        try:
            op_mod.get_diff_summary = lambda ws: ''
            decision = op._decide(assessment, [], 1)
            self.assertEqual(decision.action, 'continue')
            self.assertEqual(decision.reason_code, 'COPILOT_BLOCKED')
            self.assertIn('You must end your response with a valid OPERATOR_STATE', decision.next_prompt)
        finally:
            op_mod.get_diff_summary = original_fn


def _make_test_config():
    """Create a minimal test config."""
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    from copilot_operator.config import OperatorConfig
    return OperatorConfig(workspace=tmp, state_file=tmp / 'state.json')


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------

import os

from copilot_operator.cli import build_parser


class TestCLIBrainVersion(unittest.TestCase):
    def test_version_command(self):
        parser = build_parser()
        args = parser.parse_args(['version'])
        self.assertIsNotNone(args.handler)

    def test_brain_command(self):
        parser = build_parser()
        args = parser.parse_args(['brain'])
        self.assertIsNotNone(args.handler)

    def test_brain_with_test_flag(self):
        parser = build_parser()
        args = parser.parse_args(['brain', '--test', 'hello'])
        self.assertEqual(args.test_prompt, 'hello')


# ---------------------------------------------------------------------------
# Public API surface (__init__.py)
# ---------------------------------------------------------------------------

class TestPublicAPI(unittest.TestCase):
    def test_version_exported(self):
        import copilot_operator
        self.assertTrue(hasattr(copilot_operator, '__version__'))
        self.assertRegex(copilot_operator.__version__, r'\d+\.\d+\.\d+')

    def test_llm_brain_exported(self):
        from copilot_operator import LLMBrain, LLMConfig
        self.assertIsNotNone(LLMBrain)
        self.assertIsNotNone(LLMConfig)

    def test_config_exports(self):
        from copilot_operator import load_llm_config_from_dict, load_llm_config_from_env
        self.assertTrue(callable(load_llm_config_from_dict))
        self.assertTrue(callable(load_llm_config_from_env))


# ---------------------------------------------------------------------------
# pyproject.toml sanity
# ---------------------------------------------------------------------------

class TestPyProjectToml(unittest.TestCase):
    def test_pyproject_exists_and_valid(self):
        try:
            import tomllib
        except ImportError:
            try:
                import tomli as tomllib  # type: ignore[no-redef]
            except ImportError:
                self.skipTest('tomllib/tomli not available (Python < 3.11)')
        path = Path(__file__).parent.parent / 'pyproject.toml'
        if not path.exists():
            self.skipTest('pyproject.toml not found')
        with open(path, 'rb') as f:
            data = tomllib.load(f)
        self.assertIn('project', data)
        self.assertEqual(data['project']['name'], 'copilot-operator')
        self.assertIn('copilot-operator', data['project']['scripts'])


if __name__ == '__main__':
    unittest.main()
