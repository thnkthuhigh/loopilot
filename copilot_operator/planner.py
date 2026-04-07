from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

_OPERATOR_PLAN_RE = re.compile(r"<OPERATOR_PLAN>\s*(.*?)\s*</OPERATOR_PLAN>", re.IGNORECASE | re.DOTALL)
_ALLOWED_STATUSES = {'pending', 'in_progress', 'done', 'blocked'}
_GENERIC_BATON_RE = re.compile(
    r'^(continue|keep going|move on|proceed|resume|do the next thing)\b',
    re.IGNORECASE,
)


@dataclass(slots=True)
class TaskItem:
    id: str
    title: str
    status: str = 'pending'
    summary: str = ''


@dataclass(slots=True)
class Milestone:
    id: str
    title: str
    status: str = 'pending'
    summary: str = ''
    acceptance: list[str] = field(default_factory=list)
    current_task_id: str = ''
    next_task_id: str = ''
    tasks: list[TaskItem] = field(default_factory=list)


@dataclass(slots=True)
class PlanState:
    summary: str = ''
    current_milestone_id: str = ''
    next_milestone_id: str = ''
    current_task_id: str = ''
    next_task_id: str = ''
    milestones: list[Milestone] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


def _strip_code_fence(text: str) -> str:
    candidate = text.strip()
    if candidate.startswith('```'):
        lines = candidate.splitlines()
        if len(lines) >= 3:
            candidate = '\n'.join(lines[1:-1]).strip()
    return candidate


def _normalize_status(value: Any) -> str:
    status = str(value or 'pending').strip().lower().replace(' ', '_')
    return status if status in _ALLOWED_STATUSES else 'pending'


def _normalize_acceptance(items: Any) -> list[str]:
    normalized: list[str] = []
    for item in items or []:
        text = str(item).strip()
        if text:
            normalized.append(text)
    return normalized


def _normalize_task(item: Any, index: int, milestone_id: str) -> TaskItem:
    if isinstance(item, dict):
        task_id = str(item.get('id') or f'{milestone_id}.t{index}').strip() or f'{milestone_id}.t{index}'
        title = str(item.get('title') or item.get('name') or f'Task {index}').strip() or f'Task {index}'
        status = _normalize_status(item.get('status'))
        summary = str(item.get('summary', item.get('description', ''))).strip()
        return TaskItem(id=task_id, title=title, status=status, summary=summary)
    text = str(item).strip() or f'Task {index}'
    return TaskItem(id=f'{milestone_id}.t{index}', title=text, status='pending', summary='')


def _normalize_tasks(items: Any, milestone_id: str) -> list[TaskItem]:
    return [_normalize_task(item, index + 1, milestone_id) for index, item in enumerate(items or [])]


def _pick_current_task(tasks: list[TaskItem], parent_status: str) -> str:
    if parent_status in {'done', 'blocked'}:
        return ''
    for item in tasks:
        if item.status == 'in_progress':
            return item.id
    for item in tasks:
        if item.status == 'pending':
            return item.id
    return ''


def _pick_next_task(tasks: list[TaskItem], current_task_id: str) -> str:
    if not tasks:
        return ''
    if current_task_id:
        seen_current = False
        for item in tasks:
            if item.id == current_task_id:
                seen_current = True
                continue
            if seen_current and item.status in {'pending', 'in_progress'}:
                return item.id
    for item in tasks:
        if item.id != current_task_id and item.status in {'pending', 'in_progress'}:
            return item.id
    return ''


