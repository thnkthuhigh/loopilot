from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import RepoProfile
from .planner import render_plan
from .repo_inspector import WorkspaceInsight

_OPERATOR_STATE_RE = re.compile(r"<OPERATOR_STATE>\s*(.*?)\s*</OPERATOR_STATE>", re.IGNORECASE | re.DOTALL)
_SUPERVISOR_AUDIT_RE = re.compile(r"<SUPERVISOR_AUDIT>\s*(.*?)\s*</SUPERVISOR_AUDIT>", re.IGNORECASE | re.DOTALL)

_GOAL_PROFILE_GUIDANCE: dict[str, dict[str, list[str] | str]] = {
    'default': {
        'label': 'General delivery mode',
        'rules': [
            'Make the highest leverage change you can finish now.',
            'Prefer direct evidence over guesswork when deciding whether the task is done.',
        ],
    },
    'bug': {
        'label': 'Bug fix mode',
        'rules': [
            'Reproduce or localize the behavior gap before broad edits.',
            'Prefer the smallest safe patch that closes the bug.',
            'Add or update a regression check close to the broken behavior when possible.',
        ],
    },
    'feature': {
        'label': 'Feature mode',
        'rules': [
            'Implement the thinnest vertical slice first, then fill in missing edges.',
            'Update tests and docs that define the new behavior.',
            'Avoid broad rewrites unless the feature truly requires them.',
        ],
    },
    'refactor': {
        'label': 'Refactor mode',
        'rules': [
            'Preserve behavior unless the goal explicitly says otherwise.',
            'Reduce risk by keeping the diff reviewable and well-explained.',
            'Lean harder on tests and static checks before claiming done.',
        ],
    },
    'audit': {
        'label': 'Audit mode',
        'rules': [
            'Prioritize findings, evidence, and next actions over code churn.',
            'Only change files when the goal explicitly requests a fix.',
            'Surface severity clearly and explain the impact of each issue.',
        ],
    },
    'docs': {
        'label': 'Docs mode',
        'rules': [
            'Favor clarity, accuracy, and examples that match the real codebase.',
            'Do not claim docs are done if they drift from current implementation.',
            'Keep terminology consistent with the repository itself.',
        ],
    },
}


@dataclass(slots=True)
class Assessment:
    status: str = 'continue'
    score: int | None = None
    summary: str = ''
    next_prompt: str = ''
    blockers: list[dict[str, str]] = field(default_factory=list)
    tests: str = 'not_run'
    lint: str = 'not_run'
    needs_continue: bool = False
    done_reason: str = ''
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PromptContext:
    recent_history: str
    validation_snapshot: str
    repo_profile_text: str
    workspace_insight_text: str
    goal_profile_text: str
    plan_text: str
    intelligence_text: str = ''
    brain_text: str = ''
    intention_text: str = ''
    cross_repo_text: str = ''
    repo_map_text: str = ''


def _strip_code_fence(text: str) -> str:
    candidate = text.strip()
    if candidate.startswith('```'):
        lines = candidate.splitlines()
        if len(lines) >= 3:
            candidate = '\n'.join(lines[1:-1]).strip()
    return candidate


def _coerce_blockers(items: Any) -> list[dict[str, str]]:
    blockers: list[dict[str, str]] = []
    for item in items or []:
        if isinstance(item, dict):
            blockers.append(
                {
                    'severity': str(item.get('severity', 'medium')).lower(),
                    'item': str(item.get('item', item.get('description', ''))).strip(),
                }
            )
        elif isinstance(item, str):
            blockers.append({'severity': 'medium', 'item': item.strip()})
    return blockers


def _build_assessment(data: dict[str, Any], source: str) -> Assessment:
    next_prompt = str(
        data.get('next_prompt')
        or data.get('nextPrompt')
        or data.get('nextDirective')
        or ''
    ).strip()
    summary = str(data.get('summary', '')).strip()
    status = str(data.get('status', 'continue')).strip().lower() or 'continue'
    done_reason = str(data.get('done_reason', data.get('doneReason', ''))).strip()
    needs_continue = bool(data.get('needs_continue', data.get('needsContinue', False)))
    if source == 'supervisor_audit' and status != 'done':
        needs_continue = True
    return Assessment(
        status=status,
        score=int(data['score']) if data.get('score') is not None else None,
        summary=summary,
        next_prompt=next_prompt,
        blockers=_coerce_blockers(data.get('blockers', [])),
        tests=str(data.get('tests', 'not_run')).strip().lower(),
        lint=str(data.get('lint', 'not_run')).strip().lower(),
        needs_continue=needs_continue,
        done_reason=done_reason,
        raw=data,
    )


