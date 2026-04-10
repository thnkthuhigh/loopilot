"""Autonomous goal decomposition — breaks high-level goals into milestone plans.

Provides heuristic-based goal decomposition when Copilot does not return
a structured plan, and re-planning logic when the current approach is stuck.

When an LLM brain is available, uses AI-powered decomposition for more
accurate plans. Falls back to heuristics otherwise.
"""

from __future__ import annotations

__all__ = [
    'classify_goal',
    'decompose_goal',
    'build_replan_prompt',
    'should_replan',
    'decompose_goal_with_llm',
]

from typing import Any

from .logging_config import get_logger
from .reasoning import Diagnosis

logger = get_logger('goal_decomposer')

# ---------------------------------------------------------------------------
# Goal classification
# ---------------------------------------------------------------------------

_GOAL_SIGNALS: dict[str, list[str]] = {
    'bug': ['fix', 'bug', 'broken', 'regression', 'crash', 'error', 'fail', 'issue'],
    'feature': ['implement', 'add', 'create', 'build', 'new', 'feature', 'support'],
    'refactor': ['refactor', 'restructure', 'extract', 'reorganize', 'simplify', 'clean'],
    'audit': ['audit', 'review', 'inspect', 'assess', 'analyse', 'analyze', 'check'],
    'docs': ['document', 'docs', 'readme', 'docstring', 'comment', 'explain'],
    'stabilize': ['stabilize', 'stabilise', 'harden', 'fix tests', 'fix lint', 'pass'],
}


def classify_goal(goal: str) -> str:
    """Infer the most likely goal profile from the goal text."""
    lower = goal.lower()
    scores: dict[str, int] = {}
    for profile, signals in _GOAL_SIGNALS.items():
        score = sum(1 for s in signals if s in lower)
        if score > 0:
            scores[profile] = score
    if not scores:
        return 'default'
    return max(scores, key=scores.get)


# ---------------------------------------------------------------------------
# Heuristic goal decomposition
# ---------------------------------------------------------------------------

def decompose_goal(goal: str, goal_profile: str = '') -> dict[str, Any]:
    """Break a high-level goal into a milestone plan using heuristics.

    This is used when Copilot doesn't return an OPERATOR_PLAN or when the
    operator wants a smarter initial plan than the single-milestone fallback.
    """
    profile = goal_profile or classify_goal(goal)
    title = _goal_title(goal)

    if profile == 'bug':
        return _bug_plan(title, goal)
    if profile == 'feature':
        return _feature_plan(title, goal)
    if profile == 'refactor':
        return _refactor_plan(title, goal)
    if profile == 'audit':
        return _audit_plan(title, goal)
    if profile == 'docs':
        return _docs_plan(title, goal)
    if profile == 'stabilize':
        return _stabilize_plan(title, goal)
    return _default_plan(title, goal)


def _goal_title(goal: str) -> str:
    title = goal.strip().splitlines()[0].strip() if goal.strip() else 'Complete the assigned goal'
    return title[:100].rstrip() + ('...' if len(title) > 100 else '')


def _bug_plan(title: str, goal: str) -> dict[str, Any]:
    return {
        'summary': f'Bug fix plan: {title}',
        'currentMilestoneId': 'm1',
        'nextMilestoneId': 'm2',
        'currentTaskId': 'm1.t1',
        'nextTaskId': 'm1.t2',
        'milestones': [
            {
                'id': 'm1', 'title': 'Reproduce and localise', 'status': 'in_progress',
                'acceptance': ['Bug is reproducible or localised to specific code path'],
                'tasks': [
                    {'id': 'm1.t1', 'title': 'Read error context and identify failing path', 'status': 'in_progress'},
                    {'id': 'm1.t2', 'title': 'Write or identify a failing test', 'status': 'pending'},
                ],
            },
            {
                'id': 'm2', 'title': 'Patch and verify', 'status': 'pending',
                'acceptance': ['Minimal patch applied', 'Failing test passes', 'No regressions'],
                'tasks': [
                    {'id': 'm2.t1', 'title': 'Apply the smallest safe fix', 'status': 'pending'},
                    {'id': 'm2.t2', 'title': 'Run tests and lint', 'status': 'pending'},
                ],
            },
            {
                'id': 'm3', 'title': 'Verify no regressions', 'status': 'pending',
                'acceptance': ['All tests pass', 'Lint clean'],
                'tasks': [
                    {'id': 'm3.t1', 'title': 'Full test suite + lint pass', 'status': 'pending'},
                ],
            },
        ],
    }


