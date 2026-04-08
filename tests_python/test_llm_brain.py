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
