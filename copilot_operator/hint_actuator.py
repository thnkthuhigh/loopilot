"""Hint Actuator — execute reasoning engine recommendations.

The reasoning engine (reasoning.py) produces ``StrategyHint`` objects with
concrete actions like ``escalate_profile`` and ``narrow_scope``, but until now
these were injected as TEXT only — advice that the model may or may not follow.

This module closes the gap: it *executes* the hints by mutating the operator
config or runtime state so the next iteration actually behaves differently.

Supported actions:
  - ``escalate_profile``  → change goal_profile to suggested_profile
  - ``narrow_scope``      → reduce max_files_changed, mark low-priority tasks deferred
  - ``increase_iterations`` → bump max_iterations if room remains
  - ``switch_baton``      → inject suggested_prompt as the baton override
  - ``abort``             → set runtime abort flag
"""

from __future__ import annotations

__all__ = [
    'ActuationResult',
    'actuate_hints',
]

from dataclasses import dataclass, field
from typing import Any

from .logging_config import get_logger
from .reasoning import StrategyHint

logger = get_logger(__name__)

# Profiles ordered by aggressiveness — used for validation
_VALID_PROFILES = {'default', 'bug', 'feature', 'refactor', 'audit', 'docs'}


@dataclass(slots=True)
class ActuationResult:
    """What the actuator did in response to hints."""
    actions_taken: list[str] = field(default_factory=list)
    config_changes: dict[str, Any] = field(default_factory=dict)
    runtime_changes: dict[str, Any] = field(default_factory=dict)
    aborted: bool = False


def actuate_hints(
    hints: list[StrategyHint],
    config: Any,
    runtime: dict[str, Any],
    iteration: int,
    max_iterations: int,
) -> ActuationResult:
    """Execute strategy hints by mutating config/runtime.

    Each hint is processed in order.  Only hints with concrete actions
    (not ``'none'``) are executed.

    Returns an ``ActuationResult`` summarising what changed.
    """
    result = ActuationResult()

    for hint in hints:
        if hint.action == 'none' or not hint.action:
            continue

        if hint.action == 'escalate_profile':
            _actuate_escalate_profile(hint, config, runtime, result)

        elif hint.action == 'narrow_scope':
            _actuate_narrow_scope(config, runtime, result)

        elif hint.action == 'increase_iterations':
            _actuate_increase_iterations(config, iteration, max_iterations, result)

        elif hint.action == 'switch_baton':
            _actuate_switch_baton(hint, runtime, result)

        elif hint.action == 'abort':
            _actuate_abort(hint, runtime, result)

        else:
            logger.debug('Unknown hint action: %s', hint.action)

    if result.actions_taken:
        logger.info('Hint actuator executed: %s', result.actions_taken)

    return result


# --- Individual actuators ---


def _actuate_escalate_profile(
    hint: StrategyHint,
    config: Any,
    runtime: dict[str, Any],
    result: ActuationResult,
) -> None:
    """Switch goal_profile to suggested_profile."""
    new_profile = hint.suggested_profile
    if not new_profile or new_profile not in _VALID_PROFILES:
        return
    old_profile = config.goal_profile
    if new_profile == old_profile:
        return

    config.goal_profile = new_profile
    runtime['goalProfile'] = new_profile
    runtime.setdefault('_profile_history', []).append(old_profile)

    result.actions_taken.append(f'escalate_profile: {old_profile} → {new_profile}')
    result.config_changes['goal_profile'] = {'old': old_profile, 'new': new_profile}


def _actuate_narrow_scope(
    config: Any,
    runtime: dict[str, Any],
    result: ActuationResult,
) -> None:
    """Reduce scope to prevent spreading too thin."""
    old_max = getattr(config, 'expect_max_files_changed', 20)
    new_max = max(3, old_max // 2)

    if new_max < old_max:
        config.expect_max_files_changed = new_max
        result.actions_taken.append(f'narrow_scope: max_files {old_max} → {new_max}')
        result.config_changes['expect_max_files_changed'] = {'old': old_max, 'new': new_max}

    # Mark runtime flag so prompt builder can see it
    runtime['_scope_narrowed'] = True


def _actuate_increase_iterations(
    config: Any,
    iteration: int,
    max_iterations: int,
    result: ActuationResult,
) -> None:
    """Bump max_iterations if we're close to the limit."""
    remaining = max_iterations - iteration
    if remaining <= 1 and max_iterations < 12:  # hard cap
        new_max = min(max_iterations + 2, 12)
        old_max = config.max_iterations
        config.max_iterations = new_max
        result.actions_taken.append(f'increase_iterations: {old_max} → {new_max}')
        result.config_changes['max_iterations'] = {'old': old_max, 'new': new_max}


def _actuate_switch_baton(
    hint: StrategyHint,
    runtime: dict[str, Any],
    result: ActuationResult,
) -> None:
    """Override the next baton with the hint's suggested prompt."""
    if hint.suggested_prompt:
        runtime['_baton_override'] = hint.suggested_prompt
        result.actions_taken.append('switch_baton: injected suggested_prompt')
        result.runtime_changes['baton_override'] = True


def _actuate_abort(
    hint: StrategyHint,
    runtime: dict[str, Any],
    result: ActuationResult,
) -> None:
    """Signal that the operator should abort."""
    runtime['_abort_requested'] = True
    runtime['_abort_reason'] = hint.reason or 'Reasoning engine recommended abort'
    result.aborted = True
    result.actions_taken.append(f'abort: {hint.reason}')