def _normalize_milestone(item: Any, index: int) -> Milestone:
    if isinstance(item, dict):
        milestone_id = str(item.get('id') or f'm{index}').strip() or f'm{index}'
        title = str(item.get('title') or item.get('name') or f'Milestone {index}').strip() or f'Milestone {index}'
        status = _normalize_status(item.get('status'))
        summary = str(item.get('summary', item.get('description', ''))).strip()
        acceptance = _normalize_acceptance(item.get('acceptance', item.get('acceptanceCriteria', [])))
        tasks = _normalize_tasks(item.get('tasks', []), milestone_id)
        current_task = str(item.get('current_task_id') or item.get('currentTaskId') or '').strip()
        next_task = str(item.get('next_task_id') or item.get('nextTaskId') or '').strip()
        if tasks:
            task_ids = {task.id for task in tasks}
            if current_task and current_task not in task_ids:
                current_task = ''
            if next_task and next_task not in task_ids:
                next_task = ''
            if not current_task:
                current_task = _pick_current_task(tasks, status)
            if not next_task:
                next_task = _pick_next_task(tasks, current_task)
        else:
            current_task = ''
            next_task = ''
        return Milestone(
            id=milestone_id,
            title=title,
            status=status,
            summary=summary,
            acceptance=acceptance,
            current_task_id=current_task,
            next_task_id=next_task,
            tasks=tasks,
        )
    text = str(item).strip() or f'Milestone {index}'
    return Milestone(id=f'm{index}', title=text, status='pending', summary='', acceptance=[], current_task_id='', next_task_id='', tasks=[])


def parse_operator_plan(response_text: str) -> PlanState | None:
    matches = _OPERATOR_PLAN_RE.findall(response_text or '')
    if not matches:
        return None
    payload = _strip_code_fence(matches[-1])
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None

    milestones = [_normalize_milestone(item, index + 1) for index, item in enumerate(data.get('milestones', []))]
    current = str(data.get('current_milestone_id') or data.get('currentMilestoneId') or '').strip()
    next_milestone = str(data.get('next_milestone_id') or data.get('nextMilestoneId') or '').strip()
    current_task = str(data.get('current_task_id') or data.get('currentTaskId') or '').strip()
    next_task = str(data.get('next_task_id') or data.get('nextTaskId') or '').strip()
    if not current and milestones:
        current = milestones[0].id
    if current and milestones and not current_task:
        active = next((item for item in milestones if item.id == current), None)
        if active is not None:
            current_task = active.current_task_id
            next_task = next_task or active.next_task_id
    return PlanState(
        summary=str(data.get('summary', '')).strip(),
        current_milestone_id=current,
        next_milestone_id=next_milestone,
        current_task_id=current_task,
        next_task_id=next_task,
        milestones=milestones,
        raw=data,
    )


def fallback_plan(goal: str) -> dict[str, Any]:
    title = goal.strip().splitlines()[0].strip() if goal.strip() else 'Complete the assigned goal'
    if len(title) > 100:
        title = title[:97].rstrip() + '...'
    milestones = [
        {
            'id': 'm1',
            'title': title or 'Complete the assigned goal',
            'status': 'in_progress',
            'summary': 'Operator fallback plan: work the goal until a structured plan is returned.',
            'acceptance': [],
            'currentTaskId': 'm1.t1',
            'nextTaskId': '',
            'tasks': [
                {
                    'id': 'm1.t1',
                    'title': 'Make the next highest leverage change you can finish now',
                    'status': 'in_progress',
                    'summary': 'Fallback task queue: keep moving until Copilot returns a structured plan.',
                }
            ],
        }
    ]
    return _finalize_plan(
        {
            'summary': 'Single-track operator fallback plan.',
            'currentMilestoneId': 'm1',
            'nextMilestoneId': '',
            'currentTaskId': 'm1.t1',
            'nextTaskId': '',
            'milestones': milestones,
        }
    )


def plan_to_dict(plan: PlanState) -> dict[str, Any]:
    return _finalize_plan(
        {
            'summary': plan.summary,
            'currentMilestoneId': plan.current_milestone_id,
            'nextMilestoneId': plan.next_milestone_id,
            'currentTaskId': plan.current_task_id,
            'nextTaskId': plan.next_task_id,
            'milestones': [
                {
                    'id': item.id,
                    'title': item.title,
                    'status': item.status,
                    'summary': item.summary,
                    'acceptance': item.acceptance,
                    'currentTaskId': item.current_task_id,
                    'nextTaskId': item.next_task_id,
                    'tasks': [
                        {
                            'id': task.id,
                            'title': task.title,
                            'status': task.status,
                            'summary': task.summary,
                        }
                        for task in item.tasks
                    ],
                }
                for item in plan.milestones
            ],
        }
    )


