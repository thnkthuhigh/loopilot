"""Self-improving meta-learner — analyses failure patterns and auto-generates
prompt guardrails so future runs avoid the same mistakes.

After each run, the meta-learner inspects WHY it failed, extracts a concrete
rule, and persists it. On the next run, those rules are injected into the prompt
automatically.
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
class PromptRule:
    """A learned rule that gets injected into future prompts."""
    rule_id: str = ''
    trigger: str = ''           # pattern that activates the rule
    guardrail: str = ''         # text injected into the prompt
    category: str = ''          # scope_creep, test_regression, lint_drift, loop_trap, file_protection
    source_run: str = ''
    source_iteration: int = 0
    created_at: str = ''
    hit_count: int = 0          # how many times this rule was activated
    success_after_hit: int = 0  # how many times the run succeeded after this rule fired
    confidence: float = 0.5


@dataclass(slots=True)
class MetaAnalysis:
    """Result of analysing a completed run for learnable patterns."""
    new_rules: list[PromptRule] = field(default_factory=list)
    updated_rules: list[str] = field(default_factory=list)   # rule_ids that got confidence bumps
    retired_rules: list[str] = field(default_factory=list)    # rule_ids that lost confidence


# ---------------------------------------------------------------------------
# Rule storage
# ---------------------------------------------------------------------------

_RULES_FILENAME = 'learned-rules.json'


def _rules_path(workspace: Path) -> Path:
    return workspace / '.copilot-operator' / _RULES_FILENAME


def load_rules(workspace: Path) -> list[PromptRule]:
    path = _rules_path(workspace)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        return [PromptRule(**item) for item in data]
    except (OSError, json.JSONDecodeError, TypeError, KeyError):
        return []


def save_rules(workspace: Path, rules: list[PromptRule]) -> None:
    path = _rules_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [
        {
            'rule_id': r.rule_id,
            'trigger': r.trigger,
            'guardrail': r.guardrail,
            'category': r.category,
            'source_run': r.source_run,
            'source_iteration': r.source_iteration,
            'created_at': r.created_at,
            'hit_count': r.hit_count,
            'success_after_hit': r.success_after_hit,
            'confidence': r.confidence,
        }
        for r in rules
    ]
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding='utf-8')


# ---------------------------------------------------------------------------
# Pattern detectors — each returns a rule if the pattern is found
# ---------------------------------------------------------------------------

def _detect_score_regression(history: list[dict[str, Any]], run_id: str) -> PromptRule | None:
    """Detect when Copilot caused a score drop between iterations."""
    for i in range(1, len(history)):
        prev_score = history[i - 1].get('score')
        curr_score = history[i].get('score')
        if prev_score is not None and curr_score is not None and curr_score < prev_score - 10:
            return PromptRule(
                rule_id=f'regression-{run_id[:8]}-i{history[i]["iteration"]}',
                trigger='score_regression',
                guardrail=(
                    'WARNING: In a previous run, Copilot caused a score regression by making '
                    'broad changes. Before making any edit, verify that existing tests still pass. '
                    'If your change breaks something, revert it immediately.'
                ),
                category='test_regression',
                source_run=run_id,
                source_iteration=history[i].get('iteration', 0),
                created_at=datetime.now(timezone.utc).isoformat(),
                confidence=0.7,
            )
    return None


def _detect_validation_loop(history: list[dict[str, Any]], run_id: str) -> PromptRule | None:
    """Detect when the same validation fails repeatedly."""
    fail_streaks: dict[str, int] = {}
    for h in history:
        for v in h.get('validation_after') or []:
            if v.get('status') in ('fail', 'timeout'):
                name = v.get('name', '')
                fail_streaks[name] = fail_streaks.get(name, 0) + 1

    for name, count in fail_streaks.items():
        if count >= 2:
            return PromptRule(
                rule_id=f'val-loop-{name}-{run_id[:8]}',
                trigger=f'validation_fail_{name}',
                guardrail=(
                    f'CRITICAL: The [{name}] validation has failed in prior runs repeatedly. '
                    f'Before doing any feature work, read the [{name}] output carefully, '
                    f'trace the failure to its root cause, and fix it FIRST.'
                ),
                category='lint_drift',
                source_run=run_id,
                created_at=datetime.now(timezone.utc).isoformat(),
                confidence=0.8,
            )
    return None


def _detect_scope_creep(history: list[dict[str, Any]], run_id: str) -> PromptRule | None:
    """Detect when Copilot drifts from the task (summary changes topic)."""
    if len(history) < 3:
        return None
    summaries = [h.get('summary', '').lower() for h in history if h.get('summary')]
    if len(summaries) < 3:
        return None
    # Simple heuristic: if the last summary shares no significant words with the first
    first_words = set(summaries[0].split()) - {'the', 'a', 'an', 'and', 'or', 'in', 'to', 'of', 'is', 'was'}
    last_words = set(summaries[-1].split()) - {'the', 'a', 'an', 'and', 'or', 'in', 'to', 'of', 'is', 'was'}
    overlap = first_words & last_words
    if len(overlap) == 0 and len(first_words) > 3:
        return PromptRule(
            rule_id=f'scope-creep-{run_id[:8]}',
            trigger='scope_creep',
            guardrail=(
                'WARNING: In a previous run, Copilot drifted away from the original task '
                'and started working on unrelated things. Stay STRICTLY on the current task. '
                'Do NOT add features, refactor unrelated code, or change things not asked for.'
            ),
            category='scope_creep',
            source_run=run_id,
            created_at=datetime.now(timezone.utc).isoformat(),
            confidence=0.6,
        )
    return None


def _detect_blocker_pattern(history: list[dict[str, Any]], run_id: str) -> PromptRule | None:
    """Detect recurring blockers and generate avoidance rules."""
    all_blockers: dict[str, int] = {}
    for h in history:
        for b in h.get('blockers') or []:
            item = b.get('item', '')
            if item:
                all_blockers[item] = all_blockers.get(item, 0) + 1

    for blocker, count in all_blockers.items():
        if count >= 2:
            return PromptRule(
                rule_id=f'blocker-{run_id[:8]}',
                trigger='recurring_blocker',
                guardrail=(
                    f'WARNING: A recurring blocker was detected in prior runs: "{blocker}". '
                    f'This has appeared {count} times. Address this issue proactively before '
                    f'it blocks your progress again.'
                ),
                category='loop_trap',
                source_run=run_id,
                created_at=datetime.now(timezone.utc).isoformat(),
                confidence=0.7,
            )
    return None


_DETECTORS = [
    _detect_score_regression,
    _detect_validation_loop,
    _detect_scope_creep,
    _detect_blocker_pattern,
]


# ---------------------------------------------------------------------------
# Meta-analysis: run after each completed run
# ---------------------------------------------------------------------------

def analyse_run_for_rules(
    history: list[dict[str, Any]],
    run_id: str,
    run_status: str,
    existing_rules: list[PromptRule],
) -> MetaAnalysis:
    """Analyse a completed run and extract new rules / update existing ones."""
    analysis = MetaAnalysis()
    existing_triggers = {r.trigger for r in existing_rules}

    # Run detectors
    for detector in _DETECTORS:
        rule = detector(history, run_id)
        if rule and rule.trigger not in existing_triggers:
            analysis.new_rules.append(rule)

    # Update confidence on existing rules
    for rule in existing_rules:
        if run_status == 'complete':
            if rule.hit_count > 0:
                rule.success_after_hit += 1
                rule.confidence = min(1.0, rule.confidence + 0.1)
                analysis.updated_rules.append(rule.rule_id)
        elif run_status == 'blocked':
            # If the rule was supposed to help but didn't
            if rule.hit_count > 2 and rule.success_after_hit == 0:
                rule.confidence = max(0.0, rule.confidence - 0.2)
                if rule.confidence < 0.2:
                    analysis.retired_rules.append(rule.rule_id)

    return analysis


def apply_meta_learning(
    workspace: Path,
    history: list[dict[str, Any]],
    run_id: str,
    run_status: str,
) -> MetaAnalysis:
    """Full meta-learning cycle: load rules, analyse, save updated rules."""
    rules = load_rules(workspace)
    analysis = analyse_run_for_rules(history, run_id, run_status, rules)

    # Add new rules
    for new_rule in analysis.new_rules:
        rules.append(new_rule)

    # Remove retired rules
    if analysis.retired_rules:
        rules = [r for r in rules if r.rule_id not in set(analysis.retired_rules)]

    save_rules(workspace, rules)
    return analysis


# ---------------------------------------------------------------------------
# Rule activation: select and render rules for a prompt
# ---------------------------------------------------------------------------

def activate_rules(
    workspace: Path,
    history: list[dict[str, Any]],
    goal: str,
    goal_profile: str,
) -> list[PromptRule]:
    """Select rules that should fire for the current run context."""
    rules = load_rules(workspace)
    activated: list[PromptRule] = []

    for rule in rules:
        if rule.confidence < 0.3:
            continue
        # Always-on rules: high confidence
        if rule.confidence >= 0.6:
            rule.hit_count += 1
            activated.append(rule)
            continue
        # Context-sensitive: only fire if relevant to current profile
        if rule.category == 'test_regression' and goal_profile in ('refactor', 'feature'):
            rule.hit_count += 1
            activated.append(rule)
        elif rule.category == 'scope_creep' and goal_profile in ('bug', 'refactor'):
            rule.hit_count += 1
            activated.append(rule)
        elif rule.category == 'lint_drift':
            rule.hit_count += 1
            activated.append(rule)

    # Persist updated hit counts
    if activated:
        save_rules(workspace, rules)

    return activated


def render_guardrails(rules: list[PromptRule]) -> str:
    """Render activated rules into text for prompt injection."""
    if not rules:
        return ''
    lines = ['Learned guardrails from previous runs (auto-generated — do NOT ignore):']
    for rule in rules[:5]:  # Cap at 5 to avoid prompt bloat
        lines.append(f'- [{rule.category}] {rule.guardrail}')
    return '\n'.join(lines)