def _parse_tag(pattern: re.Pattern[str], response_text: str, source: str) -> Assessment | None:
    matches = pattern.findall(response_text or '')
    if not matches:
        return None
    payload = _strip_code_fence(matches[-1])
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None
    return _build_assessment(data, source)


def _parse_bare_json_assessment(response_text: str) -> Assessment | None:
    """Fallback: find a JSON object with 'status' key anywhere in the response.

    Handles cases where the LLM emits valid JSON but forgets the XML wrapper tags.
    Only accepts objects that contain a 'status' field to avoid false positives.
    """
    text = (response_text or '').strip()
    # Try last JSON code block first, then any {...} with status
    candidates: list[str] = []
    for m in re.finditer(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL):
        candidates.append(m.group(1))
    for m in re.finditer(r'(\{[^{}]*"status"\s*:[^{}]*\})', text, re.DOTALL):
        candidates.append(m.group(1))
    for candidate in reversed(candidates):
        try:
            data = json.loads(_strip_code_fence(candidate))
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, dict) and 'status' in data:
            return _build_assessment(data, 'operator_state')
    return None


def parse_operator_state(response_text: str) -> Assessment | None:
    operator_state = _parse_tag(_OPERATOR_STATE_RE, response_text, 'operator_state')
    if operator_state is not None:
        return operator_state
    audit = _parse_tag(_SUPERVISOR_AUDIT_RE, response_text, 'supervisor_audit')
    if audit is not None:
        return audit
    return _parse_bare_json_assessment(response_text)


def render_history(history: list[dict[str, Any]], limit: int = 3) -> str:
    items = history[-limit:]
    if not items:
        return '- No previous iterations yet.'
    lines: list[str] = []
    for item in items:
        lines.append(
            f"- Iteration {item['iteration']}: status={item['status']}, score={item.get('score', 'n/a')}, summary={item.get('summary', '')}"
        )
        milestone = str(item.get('currentMilestoneId', '')).strip()
        if milestone:
            lines.append(f"  Current milestone: {milestone}")
        task = str(item.get('currentTaskId', '')).strip()
        if task:
            lines.append(f"  Current task: {task}")
        next_prompt = str(item.get('next_prompt', '')).strip()
        if next_prompt:
            lines.append(f"  Next prompt: {next_prompt}")
    return '\n'.join(lines)


def render_validation_snapshot(validation_results: list[dict[str, Any]]) -> str:
    if not validation_results:
        return '- No operator-side validation commands are configured for this phase.'
    lines: list[str] = []
    for item in validation_results:
        source = item.get('source', '')
        source_text = f'; source={source}' if source else ''
        lines.append(
            f"- {item['name']}: status={item['status']}; command={item['command']}{source_text}; summary={item.get('summary', '')}"
        )
    return '\n'.join(lines)


def render_repo_profile(profile: RepoProfile, workspace: Path) -> str:
    if not profile.repo_name and not profile.summary and not profile.architecture and not profile.standards and not profile.priorities and not profile.protected_paths:
        return '- No repo profile is configured yet.'
    lines: list[str] = []
    if profile.repo_name:
        lines.append(f'- Repo name: {profile.repo_name}')
    lines.append(f'- Workspace root: {workspace}')
    if profile.summary:
        lines.append(f'- Summary: {profile.summary}')
    if profile.architecture:
        lines.append(f'- Architecture: {profile.architecture}')
    if profile.priorities:
        lines.append('- Current priorities: ' + '; '.join(profile.priorities))
    if profile.standards:
        lines.append('- Standards: ' + '; '.join(profile.standards))
    if profile.protected_paths:
        lines.append('- Protected paths: ' + '; '.join(profile.protected_paths))
    if profile.project_memory_files:
        lines.append('- Attached repo memory files: ' + '; '.join(str(path) for path in profile.project_memory_files if path.exists()))
    if profile.prompt_appendix:
        lines.append('- Extra operator note: ' + profile.prompt_appendix)
    return '\n'.join(lines)


def render_workspace_insight(insight: WorkspaceInsight) -> str:
    if insight.ecosystem == 'unknown':
        return '- Workspace ecosystem could not be auto-detected yet.'
    lines = [f'- Ecosystem: {insight.ecosystem}']
    if insight.package_manager:
        lines.append(f'- Package manager: {insight.package_manager}')
    if insight.package_manager_command:
        lines.append(f'- Package manager command style: {insight.package_manager_command}')
    if insight.validation_hints:
        hints = '; '.join(f"{name}={command}" for name, command in sorted(insight.validation_hints.items()))
        lines.append(f'- Suggested validation commands: {hints}')
    if insight.evidence:
        lines.append('- Detection evidence: ' + '; '.join(insight.evidence))
    return '\n'.join(lines)


