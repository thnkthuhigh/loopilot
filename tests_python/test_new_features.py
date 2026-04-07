"""Tests for repo_map, live progress, cost tracking, fix-issue CLI, auto-PR."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

# ---------------------------------------------------------------------------
# Repo map — symbol extraction
# ---------------------------------------------------------------------------
from copilot_operator.repo_map import (
    FileSymbols,
    RepoMap,
    _parse_python,
    _parse_with_regex,
    _score_file,
    build_repo_map,
    get_repo_map_stats,
    render_repo_map_for_prompt,
)


class TestParsePython(unittest.TestCase):
    def test_extracts_classes_and_functions(self):
        with tempfile.NamedTemporaryFile(suffix='.py', mode='w', delete=False, encoding='utf-8') as f:
            f.write('class Foo:\n    def bar(self): pass\ndef baz(): pass\n')
            path = Path(f.name)
        try:
            sym = _parse_python(path)
            self.assertIn('Foo', sym.classes)
            self.assertIn('bar', sym.functions)
            self.assertIn('baz', sym.functions)
        finally:
            path.unlink(missing_ok=True)

    def test_handles_syntax_error_gracefully(self):
        with tempfile.NamedTemporaryFile(suffix='.py', mode='w', delete=False, encoding='utf-8') as f:
            f.write('def broken(:')
            path = Path(f.name)
        try:
            sym = _parse_python(path)
            self.assertEqual(sym.classes, [])
            self.assertEqual(sym.functions, [])
        finally:
            path.unlink(missing_ok=True)

    def test_counts_lines(self):
        with tempfile.NamedTemporaryFile(suffix='.py', mode='w', delete=False, encoding='utf-8') as f:
            f.write('a = 1\nb = 2\nc = 3\n')
            path = Path(f.name)
        try:
            sym = _parse_python(path)
            # 'a = 1\nb = 2\nc = 3\n' → 3 newlines → count = 4
            self.assertEqual(sym.line_count, 4)
        finally:
            path.unlink(missing_ok=True)


class TestParseRegex(unittest.TestCase):
    def test_typescript_extracts_class_and_function(self):
        with tempfile.NamedTemporaryFile(suffix='.ts', mode='w', delete=False, encoding='utf-8') as f:
            f.write('export class UserService {}\nexport function login() {}\n')
            path = Path(f.name)
        try:
            sym = _parse_with_regex(path, 'typescript')
            self.assertIn('UserService', sym.classes)
            self.assertIn('login', sym.functions)
        finally:
            path.unlink(missing_ok=True)

    def test_go_extracts_struct_and_func(self):
        with tempfile.NamedTemporaryFile(suffix='.go', mode='w', delete=False, encoding='utf-8') as f:
            f.write('type User struct {}\nfunc GetUser(id int) User {}\n')
            path = Path(f.name)
        try:
            sym = _parse_with_regex(path, 'go')
            self.assertIn('User', sym.classes)
            self.assertIn('GetUser', sym.functions)
        finally:
            path.unlink(missing_ok=True)

    def test_rust_extracts_struct_and_fn(self):
        with tempfile.NamedTemporaryFile(suffix='.rs', mode='w', delete=False, encoding='utf-8') as f:
            f.write('pub struct Config {}\npub fn run() {}\n')
            path = Path(f.name)
        try:
            sym = _parse_with_regex(path, 'rust')
            self.assertIn('Config', sym.classes)
            self.assertIn('run', sym.functions)
        finally:
            path.unlink(missing_ok=True)


class TestBuildRepoMap(unittest.TestCase):
    def test_builds_map_from_workspace(self):
        with tempfile.TemporaryDirectory() as d:
            workspace = Path(d)
            (workspace / 'main.py').write_text(
                'class App:\n    pass\ndef run(): pass\n', encoding='utf-8'
            )
            (workspace / 'utils.py').write_text(
                'def helper(): pass\n', encoding='utf-8'
            )
            repo_map = build_repo_map(workspace)
            self.assertGreater(len(repo_map.files), 0)
            paths = [f.path for f in repo_map.files]
            self.assertTrue(any('main.py' in p for p in paths))

    def test_ignores_node_modules(self):
        with tempfile.TemporaryDirectory() as d:
            workspace = Path(d)
            nm = workspace / 'node_modules' / 'pkg'
            nm.mkdir(parents=True)
            (nm / 'index.js').write_text('function bad() {}', encoding='utf-8')
            (workspace / 'index.py').write_text('def good(): pass', encoding='utf-8')
            repo_map = build_repo_map(workspace)
            paths = [f.path for f in repo_map.files]
            self.assertFalse(any('node_modules' in p for p in paths))
            self.assertTrue(any('index.py' in p for p in paths))

    def test_ranks_relevant_files_first(self):
        with tempfile.TemporaryDirectory() as d:
            workspace = Path(d)
            (workspace / 'auth.py').write_text(
                'def authenticate_user(): pass\ndef login(): pass\n', encoding='utf-8'
            )
            (workspace / 'utils.py').write_text(
                'def format_date(): pass\n', encoding='utf-8'
            )
            repo_map = build_repo_map(workspace, goal='Fix the user authentication login')
            if len(repo_map.files) >= 2:
                self.assertIn('auth', repo_map.files[0].path)

    def test_respects_max_chars(self):
        with tempfile.TemporaryDirectory() as d:
            workspace = Path(d)
            for i in range(20):
                (workspace / f'module{i}.py').write_text(
                    f'class Class{i}:\n    pass\ndef func{i}(): pass\n', encoding='utf-8'
                )
            repo_map = build_repo_map(workspace, max_chars=500)
            rendered = render_repo_map_for_prompt(repo_map, max_chars=500)
            self.assertLessEqual(len(rendered), 600)  # small slack for header


class TestRenderRepoMap(unittest.TestCase):
    def test_renders_empty_map_as_empty_string(self):
        repo_map = RepoMap()
        result = render_repo_map_for_prompt(repo_map)
        self.assertEqual(result, '')

    def test_renders_symbols_compactly(self):
        sym = FileSymbols(
            path='src/app.py',
            language='python',
            classes=['App', 'Config'],
            functions=['run', 'stop'],
            line_count=50,
        )
        repo_map = RepoMap(files=[sym])
        result = render_repo_map_for_prompt(repo_map)
        self.assertIn('src/app.py', result)
        self.assertIn('App', result)
        self.assertIn('run', result)
        self.assertIn('50L', result)

    def test_stats_returns_correct_counts(self):
        sym = FileSymbols(
            path='a.py', language='python',
            classes=['A'], functions=['b', 'c'],
        )
        repo_map = RepoMap(files=[sym], total_files_scanned=5, skipped_files=2)
        stats = get_repo_map_stats(repo_map)
        self.assertEqual(stats['files_in_map'], 1)
        self.assertEqual(stats['total_symbols'], 3)
        self.assertEqual(stats['skipped'], 2)
        self.assertEqual(stats['languages'], {'python': 1})


class TestScoreFile(unittest.TestCase):
    def test_keyword_match_increases_score(self):
        sym = FileSymbols(path='auth/login.py', language='python',
                          functions=['authenticate', 'login'])
        kw = frozenset({'login', 'auth'})
        score = _score_file(sym, kw)
        self.assertGreater(score, 0)

    def test_no_keywords_gives_base_score(self):
        sym = FileSymbols(path='utils.py', language='python',
                          classes=['Helper'], functions=['format'])
        score_with = _score_file(sym, frozenset({'login'}))
        score_without = _score_file(sym, frozenset())
        self.assertGreaterEqual(score_with, score_without)


# ---------------------------------------------------------------------------
# LLM cost tracking
# ---------------------------------------------------------------------------
from copilot_operator.llm_brain import estimate_cost_usd


class TestCostEstimation(unittest.TestCase):
    def test_known_model_returns_positive_cost(self):
        cost = estimate_cost_usd('gpt-4o', 1000)
        self.assertGreater(cost, 0.0)

    def test_unknown_model_returns_zero(self):
        cost = estimate_cost_usd('mystery-model-xyz', 1000)
        self.assertEqual(cost, 0.0)

    def test_local_model_returns_zero(self):
        cost = estimate_cost_usd('default', 5000)
        self.assertEqual(cost, 0.0)

    def test_more_tokens_means_more_cost(self):
        cost_small = estimate_cost_usd('gpt-4o', 100)
        cost_large = estimate_cost_usd('gpt-4o', 10000)
        self.assertGreater(cost_large, cost_small)

    def test_claude_cost(self):
        cost = estimate_cost_usd('claude-sonnet-4-20250514', 2000)
        self.assertGreater(cost, 0.0)

    def test_llm_brain_tracks_cost(self):
        from unittest.mock import patch

        from copilot_operator.llm_brain import LLMBrain, LLMConfig, LLMResponse
        config = LLMConfig(
            provider='openai', model='gpt-4o', api_key='test',
            enabled=True, max_tokens=100,
        )
        brain = LLMBrain(config)
        fake_response = LLMResponse(content='hello', success=True, tokens_used=500, model='gpt-4o')
        with patch('copilot_operator.llm_brain._call_llm', return_value=fake_response):
            brain.ask('test')
        self.assertEqual(brain.stats['total_tokens'], 500)
        self.assertGreater(brain.stats['estimated_cost_usd'], 0.0)
        self.assertEqual(brain.stats['calls'], 1)


# ---------------------------------------------------------------------------
# Live flag wiring in CLI
# ---------------------------------------------------------------------------
from copilot_operator.cli import build_parser


class TestLiveFlagCLI(unittest.TestCase):
    def test_run_parser_accepts_live(self):
        parser = build_parser()
        args = parser.parse_args(['run', '--goal', 'test', '--live'])
        self.assertTrue(args.live)

    def test_resume_parser_accepts_live(self):
        parser = build_parser()
        args = parser.parse_args(['resume', '--live'])
        self.assertTrue(args.live)


# ---------------------------------------------------------------------------
# fix-issue command wiring
# ---------------------------------------------------------------------------


class TestFixIssueCommand(unittest.TestCase):
    def test_fix_issue_registered_in_parser(self):
        parser = build_parser()
        # Should parse without error
        args = parser.parse_args(['fix-issue', '--issue', '42', '--repo', 'owner/repo'])
        self.assertEqual(args.issue, 42)
        self.assertEqual(args.repo, 'owner/repo')

    def test_fix_issue_default_profile_is_bug(self):
        parser = build_parser()
        args = parser.parse_args(['fix-issue', '--issue', '1', '--repo', 'a/b'])
        self.assertEqual(args.goal_profile, 'bug')

    def test_fix_issue_dry_run_flag(self):
        parser = build_parser()
        args = parser.parse_args(['fix-issue', '--issue', '5', '--dry-run'])
        self.assertTrue(args.dry_run)


# ---------------------------------------------------------------------------
# Auto-PR config field
# ---------------------------------------------------------------------------
from copilot_operator.config import OperatorConfig


class TestAutoPRConfig(unittest.TestCase):
    def test_auto_create_pr_defaults_false(self):
        with tempfile.TemporaryDirectory() as d:
            config = OperatorConfig(
                workspace=Path(d),
                mode='agent',
                goal_profile='default',
                max_iterations=1,
                target_score=85,
                session_timeout_seconds=60,
                poll_interval_seconds=1,
                validation=[],
            )
            self.assertFalse(config.auto_create_pr)

    def test_auto_create_pr_set_true(self):
        with tempfile.TemporaryDirectory() as d:
            config = OperatorConfig(
                workspace=Path(d),
                mode='agent',
                goal_profile='default',
                max_iterations=1,
                target_score=85,
                session_timeout_seconds=60,
                poll_interval_seconds=1,
                validation=[],
                auto_create_pr=True,
            )
            self.assertTrue(config.auto_create_pr)


# ---------------------------------------------------------------------------
# Repo map wires into prompt context
# ---------------------------------------------------------------------------
from copilot_operator.prompts import PromptContext, build_initial_prompt


class TestRepoMapInPrompt(unittest.TestCase):
    def test_repo_map_text_appears_in_prompt(self):
        ctx = PromptContext(
            recent_history='',
            validation_snapshot='',
            repo_profile_text='',
            workspace_insight_text='',
            goal_profile_text='',
            plan_text='',
            repo_map_text='src/app.py (42L): classes: App | fns: run, stop',
        )
        prompt = build_initial_prompt('Fix the bug', 85, ctx)
        self.assertIn('Codebase map', prompt)
        self.assertIn('src/app.py', prompt)

    def test_empty_repo_map_does_not_appear_in_prompt(self):
        ctx = PromptContext(
            recent_history='',
            validation_snapshot='',
            repo_profile_text='',
            workspace_insight_text='',
            goal_profile_text='',
            plan_text='',
            repo_map_text='',
        )
        prompt = build_initial_prompt('Fix the bug', 85, ctx)
        self.assertNotIn('Codebase map', prompt)


# ---------------------------------------------------------------------------
# Protected paths enforcement
# ---------------------------------------------------------------------------
import subprocess as _subprocess

from copilot_operator.repo_ops import check_protected_paths


def _git_available() -> bool:
    try:
        _subprocess.run(['git', '--version'], capture_output=True, check=True, timeout=5)
        return True
    except Exception:
        return False


_GIT = _git_available()


def _init_git_repo(d: str) -> None:
    for cmd in [
        ['git', 'init'],
        ['git', 'config', 'user.email', 'test@test.com'],
        ['git', 'config', 'user.name', 'Test'],
        ['git', 'commit', '--allow-empty', '-m', 'init'],
    ]:
        _subprocess.run(cmd, cwd=d, capture_output=True)


class TestCheckProtectedPaths(unittest.TestCase):
    def test_empty_protected_list_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(check_protected_paths(Path(d), []), [])

    def test_non_git_workspace_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(check_protected_paths(Path(d), ['secret.env', 'config/']), [])

    @unittest.skipUnless(_GIT, 'git not available')
    def test_clean_repo_no_violations(self):
        with tempfile.TemporaryDirectory() as d:
            wp = Path(d)
            _init_git_repo(d)
            (wp / 'file.py').write_text('x = 1')
            _subprocess.run(['git', 'add', '.'], cwd=d, capture_output=True)
            _subprocess.run(['git', 'commit', '-m', 'add file'], cwd=d, capture_output=True)
            self.assertEqual(check_protected_paths(wp, ['secret.env']), [])

    @unittest.skipUnless(_GIT, 'git not available')
    def test_exact_file_violation(self):
        with tempfile.TemporaryDirectory() as d:
            wp = Path(d)
            _init_git_repo(d)
            (wp / 'secret.env').write_text('KEY=value')
            result = check_protected_paths(wp, ['secret.env'])
            self.assertIn('secret.env', result)

    @unittest.skipUnless(_GIT, 'git not available')
    def test_directory_prefix_violation(self):
        with tempfile.TemporaryDirectory() as d:
            wp = Path(d)
            _init_git_repo(d)
            (wp / 'config').mkdir()
            (wp / 'config' / 'prod.yml').write_text('env: production')
            result = check_protected_paths(wp, ['config'])
            self.assertTrue(len(result) > 0)
            self.assertTrue(any('prod.yml' in v for v in result))

    @unittest.skipUnless(_GIT, 'git not available')
    def test_no_match_different_file_no_violation(self):
        with tempfile.TemporaryDirectory() as d:
            wp = Path(d)
            _init_git_repo(d)
            (wp / 'main.py').write_text('print("hello")')
            self.assertEqual(check_protected_paths(wp, ['secret.env', 'config/']), [])


# ---------------------------------------------------------------------------
# _merge_vscode_settings
# ---------------------------------------------------------------------------
from copilot_operator.bootstrap import _PREFERRED_MODELS, _merge_vscode_settings


class TestMergeVscodeSettings(unittest.TestCase):
    def _run(self, workspace: Path) -> dict:
        import json
        _merge_vscode_settings(workspace)
        settings_path = workspace / '.vscode' / 'settings.json'
        return json.loads(settings_path.read_text(encoding='utf-8'))

    def test_creates_settings_file_if_missing(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)
            settings = self._run(ws)
            self.assertTrue(settings.get('chat.tools.global.autoApprove'))
            self.assertIn(settings.get('github.copilot.chat.preferredModel'), _PREFERRED_MODELS)

    def test_merges_into_existing_file(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)
            vscode = ws / '.vscode'
            vscode.mkdir()
            (vscode / 'settings.json').write_text(
                json.dumps({'editor.fontSize': 14}), encoding='utf-8'
            )
            settings = self._run(ws)
            self.assertEqual(settings.get('editor.fontSize'), 14)
            self.assertTrue(settings.get('chat.tools.global.autoApprove'))

    def test_idempotent_when_already_set(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)
            vscode = ws / '.vscode'
            vscode.mkdir()
            initial = {
                'chat.tools.global.autoApprove': True,
                'github.copilot.chat.preferredModel': _PREFERRED_MODELS[0],
            }
            path = vscode / 'settings.json'
            path.write_text(json.dumps(initial), encoding='utf-8')
            mtime_before = path.stat().st_mtime
            _merge_vscode_settings(ws)
            # File should not be rewritten when nothing changed
            self.assertEqual(path.stat().st_mtime, mtime_before)

    def test_overwrites_unknown_model(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)
            vscode = ws / '.vscode'
            vscode.mkdir()
            (vscode / 'settings.json').write_text(
                json.dumps({'github.copilot.chat.preferredModel': 'some-random-model'}),
                encoding='utf-8',
            )
            settings = self._run(ws)
            self.assertIn(settings.get('github.copilot.chat.preferredModel'), _PREFERRED_MODELS)

    def test_handles_corrupt_json_gracefully(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)
            vscode = ws / '.vscode'
            vscode.mkdir()
            (vscode / 'settings.json').write_text('{corrupt', encoding='utf-8')
            # Should not raise; should write fresh settings
            settings = self._run(ws)
            self.assertTrue(settings.get('chat.tools.global.autoApprove'))


if __name__ == '__main__':
    unittest.main()
