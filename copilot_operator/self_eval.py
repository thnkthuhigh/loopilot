"""Self-Evaluation Feedback Loop — iteration-level score analysis.

After each iteration, analyses what changed and why the score moved.
This diagnosis feeds directly into the next iteration's prompt, creating
a closed feedback loop:

  Iteration N → score → self_eval → prompt for N+1 → score → ...

Key questions answered:
  - What did the model do? (files changed, approach taken)
  - Did it help? (score delta, validation changes)
  - What should it do differently? (concrete corrections)
"""

from __future__ import annotations

__all__ = [
    'IterationEval',
    'ScoreMovement',
    'evaluate_iteration',
    'render_eval_for_prompt',
]

from dataclasses import dataclass, field
from typing import Any

from .logging_config import get_logger

logger = get_logger(__name__)


@dataclass(slots=True)
class ScoreMovement:
    """How and why the score changed."""
    before: int = 0
    after: int = 0
    delta: int = 0
    direction: str = 'neutral'   # improved | regressed | neutral
    explanation: str = ''


@dataclass(slots=True)
class IterationEval:
    """Self-evaluation of what happened in one iteration."""
    iteration: int = 0
    score: ScoreMovement = field(default_factory=ScoreMovement)
    files_changed: list[str] = field(default_factory=list)
    validations_before: dict[str, str] = field(default_factory=dict)   # name → pass/fail
    validations_after: dict[str, str] = field(default_factory=dict)
    validations_fixed: list[str] = field(default_factory=list)
    validations_broken: list[str] = field(default_factory=list)
    approach_worked: bool = False
    correction: str = ''           # what to do differently next
    what_helped: str = ''          # what worked (reinforce)
    what_hurt: str = ''            # what went wrong (avoid)


def _extract_validation_map(validations: list[dict[str, Any]] | None) -> dict[str, str]:
    """Extract name→status map from validation results."""
    if not validations:
        return {}
    return {
        v.get('name', ''): v.get('status', 'unknown')
        for v in validations
        if v.get('name')
    }


def evaluate_iteration(
    iteration: int,
    history: list[dict[str, Any]],
    validation_before: list[dict[str, Any]] | None = None,
    validation_after: list[dict[str, Any]] | None = None,
) -> IterationEval:
    """Evaluate what happened in the latest iteration.

    Compares before/after state to produce concrete feedback.
    """
    ev = IterationEval(iteration=iteration)

    # --- Score movement ---
    if len(history) >= 2:
        prev = history[-2].get('score', 0) or 0
        curr = history[-1].get('score', 0) or 0
        delta = curr - prev
        ev.score = ScoreMovement(
            before=prev,
            after=curr,
            delta=delta,
            direction='improved' if delta > 0 else ('regressed' if delta < 0 else 'neutral'),
        )
    elif len(history) == 1:
        curr = history[-1].get('score', 0) or 0
        ev.score = ScoreMovement(after=curr, direction='neutral')

    # --- Files changed ---
    latest = history[-1] if history else {}
    ev.files_changed = latest.get('changedFiles', [])

    # --- Validation comparison ---
    val_before = _extract_validation_map(validation_before)
    val_after = _extract_validation_map(validation_after)
    ev.validations_before = val_before
    ev.validations_after = val_after

    # What was fixed (was failing, now passing)
    for name, status in val_after.items():
        prev_status = val_before.get(name, 'unknown')
        if status == 'pass' and prev_status != 'pass':
            ev.validations_fixed.append(name)
        elif status != 'pass' and prev_status == 'pass':
            ev.validations_broken.append(name)

    # --- Determine if approach worked ---
    ev.approach_worked = ev.score.delta > 0 or len(ev.validations_fixed) > 0

    # --- Generate feedback ---
    if ev.score.direction == 'improved':
        ev.what_helped = f'Score improved by {ev.score.delta} points.'
        if ev.files_changed:
            ev.what_helped += f' Changes to {", ".join(ev.files_changed[:3])} were effective.'
        ev.score.explanation = 'Progress — continue in this direction.'

    elif ev.score.direction == 'regressed':
        ev.what_hurt = f'Score dropped by {abs(ev.score.delta)} points.'
        if ev.files_changed:
            ev.what_hurt += f' Recent changes to {", ".join(ev.files_changed[:3])} may have caused this.'
        ev.correction = 'STOP doing what caused the regression. Consider reverting recent changes.'
        ev.score.explanation = 'Regression — the last change made things worse.'

    elif ev.score.direction == 'neutral' and iteration >= 2:
        ev.correction = 'Score unchanged. The last attempt had no effect. Try a different approach.'
        ev.score.explanation = 'Stagnant — need a different strategy.'

    # Validation-specific feedback
    if ev.validations_broken:
        broken_str = ', '.join(ev.validations_broken)
        ev.what_hurt = f'{ev.what_hurt} Broke validation: {broken_str}.' if ev.what_hurt else f'Broke validation: {broken_str}.'
        ev.correction = f'{ev.correction} Fix {broken_str} before proceeding.' if ev.correction else f'Fix {broken_str} before proceeding.'

    if ev.validations_fixed:
        fixed_str = ', '.join(ev.validations_fixed)
        ev.what_helped = f'{ev.what_helped} Fixed validation: {fixed_str}.' if ev.what_helped else f'Fixed validation: {fixed_str}.'

    return ev


def render_eval_for_prompt(ev: IterationEval) -> str:
    """Render self-evaluation for injection into the next prompt.

    Returns empty string if nothing meaningful to say.
    """
    if ev.iteration < 1:
        return ''

    lines: list[str] = []
    lines.append('## 📊 Last Iteration Self-Evaluation')

    # Score
    if ev.score.delta != 0:
        icon = '📈' if ev.score.delta > 0 else '📉'
        lines.append(f'{icon} **Score:** {ev.score.before} → {ev.score.after} ({ev.score.delta:+d})')
    elif ev.score.after > 0:
        lines.append(f'➡️ **Score:** {ev.score.after} (unchanged)')

    if ev.score.explanation:
        lines.append(f'  _{ev.score.explanation}_')

    # What worked
    if ev.what_helped:
        lines.append(f'✅ **What worked:** {ev.what_helped}')

    # What went wrong
    if ev.what_hurt:
        lines.append(f'❌ **What went wrong:** {ev.what_hurt}')

    # Correction directive
    if ev.correction:
        lines.append(f'🔧 **Correction:** {ev.correction}')

    # Validation changes
    if ev.validations_fixed:
        lines.append(f'🟢 **Fixed:** {", ".join(ev.validations_fixed)}')
    if ev.validations_broken:
        lines.append(f'🔴 **Broken:** {", ".join(ev.validations_broken)}')

    return '\n'.join(lines)
