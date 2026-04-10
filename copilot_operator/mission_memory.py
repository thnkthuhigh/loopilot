"""Mission Memory — the highest-level persistent memory layer.

Tracks the overall project direction, current mission, success criteria,
and active priorities across many sessions.  This is the layer that prevents
the operator from "forgetting what we're trying to achieve" after a session
reset.

Stored as `.copilot-operator/mission.yml` inside the workspace.
"""

from __future__ import annotations

__all__ = [
    'MissionObjective',
    'Mission',
    'DriftCheckResult',
    'MissionDirective',
    'MissionVeto',
    'MissionAuthority',
    'load_mission',
    'save_mission',
    'update_mission_from_run',
    'render_mission_for_prompt',
    'check_mission_drift',
    'render_drift_correction',
]

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class MissionObjective:
    """A single top-level objective within the mission."""
    id: str = ''
    description: str = ''
    status: str = 'active'  # active | done | deferred
    success_criteria: list[str] = field(default_factory=list)
    notes: str = ''


@dataclass(slots=True)
class Mission:
    """Top-level project mission — survives across all sessions."""
    project_name: str = ''
    direction: str = ''              # one-line "where are we going"
    current_phase: str = ''          # which phase is active
    active_goals: list[str] = field(default_factory=list)
    success_criteria: list[str] = field(default_factory=list)
    priorities: list[str] = field(default_factory=list)
    hard_constraints: list[str] = field(default_factory=list)  # MUST NOT violate
    objectives: list[MissionObjective] = field(default_factory=list)
    completed_objectives: list[str] = field(default_factory=list)  # ids
    lessons_learned: list[str] = field(default_factory=list)
    updated_at: str = ''

    def render(self) -> str:
        """Render mission as a human-readable block for prompt injection."""
        lines = []
        lines.append('## Mission Context')
        if self.project_name:
            lines.append(f'**Project:** {self.project_name}')
        if self.direction:
            lines.append(f'**Direction:** {self.direction}')
        if self.current_phase:
            lines.append(f'**Current phase:** {self.current_phase}')

        # Hard constraints — ALWAYS shown first, non-negotiable
        if self.hard_constraints:
            lines.append('\n🚫 **HARD CONSTRAINTS (must NOT violate):**')
            for c in self.hard_constraints:
                lines.append(f'  ✖ {c}')

        if self.active_goals:
            lines.append('**Active goals:**')
            for g in self.active_goals:
                lines.append(f'  - {g}')
        if self.priorities:
            lines.append('**Priorities:**')
            for p in self.priorities:
                lines.append(f'  - {p}')
        if self.success_criteria:
            lines.append('**Success criteria:**')
            for c in self.success_criteria:
                lines.append(f'  - {c}')

        active_obj = [o for o in self.objectives if o.status == 'active']
        if active_obj:
            lines.append('**Active objectives:**')
            for o in active_obj:
                lines.append(f'  [{o.id}] {o.description}')
                for c in o.success_criteria:
                    lines.append(f'    ✓ {c}')

        if self.lessons_learned:
            lines.append('**Lessons learned:**')
            for lesson in self.lessons_learned[-5:]:
                lines.append(f'  - {lesson}')

        done_count = len(self.completed_objectives)
        total_count = len(self.objectives)
        if total_count > 0:
            lines.append(f'\n**Progress:** {done_count}/{total_count} objectives completed')

        return '\n'.join(lines)


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------

_MISSION_FILENAME = 'mission.yml'
_MISSION_DIR = '.copilot-operator'


def _mission_path(workspace: str | Path) -> Path:
    return Path(workspace) / _MISSION_DIR / _MISSION_FILENAME


def load_mission(workspace: str | Path) -> Mission:
    """Load mission from disk.  Returns empty Mission if file doesn't exist."""
    path = _mission_path(workspace)
    if not path.exists():
        return Mission()
    try:
        text = path.read_text(encoding='utf-8')
        if yaml is not None:
            data = yaml.safe_load(text)
        else:
            data = json.loads(text)
        if not isinstance(data, dict):
            return Mission()
        return _dict_to_mission(data)
    except Exception:
        return Mission()


