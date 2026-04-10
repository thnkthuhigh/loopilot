"""Worker Runtime Layer — persistent worker abstraction for Copilot sessions.

A Worker wraps a Copilot instance with:
  - Persistent context across iterations (prompt history condensation)
  - Health monitoring (consecutive errors, score trends)
  - Session state independent of VS Code session IDs

Key insight: VS Code CLI creates a new chat session per invocation.
Workers compensate by maintaining a **context window** that gets injected
into each new session, so Copilot behaves as if it has continuity.
"""

from __future__ import annotations

__all__ = [
    'WorkerStatus',
    'HealthSignal',
    'IterationRecord',
    'WorkerHealth',
    'TaskInput',
    'RequiredArtifacts',
    'RecyclePolicy',
    'Worker',
    'Task',
    'TaskQueue',
]

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4


class WorkerStatus(str, Enum):
    IDLE = 'idle'
    RUNNING = 'running'
    PAUSED = 'paused'
    FAILED = 'failed'
    DONE = 'done'
    RECYCLED = 'recycled'  # Worker was recycled due to degradation


class HealthSignal(str, Enum):
    """Worker health signal — reported to scheduler for orchestration decisions."""
    HEALTHY = 'healthy'       # Normal operation
    DEGRADED = 'degraded'     # Experiencing issues but can continue
    DEAD = 'dead'             # Unrecoverable — needs recycle


@dataclass(slots=True)
class IterationRecord:
    """A single iteration's summary for context condensation."""
    iteration: int
    score: int | None = None
    decision_code: str = ''
    summary: str = ''           # LLM or heuristic summary of what happened
    changed_files: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    timestamp: float = 0.0


@dataclass(slots=True)
class WorkerHealth:
    """Health metrics for a worker."""
    consecutive_errors: int = 0
    total_errors: int = 0
    total_iterations: int = 0
    score_history: list[int] = field(default_factory=list)
    last_error: str = ''
    last_activity: float = 0.0

    @property
    def is_healthy(self) -> bool:
        return self.consecutive_errors < 3

    @property
    def avg_score(self) -> float:
        if not self.score_history:
            return 0.0
        return sum(self.score_history) / len(self.score_history)

    @property
    def score_trend(self) -> str:
        """Return 'improving', 'declining', or 'stable'."""
        if len(self.score_history) < 2:
            return 'stable'
        recent = self.score_history[-3:]
        if len(recent) >= 2 and recent[-1] > recent[0]:
            return 'improving'
        if len(recent) >= 2 and recent[-1] < recent[0]:
            return 'declining'
        return 'stable'

    def record_success(self, score: int | None = None) -> None:
        self.consecutive_errors = 0
        self.total_iterations += 1
        self.last_activity = time.time()
        if score is not None:
            self.score_history.append(score)

    def record_error(self, error: str) -> None:
        self.consecutive_errors += 1
        self.total_errors += 1
        self.total_iterations += 1
        self.last_error = error
        self.last_activity = time.time()

    @property
    def signal(self) -> HealthSignal:
        """Return the current health signal for orchestration."""
        if self.consecutive_errors >= 3:
            return HealthSignal.DEAD
        if self.consecutive_errors >= 1 or (self.score_trend == 'declining' and len(self.score_history) >= 3):
            return HealthSignal.DEGRADED
        return HealthSignal.HEALTHY


# ---------------------------------------------------------------------------
# Worker Contract — formalized task interface
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class TaskInput:
    """Standardized task input for a worker."""
    goal: str = ''
    goal_profile: str = 'default'
    target_score: int = 85
    max_iterations: int = 6
    context: str = ''           # Injected context from prior sessions
    attached_files: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)  # e.g. ["do not modify test files"]


@dataclass(slots=True)
class RequiredArtifacts:
    """Artifacts that MUST be produced by each iteration."""
    prompt_file: bool = True       # Prompt sent to Copilot
    response_file: bool = True     # Response received
    decision_file: bool = True     # Decision log
    validation_result: bool = True  # Validation output
    diff_summary: bool = False     # Git diff summary (optional)

    def check(self, produced: dict[str, bool]) -> list[str]:
        """Return list of missing required artifacts."""
        missing: list[str] = []
        if self.prompt_file and not produced.get('prompt_file'):
            missing.append('prompt_file')
        if self.response_file and not produced.get('response_file'):
            missing.append('response_file')
        if self.decision_file and not produced.get('decision_file'):
            missing.append('decision_file')
        if self.validation_result and not produced.get('validation_result'):
            missing.append('validation_result')
        if self.diff_summary and not produced.get('diff_summary'):
            missing.append('diff_summary')
        return missing


