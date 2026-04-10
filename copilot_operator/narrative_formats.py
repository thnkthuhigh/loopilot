"""Narrative Formats — fixed-structure output for all views.

Enforces consistent format across runs so output is always predictable.
Prevents the "dump log" anti-pattern by structuring everything into
tagged blocks: [STATE], [ACTION], [OBSERVATION], [DECISION], etc.

Two output modes:
  - ``text``: Plain-text for CLI / TUI
  - ``dict``: Structured dict for JSON export / programmatic use
"""

from __future__ import annotations

__all__ = [
    'format_live_block',
    'format_decision_block',
    'format_summary_block',
    'format_summary_short',
    'format_memory_block',
    'format_iteration_log',
    'format_live_stream_line',
]

from typing import Any


def format_live_block(live: Any) -> str:
    """Format a LiveNarrative into the fixed [STATE]/[ACTION] structure."""
    lines: list[str] = []

    lines.append('[STATE]')
    lines.append(f'  Task:      {live.task}')
    if live.milestone:
        lines.append(f'  Milestone: {live.milestone}')
    lines.append(f'  Iteration: {live.iteration}/{live.max_iterations}')
    lines.append(f'  Score:     {live.score}/{live.target_score}')
    if live.strategy != 'default':
        lines.append(f'  Strategy:  {live.strategy} (escalation {live.escalation_level})')
    lines.append(f'  Status:    {live.worker_status}')

    lines.append('')
    lines.append('[ACTION]')
    lines.append(f'  Just did: {live.just_did or "(starting)"}')
    lines.append(f'  Next:     {live.next_action or "(pending)"}')

    if live.urgency in ('critical', 'high') or live.blocking_now:
        lines.append('')
        lines.append('[PRESSURE]')
        lines.append(f'  Urgency: {live.urgency}')
        for b in live.blocking_now[:3]:
            lines.append(f'  Blocked: {b}')

    return '\n'.join(lines)


def format_decision_block(trace: Any) -> str:
    """Format a DecisionTrace into the fixed tagged structure."""
    lines = [f'[ITERATION {trace.iteration}]']

    if trace.observation:
        lines.append(f'  [OBSERVATION]          {trace.observation}')
    if trace.hypothesis:
        lines.append(f'  [HYPOTHESIS]           {trace.hypothesis}')
    if trace.decision:
        code = f' ({trace.decision_code})' if trace.decision_code else ''
        lines.append(f'  [DECISION]             {trace.decision}{code}')
    if trace.alternatives_rejected:
        lines.append(f'  [ALTERNATIVES_REJECTED] {" │ ".join(trace.alternatives_rejected)}')
    if trace.actions_taken:
        lines.append(f'  [ACTUATION]            {", ".join(trace.actions_taken)}')

    if trace.score_delta != 0 or trace.what_helped or trace.what_hurt:
        lines.append('  [SELF_EVAL]')
        if trace.score_delta != 0:
            lines.append(f'    Score: {trace.score_delta:+d}')
        if trace.what_helped:
            lines.append(f'    Helped: {trace.what_helped}')
        if trace.what_hurt:
            lines.append(f'    Hurt:   {trace.what_hurt}')
        if trace.correction:
            lines.append(f'    Fix:    {trace.correction}')

    return '\n'.join(lines)


def format_summary_block(summary: Any) -> str:
    """Format a RunSummaryView into the fixed tagged structure."""
    lines: list[str] = []

    lines.append(f'[GOAL]       {summary.goal}')
    lines.append(f'[OUTCOME]    {summary.outcome} │ Score {summary.score}/{summary.target_score} │ {summary.iterations} iterations │ Profile: {summary.goal_profile}')

    if summary.files_changed:
        count = len(summary.files_changed)
        preview = ', '.join(summary.files_changed[:5])
        if count > 5:
            preview += f' (+{count - 5})'
        lines.append(f'[CHANGES]    {count} files: {preview}')

    val_parts = []
    if summary.tests_passed or summary.tests_failed:
        val_parts.append(f'Tests: {summary.tests_passed}✓ {summary.tests_failed}✗')
    if summary.lint_status:
        val_parts.append(f'Lint: {summary.lint_status}')
    if val_parts:
        lines.append(f'[VALIDATION] {" │ ".join(val_parts)}')

    lines.append(f'[WHY_DONE]   {summary.stop_reason}')
    if summary.stop_reason_code:
        lines.append(f'             Code: {summary.stop_reason_code}')
    for c in summary.conditions_met:
        lines.append(f'             ✅ {c}')
    for c in summary.conditions_unmet:
        lines.append(f'             ❌ {c}')

    if summary.risks:
        lines.append('[RISKS]')
        for r in summary.risks[:5]:
            lines.append(f'             ⚠ {r}')

    if summary.next_step or summary.commitments_owed:
        lines.append('[NEXT]')
        if summary.next_step:
            lines.append(f'             → {summary.next_step}')
        for c in summary.commitments_owed[:3]:
            lines.append(f'             📌 {c}')

    return '\n'.join(lines)


