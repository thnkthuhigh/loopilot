"""Adaptive Strategy Engine — mid-run learning and strategy rotation.

The reasoning engine (reasoning.py) detects loops and suggests strategies,
but those are TEXT hints only — they don't actually modify behaviour.

This module closes the loop:
  1. Tracks which strategies have been tried this run
  2. Generates mid-run guardrails from detected patterns
  3. Rotates strategies when one fails (escalation chain)
  4. Forces specific actions when loops are detected

Strategy escalation chain:
  default → narrow_scope → switch_approach → escalate_profile → abort
"""

from __future__ import annotations

__all__ = [
    'StrategyState',
    'RunFeedback',
    'AdaptiveEngine',
]

from dataclasses import dataclass, field
from typing import Any

from .logging_config import get_logger

logger = get_logger(__name__)


@dataclass(slots=True)
class RunFeedback:
    """What happened in the last iteration — used to adapt strategy."""
    iteration: int = 0
    score_before: int = 0
    score_after: int = 0
    had_blockers: bool = False
    validation_passed: bool = True
    files_changed: int = 0
    loop_detected: bool = False
    loop_kind: str = ''


@dataclass(slots=True)
class StrategyState:
    """Tracks the adaptive strategy across iterations."""
    current_strategy: str = 'default'
    strategies_tried: list[str] = field(default_factory=list)
    escalation_level: int = 0       # 0=default, 1=narrow, 2=switch, 3=escalate, 4=abort
    consecutive_no_progress: int = 0
    consecutive_loops: int = 0
    mid_run_guardrails: list[str] = field(default_factory=list)
    score_history: list[int] = field(default_factory=list)
    effective_strategies: list[str] = field(default_factory=list)
    failed_strategies: list[str] = field(default_factory=list)


# Escalation chain — each level more aggressive than the last
_ESCALATION_CHAIN = [
    'default',
    'narrow_scope',
    'switch_approach',
    'escalate_profile',
    'abort',
]

# Goal-type → recommended approaches when stuck
_GOAL_STRATEGIES: dict[str, list[str]] = {
    'bug': ['isolate_root_cause', 'add_debug_logging', 'bisect_changes', 'minimal_repro'],
    'feature': ['skeleton_first', 'test_driven', 'incremental_build', 'decompose_smaller'],
    'refactor': ['extract_pattern', 'inline_first', 'test_before_refactor', 'one_change_at_a_time'],
    'test': ['test_happy_path_first', 'edge_cases_after', 'mock_boundaries', 'assert_contracts'],
    'generic': ['start_simple', 'test_early', 'one_file_at_a_time', 'validate_often'],
}


