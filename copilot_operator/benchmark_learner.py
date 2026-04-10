"""Benchmark Learner — extract meta-learner rules from benchmark failures.

After a benchmark run, ``BenchmarkResult`` contains per-case pass/fail data
including ``missing_keywords`` and ``error`` fields.  This module analyses
those results and creates ``PromptRule`` objects that the meta-learner can
persist and inject into future production runs.

Pipeline:
  benchmark run → BenchmarkResult → analyse_benchmark_for_rules() → PromptRule[]
  → save via meta_learner.save_rules() → activate in future runs
"""

from __future__ import annotations

__all__ = [
    'BenchmarkLesson',
    'analyse_benchmark_for_rules',
    'render_benchmark_lessons',
]

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .logging_config import get_logger

logger = get_logger(__name__)


@dataclass(slots=True)
class BenchmarkLesson:
    """A single lesson extracted from a benchmark failure."""
    case_id: str = ''
    goal: str = ''
    missing_keywords: list[str] = field(default_factory=list)
    error: str = ''
    score: float = 0.0
    lesson_type: str = ''        # keyword_miss | low_score | error | regression


def analyse_benchmark_for_rules(
    case_results: list[dict[str, Any]],
    benchmark_id: str = '',
    existing_rule_triggers: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Analyse benchmark case results and produce new rules.

    Each failed case is examined for patterns:
      - Missing keywords → guardrail requiring those concepts
      - Low scores → guardrail about thoroughness
      - Errors → guardrail about error class

    Returns list of rule dicts compatible with ``PromptRule`` fields
    from meta_learner.
    """
    if existing_rule_triggers is None:
        existing_rule_triggers = set()

    lessons: list[BenchmarkLesson] = []
    rules: list[dict[str, Any]] = []

    for case in case_results:
        passed = case.get('passed', False)
        score = case.get('score', 0.0)
        case_id = case.get('case_id', '')
        missing = case.get('missing_keywords', [])
        error = case.get('error', '')

        if passed and score >= 0.8:
            continue  # nothing to learn from strong passes

        # --- Pattern 1: Missing keywords ---
        if missing:
            lessons.append(BenchmarkLesson(
                case_id=case_id,
                missing_keywords=missing,
                score=score,
                lesson_type='keyword_miss',
            ))

        # --- Pattern 2: Low score (< 0.5) ---
        if score < 0.5 and not missing:
            lessons.append(BenchmarkLesson(
                case_id=case_id,
                score=score,
                lesson_type='low_score',
            ))

        # --- Pattern 3: Error during benchmark ---
        if error:
            lessons.append(BenchmarkLesson(
                case_id=case_id,
                error=error,
                score=score,
                lesson_type='error',
            ))

    # Convert lessons to rules
    now = datetime.now(timezone.utc).isoformat()

    for lesson in lessons:
        trigger = _build_trigger(lesson)
        if trigger in existing_rule_triggers:
            continue  # don't duplicate

        guardrail = _build_guardrail(lesson)
        if not guardrail:
            continue

        rules.append({
            'rule_id': f'bench_{uuid4().hex[:8]}',
            'trigger': trigger,
            'guardrail': guardrail,
            'category': _map_category(lesson.lesson_type),
            'source_run': benchmark_id or 'benchmark',
            'source_iteration': 0,
            'created_at': now,
            'hit_count': 0,
            'success_after_hit': 0,
            'confidence': 0.5,
        })

    if rules:
        logger.info(
            'Benchmark learner extracted %d rules from %d lessons',
            len(rules), len(lessons),
        )

    return rules


def _build_trigger(lesson: BenchmarkLesson) -> str:
    """Build a trigger string that identifies when this rule should fire."""
    if lesson.lesson_type == 'keyword_miss':
        # Trigger when goal mentions similar concepts
        keywords = ', '.join(lesson.missing_keywords[:5])
        return f'benchmark_miss:{keywords}'

    if lesson.lesson_type == 'low_score':
        return f'benchmark_low_score:{lesson.case_id}'

    if lesson.lesson_type == 'error':
        # Extract error class
        error_key = lesson.error.split(':')[0][:50] if lesson.error else 'unknown'
        return f'benchmark_error:{error_key}'

    return f'benchmark:{lesson.case_id}'


def _build_guardrail(lesson: BenchmarkLesson) -> str:
    """Build the guardrail text to inject into prompts."""
    if lesson.lesson_type == 'keyword_miss':
        keywords = ', '.join(lesson.missing_keywords[:5])
        return (
            f'Benchmark case "{lesson.case_id}" failed because output lacked: {keywords}. '
            f'Make sure to explicitly include these concepts in your implementation.'
        )

    if lesson.lesson_type == 'low_score':
        return (
            f'Benchmark case "{lesson.case_id}" scored only {lesson.score:.0%}. '
            f'Be more thorough — check edge cases and validate completeness.'
        )

    if lesson.lesson_type == 'error':
        error_short = lesson.error[:100]
        return (
            f'Benchmark case "{lesson.case_id}" produced an error: {error_short}. '
            f'Avoid this error pattern in similar tasks.'
        )

    return ''


def _map_category(lesson_type: str) -> str:
    """Map lesson type to meta-learner rule category."""
    return {
        'keyword_miss': 'scope_creep',
        'low_score': 'test_regression',
        'error': 'blocker_pattern',
    }.get(lesson_type, 'scope_creep')


def render_benchmark_lessons(
    case_results: list[dict[str, Any]],
) -> str:
    """Render a human-readable summary of benchmark failures.

    Returns empty string if all cases passed well.
    """
    failures = [c for c in case_results if not c.get('passed', False) or c.get('score', 1.0) < 0.8]
    if not failures:
        return ''

    lines = [f'## 📊 Benchmark Failures ({len(failures)} cases)']
    for case in failures[:10]:
        case_id = case.get('case_id', '?')
        score = case.get('score', 0.0)
        missing = case.get('missing_keywords', [])
        error = case.get('error', '')

        line = f'- **{case_id}**: score={score:.0%}'
        if missing:
            line += f', missing: {", ".join(missing[:3])}'
        if error:
            line += f', error: {error[:60]}'
        lines.append(line)

    return '\n'.join(lines)
