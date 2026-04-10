"""Policy engine — approval gates for dangerous actions.

Provides a configurable policy layer that can:
  - Block dangerous operations (large PRs, mass file deletes, etc.)
  - Require human approval before proceeding
  - Log all policy decisions for audit trail
  - Support custom policy rules via configuration
"""

from __future__ import annotations

__all__ = [
    'PolicyVerdict',
    'PolicyRule',
    'PolicyDecision',
    'PolicyEngine',
    'BUILTIN_RULES',
]

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .logging_config import get_logger

logger = get_logger('policy')


class PolicyVerdict:
    ALLOW = 'allow'
    BLOCK = 'block'
    REQUIRE_APPROVAL = 'require_approval'
    WARN = 'warn'


@dataclass(slots=True)
class PolicyRule:
    """A single policy rule that evaluates conditions and produces a verdict."""
    name: str
    description: str = ''
    condition: str = ''       # Rule type identifier
    threshold: int | float = 0
    verdict: str = PolicyVerdict.WARN
    enabled: bool = True


@dataclass(slots=True)
class PolicyDecision:
    """Result of evaluating all policies for an action."""
    action: str = ''
    verdict: str = PolicyVerdict.ALLOW
    triggered_rules: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)
    timestamp: float = 0.0

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = time.time()


# ---------------------------------------------------------------------------
# Built-in policy rules
# ---------------------------------------------------------------------------

BUILTIN_RULES: list[PolicyRule] = [
    PolicyRule(
        name='large_diff',
        description='Block commits with more than N changed files',
        condition='changed_files_count',
        threshold=50,
        verdict=PolicyVerdict.REQUIRE_APPROVAL,
    ),
    PolicyRule(
        name='mass_delete',
        description='Block deletion of more than N files in one iteration',
        condition='deleted_files_count',
        threshold=10,
        verdict=PolicyVerdict.BLOCK,
    ),
    PolicyRule(
        name='high_cost',
        description='Warn when LLM cost exceeds threshold',
        condition='llm_cost_usd',
        threshold=1.0,
        verdict=PolicyVerdict.WARN,
    ),
    PolicyRule(
        name='low_score_pr',
        description='Block PR creation when final score is below threshold',
        condition='score_below',
        threshold=70,
        verdict=PolicyVerdict.BLOCK,
    ),
    PolicyRule(
        name='too_many_iterations',
        description='Warn when a run uses excessive iterations',
        condition='iteration_count',
        threshold=10,
        verdict=PolicyVerdict.WARN,
    ),
]


class PolicyEngine:
    """Evaluates policy rules against operator actions."""

    def __init__(
        self,
        custom_rules: list[PolicyRule] | None = None,
        audit_file: Path | None = None,
    ) -> None:
        self._rules = list(BUILTIN_RULES)
        if custom_rules:
            self._rules.extend(custom_rules)
        self._audit_file = audit_file or Path('.copilot-operator/policy-audit.jsonl')
        self._decisions: list[PolicyDecision] = []

    @property
    def rules(self) -> list[PolicyRule]:
        return [r for r in self._rules if r.enabled]

    def evaluate(self, action: str, context: dict[str, Any]) -> PolicyDecision:
        """Evaluate all enabled rules against the given context."""
        decision = PolicyDecision(action=action)
        worst_verdict = PolicyVerdict.ALLOW

        for rule in self.rules:
            triggered = self._check_rule(rule, context)
            if triggered:
                decision.triggered_rules.append(rule.name)
                decision.details[rule.name] = {
                    'description': rule.description,
                    'verdict': rule.verdict,
                    'threshold': rule.threshold,
                    'actual': context.get(rule.condition, 'N/A'),
                }
                # Escalate verdict: block > require_approval > warn > allow
                if self._severity(rule.verdict) > self._severity(worst_verdict):
                    worst_verdict = rule.verdict

        decision.verdict = worst_verdict
        self._decisions.append(decision)
        self._audit_log(decision)

        if decision.triggered_rules:
            logger.info('Policy evaluation for %s: %s (rules: %s)',
                        action, decision.verdict, ', '.join(decision.triggered_rules))

        return decision

    def _check_rule(self, rule: PolicyRule, context: dict[str, Any]) -> bool:
        """Check if a rule triggers in the given context."""
        value = context.get(rule.condition)
        if value is None:
            return False
        try:
            if rule.condition == 'score_below':
                return float(value) < float(rule.threshold)
            return float(value) > float(rule.threshold)
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _severity(verdict: str) -> int:
        return {
            PolicyVerdict.ALLOW: 0,
            PolicyVerdict.WARN: 1,
            PolicyVerdict.REQUIRE_APPROVAL: 2,
            PolicyVerdict.BLOCK: 3,
        }.get(verdict, 0)

    def _audit_log(self, decision: PolicyDecision) -> None:
        """Append decision to audit trail."""
        try:
            self._audit_file.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                'action': decision.action,
                'verdict': decision.verdict,
                'rules': decision.triggered_rules,
                'timestamp': decision.timestamp,
            }
            with self._audit_file.open('a', encoding='utf-8') as f:
                f.write(json.dumps(entry, ensure_ascii=False) + '\n')
        except OSError:
            pass  # Audit logging is best-effort

    def get_audit_trail(self, limit: int = 50) -> list[dict[str, Any]]:
        """Read recent audit trail entries."""
        if not self._audit_file.exists():
            return []
        entries: list[dict[str, Any]] = []
        try:
            for line in self._audit_file.read_text(encoding='utf-8').splitlines():
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
        except (json.JSONDecodeError, OSError):
            pass
        return entries[-limit:]

    def render_rules(self) -> str:
        """Render all rules as text."""
        lines = ['Policy Rules:']
        for rule in self._rules:
            status = '✓' if rule.enabled else '✗'
            lines.append(f'  {status} {rule.name}: {rule.description} '
                         f'(condition={rule.condition}, threshold={rule.threshold}, verdict={rule.verdict})')
        return '\n'.join(lines)


def load_policy_rules_from_config(config_data: dict[str, Any]) -> list[PolicyRule]:
    """Load custom policy rules from operator config."""
    rules: list[PolicyRule] = []
    for item in config_data.get('policyRules', []):
        rules.append(PolicyRule(
            name=str(item.get('name', 'custom')),
            description=str(item.get('description', '')),
            condition=str(item.get('condition', '')),
            threshold=item.get('threshold', 0),
            verdict=str(item.get('verdict', PolicyVerdict.WARN)),
            enabled=bool(item.get('enabled', True)),
        ))
    return rules
