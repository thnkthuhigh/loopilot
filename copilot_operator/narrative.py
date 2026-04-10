"""Run Narrative Layer — transforms raw operator data into human-readable prose.

Three levels of narrative:
1. **Live Status** — terse per-iteration status line (milestone, validation, next action)
2. **Run Summary** — prose paragraph summarizing what happened across the whole run
3. **Done Explanation** — structured evidence of why the operator stopped

All functions are pure: they take data dicts and return strings. No I/O.
"""

from __future__ import annotations

__all__ = [
    'LiveStatus',
    'DoneExplanation',
    'RunNarrative',
    'build_live_status',
    'build_done_explanation',
    'build_run_narrative',
    'write_done_explanation_file',
    'write_narrative_file',
]

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class LiveStatus:
    """Snapshot of what the operator is doing right now."""
    iteration: int = 0
    max_iterations: int = 0
    milestone: str = ''
    task: str = ''
    last_action: str = ''
    last_validation: str = ''
    score: int | None = None
    next_action: str = ''

    def render(self) -> str:
        parts = [f'[{self.iteration}/{self.max_iterations}]']
        if self.milestone:
            parts.append(f'Milestone: {self.milestone}')
        if self.task:
            parts.append(f'Task: {self.task}')
        if self.last_action:
            parts.append(f'Action: {self.last_action}')
        if self.last_validation:
            parts.append(f'Validation: {self.last_validation}')
        if self.score is not None:
            parts.append(f'Score: {self.score}')
        if self.next_action:
            parts.append(f'Next: {self.next_action}')
        return ' | '.join(parts)


@dataclass(slots=True)
class DoneExplanation:
    """Structured evidence of why the operator stopped."""
    goal: str = ''
    outcome: str = ''                                  # complete | blocked | error
    stop_reason: str = ''
    stop_reason_code: str = ''
    conditions_met: list[str] = field(default_factory=list)
    conditions_unmet: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)  # concrete proof items
    unchecked: list[str] = field(default_factory=list)  # things not verified
    risks: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = []
        lines.append('## Done Explanation')
        lines.append(f'**Goal:** {self.goal}')
        lines.append(f'**Outcome:** {self.outcome}')
        lines.append(f'**Stop reason:** {self.stop_reason} ({self.stop_reason_code})')
        if self.conditions_met:
            lines.append('\n**Conditions met:**')
            for c in self.conditions_met:
                lines.append(f'  ✓ {c}')
        if self.conditions_unmet:
            lines.append('\n**Conditions NOT met:**')
            for c in self.conditions_unmet:
                lines.append(f'  ✗ {c}')
        if self.evidence:
            lines.append('\n**Evidence:**')
            for e in self.evidence:
                lines.append(f'  • {e}')
        if self.unchecked:
            lines.append('\n**Not checked:**')
            for u in self.unchecked:
                lines.append(f'  ? {u}')
        if self.risks:
            lines.append('\n**Remaining risks:**')
            for r in self.risks:
                lines.append(f'  ⚠ {r}')
        return '\n'.join(lines)


@dataclass(slots=True)
class RunNarrative:
    """Full prose narrative of a completed run."""
    goal: str = ''
    outcome: str = ''
    iterations: int = 0
    score: int | None = None
    summary: str = ''                                     # prose paragraph
    iteration_arc: list[str] = field(default_factory=list)  # per-iteration one-liner
    files_changed: list[str] = field(default_factory=list)
    validation_summary: str = ''
    done_explanation: DoneExplanation | None = None

    def render(self) -> str:
        lines = []
        lines.append('# Run Narrative')
        lines.append(f'**Goal:** {self.goal}')
        lines.append(f'**Outcome:** {self.outcome} (score {self.score}, {self.iterations} iterations)')
        lines.append('')
        if self.summary:
            lines.append(self.summary)
            lines.append('')
        if self.iteration_arc:
            lines.append('## Iteration Arc')
            for arc in self.iteration_arc:
                lines.append(f'  {arc}')
            lines.append('')
        if self.files_changed:
            lines.append(f'## Files Changed ({len(self.files_changed)})')
            for f in self.files_changed[:30]:
                lines.append(f'  - {f}')
            if len(self.files_changed) > 30:
                lines.append(f'  ... and {len(self.files_changed) - 30} more')
            lines.append('')
        if self.validation_summary:
            lines.append('## Validation')
            lines.append(self.validation_summary)
            lines.append('')
        if self.done_explanation:
            lines.append(self.done_explanation.render())
        return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Builder: Live Status
# ---------------------------------------------------------------------------

