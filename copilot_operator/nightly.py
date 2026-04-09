"""Nightly delivery loop — scheduled queue run with reporting.

Enables configure-and-forget operation:
  1. Queue tasks (from issues, backlog file, or manual goals)
  2. Run them overnight or on a schedule
  3. Generate a morning report with results + ROI metrics
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .logging_config import get_logger

logger = get_logger('nightly')


@dataclass(slots=True)
class NightlyConfig:
    """Configuration for a nightly delivery run."""
    goals_file: Path | None = None
    labels: list[str] = field(default_factory=lambda: ['bug', 'enhancement'])
    max_issues: int = 5
    max_iterations_per_task: int = 6
    report_file: Path = Path('.copilot-operator/nightly-report.json')
    dry_run: bool = False


@dataclass(slots=True)
class NightlyReport:
    """Result of a nightly delivery run."""
    started_at: str = ''
    finished_at: str = ''
    total_tasks: int = 0
    completed: int = 0
    failed: int = 0
    blocked: int = 0
    total_iterations: int = 0
    total_llm_cost_usd: float = 0.0
    total_duration_seconds: float = 0.0
    tasks: list[dict[str, Any]] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        return (self.completed / self.total_tasks * 100) if self.total_tasks > 0 else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            'startedAt': self.started_at,
            'finishedAt': self.finished_at,
            'totalTasks': self.total_tasks,
            'completed': self.completed,
            'failed': self.failed,
            'blocked': self.blocked,
            'successRate': round(self.success_rate, 1),
            'totalIterations': self.total_iterations,
            'totalLlmCostUsd': round(self.total_llm_cost_usd, 4),
            'totalDurationSeconds': round(self.total_duration_seconds, 1),
            'tasks': self.tasks,
        }

    def render_text(self) -> str:
        """Render a human-readable morning report."""
        lines = [
            '═══ Nightly Delivery Report ═══',
            '',
            f'  Tasks:     {self.completed}✓ / {self.failed}✗ / {self.blocked}⚠ ({self.total_tasks} total)',
            f'  Success:   {self.success_rate:.0f}%',
            f'  Iters:     {self.total_iterations}',
            f'  LLM cost:  ${self.total_llm_cost_usd:.4f}',
            f'  Duration:  {self.total_duration_seconds / 60:.1f} min',
            '',
        ]
        for t in self.tasks:
            status_icon = {'complete': '✓', 'blocked': '⚠', 'error': '✗'}.get(t.get('status', ''), '?')
            score = t.get('score', 'N/A')
            lines.append(f'  {status_icon} {t.get("goal", "")[:60]}  (score={score})')
        return '\n'.join(lines)


def collect_nightly_goals(config: NightlyConfig, workspace: Path) -> list[str]:
    """Collect goals from file + optionally from GitHub issues."""
    goals: list[str] = []

    # From goals file
    if config.goals_file and config.goals_file.exists():
        for line in config.goals_file.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if line and not line.startswith('#'):
                goals.append(line)

    return goals


def run_nightly(
    workspace: Path,
    goals: list[str],
    max_iterations: int = 6,
    dry_run: bool = False,
    report_file: Path | None = None,
) -> NightlyReport:
    """Execute a nightly delivery run over a list of goals."""
    from datetime import datetime, timezone

    from .config import load_config
    from .operator import CopilotOperator

    report = NightlyReport(
        started_at=datetime.now(timezone.utc).isoformat(),
        total_tasks=len(goals),
    )
    start_time = time.monotonic()

    for idx, goal in enumerate(goals, 1):
        logger.info('[nightly] [%d/%d] %s', idx, len(goals), goal[:80])
        task_start = time.monotonic()

        try:
            config = load_config(str(workspace / 'copilot-operator.yml'), str(workspace))
            config.max_iterations = max_iterations
            operator = CopilotOperator(config, dry_run=dry_run)
            result = operator.run(goal)

            status = result.get('status', 'unknown')
            task_record: dict[str, Any] = {
                'goal': goal[:200],
                'status': status,
                'score': result.get('score'),
                'iterations': result.get('iterations', 0),
                'reasonCode': result.get('reasonCode', ''),
                'durationSeconds': round(time.monotonic() - task_start, 1),
            }

            llm_cost = result.get('llmCost', {})
            if llm_cost:
                task_record['llmCostUsd'] = llm_cost.get('estimatedUsd', 0)
                report.total_llm_cost_usd += llm_cost.get('estimatedUsd', 0)

            report.total_iterations += result.get('iterations', 0)

            if status == 'complete':
                report.completed += 1
            elif status == 'blocked':
                report.blocked += 1
            else:
                report.failed += 1

        except Exception as exc:
            logger.error('[nightly] Task failed: %s', exc)
            task_record = {
                'goal': goal[:200],
                'status': 'error',
                'error': str(exc)[:200],
                'durationSeconds': round(time.monotonic() - task_start, 1),
            }
            report.failed += 1

        report.tasks.append(task_record)

    report.finished_at = datetime.now(timezone.utc).isoformat()
    report.total_duration_seconds = time.monotonic() - start_time

    # Write report
    out_path = report_file or Path('.copilot-operator/nightly-report.json')
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report.to_dict(), indent=2, ensure_ascii=False), encoding='utf-8')
    logger.info('[nightly] Report written to %s', out_path)

    return report