def save_mission(workspace: str | Path, mission: Mission) -> Path:
    """Persist mission to disk."""
    path = _mission_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    mission.updated_at = datetime.now(timezone.utc).isoformat()
    data = _mission_to_dict(mission)
    if yaml is not None:
        text = yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)
    else:
        text = json.dumps(data, indent=2, ensure_ascii=False)
    path.write_text(text, encoding='utf-8')
    return path


# ---------------------------------------------------------------------------
# Dict conversion
# ---------------------------------------------------------------------------

def _dict_to_mission(d: dict[str, Any]) -> Mission:
    objectives = []
    for obj in d.get('objectives', []):
        if isinstance(obj, dict):
            objectives.append(MissionObjective(
                id=str(obj.get('id', '')),
                description=str(obj.get('description', '')),
                status=str(obj.get('status', 'active')),
                success_criteria=list(obj.get('success_criteria', [])),
                notes=str(obj.get('notes', '')),
            ))
    return Mission(
        project_name=str(d.get('project_name', '')),
        direction=str(d.get('direction', '')),
        current_phase=str(d.get('current_phase', '')),
        active_goals=list(d.get('active_goals', [])),
        success_criteria=list(d.get('success_criteria', [])),
        priorities=list(d.get('priorities', [])),
        hard_constraints=list(d.get('hard_constraints', [])),
        objectives=objectives,
        completed_objectives=list(d.get('completed_objectives', [])),
        lessons_learned=list(d.get('lessons_learned', [])),
        updated_at=str(d.get('updated_at', '')),
    )


def _mission_to_dict(m: Mission) -> dict[str, Any]:
    return {
        'project_name': m.project_name,
        'direction': m.direction,
        'current_phase': m.current_phase,
        'active_goals': m.active_goals,
        'success_criteria': m.success_criteria,
        'priorities': m.priorities,
        'hard_constraints': m.hard_constraints,
        'objectives': [
            {
                'id': o.id,
                'description': o.description,
                'status': o.status,
                'success_criteria': o.success_criteria,
                'notes': o.notes,
            }
            for o in m.objectives
        ],
        'completed_objectives': m.completed_objectives,
        'lessons_learned': m.lessons_learned,
        'updated_at': m.updated_at,
    }


# ---------------------------------------------------------------------------
# Mission updates from run results
# ---------------------------------------------------------------------------

def update_mission_from_run(
    mission: Mission,
    goal: str,
    result: dict[str, Any],
    lesson: str = '',
) -> Mission:
    """Update mission state after a run completes.

    - Marks matching objectives as done if result is complete.
    - Adds lessons learned.
    - Trims old lessons to keep memory bounded.
    """
    status = result.get('status', '')
    reason_code = result.get('reasonCode', '')

    # Try to match goal to an objective
    goal_lower = goal.lower().strip()
    for obj in mission.objectives:
        if obj.status != 'active':
            continue
        # Fuzzy match: goal contains objective description or vice versa
        obj_lower = obj.description.lower()
        if (goal_lower in obj_lower or obj_lower in goal_lower
                or obj.id.lower() in goal_lower):
            if status == 'complete':
                obj.status = 'done'
                if obj.id not in mission.completed_objectives:
                    mission.completed_objectives.append(obj.id)
            break

    # Add lesson
    if lesson:
        mission.lessons_learned.append(lesson)
    elif status == 'error' or status == 'blocked':
        mission.lessons_learned.append(
            f'Run blocked/errored for goal "{goal[:60]}": {reason_code}'
        )

    # Trim lessons to last 20
    if len(mission.lessons_learned) > 20:
        mission.lessons_learned = mission.lessons_learned[-20:]

    return mission


def render_mission_for_prompt(mission: Mission) -> str:
    """Render mission context for injection into operator prompts.

    Returns empty string if mission is essentially empty.
    """
    if not mission.direction and not mission.active_goals and not mission.objectives:
        return ''
    return mission.render()


# ---------------------------------------------------------------------------
# Mission Drift Detection — the "hard authority" layer
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class DriftCheckResult:
    """Result of checking whether current action aligns with mission."""
    drifted: bool = False
    severity: str = 'none'             # none | mild | severe
    violations: list[str] = field(default_factory=list)     # specific violations
    correction: str = ''               # what to do instead
    aligned_objective: str = ''        # the objective this should align with


