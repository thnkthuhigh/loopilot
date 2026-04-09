"""Intelligence engine — pattern detection, trend analysis, and adaptive strategy.

Gives the operator a 'brain' that recognises loops, stagnation, recurring
failures, and automatically adjusts its approach instead of blindly retrying.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class ScoreTrend:
    """Describes how the score has been moving across recent iterations."""
    direction: str = 'unknown'   # improving, declining, stagnant, oscillating
    delta: int = 0               # net change over the window
    values: list[int] = field(default_factory=list)
    window: int = 0


@dataclass(slots=True)
class LoopSignal:
    """Detected repetitive / stuck behaviour."""
    detected: bool = False
    kind: str = ''               # score_plateau, baton_repeat, blocker_repeat, summary_repeat
    detail: str = ''
    consecutive_count: int = 0


@dataclass(slots=True)
class StrategyHint:
    """Concrete recommendation the operator can act on."""
    action: str = 'none'  # escalate_profile, narrow_scope, switch_baton, increase_iterations, abort
    reason: str = ''
    suggested_prompt: str = ''
    suggested_profile: str = ''


@dataclass(slots=True)
class Diagnosis:
    """Aggregated intelligence output for one decision point."""
    score_trend: ScoreTrend = field(default_factory=ScoreTrend)
    loops: list[LoopSignal] = field(default_factory=list)
    hints: list[StrategyHint] = field(default_factory=list)
    risk_level: str = 'low'     # low, medium, high, critical
    confidence: float = 1.0     # 0-1 how confident the diagnosis is


# ---------------------------------------------------------------------------
# Score trend analysis
# ---------------------------------------------------------------------------

def analyse_score_trend(history: list[dict[str, Any]], window: int = 4) -> ScoreTrend:
    """Look at the last *window* iterations and characterise the score movement."""
    scores = [
        int(h['score']) for h in history[-window:]
        if h.get('score') is not None
    ]
    if len(scores) < 2:
        return ScoreTrend(direction='unknown', delta=0, values=scores, window=len(scores))

    delta = scores[-1] - scores[0]
    diffs = [scores[i + 1] - scores[i] for i in range(len(scores) - 1)]

    if all(d == 0 for d in diffs):
        direction = 'stagnant'
    elif all(d >= 0 for d in diffs) and delta > 0:
        direction = 'improving'
    elif all(d <= 0 for d in diffs) and delta < 0:
        direction = 'declining'
    elif any(d > 0 for d in diffs) and any(d < 0 for d in diffs):
        direction = 'oscillating'
    elif delta > 0:
        direction = 'improving'
    elif delta < 0:
        direction = 'declining'
    else:
        direction = 'stagnant'

    return ScoreTrend(direction=direction, delta=delta, values=scores, window=len(scores))


# ---------------------------------------------------------------------------
# Loop / stuck detection
# ---------------------------------------------------------------------------

def _normalise_text(text: str) -> str:
    return re.sub(r'\s+', ' ', text.strip().lower())


def detect_loops(history: list[dict[str, Any]], window: int = 3) -> list[LoopSignal]:
    """Scan recent history for repetition signals."""
    signals: list[LoopSignal] = []
    recent = history[-window:]
    if len(recent) < 2:
        return signals

    # 1. Score plateau: same (non-None) score N times in a row
    scores = [h.get('score') for h in recent if h.get('score') is not None]
    if len(scores) >= 2 and len(set(scores)) == 1:
        signals.append(LoopSignal(
            detected=True,
            kind='score_plateau',
            detail=f'Score stuck at {scores[0]} for {len(scores)} iterations.',
            consecutive_count=len(scores),
        ))

    # 2. Summary repeat: Copilot is saying the same thing each turn
    summaries = [_normalise_text(h.get('summary', '')) for h in recent if h.get('summary')]
    if len(summaries) >= 2 and len(set(summaries)) == 1:
        signals.append(LoopSignal(
            detected=True,
            kind='summary_repeat',
            detail=f'Copilot repeated the same summary {len(summaries)} times.',
            consecutive_count=len(summaries),
        ))

    # 3. Baton repeat: operator keeps sending the same next prompt
    batons = [_normalise_text(h.get('decisionNextPrompt', '')) for h in recent if h.get('decisionNextPrompt')]
    if len(batons) >= 2 and len(set(batons)) == 1:
        signals.append(LoopSignal(
            detected=True,
            kind='baton_repeat',
            detail=f'Same baton sent {len(batons)} times in a row.',
            consecutive_count=len(batons),
        ))

    # 4. Blocker repeat: same blocker showing up repeatedly
    blocker_sets: list[frozenset[str]] = []
    for h in recent:
        bs = frozenset(
            b.get('item', '') for b in (h.get('blockers') or []) if b.get('item')
        )
        if bs:
            blocker_sets.append(bs)
    if len(blocker_sets) >= 2 and len(set(blocker_sets)) == 1:
        signals.append(LoopSignal(
            detected=True,
            kind='blocker_repeat',
            detail=f'Same blockers persist across {len(blocker_sets)} iterations.',
            consecutive_count=len(blocker_sets),
        ))

    # 5. Decision code repeat: same reason code each time
    codes = [h.get('decisionCode', '') for h in recent if h.get('decisionCode')]
    if len(codes) >= 2 and len(set(codes)) == 1 and codes[0] not in {'INITIAL_EXECUTION', 'STOP_GATE_PASSED'}:
        signals.append(LoopSignal(
            detected=True,
            kind='decision_code_repeat',
            detail=f'Decision code {codes[0]} repeated {len(codes)} times.',
            consecutive_count=len(codes),
        ))

    # 6. Changed-files repeat: same set of files modified each iteration (diff dedup)
    changed_sets: list[frozenset[str]] = []
    for h in recent:
        cf = h.get('changedFiles') or []
        if cf:
            changed_sets.append(frozenset(cf))
    if len(changed_sets) >= 2 and len(set(changed_sets)) == 1:
        signals.append(LoopSignal(
            detected=True,
            kind='diff_repeat',
            detail=f'Same files changed in {len(changed_sets)} consecutive iterations: {", ".join(sorted(changed_sets[0])[:5])}.',
            consecutive_count=len(changed_sets),
        ))

    # 7. Response content similarity: normalized response text is near-identical
    response_hashes: list[str] = []
    for h in recent:
        resp = h.get('summary', '') + '|' + '|'.join(
            b.get('item', '') for b in (h.get('blockers') or [])
        )
        response_hashes.append(_normalise_text(resp))
    if len(response_hashes) >= 2:
        unique_responses = set(response_hashes)
        if len(unique_responses) == 1 and response_hashes[0]:
            signals.append(LoopSignal(
                detected=True,
                kind='response_repeat',
                detail=f'Near-identical response content repeated {len(response_hashes)} times.',
                consecutive_count=len(response_hashes),
            ))

    return signals


# ---------------------------------------------------------------------------
# Validation failure analysis
# ---------------------------------------------------------------------------

def analyse_validation_failures(history: list[dict[str, Any]], window: int = 3) -> list[str]:
    """Return names of validations that have failed in every recent iteration."""
    recent = history[-window:]
    if not recent:
        return []

    fail_counts: dict[str, int] = {}
    total = 0
    for h in recent:
        after_results = h.get('validation_after') or []
        if not after_results:
            continue
        total += 1
        for v in after_results:
            if v.get('status') in ('fail', 'timeout') and v.get('required'):
                fail_counts[v['name']] = fail_counts.get(v['name'], 0) + 1

    if total == 0:
        return []
    return [name for name, count in fail_counts.items() if count >= total]


# ---------------------------------------------------------------------------
# Strategy recommendation
# ---------------------------------------------------------------------------

_PROFILE_ESCALATION = {
    'default': 'bug',
    'feature': 'refactor',
    'bug': 'audit',
    'docs': 'default',
    'refactor': 'audit',
    'audit': 'audit',
}


def recommend_strategy(
    history: list[dict[str, Any]],
    current_profile: str,
    target_score: int,
    iteration: int,
    max_iterations: int,
) -> list[StrategyHint]:
    """Produce actionable hints based on pattern analysis."""
    hints: list[StrategyHint] = []
    trend = analyse_score_trend(history)
    loops = detect_loops(history)
    persistent_failures = analyse_validation_failures(history)
    remaining = max_iterations - iteration

    # --- Score declining with little runway left ---
    if trend.direction == 'declining' and remaining <= 2:
        hints.append(StrategyHint(
            action='narrow_scope',
            reason=f'Score declining ({trend.delta:+d}) with only {remaining} iterations left.',
            suggested_prompt=(
                'STOP broadening the scope. Focus ONLY on making the existing tests and lint pass. '
                'Do not add new features or refactors. Fix the regressions you introduced.'
            ),
        ))

    # --- Score stagnant / plateau loop ---
    plateau = next((sig for sig in loops if sig.kind == 'score_plateau'), None)
    if plateau and plateau.consecutive_count >= 2:
        hints.append(StrategyHint(
            action='switch_baton',
            reason=f'Score plateaued at {trend.values[-1] if trend.values else "?"} — current approach is not working.',
            suggested_prompt=(
                'Your last approach did not move the score. Try a DIFFERENT strategy: '
                'read the failing test output carefully, identify exactly which assertion fails, '
                'and make the minimal fix. Do not repeat what you already tried.'
            ),
        ))

    # --- Baton repeat loop ---
    baton_loop = next((sig for sig in loops if sig.kind == 'baton_repeat'), None)
    if baton_loop and baton_loop.consecutive_count >= 2:
        hints.append(StrategyHint(
            action='switch_baton',
            reason='Same baton repeated without progress — operator is in a prompt loop.',
            suggested_prompt=(
                'The operator has sent you the same instruction multiple times without progress. '
                'Break the loop: examine why the previous attempt failed, pick a different angle, '
                'and explain what you are doing differently this time.'
            ),
        ))

    # --- Persistent validation failure ---
    if persistent_failures:
        names = ', '.join(persistent_failures)
        hints.append(StrategyHint(
            action='narrow_scope',
            reason=f'Validation lane(s) [{names}] have failed in every recent iteration.',
            suggested_prompt=(
                f'CRITICAL: The [{names}] validation has failed every single turn. '
                f'Before doing any feature work, FIX the [{names}] failure first. '
                f'Read the full error output, trace it to the root cause, and patch it.'
            ),
        ))

    # --- Blocker repeat ---
    blocker_loop = next((sig for sig in loops if sig.kind == 'blocker_repeat'), None)
    if blocker_loop:
        hints.append(StrategyHint(
            action='escalate_profile',
            reason='Same blockers persist — current profile may lack the right approach.',
            suggested_profile=_PROFILE_ESCALATION.get(current_profile, 'audit'),
        ))

    # --- Diff repeat: same files changed each time → no real progress ---
    diff_loop = next((sig for sig in loops if sig.kind == 'diff_repeat'), None)
    if diff_loop and diff_loop.consecutive_count >= 2:
        hints.append(StrategyHint(
            action='switch_baton',
            reason=f'Same files modified {diff_loop.consecutive_count} times — changes are being undone/redone.',
            suggested_prompt=(
                'You are modifying the same files repeatedly without net progress. '
                'STOP, re-read the full test output, and take a fundamentally different approach. '
                'Consider whether your change is being overwritten or reverted by another fix.'
            ),
        ))

    # --- Response repeat: LLM saying the same thing ---
    response_loop = next((sig for sig in loops if sig.kind == 'response_repeat'), None)
    if response_loop and response_loop.consecutive_count >= 2:
        hints.append(StrategyHint(
            action='escalate_profile',
            reason='Response content is repeating — model is stuck in a loop.',
            suggested_prompt=(
                'Your responses have been nearly identical for multiple turns. '
                'Break the pattern: read the workspace state fresh, list what has actually changed, '
                'and identify what is DIFFERENT about the current situation.'
            ),
            suggested_profile=_PROFILE_ESCALATION.get(current_profile, 'audit'),
        ))

    # --- Near budget exhaustion with low score ---
    if remaining <= 1 and trend.values and trend.values[-1] < target_score:
        hints.append(StrategyHint(
            action='narrow_scope',
            reason=f'Last iteration available and score ({trend.values[-1]}) still below target ({target_score}).',
            suggested_prompt=(
                'This is your FINAL iteration. Do only what is absolutely necessary to pass '
                'the stop gate: make tests pass, make lint pass, fix critical blockers. '
                'Do NOT start new work.'
            ),
        ))

    return hints


# ---------------------------------------------------------------------------
# Full diagnosis
# ---------------------------------------------------------------------------

def diagnose(
    history: list[dict[str, Any]],
    current_profile: str,
    target_score: int,
    iteration: int,
    max_iterations: int,
) -> Diagnosis:
    """Run all analysis and return a unified diagnosis."""
    trend = analyse_score_trend(history)
    loops = detect_loops(history)
    hints = recommend_strategy(history, current_profile, target_score, iteration, max_iterations)

    # Risk level
    any_loop = any(sig.detected for sig in loops)
    if trend.direction == 'declining' and any_loop:
        risk = 'critical'
    elif any_loop or trend.direction == 'declining':
        risk = 'high'
    elif trend.direction == 'stagnant':
        risk = 'medium'
    else:
        risk = 'low'

    # Confidence degrades if we have little data
    data_points = len([h for h in history if h.get('score') is not None])
    confidence = min(1.0, data_points / 3.0)

    return Diagnosis(
        score_trend=trend,
        loops=loops,
        hints=hints,
        risk_level=risk,
        confidence=confidence,
    )


def format_diagnosis_for_prompt(diagnosis: Diagnosis) -> str:
    """Render the diagnosis into text that can be injected into the operator prompt."""
    if diagnosis.risk_level == 'low' and not diagnosis.hints:
        return ''

    lines = [f'⚠ Operator intelligence detected risk level: {diagnosis.risk_level.upper()}']

    if diagnosis.score_trend.direction != 'unknown':
        lines.append(
            f'Score trend: {diagnosis.score_trend.direction} '
            f'(delta={diagnosis.score_trend.delta:+d}, recent={diagnosis.score_trend.values})'
        )

    for loop in diagnosis.loops:
        if loop.detected:
            lines.append(f'Loop detected [{loop.kind}]: {loop.detail}')

    for hint in diagnosis.hints:
        lines.append(f'Strategy hint [{hint.action}]: {hint.reason}')
        if hint.suggested_prompt:
            lines.append(f'  → {hint.suggested_prompt}')
        if hint.suggested_profile:
            lines.append(f'  → Consider switching to profile: {hint.suggested_profile}')

    return '\n'.join(lines)
