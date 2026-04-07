"""Project Brain — learns from completed runs and enriches project knowledge.

Analyses run history to extract recurring patterns, failure hotspots, and
successful strategies, then persists those insights into the project memory
so that future runs start with more context.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class RunDigest:
    """Condensed record of a single operator run."""
    run_id: str = ''
    goal: str = ''
    goal_profile: str = ''
    status: str = ''              # complete, blocked
    final_reason_code: str = ''
    iterations: int = 0
    final_score: int | None = None
    blocker_items: list[str] = field(default_factory=list)
    validation_failures: list[str] = field(default_factory=list)
    milestone_count: int = 0
    task_done_count: int = 0
    task_total_count: int = 0
    duration_seconds: float = 0.0
    timestamp: str = ''


@dataclass(slots=True)
class LearningEntry:
    """A single insight extracted from run analysis."""
    category: str = ''       # failure_pattern, success_pattern, blocker_hotspot, strategy_note
    detail: str = ''
    source_run: str = ''
    confidence: float = 1.0
    timestamp: str = ''


@dataclass(slots=True)
class ProjectInsights:
    """Aggregated project knowledge from run history."""
    total_runs: int = 0
    success_rate: float = 0.0
    avg_iterations: float = 0.0
    avg_score: float = 0.0
    common_blockers: list[str] = field(default_factory=list)
    common_validation_failures: list[str] = field(default_factory=list)
    best_goal_profiles: dict[str, float] = field(default_factory=dict)  # profile → success rate
    learnings: list[LearningEntry] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Run digest extraction
# ---------------------------------------------------------------------------

def extract_run_digest(state: dict[str, Any]) -> RunDigest:
    """Extract a condensed digest from a full state.json."""
    history = state.get('history', [])
    plan = state.get('plan', {})
    milestones = plan.get('milestones', [])

    task_done = 0
    task_total = 0
    for m in milestones:
        for t in m.get('tasks', []):
            task_total += 1
            if t.get('status') == 'done':
                task_done += 1

    last_score = None
    all_blocker_items: list[str] = []
    all_validation_failures: list[str] = []
    for h in history:
        if h.get('score') is not None:
            last_score = h['score']
        for b in h.get('blockers') or []:
            item = b.get('item', '')
            if item and item not in all_blocker_items:
                all_blocker_items.append(item)
        for v in h.get('validation_after') or []:
            if v.get('status') in ('fail', 'timeout') and v.get('name') not in all_validation_failures:
                all_validation_failures.append(v['name'])

    started = state.get('startedAt', '')
    finished = state.get('finishedAt', '')
    duration = 0.0
    if started and finished:
        try:
            t0 = datetime.fromisoformat(started)
            t1 = datetime.fromisoformat(finished)
            duration = (t1 - t0).total_seconds()
        except (ValueError, TypeError):
            pass

    return RunDigest(
        run_id=state.get('runId', ''),
        goal=state.get('goal', ''),
        goal_profile=state.get('goalProfile', ''),
        status=state.get('status', ''),
        final_reason_code=state.get('finalReasonCode', ''),
        iterations=len(history),
        final_score=last_score,
        blocker_items=all_blocker_items,
        validation_failures=all_validation_failures,
        milestone_count=len(milestones),
        task_done_count=task_done,
        task_total_count=task_total,
        duration_seconds=duration,
        timestamp=finished or started or '',
    )


# ---------------------------------------------------------------------------
# History loading
# ---------------------------------------------------------------------------

def load_run_history(log_dir: Path) -> list[RunDigest]:
    """Walk the log directory and extract digests from each run's state snapshot."""
    digests: list[RunDigest] = []
    if not log_dir.exists():
        return digests

    for run_dir in sorted(log_dir.iterdir()):
        if not run_dir.is_dir():
            continue
        # Look for a decision file from the last iteration to reconstruct
        # We need to read the summary written alongside.
        summary_file = run_dir.parent.parent / 'session-summary.json'
        if summary_file.exists():
            try:
                summary = json.loads(summary_file.read_text(encoding='utf-8'))
                digests.append(RunDigest(
                    run_id=run_dir.name,
                    status=summary.get('status', ''),
                    final_reason_code=summary.get('reasonCode', ''),
                    iterations=summary.get('iterations', 0),
                    final_score=summary.get('score'),
                    timestamp=run_dir.stat().st_mtime if run_dir.exists() else 0,
                ))
            except (json.JSONDecodeError, OSError):
                continue
    return digests


def load_run_digests_from_states(log_dir: Path) -> list[RunDigest]:
    """Load digests by scanning run directories for decision files."""
    digests: list[RunDigest] = []
    if not log_dir.exists():
        return digests

    for run_dir in sorted(log_dir.iterdir()):
        if not run_dir.is_dir():
            continue
        # Find the last decision file
        decision_files = sorted(run_dir.glob('iteration-*-decision.json'))
        if not decision_files:
            continue
        try:
            last_decision = json.loads(decision_files[-1].read_text(encoding='utf-8'))
            plan = last_decision.get('plan', {})
            assessment = last_decision.get('assessment', {})
            digests.append(RunDigest(
                run_id=run_dir.name,
                goal='',
                goal_profile='',
                status='complete' if last_decision.get('reasonCode') == 'STOP_GATE_PASSED' else 'blocked',
                final_reason_code=last_decision.get('reasonCode', ''),
                iterations=len(decision_files),
                final_score=assessment.get('score'),
                milestone_count=len(plan.get('milestones', [])),
            ))
        except (json.JSONDecodeError, OSError):
            continue
    return digests


