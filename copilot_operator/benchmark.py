"""Benchmark — run the operator against local test cases and score results.

Test cases are defined in a JSON file.  Each case specifies a goal and
optional expected keywords that must appear in the generated prompt.
Cases run in dry-run mode so no VS Code session is required.

Example benchmark file (benchmark.json):

    {
      "name": "My project benchmark",
      "cases": [
        {
          "id": "fix-auth-bug",
          "goal": "Fix the login bug where tokens expire too early",
          "goal_profile": "bug",
          "expected_keywords": ["token", "expiry", "authentication"],
          "max_iterations": 1
        },
        {
          "id": "add-readme-docs",
          "goal": "Update README with installation and usage sections",
          "goal_profile": "docs",
          "expected_keywords": ["README", "installation", "usage"]
        }
      ]
    }

Usage:
    copilot-operator benchmark --file benchmark.json
    copilot-operator benchmark --file benchmark.json --json
"""

from __future__ import annotations

__all__ = [
    'BenchmarkCase',
    'CaseResult',
    'BenchmarkResult',
    'load_benchmark_file',
    'run_benchmark',
    'render_benchmark_result',
    'QUALITY_RUBRIC',
    'get_rubric',
]

import dataclasses
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .logging_config import get_logger

logger = get_logger('benchmark')

_DEFAULT_BENCHMARK_DIR = '.copilot-operator/benchmarks'


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class BenchmarkCase:
    id: str
    goal: str
    goal_profile: str = 'default'
    expected_keywords: list[str] = field(default_factory=list)
    max_iterations: int = 1
    description: str = ''


@dataclass
class CaseResult:
    case_id: str
    passed: bool
    score: float                    # 0.0 – 1.0
    matched_keywords: list[str]
    missing_keywords: list[str]
    prompt_path: str = ''
    elapsed_seconds: float = 0.0
    error: str = ''