def _feature_plan(title: str, goal: str) -> dict[str, Any]:
    return {
        'summary': f'Feature plan: {title}',
        'currentMilestoneId': 'm1',
        'nextMilestoneId': 'm2',
        'currentTaskId': 'm1.t1',
        'nextTaskId': 'm1.t2',
        'milestones': [
            {
                'id': 'm1', 'title': 'Thin vertical slice', 'status': 'in_progress',
                'acceptance': ['Core happy path works', 'Basic test coverage'],
                'tasks': [
                    {'id': 'm1.t1', 'title': 'Implement the minimal working path', 'status': 'in_progress'},
                    {'id': 'm1.t2', 'title': 'Add tests for the new behaviour', 'status': 'pending'},
                ],
            },
            {
                'id': 'm2', 'title': 'Edge cases and integration', 'status': 'pending',
                'acceptance': ['Error handling in place', 'Integration verified'],
                'tasks': [
                    {'id': 'm2.t1', 'title': 'Handle edge cases and errors', 'status': 'pending'},
                    {'id': 'm2.t2', 'title': 'Update docs or README if needed', 'status': 'pending'},
                ],
            },
            {
                'id': 'm3', 'title': 'Polish and gate', 'status': 'pending',
                'acceptance': ['Tests pass', 'Lint clean', 'No critical blockers'],
                'tasks': [
                    {'id': 'm3.t1', 'title': 'Full test suite + lint', 'status': 'pending'},
                ],
            },
        ],
    }


def _refactor_plan(title: str, goal: str) -> dict[str, Any]:
    return {
        'summary': f'Refactor plan: {title}',
        'currentMilestoneId': 'm1',
        'nextMilestoneId': 'm2',
        'currentTaskId': 'm1.t1',
        'nextTaskId': 'm1.t2',
        'milestones': [
            {
                'id': 'm1', 'title': 'Baseline and scope', 'status': 'in_progress',
                'acceptance': ['Tests pass before any change', 'Scope is clearly bounded'],
                'tasks': [
                    {'id': 'm1.t1', 'title': 'Verify current tests pass', 'status': 'in_progress'},
                    {'id': 'm1.t2', 'title': 'Identify exactly what to refactor', 'status': 'pending'},
                ],
            },
            {
                'id': 'm2', 'title': 'Apply refactor', 'status': 'pending',
                'acceptance': ['Behaviour preserved', 'Diff is reviewable'],
                'tasks': [
                    {'id': 'm2.t1', 'title': 'Make the structural change', 'status': 'pending'},
                    {'id': 'm2.t2', 'title': 'Update affected tests', 'status': 'pending'},
                ],
            },
            {
                'id': 'm3', 'title': 'Verify preservation', 'status': 'pending',
                'acceptance': ['All tests pass', 'Lint clean', 'Behaviour unchanged'],
                'tasks': [
                    {'id': 'm3.t1', 'title': 'Full test suite + lint + manual spot check', 'status': 'pending'},
                ],
            },
        ],
    }