# ---------------------------------------------------------------------------
# Pattern analysis
# ---------------------------------------------------------------------------

def analyse_runs(digests: list[RunDigest]) -> ProjectInsights:
    """Analyse a collection of run digests and extract project-level insights."""
    if not digests:
        return ProjectInsights()

    total = len(digests)
    successes = [d for d in digests if d.status == 'complete']
    success_rate = len(successes) / total if total else 0.0

    iterations = [d.iterations for d in digests if d.iterations > 0]
    avg_iterations = sum(iterations) / len(iterations) if iterations else 0.0

    scores = [d.final_score for d in digests if d.final_score is not None]
    avg_score = sum(scores) / len(scores) if scores else 0.0

    # Common blockers
    blocker_freq: dict[str, int] = {}
    for d in digests:
        for b in d.blocker_items:
            blocker_freq[b] = blocker_freq.get(b, 0) + 1
    common_blockers = sorted(blocker_freq, key=blocker_freq.get, reverse=True)[:5]

    # Common validation failures
    vf_freq: dict[str, int] = {}
    for d in digests:
        for v in d.validation_failures:
            vf_freq[v] = vf_freq.get(v, 0) + 1
    common_vf = sorted(vf_freq, key=vf_freq.get, reverse=True)[:3]

    # Profile success rates
    profile_stats: dict[str, list[bool]] = {}
    for d in digests:
        if d.goal_profile:
            profile_stats.setdefault(d.goal_profile, []).append(d.status == 'complete')
    best_profiles = {
        p: sum(vs) / len(vs) for p, vs in profile_stats.items() if vs
    }

    # Extract learnings
    learnings: list[LearningEntry] = []
    now = datetime.now(timezone.utc).isoformat()

    # Pattern: runs that complete quickly
    fast_successes = [d for d in successes if d.iterations <= 2]
    if fast_successes and total >= 3:
        learnings.append(LearningEntry(
            category='success_pattern',
            detail=f'{len(fast_successes)}/{total} runs completed in 2 or fewer iterations.',
            confidence=0.8,
            timestamp=now,
        ))

    # Pattern: persistent blocker
    for b, count in blocker_freq.items():
        if count >= 2:
            learnings.append(LearningEntry(
                category='blocker_hotspot',
                detail=f'Blocker "{b}" appeared in {count}/{total} runs.',
                confidence=min(1.0, count / total),
                timestamp=now,
            ))

    # Pattern: validation always fails
    for v, count in vf_freq.items():
        if count >= 2:
            learnings.append(LearningEntry(
                category='failure_pattern',
                detail=f'Validation [{v}] failed in {count}/{total} runs — likely a systemic issue.',
                confidence=min(1.0, count / total),
                timestamp=now,
            ))

    # Pattern: certain profiles work better
    for p, rate in best_profiles.items():
        if rate >= 0.8 and len(profile_stats.get(p, [])) >= 2:
            learnings.append(LearningEntry(
                category='strategy_note',
                detail=f'Profile "{p}" has {rate:.0%} success rate across {len(profile_stats[p])} runs.',
                confidence=0.7,
                timestamp=now,
            ))

    return ProjectInsights(
        total_runs=total,
        success_rate=success_rate,
        avg_iterations=avg_iterations,
        avg_score=avg_score,
        common_blockers=common_blockers,
        common_validation_failures=common_vf,
        best_goal_profiles=best_profiles,
        learnings=learnings,
    )


# ---------------------------------------------------------------------------
# Memory persistence
# ---------------------------------------------------------------------------

def render_insights_for_memory(insights: ProjectInsights) -> str:
    """Render project insights as a markdown section for the memory file."""
    if insights.total_runs == 0:
        return ''

    lines = [
        '',
        '## Project Brain Insights',
        f'- Total runs analysed: {insights.total_runs}',
        f'- Success rate: {insights.success_rate:.0%}',
        f'- Average iterations: {insights.avg_iterations:.1f}',
        f'- Average final score: {insights.avg_score:.0f}',
    ]

    if insights.common_blockers:
        lines.append(f"- Recurring blockers: {'; '.join(insights.common_blockers[:3])}")

    if insights.common_validation_failures:
        lines.append(f"- Frequently failing validations: {', '.join(insights.common_validation_failures)}")

    if insights.best_goal_profiles:
        profile_text = ', '.join(
            f'{p} ({r:.0%})' for p, r in sorted(insights.best_goal_profiles.items(), key=lambda x: -x[1])
        )
        lines.append(f'- Best performing profiles: {profile_text}')

    if insights.learnings:
        lines.append('')
        lines.append('### Learnings')
        for entry in insights.learnings[:10]:
            lines.append(f'- [{entry.category}] {entry.detail}')

    return '\n'.join(lines)


def render_insights_for_prompt(insights: ProjectInsights) -> str:
    """Render concise insight text suitable for injection into the operator prompt."""
    if insights.total_runs == 0:
        return ''

    lines = ['Project brain (from past runs):']
    lines.append(f'- {insights.total_runs} prior runs, {insights.success_rate:.0%} success rate, avg score {insights.avg_score:.0f}')

    if insights.common_blockers:
        lines.append(f"- Watch out for recurring blockers: {'; '.join(insights.common_blockers[:3])}")

    if insights.common_validation_failures:
        lines.append(f"- Validations that often fail: {', '.join(insights.common_validation_failures)}")

    for entry in insights.learnings[:3]:
        lines.append(f'- {entry.detail}')

    return '\n'.join(lines)