@dataclass(slots=True)
class RecyclePolicy:
    """Policy for when to kill and restart a worker."""
    max_consecutive_errors: int = 3     # Recycle after N consecutive errors
    max_score_floor_iterations: int = 4  # Recycle if below score floor for N iters
    score_floor: int = 20               # Score threshold for floor detection
    max_idle_seconds: int = 300         # Recycle if idle for too long

    def should_recycle(self, health: WorkerHealth) -> tuple[bool, str]:
        """Check if the worker should be recycled."""
        if health.signal == HealthSignal.DEAD:
            return True, f'Worker dead: {health.consecutive_errors} consecutive errors'
        # Score floor check
        if len(health.score_history) >= self.max_score_floor_iterations:
            recent = health.score_history[-self.max_score_floor_iterations:]
            if all(s < self.score_floor for s in recent):
                return True, f'Score below {self.score_floor} for {self.max_score_floor_iterations} iterations'
        # Idle check
        if health.last_activity > 0:
            idle = time.time() - health.last_activity
            if idle > self.max_idle_seconds:
                return True, f'Worker idle for {idle:.0f}s (limit: {self.max_idle_seconds}s)'
        return False, ''


@dataclass(slots=True)
class Worker:
    """A persistent Copilot worker that maintains context across iterations.

    The worker does NOT hold a live session open (VS Code CLI doesn't support it).
    Instead, it maintains a **context window** — a condensed summary of all prior
    iterations — that gets injected into each new VS Code chat session.

    Worker Contract:
      - Task input: receives TaskInput with goal, constraints, context
      - Minimum state: health, context_window, all_changed_files
      - Health signal: HEALTHY / DEGRADED / DEAD
      - Recycle policy: configurable thresholds for kill+restart
      - Required artifacts: prompt, response, decision, validation per iteration
    """
    worker_id: str = ''
    role: str = 'coder'         # coder, reviewer, tester, fixer, docs
    status: WorkerStatus = WorkerStatus.IDLE
    goal: str = ''
    goal_profile: str = 'default'

    # Context window — condensed history for injection into new sessions
    context_window: list[IterationRecord] = field(default_factory=list)
    max_context_records: int = 10  # Keep last N iteration summaries

    # Health
    health: WorkerHealth = field(default_factory=WorkerHealth)

    # Contract components
    recycle_policy: RecyclePolicy = field(default_factory=RecyclePolicy)
    required_artifacts: RequiredArtifacts = field(default_factory=RequiredArtifacts)

    # Tracking
    current_iteration: int = 0
    max_iterations: int = 6
    all_changed_files: list[str] = field(default_factory=list)
    created_at: float = 0.0
    run_id: str = ''

    def __post_init__(self) -> None:
        if not self.worker_id:
            self.worker_id = str(uuid4())[:8]
        if not self.created_at:
            self.created_at = time.time()

    def record_iteration(
        self,
        iteration: int,
        score: int | None,
        decision_code: str,
        summary: str,
        changed_files: list[str] | None = None,
        error: str | None = None,
    ) -> None:
        """Record an iteration result and update health."""
        record = IterationRecord(
            iteration=iteration,
            score=score,
            decision_code=decision_code,
            summary=summary,
            changed_files=changed_files or [],
            errors=[error] if error else [],
            timestamp=time.time(),
        )
        self.context_window.append(record)

        # Trim context window to max size
        if len(self.context_window) > self.max_context_records:
            self.context_window = self.context_window[-self.max_context_records:]

        self.current_iteration = iteration

        # Track all changed files (dedup)
        for f in (changed_files or []):
            if f not in self.all_changed_files:
                self.all_changed_files.append(f)

        # Update health
        if error:
            self.health.record_error(error)
        else:
            self.health.record_success(score)

    def build_context_summary(self) -> str:
        """Build a condensed context string from iteration history.

        This gets injected into new VS Code sessions so Copilot has
        awareness of what happened in prior iterations.
        """
        if not self.context_window:
            return ''

        lines = ['## Prior Iteration Context (Worker Memory)', '']
        for rec in self.context_window:
            status = f'score={rec.score}' if rec.score is not None else 'no score'
            line = f'- **Iteration {rec.iteration}** ({status}, {rec.decision_code}): {rec.summary}'
            if rec.changed_files:
                line += f' [files: {", ".join(rec.changed_files[:5])}]'
            if rec.errors:
                line += f' ⚠ {rec.errors[0][:100]}'
            lines.append(line)

        if self.all_changed_files:
            lines.append('')
            lines.append(f'**All changed files so far**: {", ".join(self.all_changed_files[:20])}')

        lines.append('')
        lines.append(f'**Worker health**: {self.health.total_iterations} iterations, '
                      f'avg score {self.health.avg_score:.0f}, '
                      f'trend: {self.health.score_trend}')

        return '\n'.join(lines)

    def should_stop(self) -> tuple[bool, str]:
        """Check if this worker should stop.

        Returns (should_stop, reason).
        """
        if not self.health.is_healthy:
            return True, f'Worker unhealthy: {self.health.consecutive_errors} consecutive errors'
        if self.current_iteration >= self.max_iterations:
            return True, f'Max iterations reached ({self.max_iterations})'
        if self.health.score_trend == 'declining' and len(self.health.score_history) >= 3:
            recent = self.health.score_history[-3:]
            if all(s < 30 for s in recent):
                return True, 'Score consistently below 30 — likely stuck'
        # Check recycle policy
        should_recycle, recycle_reason = self.recycle_policy.should_recycle(self.health)
        if should_recycle:
            return True, f'Recycle triggered: {recycle_reason}'
        return False, ''

    def check_artifacts(self, produced: dict[str, bool]) -> list[str]:
        """Validate that required artifacts were produced. Returns missing artifact names."""
        return self.required_artifacts.check(produced)

    def to_dict(self) -> dict[str, Any]:
        return {
            'workerId': self.worker_id,
            'role': self.role,
            'status': self.status.value,
            'goal': self.goal,
            'goalProfile': self.goal_profile,
            'currentIteration': self.current_iteration,
            'maxIterations': self.max_iterations,
            'health': {
                'consecutiveErrors': self.health.consecutive_errors,
                'totalErrors': self.health.total_errors,
                'totalIterations': self.health.total_iterations,
                'avgScore': round(self.health.avg_score, 1),
                'scoreTrend': self.health.score_trend,
                'isHealthy': self.health.is_healthy,
                'signal': self.health.signal.value,
            },
            'contextRecords': len(self.context_window),
            'allChangedFiles': self.all_changed_files,
            'runId': self.run_id,
        }