def _audit_plan(title: str, goal: str) -> dict[str, Any]:
    return {
        'summary': f'Audit plan: {title}',
        'currentMilestoneId': 'm1',
        'nextMilestoneId': 'm2',
        'currentTaskId': 'm1.t1',
        'nextTaskId': 'm1.t2',
        'milestones': [
            {
                'id': 'm1', 'title': 'Investigate and document findings', 'status': 'in_progress',
                'acceptance': ['Findings documented with severity and evidence'],
                'tasks': [
                    {'id': 'm1.t1', 'title': 'Read and analyse the target area', 'status': 'in_progress'},
                    {'id': 'm1.t2', 'title': 'Document findings with severity ratings', 'status': 'pending'},
                ],
            },
            {
                'id': 'm2', 'title': 'Fix if requested', 'status': 'pending',
                'acceptance': ['Only fix what the goal asks for', 'No scope creep'],
                'tasks': [
                    {'id': 'm2.t1', 'title': 'Apply targeted fixes (if goal says to)', 'status': 'pending'},
                    {'id': 'm2.t2', 'title': 'Verify fixes pass tests', 'status': 'pending'},
                ],
            },
        ],
    }


def _docs_plan(title: str, goal: str) -> dict[str, Any]:
    return {
        'summary': f'Docs plan: {title}',
        'currentMilestoneId': 'm1',
        'nextMilestoneId': 'm2',
        'currentTaskId': 'm1.t1',
        'nextTaskId': 'm1.t2',
        'milestones': [
            {
                'id': 'm1', 'title': 'Write or update documentation', 'status': 'in_progress',
                'acceptance': ['Docs match current code', 'Examples are correct'],
                'tasks': [
                    {'id': 'm1.t1', 'title': 'Review current code state', 'status': 'in_progress'},
                    {'id': 'm1.t2', 'title': 'Write/update the target docs', 'status': 'pending'},
                ],
            },
            {
                'id': 'm2', 'title': 'Verify accuracy', 'status': 'pending',
                'acceptance': ['Examples compile/run', 'No drift from code'],
                'tasks': [
                    {'id': 'm2.t1', 'title': 'Spot-check examples against real code', 'status': 'pending'},
                ],
            },
        ],
    }


def _stabilize_plan(title: str, goal: str) -> dict[str, Any]:
    return {
        'summary': f'Stabilization plan: {title}',
        'currentMilestoneId': 'm1',
        'nextMilestoneId': 'm2',
        'currentTaskId': 'm1.t1',
        'nextTaskId': 'm1.t2',
        'milestones': [
            {
                'id': 'm1', 'title': 'Fix failing checks', 'status': 'in_progress',
                'acceptance': ['Tests pass', 'Lint pass'],
                'tasks': [
                    {'id': 'm1.t1', 'title': 'Identify and fix test failures', 'status': 'in_progress'},
                    {'id': 'm1.t2', 'title': 'Fix lint issues', 'status': 'pending'},
                ],
            },
            {
                'id': 'm2', 'title': 'Confirm stability', 'status': 'pending',
                'acceptance': ['Full suite green', 'No new blockers'],
                'tasks': [
                    {'id': 'm2.t1', 'title': 'Run full test + lint + build', 'status': 'pending'},
                ],
            },
        ],
    }


def _default_plan(title: str, goal: str) -> dict[str, Any]:
    return {
        'summary': f'General plan: {title}',
        'currentMilestoneId': 'm1',
        'nextMilestoneId': 'm2',
        'currentTaskId': 'm1.t1',
        'nextTaskId': 'm1.t2',
        'milestones': [
            {
                'id': 'm1', 'title': 'Make progress on the goal', 'status': 'in_progress',
                'acceptance': ['Meaningful change is applied', 'Tests pass'],
                'tasks': [
                    {'id': 'm1.t1', 'title': 'Make the highest leverage change', 'status': 'in_progress'},
                    {'id': 'm1.t2', 'title': 'Verify with tests and lint', 'status': 'pending'},
                ],
            },
            {
                'id': 'm2', 'title': 'Verify and finish', 'status': 'pending',
                'acceptance': ['All checks pass', 'Score meets target'],
                'tasks': [
                    {'id': 'm2.t1', 'title': 'Final verification pass', 'status': 'pending'},
                ],
            },
        ],
    }


