"""Task Ledger — structured per-run state that survives session resets.

Each operator run gets a ledger that tracks:
  - goal + acceptance criteria
  - plan milestones
  - decisions made
  - files touched
  - validations run
  - commitments (what was decided, what is owed, what must not be forgotten)
  - outcome

The ledger is persisted to ``logs/{runId}/ledger.json`` and injected into
the prompt each iteration so the model "lives in the plan" rather than
just seeing it.

This is the **Task Memory** layer in the 5-tier memory model:
  Working → Task (this) → Project → Mission → Cross-repo
"""

from __future__ import annotations

__all__ = [
    'TaskLedger',
    'LedgerEntry',
    'Commitments',
    'PriorityPressure',
    'load_ledger',
    'save_ledger',
    'update_ledger_from_iteration',
    'build_commitments',
    'compute_priority_pressure',
    'render_ledger_for_prompt',
]

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .logging_config import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class LedgerEntry:
    """A single line-item in the ledger."""
    iteration: int = 0
    action: str = ''           # what was done
    result: str = ''           # outcome of the action
    score: int | None = None
    decision_code: str = ''    # CONTINUE / STOP_GATE_PASSED / etc.
    blockers: list[str] = field(default_factory=list)
    files_changed: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Commitments:
    """What the run committed to — survives session resets."""
    decided: list[str] = field(default_factory=list)          # decisions already made
    owed: list[str] = field(default_factory=list)             # obligations not yet fulfilled
    next_step: str = ''                                        # immediate next action
    must_not_forget: list[str] = field(default_factory=list)  # critical context


@dataclass(slots=True)
class TaskLedger:
    """Structured per-run task state."""
    run_id: str = ''
    goal: str = ''
    acceptance_criteria: list[str] = field(default_factory=list)
    plan_milestones: list[str] = field(default_factory=list)
    entries: list[LedgerEntry] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    files_touched: list[str] = field(default_factory=list)
    validations_run: list[str] = field(default_factory=list)
    commitments: Commitments = field(default_factory=Commitments)
    outcome: str = ''          # complete | blocked | error | running
    stop_reason: str = ''


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _ledger_path(workspace: Path, run_id: str) -> Path:
    return workspace / '.copilot-operator' / 'logs' / run_id / 'ledger.json'


def load_ledger(workspace: Path, run_id: str) -> TaskLedger:
    """Load a task ledger from disk. Returns empty ledger if not found."""
    path = _ledger_path(workspace, run_id)
    if not path.exists():
        return TaskLedger(run_id=run_id)
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        ledger = TaskLedger(
            run_id=data.get('run_id', run_id),
            goal=data.get('goal', ''),
            acceptance_criteria=data.get('acceptance_criteria', []),
            plan_milestones=data.get('plan_milestones', []),
            decisions=data.get('decisions', []),
            files_touched=data.get('files_touched', []),
            validations_run=data.get('validations_run', []),
            outcome=data.get('outcome', ''),
            stop_reason=data.get('stop_reason', ''),
        )
        # Restore entries
        for e in data.get('entries', []):
            ledger.entries.append(LedgerEntry(
                iteration=e.get('iteration', 0),
                action=e.get('action', ''),
                result=e.get('result', ''),
                score=e.get('score'),
                decision_code=e.get('decision_code', ''),
                blockers=e.get('blockers', []),
                files_changed=e.get('files_changed', []),
            ))
        # Restore commitments
        c = data.get('commitments', {})
        ledger.commitments = Commitments(
            decided=c.get('decided', []),
            owed=c.get('owed', []),
            next_step=c.get('next_step', ''),
            must_not_forget=c.get('must_not_forget', []),
        )
        return ledger
    except Exception:
        logger.debug('Failed to load ledger from %s', path)
        return TaskLedger(run_id=run_id)


def save_ledger(workspace: Path, ledger: TaskLedger) -> Path:
    """Persist the task ledger to disk."""
    path = _ledger_path(workspace, ledger.run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(ledger), indent=2, default=str), encoding='utf-8')
    return path


# ---------------------------------------------------------------------------
# Update from iteration
# ---------------------------------------------------------------------------

