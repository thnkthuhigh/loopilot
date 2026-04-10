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
    'load_mission',
    'save_mission',
    'update_mission_from_run',
    'render_mission_for_prompt',
]

import json
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