# ---------------------------------------------------------------------------
# Re-planning when stuck
# ---------------------------------------------------------------------------

def build_replan_prompt(diagnosis: Diagnosis, current_plan: dict[str, Any] | None, goal: str) -> str:
    """Generate an explicit re-planning instruction when the operator detects a stuck loop."""
    lines = ['Your current plan is not working. The operator has detected the following issues:']

    for loop in diagnosis.loops:
        if loop.detected:
            lines.append(f'- {loop.detail}')

    for hint in diagnosis.hints:
        if hint.suggested_prompt:
            lines.append(f'- Strategy: {hint.suggested_prompt}')

    lines.append('')
    lines.append('You MUST change your approach:')
    lines.append('1. Do NOT repeat what you already tried.')
    lines.append('2. Re-read the actual error output or test failure.')
    lines.append('3. Try a completely different angle.')
    lines.append('4. Update the OPERATOR_PLAN to reflect the new approach.')
    lines.append(f'5. The original goal is: {goal}')

    if current_plan and current_plan.get('milestones'):
        current_milestone = current_plan.get('currentMilestoneId', '')
        current_task = current_plan.get('currentTaskId', '')
        if current_milestone:
            lines.append(f'6. You were stuck on milestone {current_milestone}, task {current_task}.')
            lines.append('   Consider whether this task needs to be broken down differently.')

    return '\n'.join(lines)


def should_replan(diagnosis: Diagnosis) -> bool:
    """Return True if the diagnosis suggests the current plan needs replacement."""
    if diagnosis.risk_level in ('critical', 'high'):
        return True
    loop_kinds = {sig.kind for sig in diagnosis.loops if sig.detected}
    return bool(loop_kinds & {'score_plateau', 'baton_repeat', 'summary_repeat'})


# ---------------------------------------------------------------------------
# LLM-powered decomposition (used when LLM brain is available)
# ---------------------------------------------------------------------------

def decompose_goal_with_llm(
    goal: str,
    goal_profile: str,
    llm_brain: Any,
) -> dict[str, Any] | None:
    """Use the LLM brain to create a tailored decomposition plan.

    Returns the plan dict on success, None on failure (caller should fall back
    to heuristic decompose_goal).
    """
    if llm_brain is None or not llm_brain.is_ready:
        return None

    system = (
        'You are an expert software project planner. '
        'Break down the goal into concrete, sequenced milestones with tasks. '
        'Be specific — each task should be a single, actionable coding step.'
    )
    prompt = f"""Decompose this software goal into a milestone plan.

Goal: {goal}
Goal type: {goal_profile}

Return JSON with this exact structure:
{{
  "summary": "Brief plan description",
  "currentMilestoneId": "m1",
  "nextMilestoneId": "m2",
  "currentTaskId": "m1.t1",
  "nextTaskId": "m1.t2",
  "milestones": [
    {{
      "id": "m1",
      "title": "First milestone",
      "status": "in_progress",
      "acceptance": ["Criteria 1", "Criteria 2"],
      "tasks": [
        {{"id": "m1.t1", "title": "First task", "status": "in_progress"}},
        {{"id": "m1.t2", "title": "Second task", "status": "pending"}}
      ]
    }}
  ]
}}

Rules:
- 2-4 milestones maximum
- 1-3 tasks per milestone
- First milestone and task should be in_progress
- Last milestone should always be verification (tests + lint)
- Task titles should be specific to THIS goal, not generic"""

    try:
        result, response = llm_brain.ask_json(prompt, system=system)
        if result and isinstance(result.get('milestones'), list) and result['milestones']:
            logger.info('LLM decomposition successful: %d milestones', len(result['milestones']))
            return result
    except Exception as exc:
        logger.warning('LLM decomposition failed: %s', exc)

    return None