def check_mission_drift(
    mission: Mission,
    current_goal: str,
    current_action: str = '',
    files_changed: list[str] | None = None,
) -> DriftCheckResult:
    """Check if the current action drifts from the mission.

    This is the mechanism that makes mission memory *authoritative* — not
    just informational, but able to detect and correct drift.

    Checks:
    1. Hard constraint violations
    2. Goal alignment with active objectives
    3. File scope violations (touching files outside mission scope)
    4. Direction alignment
    """
    result = DriftCheckResult()
    violations: list[str] = []
    goal_lower = current_goal.lower()
    action_lower = current_action.lower()
    combined = f'{goal_lower} {action_lower}'

    # --- 1. Hard constraint violations ---
    for constraint in mission.hard_constraints:
        constraint_lower = constraint.lower()
        # Extract keywords from constraint (anything after "do not", "never", "must not")
        forbidden_patterns = re.findall(
            r'(?:do not|never|must not|don\'t|cannot|shall not)\s+(.+?)(?:\.|$)',
            constraint_lower,
        )
        for pattern in forbidden_patterns:
            pattern_words = set(pattern.split())
            combined_words = set(combined.split())
            # If >50% of the forbidden words appear in the action
            overlap = pattern_words & combined_words
            if len(overlap) >= max(1, len(pattern_words) * 0.5):
                violations.append(f'Hard constraint violated: "{constraint}"')

        # Direct keyword check for common constraint types
        if 'production' in constraint_lower and any(
            kw in combined for kw in ('deploy', 'push to prod', 'production release')
        ):
            violations.append(f'Hard constraint violated: "{constraint}"')
        if 'delete' in constraint_lower and 'delete' in combined:
            violations.append(f'Hard constraint violated: "{constraint}"')

    # --- 2. Goal alignment with active objectives ---
    active_objs = [o for o in mission.objectives if o.status == 'active']
    if active_objs and current_goal:
        # Check if the goal relates to any active objective
        best_match = 0.0
        best_obj = ''
        for obj in active_objs:
            obj_words = set(re.findall(r'\w{3,}', obj.description.lower()))
            goal_words = set(re.findall(r'\w{3,}', goal_lower))
            if obj_words and goal_words:
                overlap = len(obj_words & goal_words) / max(len(obj_words), 1)
                if overlap > best_match:
                    best_match = overlap
                    best_obj = obj.description

        result.aligned_objective = best_obj
        if best_match < 0.1 and len(active_objs) > 0:
            # Goal doesn't match any active objective
            obj_list = ', '.join(o.description[:40] for o in active_objs[:3])
            violations.append(
                f'Goal "{current_goal[:50]}" does not align with any active objective: [{obj_list}]'
            )

    # --- 3. Priority alignment ---
    if mission.priorities:
        top_priority = mission.priorities[0].lower()
        top_words = set(re.findall(r'\w{3,}', top_priority))
        goal_words = set(re.findall(r'\w{3,}', goal_lower))
        if top_words and goal_words and not (top_words & goal_words):
            violations.append(
                f'Current work may not align with top priority: "{mission.priorities[0]}"'
            )

    # --- Compute severity ---
    if not violations:
        return result

    result.drifted = True
    result.violations = violations

    # Hard constraint violations are always severe
    hard_violations = [v for v in violations if 'Hard constraint' in v]
    if hard_violations:
        result.severity = 'severe'
        result.correction = (
            'STOP. A hard mission constraint has been violated. '
            'Do NOT proceed with this action. '
            f'Constraint: {hard_violations[0]}'
        )
    else:
        result.severity = 'mild'
        result.correction = (
            f'Current work may be drifting from mission. '
            f'Active objectives: {", ".join(o.description[:40] for o in active_objs[:3])}. '
            f'Consider realigning.'
        )

    return result


