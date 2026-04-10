"""Stop controller — intelligent run termination logic.

Enhances the basic score-threshold stop gate with:
  - No-progress detection: stops after N iterations with no new diff
  - Diff dedup: detects Copilot repeating the same code changes
  - Wall-clock timeout per individual task (not just per session)
  - Hard stop vs soft escalate distinction
"""

from __future__ import annotations

__all__ = [
    'StopSignal',
    'StopControllerConfig',
    'StopController',
    'build_stop_controller_config',
]

import hashlib
import time
from dataclasses import dataclass
from typing import Any

from .logging_config import get_logger

logger = get_logger('stop_controller')


@dataclass(slots=True)
class StopSignal:
    """Result of a stop-controller evaluation."""
    should_stop: bool = False
    is_hard_stop: bool = False  # True = abort immediately; False = escalate to human
    reason: str = ''
    reason_code: str = ''


@dataclass(slots=True)
class StopControllerConfig:
    """Tunable parameters for the stop controller."""
    # No-progress detection
    no_progress_max_iterations: int = 2    # Stop after N iterations with no diff change
    # Diff dedup detection
    diff_dedup_max_repeats: int = 2        # Stop if the same diff hash repeats N times
    # Wall-clock timeout per task (seconds; 0 = disabled)
    task_timeout_seconds: int = 0
    # Score floor — hard stop if score stays below this for N iterations
    score_floor: int = 20
    score_floor_max_iterations: int = 3
    # Escalation mode: 'hard' stops immediately, 'soft' signals human review
    default_escalation: str = 'soft'       # 'hard' | 'soft'


class StopController:
    """Evaluates whether a run should be terminated based on multiple signals."""

    def __init__(self, config: StopControllerConfig | None = None) -> None:
        self.config = config or StopControllerConfig()
        self._diff_hashes: list[str] = []
        self._start_time: float = time.monotonic()
        self._no_progress_count: int = 0
        self._prev_diff_hash: str = ''

    def record_diff(self, diff_text: str) -> None:
        """Record the diff output from current iteration for dedup tracking."""
        h = _hash_diff(diff_text)
        self._diff_hashes.append(h)

        if h == self._prev_diff_hash:
            self._no_progress_count += 1
        else:
            self._no_progress_count = 0
        self._prev_diff_hash = h

    def evaluate(self, iteration: int, history: list[dict[str, Any]]) -> StopSignal:
        """Run all stop checks and return the first triggered signal."""
        # 1. No-progress detection
        signal = self._check_no_progress()
        if signal.should_stop:
            return signal

        # 2. Diff dedup
        signal = self._check_diff_dedup()
        if signal.should_stop:
            return signal

        # 3. Wall-clock timeout
        signal = self._check_task_timeout()
        if signal.should_stop:
            return signal

        # 4. Score floor
        signal = self._check_score_floor(history)
        if signal.should_stop:
            return signal

        return StopSignal()

    def _check_no_progress(self) -> StopSignal:
        """Detect when iterations produce no new changes."""
        if self._no_progress_count >= self.config.no_progress_max_iterations:
            is_hard = self.config.default_escalation == 'hard'
            return StopSignal(
                should_stop=True,
                is_hard_stop=is_hard,
                reason=f'No new changes detected for {self._no_progress_count} consecutive iterations.',
                reason_code='NO_PROGRESS',
            )
        return StopSignal()

    def _check_diff_dedup(self) -> StopSignal:
        """Detect Copilot repeating the same code changes."""
        if len(self._diff_hashes) < 2:
            return StopSignal()

        # Count how many times the latest hash appeared
        latest = self._diff_hashes[-1]
        repeat_count = sum(1 for h in self._diff_hashes if h == latest)

        if repeat_count >= self.config.diff_dedup_max_repeats:
            is_hard = self.config.default_escalation == 'hard'
            return StopSignal(
                should_stop=True,
                is_hard_stop=is_hard,
                reason=f'Same diff repeated {repeat_count} times — Copilot is looping.',
                reason_code='DIFF_DEDUP',
            )
        return StopSignal()

    def _check_task_timeout(self) -> StopSignal:
        """Check wall-clock timeout for the task."""
        if self.config.task_timeout_seconds <= 0:
            return StopSignal()

        elapsed = time.monotonic() - self._start_time
        if elapsed >= self.config.task_timeout_seconds:
            return StopSignal(
                should_stop=True,
                is_hard_stop=False,  # Timeout is always soft — allows resume
                reason=f'Task wall-clock timeout: {elapsed:.0f}s >= {self.config.task_timeout_seconds}s',
                reason_code='TASK_TIMEOUT',
            )
        return StopSignal()

    def _check_score_floor(self, history: list[dict[str, Any]]) -> StopSignal:
        """Hard stop if score stays below floor for too long."""
        if len(history) < self.config.score_floor_max_iterations:
            return StopSignal()

        recent_scores = [
            h.get('score')
            for h in history[-self.config.score_floor_max_iterations:]
            if h.get('score') is not None
        ]
        if len(recent_scores) < self.config.score_floor_max_iterations:
            return StopSignal()

        if all(s < self.config.score_floor for s in recent_scores):
            return StopSignal(
                should_stop=True,
                is_hard_stop=True,
                reason=(
                    f'Score has stayed below {self.config.score_floor} for '
                    f'{self.config.score_floor_max_iterations} iterations: {recent_scores}. '
                    f'Task appears stuck.'
                ),
                reason_code='SCORE_FLOOR_BREACH',
            )
        return StopSignal()


def _hash_diff(diff_text: str) -> str:
    """Create a stable hash of diff output for comparison."""
    # Normalize whitespace and strip timestamps/noise
    normalized = diff_text.strip()
    if not normalized:
        return 'empty'
    return hashlib.sha256(normalized.encode('utf-8', errors='replace')).hexdigest()[:16]


def build_stop_controller_config(operator_config: Any) -> StopControllerConfig:
    """Build StopControllerConfig from OperatorConfig fields."""
    return StopControllerConfig(
        no_progress_max_iterations=getattr(operator_config, 'stop_no_progress_iterations', 2) or 2,
        score_floor=getattr(operator_config, 'stop_score_floor', 20) or 20,
        score_floor_max_iterations=getattr(operator_config, 'stop_score_floor_iterations', 3) or 3,
        task_timeout_seconds=getattr(operator_config, 'max_task_seconds', 0) or 0,
        default_escalation=getattr(operator_config, 'escalation_policy', 'soft') or 'soft',
    )