def merge_plan(existing: dict[str, Any] | None, incoming: PlanState | None, goal: str, terminal_status: str = '') -> dict[str, Any]:
    if incoming is not None and incoming.milestones:
        plan = plan_to_dict(incoming)
    elif existing and existing.get('milestones'):
        plan = _finalize_plan(existing)
    else:
        plan = fallback_plan(goal)

    if terminal_status == 'done':
        for milestone in plan.get('milestones', []):
            if milestone.get('status') != 'blocked':
                milestone['status'] = 'done'
            for task in milestone.get('tasks', []):
                if task.get('status') != 'blocked':
                    task['status'] = 'done'
            milestone['currentTaskId'] = ''
            milestone['nextTaskId'] = ''
        plan['currentMilestoneId'] = ''
        plan['nextMilestoneId'] = ''
        plan['currentTaskId'] = ''
        plan['nextTaskId'] = ''
    elif terminal_status == 'blocked':
        current_milestone = str(plan.get('currentMilestoneId', '')).strip()
        current_task = str(plan.get('currentTaskId', '')).strip()
        for milestone in plan.get('milestones', []):
            if milestone.get('id') == current_milestone and milestone.get('status') != 'done':
                milestone['status'] = 'blocked'
                if current_task:
                    for task in milestone.get('tasks', []):
                        if task.get('id') == current_task and task.get('status') != 'done':
                            task['status'] = 'blocked'
                            break
                break

    return _finalize_plan(plan)


def summarize_plan(plan: dict[str, Any] | None) -> dict[str, Any]:
    if not plan:
        return {
            'summary': '',
            'currentMilestoneId': '',
            'nextMilestoneId': '',
            'currentTaskId': '',
            'nextTaskId': '',
            'counts': {},
            'taskCounts': {},
            'milestones': [],
        }
    finalized = _finalize_plan(plan)
    return {
        'summary': finalized.get('summary', ''),
        'currentMilestoneId': finalized.get('currentMilestoneId', ''),
        'nextMilestoneId': finalized.get('nextMilestoneId', ''),
        'currentTaskId': finalized.get('currentTaskId', ''),
        'nextTaskId': finalized.get('nextTaskId', ''),
        'counts': finalized.get('counts', {}),
        'taskCounts': finalized.get('taskCounts', {}),
        'milestones': finalized.get('milestones', []),
    }


def get_current_milestone(plan: dict[str, Any] | None) -> dict[str, Any] | None:
    finalized = _finalize_plan(plan or {})
    milestones = finalized.get('milestones', []) or []
    current_id = str(finalized.get('currentMilestoneId', '')).strip()
    if current_id:
        for milestone in milestones:
            if milestone.get('id') == current_id:
                return milestone
    return milestones[0] if milestones else None


def get_current_task(plan: dict[str, Any] | None) -> dict[str, Any] | None:
    finalized = _finalize_plan(plan or {})
    milestone = get_current_milestone(finalized)
    if not milestone:
        return None
    tasks = milestone.get('tasks', []) or []
    current_task = str(finalized.get('currentTaskId', '') or milestone.get('currentTaskId', '') or '').strip()
    if current_task:
        for task in tasks:
            if task.get('id') == current_task:
                return task
    for task in tasks:
        if task.get('status') in {'in_progress', 'pending'}:
            return task
    return tasks[0] if tasks else None


def is_generic_baton(text: str) -> bool:
    candidate = str(text or '').strip()
    if not candidate:
        return True
    if len(candidate) < 45 and _GENERIC_BATON_RE.match(candidate):
        return True
    normalized = ' '.join(candidate.lower().split())
    generic_values = {
        'continue',
        'continue from the first unfinished task.',
        'continue from the first unfinished task in the workspace.',
        'resume from the current task.',
    }
    return normalized in generic_values


