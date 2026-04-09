"""Terminal dashboard — live operator status display.

Provides a compact, refreshable terminal UI showing:
  - Current run status + score trajectory
  - Active milestone/task progress
  - Worker health
  - Validation results
  - LLM cost summary
  - Queue status (if active)
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .terminal import bold, cyan, dim, score_color, status_badge


@dataclass(slots=True)
class DashboardSnapshot:
    """All data needed to render one frame of the dashboard."""
    status: str = ''
    goal: str = ''
    goal_profile: str = ''
    run_id: str = ''
    iteration: int = 0
    max_iterations: int = 6
    score: int | None = None
    target_score: int = 85
    score_history: list[int | None] = None  # type: ignore[assignment]
    plan_summary: str = ''
    current_milestone: str = ''
    current_task: str = ''
    milestones: list[dict[str, Any]] = None  # type: ignore[assignment]
    worker_health: dict[str, Any] = None  # type: ignore[assignment]
    llm_cost: dict[str, Any] = None  # type: ignore[assignment]
    validation_results: list[dict[str, Any]] = None  # type: ignore[assignment]
    errors: list[dict[str, Any]] = None  # type: ignore[assignment]
    all_changed_files: list[str] = None  # type: ignore[assignment]
    elapsed_seconds: float = 0.0
    resume_count: int = 0

    def __post_init__(self) -> None:
        if self.score_history is None:
            self.score_history = []
        if self.milestones is None:
            self.milestones = []
        if self.worker_health is None:
            self.worker_health = {}
        if self.llm_cost is None:
            self.llm_cost = {}
        if self.validation_results is None:
            self.validation_results = []
        if self.errors is None:
            self.errors = []
        if self.all_changed_files is None:
            self.all_changed_files = []


def load_dashboard_snapshot(state_file: Path) -> DashboardSnapshot | None:
    """Load a dashboard snapshot from the operator state file."""
    if not state_file.exists():
        return None
    try:
        data = json.loads(state_file.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError):
        return None

    history = data.get('history', [])
    last = history[-1] if history else {}
    scores = [h.get('score') for h in history]

    plan = data.get('plan', {}) or {}
    milestones = plan.get('milestones', []) if isinstance(plan, dict) else []

    return DashboardSnapshot(
        status=data.get('status', 'unknown'),
        goal=data.get('goal', ''),
        goal_profile=data.get('goalProfile', ''),
        run_id=data.get('runId', ''),
        iteration=last.get('iteration', len(history)),
        max_iterations=data.get('maxIterations', 6),
        score=last.get('score'),
        target_score=data.get('targetScore', 85),
        score_history=scores,
        plan_summary=plan.get('summary', '') if isinstance(plan, dict) else '',
        current_milestone=last.get('currentMilestoneId', ''),
        current_task=last.get('currentTaskId', ''),
        milestones=milestones,
        worker_health=data.get('workerHealth', {}),
        llm_cost=data.get('llmCost', {}),
        validation_results=last.get('validation_after', []),
        errors=data.get('errors', []),
        all_changed_files=data.get('allChangedFiles', []),
        resume_count=data.get('resumeCount', 0),
    )


def _score_bar(score: int | None, target: int, width: int = 20) -> str:
    """Render a compact progress bar for the score."""
    if score is None:
        return dim('▒' * width + ' ?/?')
    filled = min(int(score / 100 * width), width)
    bar = '█' * filled + '░' * (width - filled)
    color_score = score_color(score)
    target_mark = f' {color_score}/{target}'
    return bar + target_mark


def _score_sparkline(scores: list[int | None]) -> str:
    """Render a text sparkline of score history."""
    chars = ' ▁▂▃▄▅▆▇█'
    values = [s for s in scores if s is not None]
    if not values:
        return dim('no data')
    lo, hi = min(values), max(values)
    rng = max(hi - lo, 1)
    return ''.join(chars[min(int((v - lo) / rng * 8), 8)] for v in values)


def render_dashboard(snap: DashboardSnapshot) -> str:
    """Render a full dashboard frame as a string."""
    lines: list[str] = []

    # Header
    lines.append(bold('═══ Copilot Operator Dashboard ═══'))
    lines.append('')

    # Status bar
    badge = status_badge(snap.status)
    iter_text = f'{snap.iteration}/{snap.max_iterations}'
    lines.append(f'  Status: {badge}  |  Iteration: {cyan(iter_text)}  |  Profile: {snap.goal_profile}')

    # Goal
    goal_short = snap.goal[:80] + ('…' if len(snap.goal) > 80 else '')
    lines.append(f'  Goal: {goal_short}')
    lines.append('')

    # Score section
    lines.append(bold('── Score ──'))
    bar = _score_bar(snap.score, snap.target_score)
    sparkline = _score_sparkline(snap.score_history)
    lines.append(f'  Current: {bar}')
    lines.append(f'  History: {sparkline}')
    lines.append('')

    # Milestones
    if snap.milestones:
        lines.append(bold('── Milestones ──'))
        for ms in snap.milestones[:6]:
            ms_status = ms.get('status', 'pending')
            icon = {'done': '✓', 'in_progress': '▶', 'active': '▶'}.get(ms_status, '○')
            title = ms.get('title', ms.get('id', ''))[:50]
            lines.append(f'  {icon} {title}')
        lines.append('')

    # Validation
    if snap.validation_results:
        lines.append(bold('── Validation ──'))
        for v in snap.validation_results:
            icon = '✓' if v.get('status') == 'pass' else '✗'
            lines.append(f'  {icon} {v.get("name", "?")} — {v.get("summary", "")[:60]}')
        lines.append('')

    # Worker health
    if snap.worker_health:
        lines.append(bold('── Worker ──'))
        avg = snap.worker_health.get('avgScore', 0)
        trend = snap.worker_health.get('scoreTrend', 'stable')
        errors = snap.worker_health.get('consecutiveErrors', 0)
        healthy = '✓' if snap.worker_health.get('isHealthy', True) else '✗'
        lines.append(f'  Health: {healthy}  |  Avg score: {avg:.0f}  |  Trend: {trend}  |  Errors: {errors}')
        lines.append('')

    # LLM cost
    if snap.llm_cost:
        calls = snap.llm_cost.get('calls', 0)
        tokens = snap.llm_cost.get('totalTokens', 0)
        cost = snap.llm_cost.get('estimatedUsd', 0)
        prompt_tokens = snap.llm_cost.get('promptTokens', 0)
        completion_tokens = snap.llm_cost.get('completionTokens', 0)
        lines.append(bold('── LLM Cost ──'))
        lines.append(f'  Calls: {calls}  |  Tokens: {tokens:,}  |  Cost: ${cost:.4f}')
        if prompt_tokens or completion_tokens:
            lines.append(f'  Prompt: {prompt_tokens:,}  |  Completion: {completion_tokens:,}')
        if snap.iteration and cost > 0:
            lines.append(f'  Cost/iteration: ${cost / max(snap.iteration, 1):.4f}')
        lines.append('')

    # Changed files
    if snap.all_changed_files:
        lines.append(bold('── Changed Files ──'))
        for f in snap.all_changed_files[:10]:
            lines.append(f'  • {f}')
        if len(snap.all_changed_files) > 10:
            lines.append(f'  … +{len(snap.all_changed_files) - 10} more')
        lines.append('')

    # Errors
    if snap.errors:
        lines.append(bold('── Recent Errors ──'))
        for err in snap.errors[-3:]:
            lines.append(f'  ⚠ iter {err.get("iteration", "?")} — {err.get("error", "")[:80]}')
        lines.append('')

    # Footer
    lines.append(dim(f'Run: {snap.run_id[:12]}  |  Resumes: {snap.resume_count}'))
    return '\n'.join(lines)


def render_blocker_trend(state_file: Path) -> str:
    """Render blocker trend across iterations from state file."""
    if not state_file.exists():
        return 'No state file found.'
    try:
        data = json.loads(state_file.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError):
        return 'Cannot read state file.'
    history = data.get('history', [])
    if not history:
        return 'No iteration history.'
    lines = [bold('── Blocker Trend ──')]
    for entry in history[-10:]:
        iteration = entry.get('iteration', '?')
        blockers = entry.get('blockers', []) or []
        count = len(blockers)
        bar = '█' * min(count, 10) if count else dim('none')
        lines.append(f'  iter {iteration}: {bar} ({count})')
    return '\n'.join(lines)


def render_repo_health(log_dir: Path) -> str:
    """Render per-repo health summary from run logs."""
    if not log_dir.exists():
        return 'No log directory.'
    run_dirs = sorted(
        [d for d in log_dir.iterdir() if d.is_dir()],
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )
    if not run_dirs:
        return 'No runs found.'

    scores: list[int] = []
    statuses: dict[str, int] = {}
    for run_dir in run_dirs[:20]:
        decision_files = list(run_dir.glob('iteration-*-decision.json'))
        for df in decision_files:
            try:
                dec = json.loads(df.read_text(encoding='utf-8'))
                score = dec.get('score')
                if isinstance(score, int):
                    scores.append(score)
                status = dec.get('action', 'unknown')
                statuses[status] = statuses.get(status, 0) + 1
            except (json.JSONDecodeError, OSError):
                continue

    lines = [bold('── Repo Health ──')]
    lines.append(f'  Runs analysed: {len(run_dirs[:20])}')
    if scores:
        avg = sum(scores) / len(scores)
        lines.append(f'  Avg score: {avg:.0f}  |  Min: {min(scores)}  |  Max: {max(scores)}')
    if statuses:
        status_text = ', '.join(f'{k}={v}' for k, v in sorted(statuses.items()))
        lines.append(f'  Decisions: {status_text}')
    return '\n'.join(lines)


def run_dashboard(
    state_file: Path,
    interval: float = 2.0,
    count: int | None = None,
    clear: bool = True,
) -> None:
    """Run a live-updating dashboard in the terminal."""
    import os

    updates = 0
    try:
        while True:
            snap = load_dashboard_snapshot(state_file)
            if clear:
                os.system('cls' if os.name == 'nt' else 'clear')

            if snap is None:
                print(dim('Waiting for operator to start...'))
            else:
                print(render_dashboard(snap))

                # Stop if terminal state
                if snap.status in ('complete', 'error', 'escalated'):
                    print(f'\n{bold("Run finished.")} Status: {status_badge(snap.status)}')
                    break

            updates += 1
            if count and updates >= count:
                break

            time.sleep(interval)
    except KeyboardInterrupt:
        print('\nDashboard stopped.')