# ---------------------------------------------------------------------------
# Task Queue
# ---------------------------------------------------------------------------

class TaskPriority(int, Enum):
    CRITICAL = 100   # failing tests, broken build
    HIGH = 80        # bugs
    NORMAL = 50      # features
    LOW = 20         # docs, refactor
    BACKGROUND = 10  # cleanup


def _priority_for_profile(profile: str) -> int:
    """Map goal profile to default priority."""
    return {
        'bug': TaskPriority.HIGH,
        'feature': TaskPriority.NORMAL,
        'refactor': TaskPriority.LOW,
        'docs': TaskPriority.LOW,
        'audit': TaskPriority.NORMAL,
        'stabilize': TaskPriority.NORMAL,
    }.get(profile, TaskPriority.NORMAL)


@dataclass(slots=True)
class Task:
    """A unit of work to be assigned to a worker."""
    task_id: str = ''
    goal: str = ''
    goal_profile: str = 'default'
    priority: int = TaskPriority.NORMAL
    source: str = 'manual'      # manual, github_issue, failing_test, backlog
    source_ref: str = ''        # e.g. "issue #42", "test_login.py::test_auth"
    status: str = 'queued'      # queued, assigned, running, complete, failed
    assigned_worker: str = ''
    result: dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    started_at: float = 0.0
    completed_at: float = 0.0

    def __post_init__(self) -> None:
        if not self.task_id:
            self.task_id = str(uuid4())[:8]
        if not self.created_at:
            self.created_at = time.time()
        if self.priority == TaskPriority.NORMAL and self.goal_profile != 'default':
            self.priority = _priority_for_profile(self.goal_profile)


class TaskQueue:
    """Priority queue of tasks waiting to be executed."""

    def __init__(self) -> None:
        self._tasks: list[Task] = []

    def enqueue(self, task: Task) -> None:
        self._tasks.append(task)
        # Sort by priority descending (highest first)
        self._tasks.sort(key=lambda t: -t.priority)

    def dequeue(self) -> Task | None:
        """Remove and return the highest-priority queued task."""
        for i, task in enumerate(self._tasks):
            if task.status == 'queued':
                task.status = 'assigned'
                task.started_at = time.time()
                return task
        return None

    def peek(self) -> Task | None:
        """Return the highest-priority queued task without removing it."""
        for task in self._tasks:
            if task.status == 'queued':
                return task
        return None

    @property
    def pending_count(self) -> int:
        return sum(1 for t in self._tasks if t.status == 'queued')

    @property
    def all_tasks(self) -> list[Task]:
        return list(self._tasks)

    def mark_complete(self, task_id: str, result: dict[str, Any]) -> None:
        for task in self._tasks:
            if task.task_id == task_id:
                task.status = 'complete'
                task.result = result
                task.completed_at = time.time()
                return

    def mark_failed(self, task_id: str, error: str) -> None:
        for task in self._tasks:
            if task.task_id == task_id:
                task.status = 'failed'
                task.result = {'error': error}
                task.completed_at = time.time()
                return

    def to_list(self) -> list[dict[str, Any]]:
        return [
            {
                'taskId': t.task_id,
                'goal': t.goal[:80],
                'profile': t.goal_profile,
                'priority': t.priority,
                'status': t.status,
                'source': t.source,
                'sourceRef': t.source_ref,
                'assignedWorker': t.assigned_worker,
            }
            for t in self._tasks
        ]