def build_milestone_baton(plan: dict[str, Any] | None, fallback: str = '') -> str:
    finalized = _finalize_plan(plan or {})
    milestone = get_current_milestone(finalized)
    if not milestone:
        return fallback
    task = get_current_task(finalized)
    lines = [
        'Continue from the current milestone before moving to anything else.',
        f"Milestone: {milestone.get('id', '')} - {milestone.get('title', '')}",
    ]
    if milestone.get('summary'):
        lines.append(f"Milestone summary: {milestone['summary']}")
    if task:
        lines.append(f"Task: {task.get('id', '')} - {task.get('title', '')}")
        if task.get('summary'):
            lines.append(f"Task summary: {task['summary']}")
    acceptance = milestone.get('acceptance', []) or []
    if acceptance:
        lines.append('Acceptance targets:')
        for item in acceptance[:5]:
            lines.append(f'- {item}')
    next_task = str(finalized.get('nextTaskId', '') or milestone.get('nextTaskId', '') or '').strip()
    if next_task:
        lines.append(f'Do not move to task {next_task} until this task is honestly complete.')
    elif finalized.get('nextMilestoneId'):
        lines.append(f"Do not move to milestone {finalized['nextMilestoneId']} until this milestone is honestly complete.")
    lines.append('Update OPERATOR_PLAN to reflect real progress, including task queue state, before you finish the turn.')
    return '\n'.join(lines)


def render_plan(plan: dict[str, Any] | None) -> str:
    if not plan or not plan.get('milestones'):
        return '- No milestone plan exists yet.'
    finalized = _finalize_plan(plan)
    lines: list[str] = []
    if finalized.get('summary'):
        lines.append(f"- Plan summary: {finalized['summary']}")
    if finalized.get('currentMilestoneId'):
        lines.append(f"- Current milestone: {finalized['currentMilestoneId']}")
    if finalized.get('nextMilestoneId'):
        lines.append(f"- Next milestone: {finalized['nextMilestoneId']}")
    if finalized.get('currentTaskId'):
        lines.append(f"- Current task: {finalized['currentTaskId']}")
    if finalized.get('nextTaskId'):
        lines.append(f"- Next task: {finalized['nextTaskId']}")
    counts = finalized.get('counts', {})
    if counts:
        counts_text = '; '.join(f"{key}={value}" for key, value in sorted(counts.items()) if value)
        if counts_text:
            lines.append(f"- Milestone counts: {counts_text}")
    task_counts = finalized.get('taskCounts', {})
    if task_counts:
        task_counts_text = '; '.join(f"{key}={value}" for key, value in sorted(task_counts.items()) if value)
        if task_counts_text:
            lines.append(f"- Task counts: {task_counts_text}")
    for milestone in finalized.get('milestones', []):
        line = f"- [{milestone['status']}] {milestone['id']}: {milestone['title']}"
        if milestone.get('summary'):
            line += f" -- {milestone['summary']}"
        lines.append(line)
        acceptance = milestone.get('acceptance', []) or []
        for item in acceptance[:3]:
            lines.append(f"  Acceptance: {item}")
        tasks = milestone.get('tasks', []) or []
        for task in tasks[:4]:
            task_line = f"  Task [{task.get('status', 'pending')}] {task.get('id', '')}: {task.get('title', '')}"
            if task.get('summary'):
                task_line += f" -- {task['summary']}"
            lines.append(task_line)
    return '\n'.join(lines)


def _finalize_milestone_state(milestone: Milestone) -> dict[str, Any]:
    task_ids = {task.id for task in milestone.tasks}
    current_task = milestone.current_task_id if milestone.current_task_id in task_ids else ''
    next_task = milestone.next_task_id if milestone.next_task_id in task_ids else ''
    if not current_task:
        current_task = _pick_current_task(milestone.tasks, milestone.status)
    if not next_task:
        next_task = _pick_next_task(milestone.tasks, current_task)
    task_counts = {'pending': 0, 'in_progress': 0, 'done': 0, 'blocked': 0}
    tasks: list[dict[str, Any]] = []
    for task in milestone.tasks[:12]:
        task_counts[task.status] = task_counts.get(task.status, 0) + 1
        tasks.append(
            {
                'id': task.id,
                'title': task.title,
                'status': task.status,
                'summary': task.summary,
            }
        )
    if milestone.status in {'done', 'blocked'}:
        current_task = ''
        next_task = ''
    return {
        'id': milestone.id,
        'title': milestone.title,
        'status': milestone.status,
        'summary': milestone.summary,
        'acceptance': milestone.acceptance,
        'currentTaskId': current_task,
        'nextTaskId': next_task,
        'taskCounts': task_counts,
        'tasks': tasks,
    }


