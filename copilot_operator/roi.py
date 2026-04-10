"""ROI analytics — track time/cost savings from operator runs.

Analyses past run data to answer:
  - How much developer time was saved?
  - What is the LLM cost per successful task?
  - What is the success rate over time?
  - Which goal profiles are most effective?
"""

from __future__ import annotations

__all__ = [
    'ROIMetrics',
    'analyse_roi',
]

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .logging_config import get_logger

logger = get_logger('roi')

# Estimated developer minutes per task by profile
_DEVELOPER_MINUTES_ESTIMATE: dict[str, int] = {
    'bug': 45,
    'feature': 90,
    'refactor': 60,
    'audit': 30,
    'docs': 20,
    'stabilize': 40,
    'default': 60,
}


@dataclass(slots=True)
class ROIMetrics:
    """Aggregated ROI metrics from operator runs."""
    total_runs: int = 0
    successful_runs: int = 0
    failed_runs: int = 0
    total_iterations: int = 0
    total_llm_cost_usd: float = 0.0
    total_duration_minutes: float = 0.0
    estimated_dev_minutes_saved: float = 0.0
    cost_per_success_usd: float = 0.0
    success_rate: float = 0.0
    avg_iterations_per_run: float = 0.0
    avg_score: float = 0.0
    profile_breakdown: dict[str, dict[str, Any]] = field(default_factory=dict)
    monthly_trend: list[dict[str, Any]] = field(default_factory=list)

    @property
    def roi_ratio(self) -> float:
        """Return estimated ROI: dev hours saved / LLM cost in hours at $100/hr."""
        if self.total_llm_cost_usd <= 0:
            return float('inf') if self.estimated_dev_minutes_saved > 0 else 0.0
        dev_cost_saved = (self.estimated_dev_minutes_saved / 60) * 100  # $100/hr developer rate
        return dev_cost_saved / self.total_llm_cost_usd

    def to_dict(self) -> dict[str, Any]:
        return {
            'totalRuns': self.total_runs,
            'successfulRuns': self.successful_runs,
            'failedRuns': self.failed_runs,
            'successRate': round(self.success_rate, 1),
            'totalIterations': self.total_iterations,
            'avgIterationsPerRun': round(self.avg_iterations_per_run, 1),
            'avgScore': round(self.avg_score, 1),
            'totalLlmCostUsd': round(self.total_llm_cost_usd, 4),
            'costPerSuccessUsd': round(self.cost_per_success_usd, 4),
            'totalDurationMinutes': round(self.total_duration_minutes, 1),
            'estimatedDevMinutesSaved': round(self.estimated_dev_minutes_saved, 0),
            'roiRatio': round(self.roi_ratio, 1),
            'profileBreakdown': self.profile_breakdown,
        }

    def render_text(self) -> str:
        """Render a human-readable ROI report."""
        lines = [
            '═══ ROI Analytics ═══',
            '',
            f'  Runs:          {self.successful_runs}✓ / {self.failed_runs}✗ ({self.total_runs} total)',
            f'  Success rate:  {self.success_rate:.0f}%',
            f'  Avg score:     {self.avg_score:.0f}',
            f'  Iterations:    {self.total_iterations} total ({self.avg_iterations_per_run:.1f} avg)',
            '',
            f'  LLM cost:      ${self.total_llm_cost_usd:.4f} total',
            f'  Cost/success:  ${self.cost_per_success_usd:.4f}',
            f'  Duration:      {self.total_duration_minutes:.0f} min',
            '',
            f'  Dev time saved: ~{self.estimated_dev_minutes_saved:.0f} min ({self.estimated_dev_minutes_saved / 60:.1f} hr)',
            f'  ROI ratio:     {self.roi_ratio:.1f}x',
            '',
        ]
        if self.profile_breakdown:
            lines.append('  Per profile:')
            for profile, data in sorted(self.profile_breakdown.items()):
                runs = data.get('runs', 0)
                success = data.get('success', 0)
                lines.append(f'    {profile}: {success}/{runs} runs, avg score {data.get("avgScore", 0):.0f}')
        return '\n'.join(lines)


def analyse_roi(log_dir: Path) -> ROIMetrics:
    """Analyse all past runs to compute ROI metrics."""
    metrics = ROIMetrics()
    scores: list[int] = []
    profile_data: dict[str, dict[str, Any]] = {}

    if not log_dir.exists():
        return metrics

    for run_dir in log_dir.iterdir():
        if not run_dir.is_dir():
            continue

        # Find the last decision file
        decision_files = sorted(run_dir.glob('iteration-*-decision.json'))
        if not decision_files:
            continue

        try:
            last_decision = json.loads(decision_files[-1].read_text(encoding='utf-8'))
        except (json.JSONDecodeError, OSError):
            continue

        metrics.total_runs += 1
        reason_code = last_decision.get('reasonCode', '')
        assessment = last_decision.get('assessment', {}) or {}
        score = assessment.get('score')
        plan = last_decision.get('plan', {}) or {}

        # Infer profile from plan or default
        profile = 'default'
        if isinstance(plan, dict):
            profile = plan.get('profile', 'default') or 'default'

        iterations = len(decision_files)
        metrics.total_iterations += iterations

        is_success = reason_code == 'STOP_GATE_PASSED'
        if is_success:
            metrics.successful_runs += 1
            metrics.estimated_dev_minutes_saved += _DEVELOPER_MINUTES_ESTIMATE.get(profile, 60)
        else:
            metrics.failed_runs += 1

        if score is not None:
            scores.append(score)

        # Profile breakdown
        if profile not in profile_data:
            profile_data[profile] = {'runs': 0, 'success': 0, 'scores': []}
        profile_data[profile]['runs'] += 1
        if is_success:
            profile_data[profile]['success'] += 1
        if score is not None:
            profile_data[profile]['scores'].append(score)

    # Compute aggregates
    if metrics.total_runs > 0:
        metrics.success_rate = metrics.successful_runs / metrics.total_runs * 100
        metrics.avg_iterations_per_run = metrics.total_iterations / metrics.total_runs
    if scores:
        metrics.avg_score = sum(scores) / len(scores)
    if metrics.successful_runs > 0 and metrics.total_llm_cost_usd > 0:
        metrics.cost_per_success_usd = metrics.total_llm_cost_usd / metrics.successful_runs

    # Profile breakdown
    for profile, data in profile_data.items():
        avg = sum(data['scores']) / len(data['scores']) if data['scores'] else 0
        metrics.profile_breakdown[profile] = {
            'runs': data['runs'],
            'success': data['success'],
            'avgScore': round(avg, 1),
        }

    return metrics