def format_summary_short(summary: Any) -> str:
    """Ultra-compact run summary — 5 lines max. For --short and auto-summary."""
    outcome_icon = {'success': '✅', 'partial': '🟡', 'fail': '❌', 'blocked': '🔴'}.get(
        getattr(summary, 'outcome', ''), '⚪'
    )
    lines = [
        f'Goal:   {summary.goal}',
        f'Result: {outcome_icon} {summary.outcome.upper()} — score {summary.score}/{summary.target_score} in {summary.iterations} iter',
    ]
    if summary.stop_reason:
        lines.append(f'Reason: {summary.stop_reason}')
    if summary.risks:
        lines.append(f'Risk:   {summary.risks[0]}')
    if summary.next_step:
        lines.append(f'Next:   {summary.next_step}')
    elif summary.commitments_owed:
        lines.append(f'Next:   {summary.commitments_owed[0]}')
    return '\n'.join(lines)


def format_live_stream_line(iteration: int, phase: str, detail: str) -> str:
    """Single-line live progress for --live streaming. Compact and timestamped."""
    return f'[ITER {iteration}] {phase}: {detail}'


def format_memory_block(memory: Any) -> str:
    """Format a MemorySnapshot into the fixed tagged structure."""
    lines: list[str] = []

    if memory.mission_direction:
        lines.append(f'[MISSION]    {memory.mission_direction}')
        if memory.mission_phase:
            lines.append(f'             Phase: {memory.mission_phase}')
        if memory.mission_priorities:
            lines.append(f'             Priorities: {", ".join(memory.mission_priorities[:3])}')

    if memory.total_runs > 0:
        lines.append(f'[PROJECT]    {memory.total_runs} runs │ {memory.success_rate:.0%} success')
        if memory.common_blockers:
            lines.append(f'             Blockers: {", ".join(memory.common_blockers[:3])}')

    if memory.active_rules > 0:
        lines.append(f'[RULES]      {memory.active_rules} active')
        for r in memory.top_rules[:3]:
            lines.append(f'             • {r}')

    if memory.recent_lessons:
        lines.append('[LESSONS]')
        for lesson in memory.recent_lessons[:5]:
            lines.append(f'             • {lesson}')

    if memory.effective_strategies or memory.failed_strategies:
        lines.append('[STRATEGIES]')
        if memory.effective_strategies:
            lines.append(f'             ✅ {", ".join(memory.effective_strategies[:3])}')
        if memory.failed_strategies:
            lines.append(f'             ❌ {", ".join(memory.failed_strategies[:3])}')

    if memory.top_intel_sources or memory.wasteful_sources:
        lines.append('[TELEMETRY]')
        if memory.top_intel_sources:
            lines.append(f'             Top: {", ".join(memory.top_intel_sources[:3])}')
        if memory.wasteful_sources:
            lines.append(f'             Waste: {", ".join(memory.wasteful_sources[:3])}')

    return '\n'.join(lines) if lines else '[MEMORY]     No data available.'


def format_iteration_log(
    iteration: int,
    live: Any,
    trace: Any | None = None,
) -> str:
    """Format a single iteration's narrative log entry.

    Combines live state + decision trace into one compact block.
    """
    parts = [format_live_block(live)]
    if trace:
        parts.append('')
        parts.append(format_decision_block(trace))
    return '\n'.join(parts)


def views_to_dict(
    live: Any | None = None,
    traces: list[Any] | None = None,
    summary: Any | None = None,
    memory: Any | None = None,
) -> dict[str, Any]:
    """Convert all views to a structured dict for JSON export."""
    result: dict[str, Any] = {}

    if live:
        result['live'] = {
            'task': live.task,
            'milestone': live.milestone,
            'iteration': live.iteration,
            'max_iterations': live.max_iterations,
            'score': live.score,
            'target_score': live.target_score,
            'strategy': live.strategy,
            'urgency': live.urgency,
            'just_did': live.just_did,
            'next_action': live.next_action,
        }

    if traces:
        result['decisions'] = [
            {
                'iteration': t.iteration,
                'observation': t.observation,
                'hypothesis': t.hypothesis,
                'decision': t.decision,
                'decision_code': t.decision_code,
                'alternatives_rejected': t.alternatives_rejected,
                'actions_taken': t.actions_taken,
                'score_delta': t.score_delta,
                'what_helped': t.what_helped,
                'what_hurt': t.what_hurt,
                'correction': t.correction,
            }
            for t in traces
        ]

    if summary:
        result['summary'] = {
            'goal': summary.goal,
            'outcome': summary.outcome,
            'score': summary.score,
            'target_score': summary.target_score,
            'iterations': summary.iterations,
            'files_changed': summary.files_changed,
            'stop_reason': summary.stop_reason,
            'stop_reason_code': summary.stop_reason_code,
            'conditions_met': summary.conditions_met,
            'conditions_unmet': summary.conditions_unmet,
            'risks': summary.risks,
            'next_step': summary.next_step,
        }

    if memory:
        result['memory'] = {
            'mission': memory.mission_direction,
            'phase': memory.mission_phase,
            'priorities': memory.mission_priorities,
            'total_runs': memory.total_runs,
            'success_rate': memory.success_rate,
            'active_rules': memory.active_rules,
            'recent_lessons': memory.recent_lessons,
            'effective_strategies': memory.effective_strategies,
            'failed_strategies': memory.failed_strategies,
        }

    return result