class AdaptiveEngine:
    """Mid-run learning engine that adapts strategy based on feedback.

    Usage:
      engine = AdaptiveEngine(goal_type='bug')
      # After each iteration:
      engine.record_feedback(RunFeedback(...))
      directives = engine.get_directives()  # inject into prompt
    """

    def __init__(self, goal_type: str = 'generic') -> None:
        self.state = StrategyState()
        self.goal_type = goal_type

    def record_feedback(self, feedback: RunFeedback) -> list[str]:
        """Process iteration feedback and adapt strategy.

        Returns list of changes made (for logging).
        """
        changes: list[str] = []
        self.state.score_history.append(feedback.score_after)

        # Track progress
        if feedback.score_after <= feedback.score_before and feedback.iteration > 1:
            self.state.consecutive_no_progress += 1
        else:
            self.state.consecutive_no_progress = 0
            if self.state.current_strategy not in self.state.effective_strategies:
                self.state.effective_strategies.append(self.state.current_strategy)

        # Track loops
        if feedback.loop_detected:
            self.state.consecutive_loops += 1
        else:
            self.state.consecutive_loops = 0

        # --- Escalation Logic ---

        # Rule 1: 2 consecutive loops → escalate
        if self.state.consecutive_loops >= 2:
            old = self.state.current_strategy
            self._escalate()
            changes.append(f'Strategy escalated: {old} → {self.state.current_strategy} (2 loops)')

        # Rule 2: 3 iterations no progress → escalate
        elif self.state.consecutive_no_progress >= 3:
            old = self.state.current_strategy
            self._escalate()
            changes.append(f'Strategy escalated: {old} → {self.state.current_strategy} (no progress)')

        # Rule 3: Score dropped significantly → generate mid-run guardrails
        if feedback.score_after < feedback.score_before - 10:
            guardrail = (
                f'Score dropped from {feedback.score_before} to {feedback.score_after}. '
                f'Do NOT repeat what was just tried. Try a completely different approach.'
            )
            if guardrail not in self.state.mid_run_guardrails:
                self.state.mid_run_guardrails.append(guardrail)
                changes.append('Added guardrail: score drop detected')

        # Rule 4: Validation failing repeatedly → add targeted guardrail
        if not feedback.validation_passed and feedback.iteration >= 2:
            guardrail = 'Validation is failing. Fix existing test failures BEFORE adding new code.'
            if guardrail not in self.state.mid_run_guardrails:
                self.state.mid_run_guardrails.append(guardrail)
                changes.append('Added guardrail: persistent validation failure')

        # Rule 5: No files changed → something is wrong
        if feedback.files_changed == 0 and feedback.iteration >= 2:
            guardrail = 'No files were changed last iteration. Make sure to actually edit code, not just discuss.'
            if guardrail not in self.state.mid_run_guardrails:
                self.state.mid_run_guardrails.append(guardrail)
                changes.append('Added guardrail: no files changed')

        if changes:
            logger.info('Adaptive strategy changes: %s', changes)

        return changes

    def _escalate(self) -> None:
        """Move to the next strategy in the escalation chain."""
        if self.state.current_strategy not in self.state.failed_strategies:
            self.state.failed_strategies.append(self.state.current_strategy)

        self.state.escalation_level = min(
            self.state.escalation_level + 1,
            len(_ESCALATION_CHAIN) - 1,
        )
        self.state.current_strategy = _ESCALATION_CHAIN[self.state.escalation_level]
        if self.state.current_strategy not in self.state.strategies_tried:
            self.state.strategies_tried.append(self.state.current_strategy)

        # Reset consecutive counters on escalation
        self.state.consecutive_no_progress = 0
        self.state.consecutive_loops = 0

    def get_directives(self) -> str:
        """Render adaptive strategy directives for prompt injection.

        Returns empty string if no special directives needed.
        """
        if (self.state.escalation_level == 0
                and not self.state.mid_run_guardrails):
            return ''

        lines: list[str] = []
        lines.append('## 🧠 Adaptive Strategy')

        # Current strategy
        strategy = self.state.current_strategy
        if strategy != 'default':
            strategy_desc = {
                'narrow_scope': 'Focus on the SMALLEST possible change. Do not modify more than 1-2 files.',
                'switch_approach': 'Previous approach failed. Try a COMPLETELY DIFFERENT method.',
                'escalate_profile': 'Switching to more aggressive profile. Be thorough and verify every change.',
                'abort': 'Multiple strategies failed. Make the MINIMUM viable fix and stop.',
            }
            lines.append(f'**Strategy: {strategy}**')
            if strategy in strategy_desc:
                lines.append(f'  _{strategy_desc[strategy]}_')

        # Goal-specific recommendations
        if self.state.escalation_level >= 1:
            suggestions = _GOAL_STRATEGIES.get(self.goal_type, _GOAL_STRATEGIES['generic'])
            # Skip already-tried approaches
            remaining = [s for s in suggestions if s not in self.state.failed_strategies]
            if remaining:
                lines.append(f'**Suggested approaches:** {", ".join(remaining[:3])}')

        # Failed strategies warning
        if self.state.failed_strategies:
            lines.append(f'**Already tried (do NOT repeat):** {", ".join(self.state.failed_strategies)}')

        # Mid-run guardrails
        if self.state.mid_run_guardrails:
            lines.append('**Guardrails (learned this run):**')
            for g in self.state.mid_run_guardrails[-5:]:
                lines.append(f'  ⚠ {g}')

        return '\n'.join(lines)

    def should_abort(self) -> bool:
        """Return True if the adaptive engine recommends aborting."""
        return self.state.current_strategy == 'abort'

    def get_state_summary(self) -> dict[str, Any]:
        """Return a summary of adaptive state for logging."""
        return {
            'strategy': self.state.current_strategy,
            'escalation_level': self.state.escalation_level,
            'no_progress_count': self.state.consecutive_no_progress,
            'loop_count': self.state.consecutive_loops,
            'guardrails_count': len(self.state.mid_run_guardrails),
            'tried': self.state.strategies_tried,
            'effective': self.state.effective_strategies,
            'failed': self.state.failed_strategies,
        }