def render_goal_profile(goal_profile: str) -> str:
    profile = _GOAL_PROFILE_GUIDANCE.get(goal_profile, _GOAL_PROFILE_GUIDANCE['default'])
    lines = [f"- Goal profile: {goal_profile} ({profile['label']})"]
    for rule in profile['rules']:
        lines.append(f'- {rule}')
    return '\n'.join(lines)


def _optional_section(title: str, content: str) -> str:
    """Render a prompt section only if content is non-empty."""
    if not content or not content.strip():
        return ''
    return f'\n{title}:\n{content}\n'


def build_prompt_context(
    history: list[dict[str, Any]],
    validation_results: list[dict[str, Any]],
    repo_profile: RepoProfile,
    workspace: Path,
    workspace_insight: WorkspaceInsight,
    goal_profile: str,
    plan: dict[str, Any] | None,
    intelligence_text: str = '',
    brain_text: str = '',
    intention_text: str = '',
    cross_repo_text: str = '',
    repo_map_text: str = '',
) -> PromptContext:
    return PromptContext(
        recent_history=render_history(history),
        validation_snapshot=render_validation_snapshot(validation_results),
        repo_profile_text=render_repo_profile(repo_profile, workspace),
        workspace_insight_text=render_workspace_insight(workspace_insight),
        goal_profile_text=render_goal_profile(goal_profile),
        plan_text=render_plan(plan),
        intelligence_text=intelligence_text,
        brain_text=brain_text,
        intention_text=intention_text,
        cross_repo_text=cross_repo_text,
        repo_map_text=repo_map_text,
    )


def build_initial_prompt(goal: str, target_score: int, context: PromptContext) -> str:
    return f"""You are GitHub Copilot running inside VS Code and you are being driven by an external local operator loop.
Treat this as a fresh session that must pick up context from the workspace plus the attached memory files.

⚠️ CRITICAL OUTPUT REQUIREMENT — READ FIRST:
Your reply MUST end with EXACTLY these two machine-readable blocks or the operator loop will treat this session as failed and retry.
Do not use any other format. Do not use [AUDIT_SCORE:...] or any other tag.
Fill in the real values — do not leave the template placeholders.

<OPERATOR_STATE>
{{
  "status": "continue",
  "score": 0,
  "summary": "what changed this turn",
  "next_prompt": "exact prompt the operator should send next if another turn is needed",
  "tests": "pass|fail|not_run|not_applicable",
  "lint": "pass|fail|not_run|not_applicable",
  "blockers": [{{"severity": "low|medium|high|critical", "item": "description"}}],
  "needs_continue": false,
  "done_reason": "short explanation"
}}
</OPERATOR_STATE>

<OPERATOR_PLAN>
{{
  "summary": "short plan summary",
  "current_milestone_id": "m1",
  "next_milestone_id": "m2",
  "current_task_id": "m1.t1",
  "next_task_id": "m1.t2",
  "milestones": [
    {{
      "id": "m1",
      "title": "milestone title",
      "status": "pending|in_progress|done|blocked",
      "summary": "what changed or what remains",
      "acceptance": ["acceptance item"],
      "current_task_id": "m1.t1",
      "next_task_id": "m1.t2",
      "tasks": [
        {{
          "id": "m1.t1",
          "title": "current task title",
          "status": "pending|in_progress|done|blocked",
          "summary": "task-level progress or remaining work"
        }}
      ]
    }}
  ]
}}
</OPERATOR_PLAN>

---

Goal:
{goal}

Goal profile:
{context.goal_profile_text}

Repo profile:
{context.repo_profile_text}

Workspace insight:
{context.workspace_insight_text}

Current milestone plan:
{context.plan_text}

Rules:
- Make real progress in the workspace, not just advice.
- Do the highest leverage chunk you can finish now.
- Respect protected paths unless the change is absolutely necessary and justified.
- Run the most relevant checks before claiming done.
- Keep milestone ids and task ids stable once you introduce them.
- If the plan is incomplete or naive, replace it with a tighter 2-7 milestone plan and add concrete tasks to the active milestone.
- If you need another turn, do not end vaguely. Put the exact baton for the next turn into next_prompt and make it match the current task queue.
- If you hit a tool ceiling or a continuation boundary, set needs_continue=true.
- Score the project honestly from 0 to 100.
- Only set status=done if the work is genuinely ready, score is at least {target_score}, and there are no critical/high blockers left.
- AUTONOMOUS MODE: Never ask the user a question. Never request confirmation. When uncertain, read files or search, then decide.

Recent operator memory:
{context.recent_history}

Operator-side validation snapshot:
{context.validation_snapshot}
{_optional_section('Codebase map', context.repo_map_text)}{_optional_section('Operator intelligence analysis', context.intelligence_text)}{_optional_section('Project brain (from past runs)', context.brain_text)}{_optional_section('Intention-aware guardrails', context.intention_text)}{_optional_section('Cross-repo insights', context.cross_repo_text)}
Remember: your reply MUST end with the <OPERATOR_STATE> and <OPERATOR_PLAN> blocks shown at the top of this prompt."""