def update_ledger_from_iteration(
    ledger: TaskLedger,
    iteration: int,
    record: dict[str, Any],
) -> None:
    """Update the ledger after an iteration completes.

    Extracts relevant fields from the history record dict.
    """
    entry = LedgerEntry(
        iteration=iteration,
        action=record.get('summary', '')[:200],
        result=record.get('status', ''),
        score=record.get('score'),
        decision_code=record.get('decisionCode', ''),
        blockers=[b.get('item', '') for b in (record.get('blockers') or []) if b.get('item')],
        files_changed=record.get('changedFiles', []),
    )
    ledger.entries.append(entry)

    # Accumulate files touched (deduplicated)
    for f in entry.files_changed:
        if f not in ledger.files_touched:
            ledger.files_touched.append(f)

    # Accumulate validations run
    for v in record.get('validation_after') or []:
        name = v.get('name', '')
        status = v.get('status', '')
        tag = f'{name}:{status}'
        if name and tag not in ledger.validations_run:
            ledger.validations_run.append(tag)


# ---------------------------------------------------------------------------
# Commitment builder
# ---------------------------------------------------------------------------

def build_commitments(
    ledger: TaskLedger,
    result: dict[str, Any],
) -> Commitments:
    """Build commitments from the current ledger and run result.

    Called at end of run to capture what was decided, what is owed, and
    what must not be forgotten.
    """
    decided: list[str] = list(ledger.decisions)
    owed: list[str] = []
    must_not_forget: list[str] = []
    next_step = ''

    status = result.get('status', '')
    reason_code = result.get('reasonCode', '')

    # If not complete, the remaining plan items are owed
    if status != 'complete':
        plan = result.get('plan', {})
        remaining = []
        for ms in plan.get('milestones', []):
            for task in ms.get('tasks', []):
                if task.get('status') != 'done':
                    remaining.append(task.get('title', ''))
        owed = remaining[:5]  # cap at 5
        next_step = f'Resume: {reason_code} — {result.get("reason", "")[:100]}'

        # Blockers are must-not-forget
        for entry in ledger.entries:
            for b in entry.blockers:
                if b and b not in must_not_forget:
                    must_not_forget.append(b)
    else:
        next_step = 'Task complete.'

    # Validation failures are must-not-forget
    failing = [v for v in ledger.validations_run if ':fail' in v or ':timeout' in v]
    for f in failing:
        if f not in must_not_forget:
            must_not_forget.append(f)

    return Commitments(
        decided=decided[:10],
        owed=owed[:5],
        next_step=next_step,
        must_not_forget=must_not_forget[:10],
    )


# ---------------------------------------------------------------------------
# Priority Pressure — guides the model on what matters NOW
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class PriorityPressure:
    """What the model should focus on, what's blocking, what can be deferred.

    This is the missing "pressure" that prevents the model from going off
    on tangents or optimising the wrong thing.
    """
    focus: str = ''                                      # THE most important thing right now
    blocking_now: list[str] = field(default_factory=list)  # things that prevent progress
    critical_path: list[str] = field(default_factory=list)  # ordered steps to unblock
    can_defer: list[str] = field(default_factory=list)     # things safe to skip for now
    urgency: str = 'normal'                               # low | normal | high | critical
    reasoning: str = ''                                    # why this is the priority


