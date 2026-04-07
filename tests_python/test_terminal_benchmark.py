"""Tests for terminal.py colour helpers and benchmark engine."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

# ---------------------------------------------------------------------------
# Terminal module tests
# ---------------------------------------------------------------------------
from copilot_operator.terminal import (
    bold,
    cyan,
    dim,
    green,
    header_line,
    ok,
    progress_bar,
    red,
    score_color,
    status_badge,
    warn,
    yellow,
)


class TestTerminalFunctions(unittest.TestCase):
    """All helpers must return str — even when colours are disabled (non-TTY)."""

    def test_green_returns_str(self):
        result = green('hello')
        self.assertIsInstance(result, str)
        self.assertIn('hello', result)

    def test_yellow_returns_str(self):
        self.assertIn('world', yellow('world'))

    def test_red_returns_str(self):
        self.assertIn('err', red('err'))

    def test_cyan_returns_str(self):
        self.assertIn('info', cyan('info'))

    def test_bold_returns_str(self):
        self.assertIn('BOLD', bold('BOLD'))

    def test_dim_returns_str(self):
        self.assertIn('dim', dim('dim'))

    def test_ok_contains_message(self):
        result = ok('all good')
        self.assertIn('all good', result)
        self.assertIn('ok', result)

    def test_warn_contains_message(self):
        result = warn('something off')
        self.assertIn('something off', result)
        self.assertIn('warn', result)

    def test_score_color_none(self):
        result = score_color(None)
        self.assertIn('??', result)

    def test_score_color_high(self):
        result = score_color(90)
        self.assertIn('90', result)

    def test_score_color_medium(self):
        result = score_color(70)
        self.assertIn('70', result)

    def test_score_color_low(self):
        result = score_color(30)
        self.assertIn('30', result)

    def test_status_badge_complete(self):
        result = status_badge('complete')
        self.assertIn('complete', result)

    def test_status_badge_error(self):
        result = status_badge('error')
        self.assertIn('error', result)

    def test_status_badge_unknown(self):
        result = status_badge('something_weird')
        self.assertIn('something_weird', result)


class TestProgressBar(unittest.TestCase):
    def test_contains_fraction(self):
        result = progress_bar(3, 10)
        self.assertIn('3/10', result)

    def test_contains_percentage(self):
        result = progress_bar(5, 10)
        self.assertIn('50%', result)

    def test_zero_total(self):
        result = progress_bar(0, 0)
        self.assertIn('0/?', result)

    def test_full_bar(self):
        result = progress_bar(10, 10)
        self.assertIn('100%', result)

    def test_returns_str(self):
        self.assertIsInstance(progress_bar(1, 5), str)


class TestHeaderLine(unittest.TestCase):
    def test_contains_title(self):
        result = header_line('My Section')
        self.assertIn('My Section', result)

    def test_returns_str(self):
        self.assertIsInstance(header_line('Test'), str)


# ---------------------------------------------------------------------------
# Benchmark engine tests
# ---------------------------------------------------------------------------
from copilot_operator.benchmark import (
    BenchmarkResult,
    CaseResult,
    _score_case,
    load_benchmark_file,
    render_benchmark_result,
)


class TestLoadBenchmarkFile(unittest.TestCase):
    def _write_file(self, data: dict) -> Path:
        f = tempfile.NamedTemporaryFile(
            suffix='.json', mode='w', delete=False, encoding='utf-8'
        )
        json.dump(data, f)
        f.close()
        return Path(f.name)

    def test_loads_name_and_cases(self):
        path = self._write_file({
            'name': 'My Bench',
            'cases': [
                {'id': 'c1', 'goal': 'Fix the bug', 'expected_keywords': ['bug']},
            ],
        })
        try:
            name, cases = load_benchmark_file(path)
            self.assertEqual(name, 'My Bench')
            self.assertEqual(len(cases), 1)
            self.assertEqual(cases[0].id, 'c1')
            self.assertEqual(cases[0].goal, 'Fix the bug')
            self.assertEqual(cases[0].expected_keywords, ['bug'])
        finally:
            path.unlink(missing_ok=True)

    def test_uses_filename_as_name_if_missing(self):
        path = self._write_file({'cases': [{'goal': 'Do something'}]})
        try:
            name, cases = load_benchmark_file(path)
            self.assertEqual(name, path.stem)
            self.assertEqual(len(cases), 1)
        finally:
            path.unlink(missing_ok=True)

    def test_missing_cases_key_raises(self):
        path = self._write_file({'name': 'bad'})
        try:
            with self.assertRaises(ValueError):
                load_benchmark_file(path)
        finally:
            path.unlink(missing_ok=True)

    def test_case_missing_goal_raises(self):
        path = self._write_file({'cases': [{'id': 'x'}]})
        try:
            with self.assertRaises(ValueError):
                load_benchmark_file(path)
        finally:
            path.unlink(missing_ok=True)

    def test_default_fields(self):
        path = self._write_file({'cases': [{'goal': 'My goal'}]})
        try:
            _, cases = load_benchmark_file(path)
            c = cases[0]
            self.assertEqual(c.goal_profile, 'default')
            self.assertEqual(c.max_iterations, 1)
            self.assertEqual(c.expected_keywords, [])
        finally:
            path.unlink(missing_ok=True)


class TestScoreCase(unittest.TestCase):
    def test_no_keywords_dry_run_passes(self):
        score, matched, missing = _score_case({'status': 'dry_run'}, [])
        self.assertEqual(score, 1.0)
        self.assertEqual(matched, [])
        self.assertEqual(missing, [])

    def test_no_keywords_non_dry_run_fails(self):
        score, matched, missing = _score_case({'status': 'error'}, [])
        self.assertEqual(score, 0.0)

    def test_keyword_in_status(self):
        score, matched, missing = _score_case({'status': 'authentication error'}, ['authentication'])
        self.assertEqual(score, 1.0)
        self.assertIn('authentication', matched)

    def test_keyword_in_reason(self):
        score, matched, missing = _score_case(
            {'status': 'dry_run', 'reason': 'fixed the token expiry bug'}, ['token']
        )
        self.assertIn('token', matched)
        self.assertAlmostEqual(score, 1.0)

    def test_partial_keywords(self):
        score, matched, missing = _score_case(
            {'status': 'dry_run', 'reason': 'token updated'}, ['token', 'authentication']
        )
        self.assertAlmostEqual(score, 0.5)
        self.assertIn('token', matched)
        self.assertIn('authentication', missing)

    def test_keyword_in_prompt_file(self):
        with tempfile.NamedTemporaryFile(suffix='.md', mode='w', delete=False, encoding='utf-8') as f:
            f.write('Fix the authentication flow')
            prompt_path = f.name
        try:
            score, matched, missing = _score_case(
                {'status': 'dry_run', 'promptPath': prompt_path}, ['authentication']
            )
            self.assertIn('authentication', matched)
        finally:
            Path(prompt_path).unlink(missing_ok=True)

    def test_missing_prompt_file_ok(self):
        score, matched, missing = _score_case(
            {'status': 'dry_run', 'promptPath': '/nonexistent/prompt.md'}, ['keyword']
        )
        self.assertIn('keyword', missing)


class TestBenchmarkResult(unittest.TestCase):
    def test_to_dict_is_serialisable(self):
        result = BenchmarkResult(
            name='test',
            cases_run=1,
            cases_passed=1,
            cases_failed=0,
            overall_score=1.0,
            case_results=[
                CaseResult(
                    case_id='c1', passed=True, score=1.0,
                    matched_keywords=['x'], missing_keywords=[],
                )
            ],
            elapsed_seconds=0.1,
            timestamp='2026-01-01T00:00:00+00:00',
        )
        d = result.to_dict()
        self.assertEqual(d['name'], 'test')
        self.assertEqual(d['cases_passed'], 1)
        self.assertEqual(len(d['case_results']), 1)
        # Must be JSON-serialisable
        json.dumps(d)


class TestRenderBenchmarkResult(unittest.TestCase):
    def test_render_contains_name(self):
        result = BenchmarkResult(
            name='My Bench',
            cases_run=2,
            cases_passed=1,
            cases_failed=1,
            overall_score=0.5,
            case_results=[
                CaseResult('a', True, 1.0, ['kw'], []),
                CaseResult('b', False, 0.0, [], ['kw'], error='oops'),
            ],
            elapsed_seconds=1.0,
            timestamp='2026-01-01T00:00:00+00:00',
        )
        rendered = render_benchmark_result(result)
        self.assertIn('My Bench', rendered)
        self.assertIn('1/2', rendered)

    def test_render_returns_str(self):
        result = BenchmarkResult(
            name='x', cases_run=0, cases_passed=0, cases_failed=0,
            overall_score=0.0, case_results=[], elapsed_seconds=0.0,
            timestamp='2026-01-01T00:00:00+00:00',
        )
        self.assertIsInstance(render_benchmark_result(result), str)


# ---------------------------------------------------------------------------
# CLI benchmark command registration
# ---------------------------------------------------------------------------
from copilot_operator.cli import build_parser


class TestBenchmarkCLI(unittest.TestCase):
    def test_benchmark_registered(self):
        parser = build_parser()
        subparsers_action = next(
            a for a in parser._actions
            if hasattr(a, '_name_parser_map')
        )
        self.assertIn('benchmark', subparsers_action._name_parser_map)

    def test_benchmark_requires_file(self):
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(['benchmark'])

    def test_benchmark_json_flag(self):
        parser = build_parser()
        args = parser.parse_args(['benchmark', '--file', 'bench.json', '--json'])
        self.assertTrue(args.output_json)

    def test_benchmark_no_json_flag_default(self):
        parser = build_parser()
        args = parser.parse_args(['benchmark', '--file', 'bench.json'])
        self.assertFalse(args.output_json)


# ---------------------------------------------------------------------------
# Terminal CLI -V flag
# ---------------------------------------------------------------------------
class TestVersionFlag(unittest.TestCase):
    def test_version_subcommand_exists(self):
        parser = build_parser()
        subparsers_action = next(
            a for a in parser._actions
            if hasattr(a, '_name_parser_map')
        )
        self.assertIn('version', subparsers_action._name_parser_map)