@dataclass
class BenchmarkResult:
    name: str
    cases_run: int
    cases_passed: int
    cases_failed: int
    overall_score: float            # 0.0 – 1.0 (mean per-case score)
    case_results: list[CaseResult]
    elapsed_seconds: float
    timestamp: str

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_benchmark_file(path: Path) -> tuple[str, list[BenchmarkCase]]:
    """Parse a benchmark JSON file and return *(name, cases)*."""
    raw = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(raw, dict) or 'cases' not in raw:
        raise ValueError(f'Benchmark file must contain a "cases" array: {path}')
    name = raw.get('name', path.stem)
    cases: list[BenchmarkCase] = []
    for item in raw['cases']:
        if 'goal' not in item:
            raise ValueError(f'Each benchmark case must have a "goal" field: {item}')
        cases.append(BenchmarkCase(
            id=item.get('id', f'case-{len(cases) + 1}'),
            goal=item['goal'],
            goal_profile=item.get('goal_profile', 'default'),
            expected_keywords=item.get('expected_keywords', []),
            max_iterations=item.get('max_iterations', 1),
            description=item.get('description', ''),
        ))
    return name, cases


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _score_case(
    result_dict: dict[str, Any],
    expected_keywords: list[str],
) -> tuple[float, list[str], list[str]]:
    """Score a dry-run *result_dict* against *expected_keywords*.

    Returns *(score 0.0-1.0, matched_keywords, missing_keywords)*.
    """
    if not expected_keywords:
        # No keywords: pass if the operator run succeeded
        ok = result_dict.get('status') in ('dry_run', 'complete')
        return (1.0 if ok else 0.0), [], []

    # Collect all searchable text from the result + generated prompt file
    parts: list[str] = [
        result_dict.get('status', ''),
        result_dict.get('reason', ''),
    ]
    prompt_path = result_dict.get('promptPath', '')
    if prompt_path:
        try:
            parts.append(Path(prompt_path).read_text(encoding='utf-8', errors='ignore'))
        except OSError:
            pass

    haystack = ' '.join(parts).lower()
    matched = [kw for kw in expected_keywords if kw.lower() in haystack]
    missing = [kw for kw in expected_keywords if kw.lower() not in haystack]
    score = len(matched) / len(expected_keywords)
    return score, matched, missing


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_benchmark(
    config_path: str | None,
    workspace_override: str | None,
    benchmark_file: Path,
) -> BenchmarkResult:
    """Run all cases in *benchmark_file* and return aggregated :class:`BenchmarkResult`."""
    from .config import load_config
    from .operator import CopilotOperator

    bm_name, cases = load_benchmark_file(benchmark_file)
    case_results: list[CaseResult] = []
    total_start = time.monotonic()

    for case in cases:
        case_start = time.monotonic()
        try:
            config = load_config(config_path, workspace_override)
            config.goal_profile = case.goal_profile
            config.max_iterations = case.max_iterations
            operator = CopilotOperator(config, dry_run=True, live=False)
            result_dict = operator.run(case.goal)
            elapsed = time.monotonic() - case_start

            score, matched, missing = _score_case(result_dict, case.expected_keywords)
            if case.expected_keywords:
                passed = score >= 1.0
            else:
                passed = result_dict.get('status') == 'dry_run'

            case_results.append(CaseResult(
                case_id=case.id,
                passed=passed,
                score=round(score, 4),
                matched_keywords=matched,
                missing_keywords=missing,
                prompt_path=result_dict.get('promptPath', ''),
                elapsed_seconds=round(elapsed, 3),
            ))
        except Exception as exc:
            elapsed = time.monotonic() - case_start
            logger.warning('Benchmark case %r raised exception: %s', case.id, exc)
            case_results.append(CaseResult(
                case_id=case.id,
                passed=False,
                score=0.0,
                matched_keywords=[],
                missing_keywords=case.expected_keywords[:],
                elapsed_seconds=round(elapsed, 3),
                error=str(exc),
            ))

    total_elapsed = time.monotonic() - total_start
    passed_count = sum(1 for r in case_results if r.passed)
    overall = sum(r.score for r in case_results) / len(case_results) if case_results else 0.0

    return BenchmarkResult(
        name=bm_name,
        cases_run=len(case_results),
        cases_passed=passed_count,
        cases_failed=len(case_results) - passed_count,
        overall_score=round(overall, 4),
        case_results=case_results,
        elapsed_seconds=round(total_elapsed, 3),
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


# ---------------------------------------------------------------------------
# Pretty-print helpers (used by CLI)
# ---------------------------------------------------------------------------

def render_benchmark_result(result: BenchmarkResult, use_color: bool = True) -> str:
    """Return a human-readable multi-line summary of *result*."""
    from .terminal import bold, cyan, dim, err, header_line, ok, score_color, warn

    lines: list[str] = [
        header_line(f'Benchmark: {result.name}'),
        '',
    ]

    for cr in result.case_results:
        if cr.passed:
            prefix = ok(cr.case_id)
        else:
            prefix = (err if cr.error else warn)(cr.case_id)

        pct = int(cr.score * 100)
        detail = f'score={score_color(pct)} ({cr.elapsed_seconds:.2f}s)'
        if cr.missing_keywords:
            detail += f' missing={cyan(", ".join(cr.missing_keywords))}'
        if cr.error:
            detail += f' error={dim(cr.error[:80])}'
        lines.append(f'  {prefix} — {detail}')

    overall_pct = int(result.overall_score * 100)
    lines += [
        '',
        bold(f'Results: {result.cases_passed}/{result.cases_run} passed'
             f' — overall score {score_color(overall_pct)}%'
             f' — {result.elapsed_seconds:.2f}s total'),
    ]
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Quality rubric — per-profile validation rules
# ---------------------------------------------------------------------------

QUALITY_RUBRIC: dict[str, list[str]] = {
    'bug': [
        'test file added or modified',
        'regression test covers the fix',
        'no unrelated changes',
    ],
    'feature': [
        'test coverage for new code',
        'documentation updated if public API changed',
        'no performance regressions',
    ],
    'refactor': [
        'no functional changes',
        'test suite unchanged or expanded',
        'lint clean',
    ],
    'audit': [
        'security findings documented',
        'risk classification applied',
        'remediation recommendations present',
    ],
    'docs': [
        'no code changes',
        'examples match actual API',
        'spelling and grammar checked',
    ],
}


def get_rubric(goal_profile: str) -> list[str]:
    """Return the quality rubric for a given goal profile."""
    return QUALITY_RUBRIC.get(goal_profile, QUALITY_RUBRIC.get('feature', []))


# ---------------------------------------------------------------------------
# "Not done if…" guards
# ---------------------------------------------------------------------------

@dataclass
class DoneGuard:
    """A blocking condition that prevents marking a run as 'done'."""
    name: str
    check: str  # 'tests_added' | 'docs_updated' | 'max_files' | 'lint_clean' | 'custom'
    description: str = ''


DEFAULT_DONE_GUARDS: list[DoneGuard] = [
    DoneGuard('tests_added', 'tests_added', 'At least one test file must be added or modified'),
    DoneGuard('lint_clean', 'lint_clean', 'Lint must pass with zero errors'),
    DoneGuard('max_files', 'max_files', 'Changed files must not exceed configured limit'),
]


def evaluate_done_guards(
    result: dict[str, Any],
    guards: list[DoneGuard] | None = None,
    max_files: int = 0,
) -> list[dict[str, Any]]:
    """Evaluate done guards against a run result.

    Returns a list of violated guards (empty = all passed).
    """
    violations: list[dict[str, Any]] = []
    for guard in (guards or DEFAULT_DONE_GUARDS):
        if guard.check == 'tests_added':
            changed = result.get('allChangedFiles', []) or []
            has_test = any('test' in f.lower() for f in changed)
            if not has_test:
                violations.append({
                    'guard': guard.name,
                    'description': guard.description,
                    'detail': 'No test files were added or modified.',
                })
        elif guard.check == 'lint_clean':
            last_history = (result.get('history', []) or [{}])[-1] if result.get('history') else {}
            lint_status = last_history.get('lint', result.get('lint', 'not_run'))
            if lint_status not in ('pass', 'not_applicable', 'not_run'):
                violations.append({
                    'guard': guard.name,
                    'description': guard.description,
                    'detail': f'Lint status: {lint_status}',
                })
        elif guard.check == 'max_files':
            if max_files > 0:
                changed = result.get('allChangedFiles', []) or []
                if len(changed) > max_files:
                    violations.append({
                        'guard': guard.name,
                        'description': guard.description,
                        'detail': f'{len(changed)} files changed (max {max_files}).',
                    })
    return violations