def build_live_status(runtime: dict[str, Any], iteration: int, max_iterations: int) -> LiveStatus:
    """Build a LiveStatus snapshot from current operator runtime state."""
    history = runtime.get('history', [])
    last_record = history[-1] if history else {}

    # Milestone + task
    milestone = last_record.get('currentMilestoneId', '') or last_record.get('planSummary', '')
    task = last_record.get('currentTaskId', '')

    # Last action + validation
    last_action = last_record.get('decisionCode', '')
    validation_items = last_record.get('validation_after', [])
    v_pass = sum(1 for v in validation_items if v.get('status') == 'pass')
    v_fail = sum(1 for v in validation_items if v.get('status') != 'pass')
    last_validation = f'{v_pass} pass / {v_fail} fail' if validation_items else ''

    # Score
    score = last_record.get('score')

    # Next action
    pending = runtime.get('pendingDecision', {})
    next_action = pending.get('nextPrompt', '')[:100] if pending else ''

    return LiveStatus(
        iteration=iteration,
        max_iterations=max_iterations,
        milestone=milestone[:60],
        task=task[:40],
        last_action=last_action,
        last_validation=last_validation,
        score=score,
        next_action=next_action,
    )


# ---------------------------------------------------------------------------
# Builder: Done Explanation
# ---------------------------------------------------------------------------

def build_done_explanation(
    goal: str,
    runtime: dict[str, Any],
    result: dict[str, Any],
    config_target_score: int = 85,
) -> DoneExplanation:
    """Build a structured Done Explanation from operator result + runtime."""
    outcome = result.get('status', 'unknown')
    stop_reason = result.get('reason', '')
    stop_reason_code = result.get('reasonCode', '')

    history = runtime.get('history', [])
    last_record = history[-1] if history else {}
    final_score = result.get('score') or last_record.get('score')

    # --- Conditions Met / Unmet ---
    conditions_met: list[str] = []
    conditions_unmet: list[str] = []

    # Score threshold
    if final_score is not None:
        if final_score >= config_target_score:
            conditions_met.append(f'Score {final_score} >= target {config_target_score}')
        else:
            conditions_unmet.append(f'Score {final_score} < target {config_target_score}')

    # Validation
    validation = last_record.get('validation_after', [])
    v_pass = [v for v in validation if v.get('status') == 'pass']
    v_fail = [v for v in validation if v.get('status') != 'pass']
    if v_pass:
        names = ', '.join(v['name'] for v in v_pass)
        conditions_met.append(f'Validation pass: {names}')
    if v_fail:
        names = ', '.join(v['name'] for v in v_fail)
        conditions_unmet.append(f'Validation fail: {names}')

    # Blockers
    blockers = last_record.get('blockers') or []
    critical = [b for b in blockers if b.get('severity') in ('critical', 'high')]
    if not critical:
        conditions_met.append('No critical/high blockers')
    else:
        items = ', '.join(b.get('item', '?') for b in critical)
        conditions_unmet.append(f'Blockers remain: {items}')

    # Status=done from Copilot
    if last_record.get('status') == 'done':
        conditions_met.append('Copilot reported status=done')

    # --- Evidence ---
    evidence: list[str] = []
    changed = runtime.get('allChangedFiles', [])
    if changed:
        evidence.append(f'{len(changed)} file(s) changed: {", ".join(changed[:10])}')
    if v_pass:
        for v in v_pass:
            evidence.append(f'{v["name"]}: pass')
    if final_score is not None:
        # Score trajectory
        scores = [h.get('score') for h in history if h.get('score') is not None]
        if len(scores) > 1:
            evidence.append(f'Score trajectory: {" → ".join(str(s) for s in scores)}')

    # --- Unchecked ---
    unchecked: list[str] = []
    # Integration tests, type-check, etc. — heuristic
    val_names = {v.get('name', '') for v in validation}
    if 'typecheck' not in val_names and 'type-check' not in val_names:
        unchecked.append('Type checking not configured')
    if 'integration' not in val_names:
        unchecked.append('Integration tests not configured')
    if 'security' not in val_names:
        unchecked.append('Security scanning not configured')

    # --- Risks ---
    risks: list[str] = []
    if outcome == 'blocked':
        risks.append(f'Run blocked — {stop_reason}')
    if outcome == 'error':
        risks.append(f'Run errored — {stop_reason}')
    if v_fail:
        risks.append(f'{len(v_fail)} validation(s) still failing')

    # Score regression risk
    scores = [h.get('score') for h in history if h.get('score') is not None]
    if len(scores) >= 2 and scores[-1] is not None and scores[-2] is not None:
        if scores[-1] < scores[-2]:
            risks.append(f'Score dropped in final iteration: {scores[-2]} → {scores[-1]}')

    # Large diff risk
    if len(changed) > 15:
        risks.append(f'Large change set ({len(changed)} files) — review carefully')

    return DoneExplanation(
        goal=goal,
        outcome=outcome,
        stop_reason=stop_reason,
        stop_reason_code=stop_reason_code,
        conditions_met=conditions_met,
        conditions_unmet=conditions_unmet,
        evidence=evidence,
        unchecked=unchecked,
        risks=risks,
    )


# ---------------------------------------------------------------------------
# Builder: Run Narrative (prose summary of the whole run)
# ---------------------------------------------------------------------------