def compute_priority_pressure(
    ledger: TaskLedger,
    plan: dict[str, Any] | None = None,
    target_score: int = 85,
) -> PriorityPressure:
    """Analyse the current ledger and plan to determine priority pressure.

    This answers: "What should I focus on RIGHT NOW?"
    """
    pp = PriorityPressure()

    # --- Analyse score trajectory ---
    scores = [e.score for e in ledger.entries if e.score is not None]
    latest_score = scores[-1] if scores else 0
    is_regressing = len(scores) >= 2 and scores[-1] < scores[-2]
    is_stuck = len(scores) >= 3 and len(set(scores[-3:])) == 1 and latest_score < target_score
    score_gap = target_score - latest_score if latest_score < target_score else 0

    # --- Collect active blockers ---
    if ledger.entries:
        latest = ledger.entries[-1]
        pp.blocking_now = list(latest.blockers)

    # --- Collect validation failures ---
    failing_validations = [v for v in ledger.validations_run if ':fail' in v or ':timeout' in v]

    # --- Determine urgency ---
    if pp.blocking_now or is_regressing:
        pp.urgency = 'critical'
    elif failing_validations or score_gap > 30:
        pp.urgency = 'high'
    elif score_gap > 0:
        pp.urgency = 'normal'
    else:
        pp.urgency = 'low'

    # --- Determine focus ---
    if pp.blocking_now:
        pp.focus = f'Unblock: {pp.blocking_now[0]}'
        pp.reasoning = 'Active blocker prevents all progress. Fix this first.'
        pp.critical_path = [f'Fix: {b}' for b in pp.blocking_now[:3]]
    elif failing_validations:
        top_fail = failing_validations[0].split(':')[0]
        pp.focus = f'Fix failing validation: {top_fail}'
        pp.reasoning = 'Validation must pass before score can reach target.'
        pp.critical_path = [f'Fix: {v.split(":")[0]}' for v in failing_validations[:3]]
    elif is_regressing:
        pp.focus = f'Stop regression — score dropped from {scores[-2]} to {scores[-1]}'
        pp.reasoning = 'Score is going backward. Revert or fix the cause before continuing.'
        pp.critical_path = ['Identify what caused regression', 'Revert or fix', 'Verify score recovers']
    elif is_stuck:
        pp.focus = f'Break stall — stuck at score {latest_score} for {len(scores)} iterations'
        pp.reasoning = 'Same score repeated. Need a different approach.'
        pp.critical_path = ['Try a different strategy', 'Check if blocked by undetected issue']
    elif score_gap > 0:
        pp.focus = f'Close gap: {latest_score} → {target_score} ({score_gap} points needed)'
        pp.reasoning = 'On track but not done yet. Focus on highest-impact remaining work.'
    else:
        pp.focus = 'Verify completion — score meets target'
        pp.reasoning = 'Score looks good. Verify all acceptance criteria are met.'

    # --- Determine what can be deferred ---
    if plan and isinstance(plan, dict):
        for ms in plan.get('milestones', []):
            for task in ms.get('tasks', []):
                title = task.get('title', '')
                status = task.get('status', '')
                if status == 'done':
                    continue
                # Low-priority items that don't affect core goal
                lower = title.lower()
                if any(kw in lower for kw in ('doc', 'readme', 'comment', 'style', 'format', 'cleanup', 'refactor')):
                    pp.can_defer.append(title)

    return pp


# ---------------------------------------------------------------------------
# Prompt injection
# ---------------------------------------------------------------------------

def render_ledger_for_prompt(
    ledger: TaskLedger,
    priority: PriorityPressure | None = None,
) -> str:
    """Render the task ledger as a concise block for prompt injection.

    Returns empty string if ledger has no entries.
    """
    if not ledger.entries:
        return ''

    lines: list[str] = []
    lines.append('## Task Ledger')
    lines.append(f'**Goal:** {ledger.goal}')

    if ledger.acceptance_criteria:
        lines.append('**Acceptance criteria:**')
        for c in ledger.acceptance_criteria:
            lines.append(f'  - {c}')

    # Priority pressure — THE MOST IMPORTANT SECTION
    if priority and priority.focus:
        urgency_icon = {'critical': '🚨', 'high': '⚠️', 'normal': '📌', 'low': '📎'}.get(priority.urgency, '📌')
        lines.append(f'\n{urgency_icon} **PRIORITY [{priority.urgency.upper()}]:** {priority.focus}')
        if priority.reasoning:
            lines.append(f'  _Why:_ {priority.reasoning}')
        if priority.blocking_now:
            lines.append('  **Blocking NOW:**')
            for b in priority.blocking_now[:3]:
                lines.append(f'    🔴 {b}')
        if priority.critical_path:
            lines.append('  **Critical path:**')
            for i, step in enumerate(priority.critical_path[:3], 1):
                lines.append(f'    {i}. {step}')
        if priority.can_defer:
            lines.append('  **Can defer:**')
            for d in priority.can_defer[:3]:
                lines.append(f'    ↩ {d}')

    # Score trajectory (last 5)
    scores = [(e.iteration, e.score) for e in ledger.entries if e.score is not None]
    if scores:
        trajectory = ' → '.join(f'i{it}:{s}' for it, s in scores[-5:])
        lines.append(f'**Score trajectory:** {trajectory}')

    # Current status
    latest = ledger.entries[-1]
    lines.append(f'**Current:** iteration {latest.iteration}, score {latest.score}, {latest.decision_code}')

    # Active blockers from latest entry
    if latest.blockers:
        lines.append('**Active blockers:**')
        for b in latest.blockers:
            lines.append(f'  ⚠ {b}')

    # Commitments
    c = ledger.commitments
    if c.decided:
        lines.append('**Decided:**')
        for d in c.decided[-5:]:
            lines.append(f'  ✓ {d}')
    if c.owed:
        lines.append('**Still owed:**')
        for o in c.owed:
            lines.append(f'  ◻ {o}')
    if c.must_not_forget:
        lines.append('**Must not forget:**')
        for m in c.must_not_forget:
            lines.append(f'  ❗ {m}')
    if c.next_step:
        lines.append(f'**Next step:** {c.next_step}')

    return '\n'.join(lines)
