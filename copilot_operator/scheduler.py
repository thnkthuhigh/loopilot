"""Multi-session scheduler — manages parallel Copilot sessions with roles.

Provides the framework for running multiple operator sessions concurrently
with different roles (coder, reviewer, tester) and merging their results.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class SessionRole:
    """Configuration for a specific session role."""
    name: str                    # coder, reviewer, tester, docs
    goal_profile: str = 'default'
    goal_suffix: str = ''        # appended to the base goal
    max_iterations: int = 6
    priority: int = 0            # higher = runs first
    depends_on: list[str] = field(default_factory=list)  # role names that must complete first


@dataclass(slots=True)
class SessionSlot:
    """A single session slot in the scheduler."""
    slot_id: str = ''
    role: str = ''
    goal: str = ''
    goal_profile: str = 'default'
    status: str = 'pending'      # pending, running, complete, blocked, skipped
    run_id: str = ''
    final_score: int | None = None
    final_reason_code: str = ''
    result: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SchedulerPlan:
    """A multi-session execution plan."""
    plan_id: str = ''
    base_goal: str = ''
    slots: list[SessionSlot] = field(default_factory=list)
    execution_order: list[str] = field(default_factory=list)  # slot_ids in order
    status: str = 'pending'      # pending, running, complete, blocked


# ---------------------------------------------------------------------------
# Built-in role templates
# ---------------------------------------------------------------------------

ROLE_TEMPLATES: dict[str, SessionRole] = {
    'coder': SessionRole(
        name='coder',
        goal_profile='feature',
        goal_suffix='Implement the changes. Focus on code correctness and test coverage.',
        priority=10,
    ),
    'reviewer': SessionRole(
        name='reviewer',
        goal_profile='audit',
        goal_suffix='Review the changes made so far. Identify issues, suggest improvements. Do not make code changes unless critical.',
        priority=5,
        depends_on=['coder'],
    ),
    'tester': SessionRole(
        name='tester',
        goal_profile='default',
        goal_suffix='Verify that all tests pass and add missing test coverage for recent changes.',
        priority=5,
        depends_on=['coder'],
    ),
    'fixer': SessionRole(
        name='fixer',
        goal_profile='bug',
        goal_suffix='Fix the issues identified by the reviewer. Focus on minimal, targeted patches.',
        priority=8,
        depends_on=['reviewer'],
    ),
    'docs': SessionRole(
        name='docs',
        goal_profile='docs',
        goal_suffix='Update documentation to reflect the changes. Ensure examples match real code.',
        priority=2,
        depends_on=['coder'],
    ),
}


# ---------------------------------------------------------------------------
# Plan creation
# ---------------------------------------------------------------------------

def create_scheduler_plan(
    base_goal: str,
    roles: list[str] | None = None,
    custom_roles: list[SessionRole] | None = None,
) -> SchedulerPlan:
    """Create a multi-session plan from a base goal and a list of roles.

    Parameters
    ----------
    base_goal:
        The high-level goal that all sessions work towards.
    roles:
        List of built-in role names to include (e.g., ['coder', 'reviewer']).
        Defaults to ['coder'] if not specified.
    custom_roles:
        Optional list of custom SessionRole objects to include alongside built-in roles.
    """
    plan_id = str(uuid4())
    selected_roles: list[SessionRole] = []

    for role_name in (roles or ['coder']):
        template = ROLE_TEMPLATES.get(role_name)
        if template:
            selected_roles.append(template)

    if custom_roles:
        selected_roles.extend(custom_roles)

    # Sort by priority (highest first), then by dependency
    selected_roles.sort(key=lambda r: -r.priority)
    selected_roles = _topological_sort(selected_roles)

    slots: list[SessionSlot] = []
    for role in selected_roles:
        slot_id = f'{plan_id[:8]}-{role.name}'
        goal = f'{base_goal}\n\nRole: {role.name}\n{role.goal_suffix}'.strip()
        slots.append(SessionSlot(
            slot_id=slot_id,
            role=role.name,
            goal=goal,
            goal_profile=role.goal_profile,
            status='pending',
        ))

    return SchedulerPlan(
        plan_id=plan_id,
        base_goal=base_goal,
        slots=slots,
        execution_order=[s.slot_id for s in slots],
        status='pending',
    )


def _topological_sort(roles: list[SessionRole]) -> list[SessionRole]:
    """Sort roles respecting depends_on constraints."""
    resolved: list[SessionRole] = []
    resolved_names: set[str] = set()
    remaining = list(roles)

    max_passes = len(remaining) + 1
    for _ in range(max_passes):
        if not remaining:
            break
        next_remaining: list[SessionRole] = []
        for role in remaining:
            deps_met = all(d in resolved_names for d in role.depends_on)
            if deps_met:
                resolved.append(role)
                resolved_names.add(role.name)
            else:
                next_remaining.append(role)
        if len(next_remaining) == len(remaining):
            # Circular dependency — just append remaining
            resolved.extend(next_remaining)
            break
        remaining = next_remaining

    return resolved


# ---------------------------------------------------------------------------
# Slot lifecycle
# ---------------------------------------------------------------------------

def get_next_runnable_slot(plan: SchedulerPlan) -> SessionSlot | None:
    """Return the next slot that is ready to run (dependencies met)."""
    completed_roles = {s.role for s in plan.slots if s.status in ('complete', 'skipped')}
    blocked_roles = {s.role for s in plan.slots if s.status == 'blocked'}

    for slot_id in plan.execution_order:
        slot = next((s for s in plan.slots if s.slot_id == slot_id), None)
        if slot is None or slot.status != 'pending':
            continue
        role_template = ROLE_TEMPLATES.get(slot.role)
        deps = role_template.depends_on if role_template else []
        # Skip if a dependency is blocked
        if any(d in blocked_roles for d in deps):
            slot.status = 'skipped'
            continue
        if all(d in completed_roles for d in deps):
            return slot
    return None


def mark_slot_running(slot: SessionSlot, run_id: str) -> None:
    slot.status = 'running'
    slot.run_id = run_id


def mark_slot_complete(slot: SessionSlot, result: dict[str, Any]) -> None:
    slot.status = result.get('status', 'complete')
    slot.final_score = result.get('score')
    slot.final_reason_code = result.get('reasonCode', '')
    slot.result = result


def is_plan_complete(plan: SchedulerPlan) -> bool:
    """Check if all slots have reached a terminal state."""
    return all(s.status in ('complete', 'blocked', 'skipped') for s in plan.slots)


def update_plan_status(plan: SchedulerPlan) -> None:
    """Recompute the overall plan status."""
    if is_plan_complete(plan):
        all_ok = all(s.status in ('complete', 'skipped') for s in plan.slots)
        plan.status = 'complete' if all_ok else 'blocked'
    elif any(s.status == 'running' for s in plan.slots):
        plan.status = 'running'


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render_scheduler_plan(plan: SchedulerPlan) -> str:
    """Human-readable summary of the scheduler plan."""
    lines = [
        f'Multi-session plan: {plan.plan_id[:8]}',
        f'Base goal: {plan.base_goal[:100]}',
        f'Status: {plan.status}',
        f'Slots: {len(plan.slots)}',
        '',
    ]
    for slot in plan.slots:
        score_text = f', score={slot.final_score}' if slot.final_score is not None else ''
        reason_text = f', reason={slot.final_reason_code}' if slot.final_reason_code else ''
        lines.append(f'  [{slot.status}] {slot.role} ({slot.goal_profile}){score_text}{reason_text}')
    return '\n'.join(lines)