def render_drift_correction(drift: DriftCheckResult) -> str:
    """Render a drift correction directive for prompt injection.

    Returns empty string if no drift detected.
    """
    if not drift.drifted:
        return ''

    lines = []
    if drift.severity == 'severe':
        lines.append('## 🚨 MISSION DRIFT — SEVERE')
        lines.append('**You MUST stop the current action and realign with the mission.**')
    else:
        lines.append('## ⚠️ MISSION DRIFT — Warning')
        lines.append('**Your current work may be drifting from the mission objectives.**')

    for v in drift.violations[:3]:
        lines.append(f'  • {v}')

    if drift.correction:
        lines.append(f'\n**Correction:** {drift.correction}')

    if drift.aligned_objective:
        lines.append(f'**Should align with:** {drift.aligned_objective}')

    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Mission Authority — the decision-override layer
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class MissionDirective:
    """A mandatory instruction from the mission that overrides operator decisions.

    Directives are injected at the TOP of every prompt and MUST be followed.
    """
    id: str = ''
    instruction: str = ''
    reason: str = ''
    priority: int = 0           # 0 = highest, lower = more important
    source: str = 'mission'     # mission | constraint | escalation


@dataclass(slots=True)
class MissionVeto:
    """A veto on a proposed action — mission says NO."""
    vetoed: bool = False
    reason: str = ''
    constraint: str = ''        # which constraint triggered the veto
    alternative: str = ''       # what to do instead