def build_follow_up_prompt(goal: str, baton: str, target_score: int, context: PromptContext, reason: str) -> str:
    baton_text = baton or 'Continue from the first unfinished task in the workspace, then end with a fresh OPERATOR_STATE block.'
    return f"""Continue the same goal in a fresh externally-operated Copilot session.
Do not restart from scratch and do not repeat long summaries.

⚠️ CRITICAL OUTPUT REQUIREMENT — READ FIRST:
Your reply MUST end with EXACTLY these two machine-readable blocks or the operator loop will treat this session as failed and retry.
Do not use any other format. Do not use [AUDIT_SCORE:...] or any other tag.
Fill in the real values — do not leave the template placeholders.

<OPERATOR_STATE>
{{
  "status": "continue",
  "score": 0,
  "summary": "what changed this turn",
  "next_prompt": "exact prompt the operator should send next if another turn is needed",
  "tests": "pass|fail|not_run|not_applicable",
  "lint": "pass|fail|not_run|not_applicable",
  "blockers": [{{"severity": "low|medium|high|critical", "item": "description"}}],
  "needs_continue": false,
  "done_reason": "short explanation"
}}
</OPERATOR_STATE>

<OPERATOR_PLAN>
{{
  "summary": "short plan summary",
  "current_milestone_id": "m1",
  "next_milestone_id": "m2",
  "current_task_id": "m1.t1",
  "next_task_id": "m1.t2",
  "milestones": [
    {{
      "id": "m1",
      "title": "milestone title",
      "status": "pending|in_progress|done|blocked",
      "summary": "what changed or what remains",
      "acceptance": ["acceptance item"],
      "current_task_id": "m1.t1",
      "next_task_id": "m1.t2",
      "tasks": [
        {{
          "id": "m1.t1",
          "title": "current task title",
          "status": "pending|in_progress|done|blocked",
          "summary": "task-level progress or remaining work"
        }}
      ]
    }}
  ]
}}
</OPERATOR_PLAN>

---

Goal:
{goal}

Why you are being called again:
{reason}

Goal profile:
{context.goal_profile_text}

Repo profile:
{context.repo_profile_text}

Workspace insight:
{context.workspace_insight_text}

Current milestone plan:
{context.plan_text}

Exact baton:
{baton_text}

Treat that baton as the active task-level instruction, not a suggestion.

Recent operator memory:
{context.recent_history}

Operator-side validation snapshot:
{context.validation_snapshot}
{_optional_section('Codebase map', context.repo_map_text)}{_optional_section('Operator intelligence analysis', context.intelligence_text)}{_optional_section('Project brain (from past runs)', context.brain_text)}{_optional_section('Intention-aware guardrails', context.intention_text)}{_optional_section('Cross-repo insights', context.cross_repo_text)}
Remember:
- Only claim done if score is at least {target_score} and there are no critical/high blockers left.
- Respect protected paths unless the change is absolutely necessary and justified.
- Update the milestone plan honestly, keep milestone/task ids stable, and keep the active task queue truthful.
- If more work remains, set status=continue and give the exact next_prompt.
- AUTONOMOUS MODE: Never ask the user a question. Never request confirmation. When uncertain, read files or search, then decide.
- Your reply MUST end with the <OPERATOR_STATE> and <OPERATOR_PLAN> blocks shown at the top of this prompt."""


def fallback_assessment(response_text: str, needs_continue: bool) -> Assessment:
    summary = (response_text or '').strip().splitlines()
    preview = ' '.join(summary[:3]).strip()[:300]
    next_prompt = 'Continue from the first unfinished task. Inspect the current workspace changes before doing anything else.'
    blocker_msg = 'Copilot did not emit a parseable OPERATOR_STATE block.'
    if preview:
        blocker_msg += f' Response preview: {preview[:200]}'
    return Assessment(
        status='continue' if needs_continue else 'blocked',
        score=None,
        summary=preview,
        next_prompt=next_prompt,
        blockers=[] if needs_continue else [{'severity': 'high', 'item': blocker_msg}],
        tests='not_run',
        lint='not_run',
        needs_continue=needs_continue,
        done_reason='Missing OPERATOR_STATE block.' if not needs_continue else 'Session needs another continuation turn.',
        raw={},
    )