def _describe_iteration(i: int, record: dict[str, Any]) -> str:
    """One-line description of a single iteration."""
    score = record.get('score')
    status = record.get('status', '?')
    code = record.get('decisionCode', '')
    summary = (record.get('summary') or '')[:80]

    # Validation summary
    v_after = record.get('validation_after', [])
    v_pass = sum(1 for v in v_after if v.get('status') == 'pass')
    v_fail = sum(1 for v in v_after if v.get('status') != 'pass')
    v_str = f' [{v_pass}✓/{v_fail}✗]' if v_after else ''

    score_str = f' score={score}' if score is not None else ''
    return f'#{i}: {status}{score_str}{v_str} → {code} — {summary}'


def _build_prose_summary(
    goal: str,
    history: list[dict[str, Any]],
    result: dict[str, Any],
    changed_files: list[str],
) -> str:
    """Build a prose paragraph summarizing the run arc."""
    n = len(history)
    outcome = result.get('status', 'unknown')
    reason_code = result.get('reasonCode', '')

    if n == 0:
        return 'No iterations were executed.'

    # Opening
    if n == 1:
        opening = 'Completed in a single iteration.'
    else:
        opening = f'Ran {n} iterations.'

    # Score arc
    scores = [h.get('score') for h in history if h.get('score') is not None]
    if len(scores) >= 2:
        first, last = scores[0], scores[-1]
        if last > first:
            score_arc = f'Score improved from {first} to {last}.'
        elif last < first:
            score_arc = f'Score regressed from {first} to {last}.'
        else:
            score_arc = f'Score remained at {last}.'
    elif scores:
        score_arc = f'Final score: {scores[-1]}.'
    else:
        score_arc = ''

    # Validation arc
    v_first = history[0].get('validation_after', [])
    v_last = history[-1].get('validation_after', [])
    first_fail = sum(1 for v in v_first if v.get('status') != 'pass')
    last_fail = sum(1 for v in v_last if v.get('status') != 'pass')
    last_pass = sum(1 for v in v_last if v.get('status') == 'pass')

    if first_fail > 0 and last_fail == 0:
        val_arc = f'Started with {first_fail} validation failure(s), all resolved by the end.'
    elif last_fail > 0:
        names = ', '.join(v['name'] for v in v_last if v.get('status') != 'pass')
        val_arc = f'{last_fail} validation(s) still failing at end: {names}.'
    elif last_pass > 0:
        val_arc = f'All {last_pass} validation(s) passing.'
    else:
        val_arc = ''

    # Files
    fc = len(changed_files)
    files_arc = f'Changed {fc} file(s).' if fc else 'No files changed.'

    # Outcome
    if outcome == 'complete':
        outcome_line = f'Run completed successfully ({reason_code}).'
    elif outcome == 'blocked':
        outcome_line = f'Run blocked: {reason_code}.'
    elif outcome == 'error':
        outcome_line = f'Run errored: {reason_code}.'
    else:
        outcome_line = f'Run ended with status: {outcome}.'

    parts = [p for p in [opening, score_arc, val_arc, files_arc, outcome_line] if p]
    return ' '.join(parts)


def build_run_narrative(
    goal: str,
    runtime: dict[str, Any],
    result: dict[str, Any],
    config_target_score: int = 85,
) -> RunNarrative:
    """Build a full RunNarrative from operator data."""
    history = runtime.get('history', [])
    changed_files = runtime.get('allChangedFiles', [])

    # Per-iteration arc
    iteration_arc = [_describe_iteration(h.get('iteration', i + 1), h) for i, h in enumerate(history)]

    # Prose summary
    summary = _build_prose_summary(goal, history, result, changed_files)

    # Validation summary
    v_last = history[-1].get('validation_after', []) if history else []
    v_lines = []
    for v in v_last:
        status_icon = '✓' if v.get('status') == 'pass' else '✗'
        v_lines.append(f'{status_icon} {v.get("name", "?")}')
    validation_summary = '\n'.join(v_lines)

    # Done explanation
    done_explanation = build_done_explanation(goal, runtime, result, config_target_score)

    return RunNarrative(
        goal=goal,
        outcome=result.get('status', 'unknown'),
        iterations=len(history),
        score=result.get('score'),
        summary=summary,
        iteration_arc=iteration_arc,
        files_changed=changed_files,
        validation_summary=validation_summary,
        done_explanation=done_explanation,
    )


# ---------------------------------------------------------------------------
# File I/O — write narrative to disk
# ---------------------------------------------------------------------------

def write_narrative_file(workspace: object, run_id: str, narrative: RunNarrative) -> object:
    """Write the narrative to .copilot-operator/logs/{run_id}/narrative.md"""
    from pathlib import Path
    ws = Path(str(workspace))
    log_dir = ws / '.copilot-operator' / 'logs' / run_id
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / 'narrative.md'
    path.write_text(narrative.render(), encoding='utf-8')
    return path


def write_done_explanation_file(workspace: object, run_id: str, explanation: DoneExplanation) -> object:
    """Write the done explanation to .copilot-operator/logs/{run_id}/done-explanation.md"""
    from pathlib import Path
    ws = Path(str(workspace))
    log_dir = ws / '.copilot-operator' / 'logs' / run_id
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / 'done-explanation.md'
    path.write_text(explanation.render(), encoding='utf-8')
    return path