class MissionAuthority:
    """The authoritative layer that makes mission memory binding, not advisory.

    This class wraps a Mission and provides decision-override capabilities:
    - Veto actions that violate constraints
    - Force priority on specific tasks
    - Inject mandatory directives every iteration
    - Override the plan when mission says so
    """

    def __init__(self, mission: Mission) -> None:
        self.mission = mission
        self._forced_priorities: dict[str, int] = {}
        self._active_directives: list[MissionDirective] = []
        self._build_directives()

    def _build_directives(self) -> None:
        """Generate directives from mission state."""
        self._active_directives.clear()
        idx = 0

        # Hard constraints become permanent directives
        for constraint in self.mission.hard_constraints:
            self._active_directives.append(MissionDirective(
                id=f'hc-{idx}',
                instruction=f'MUST NOT: {constraint}',
                reason='Hard mission constraint — non-negotiable',
                priority=0,
                source='constraint',
            ))
            idx += 1

        # Top priority becomes a focus directive
        if self.mission.priorities:
            self._active_directives.append(MissionDirective(
                id=f'pri-{idx}',
                instruction=f'TOP PRIORITY: {self.mission.priorities[0]}',
                reason='Mission-defined priority — should guide all decisions',
                priority=1,
                source='mission',
            ))
            idx += 1

        # Active objectives with success criteria become directives
        active_objs = [o for o in self.mission.objectives if o.status == 'active']
        for obj in active_objs[:3]:
            criteria = ', '.join(obj.success_criteria[:2]) if obj.success_criteria else ''
            self._active_directives.append(MissionDirective(
                id=f'obj-{idx}',
                instruction=f'OBJECTIVE: {obj.description}' + (f' (criteria: {criteria})' if criteria else ''),
                reason='Active mission objective',
                priority=2,
                source='mission',
            ))
            idx += 1

    def evaluate_action(self, action: str, goal: str = '', files: list[str] | None = None) -> MissionVeto:
        """Evaluate whether a proposed action is allowed by the mission.

        Returns a MissionVeto. If vetoed=True, the action MUST NOT proceed.
        """
        action_lower = action.lower()
        goal_lower = goal.lower()
        combined = f'{action_lower} {goal_lower}'

        for constraint in self.mission.hard_constraints:
            constraint_lower = constraint.lower()

            # Extract forbidden patterns
            forbidden = re.findall(
                r'(?:do not|never|must not|don\'t|cannot|shall not)\s+(.+?)(?:\.|$)',
                constraint_lower,
            )
            for pattern in forbidden:
                pattern_words = set(pattern.split())
                combined_words = set(combined.split())
                overlap = pattern_words & combined_words
                if len(overlap) >= max(1, len(pattern_words) * 0.5):
                    # Find alternative from mission objectives
                    alt = ''
                    active = [o for o in self.mission.objectives if o.status == 'active']
                    if active:
                        alt = f'Instead, work on: {active[0].description}'
                    return MissionVeto(
                        vetoed=True,
                        reason=f'Action violates hard constraint: "{constraint}"',
                        constraint=constraint,
                        alternative=alt,
                    )

            # Direct keyword checks
            if 'production' in constraint_lower and any(
                kw in combined for kw in ('deploy', 'push to prod', 'production release')
            ):
                return MissionVeto(
                    vetoed=True,
                    reason=f'Production deployment blocked by constraint: "{constraint}"',
                    constraint=constraint,
                    alternative='Run in staging or test environment instead.',
                )
            if 'delete' in constraint_lower and 'delete' in combined:
                return MissionVeto(
                    vetoed=True,
                    reason=f'Deletion blocked by constraint: "{constraint}"',
                    constraint=constraint,
                    alternative='Consider archiving instead of deleting.',
                )

        return MissionVeto(vetoed=False)

    def force_priority(self, task_pattern: str, priority: int, reason: str = '') -> None:
        """Force a specific priority on tasks matching the pattern.

        Called by the operator when mission needs to override the plan.
        """
        self._forced_priorities[task_pattern.lower()] = priority
        if reason:
            self._active_directives.append(MissionDirective(
                id=f'force-{len(self._active_directives)}',
                instruction=f'PRIORITY OVERRIDE: Tasks matching "{task_pattern}" → P{priority}',
                reason=reason,
                priority=0,
                source='escalation',
            ))

    def get_forced_priorities(self) -> dict[str, int]:
        """Return the map of forced task priorities."""
        return dict(self._forced_priorities)

    def get_directives(self) -> list[MissionDirective]:
        """Return all active directives, ordered by priority."""
        return sorted(self._active_directives, key=lambda d: d.priority)

    def render_authority_block(self) -> str:
        """Render the authority block for prompt injection.

        This goes at the VERY TOP of the prompt — above everything else.
        Returns empty string if no active directives.
        """
        directives = self.get_directives()
        if not directives:
            return ''

        lines = ['## 🔒 MISSION AUTHORITY (binding — must follow)']

        for d in directives:
            if d.source == 'constraint':
                lines.append(f'🚫 {d.instruction}')
            elif d.source == 'escalation':
                lines.append(f'⚡ {d.instruction}')
            else:
                lines.append(f'🎯 {d.instruction}')

        lines.append('')
        lines.append('_These directives override all other instructions. Do not deviate._')
        return '\n'.join(lines)

    def override_decision(
        self,
        proposed_action: str,
        current_score: int,
        target_score: int,
        iteration: int,
    ) -> MissionDirective | None:
        """Check if mission should override the current decision.

        Returns a MissionDirective if mission wants to override, else None.
        Situations:
        - Score regressing + mission has clear priority → force alignment
        - Too many iterations without progress → force mission's top priority
        - Action contradicts mission direction → redirect
        """
        # If score is far from target and we have priorities, force the top one
        gap = target_score - current_score
        if gap > 30 and iteration >= 3 and self.mission.priorities:
            return MissionDirective(
                id='override-focus',
                instruction=(
                    f'OVERRIDE: Score gap is {gap} points after {iteration} iterations. '
                    f'FOCUS EXCLUSIVELY on: {self.mission.priorities[0]}. '
                    f'Drop all non-essential work.'
                ),
                reason=f'Large score gap ({gap}) with time pressure ({iteration} iterations)',
                priority=0,
                source='escalation',
            )

        # Check if proposed action aligns with direction
        if self.mission.direction and proposed_action:
            direction_words = set(re.findall(r'\w{3,}', self.mission.direction.lower()))
            action_words = set(re.findall(r'\w{3,}', proposed_action.lower()))
            if direction_words and action_words and not (direction_words & action_words):
                active = [o for o in self.mission.objectives if o.status == 'active']
                alt = active[0].description if active else self.mission.direction
                return MissionDirective(
                    id='override-align',
                    instruction=(
                        f'REDIRECT: Current action does not align with mission direction '
                        f'("{self.mission.direction[:60]}"). Work on: {alt}'
                    ),
                    reason='Action misaligned with mission direction',
                    priority=0,
                    source='escalation',
                )

        return None