def _pick_current_milestone(milestones: list[Milestone], current_id: str) -> str:
    milestone_ids = {item.id for item in milestones}
    if current_id and current_id in milestone_ids:
        return current_id
    for item in milestones:
        if item.status == 'in_progress':
            return item.id
    for item in milestones:
        if item.status == 'pending':
            return item.id
    return ''


def _pick_next_milestone(milestones: list[Milestone], current_id: str) -> str:
    if not milestones:
        return ''
    if current_id:
        seen_current = False
        for item in milestones:
            if item.id == current_id:
                seen_current = True
                continue
            if seen_current and item.status in {'pending', 'in_progress'}:
                return item.id
    for item in milestones:
        if item.id != current_id and item.status in {'pending', 'in_progress'}:
            return item.id
    return ''


def _finalize_plan(plan: dict[str, Any]) -> dict[str, Any]:
    milestones = [_normalize_milestone(item, index + 1) for index, item in enumerate(plan.get('milestones', []))]
    if not milestones:
        return {
            'summary': str(plan.get('summary', '')).strip(),
            'currentMilestoneId': '',
            'nextMilestoneId': '',
            'currentTaskId': '',
            'nextTaskId': '',
            'milestones': [],
            'counts': {'pending': 0, 'in_progress': 0, 'done': 0, 'blocked': 0},
            'taskCounts': {'pending': 0, 'in_progress': 0, 'done': 0, 'blocked': 0},
        }

    current_milestone = _pick_current_milestone(milestones, str(plan.get('currentMilestoneId') or '').strip())
    all_terminal = all(item.status in {'done', 'blocked'} for item in milestones)
    if all_terminal:
        current_milestone = ''
    next_milestone = str(plan.get('nextMilestoneId') or '').strip()
    milestone_ids = {item.id for item in milestones}
    if next_milestone and next_milestone not in milestone_ids:
        next_milestone = ''
    if not next_milestone:
        next_milestone = _pick_next_milestone(milestones, current_milestone)

    counts = {'pending': 0, 'in_progress': 0, 'done': 0, 'blocked': 0}
    task_counts = {'pending': 0, 'in_progress': 0, 'done': 0, 'blocked': 0}
    normalized_milestones: list[dict[str, Any]] = []
    for item in milestones[:10]:
        counts[item.status] = counts.get(item.status, 0) + 1
        milestone_state = _finalize_milestone_state(item)
        normalized_milestones.append(milestone_state)
        for key, value in milestone_state['taskCounts'].items():
            task_counts[key] = task_counts.get(key, 0) + value

    current_task = str(plan.get('currentTaskId') or '').strip()
    next_task = str(plan.get('nextTaskId') or '').strip()
    if current_milestone:
        active = next((item for item in normalized_milestones if item.get('id') == current_milestone), None)
        if active is not None:
            active_task_ids = {task['id'] for task in active.get('tasks', [])}
            if current_task and current_task not in active_task_ids:
                current_task = ''
            if next_task and next_task not in active_task_ids:
                next_task = ''
            if not current_task:
                current_task = str(active.get('currentTaskId', '')).strip()
            if not next_task:
                next_task = str(active.get('nextTaskId', '')).strip()
    else:
        current_task = ''
        next_task = ''

    return {
        'summary': str(plan.get('summary', '')).strip(),
        'currentMilestoneId': current_milestone,
        'nextMilestoneId': next_milestone,
        'currentTaskId': current_task,
        'nextTaskId': next_task,
        'milestones': normalized_milestones,
        'counts': counts,
        'taskCounts': task_counts,
    }
