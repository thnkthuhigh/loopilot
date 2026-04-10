"""Intention-Aware Prompting — pre-emptive guardrails per goal type.

Instead of reacting to failures *after* they happen (meta_learner.py),
this module applies guardrails *before* the first iteration even starts,
based on the classified goal type and a corpus of known Copilot weaknesses.

Each goal type (bug, feature, refactor, audit, docs, stabilize) carries
a set of pre-learnt guardrails that prime the LLM to avoid common pitfalls.
"""

from __future__ import annotations

__all__ = [
    'IntentionProfile',
    'get_intention_profile',
    'all_goal_types',
    'render_combined_guardrails',
    'learn_guardrails_from_history',
]

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class IntentionProfile:
    """Collection of guardrails and hints for a specific goal type."""
    goal_type: str = 'generic'
    guardrails: list[str] = field(default_factory=list)
    focus_areas: list[str] = field(default_factory=list)
    anti_patterns: list[str] = field(default_factory=list)
    recommended_steps: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Built-in corpus — accumulated knowledge of Copilot behaviour
# ---------------------------------------------------------------------------

_PROFILES: dict[str, IntentionProfile] = {
    'bug': IntentionProfile(
        goal_type='bug',
        guardrails=[
            'Reproduce the bug before attempting a fix.',
            'Do NOT refactor surrounding code — keep the diff minimal.',
            'Write a regression test that fails without the fix.',
            'If the root cause is unclear after 2 iterations, stop and report.',
        ],
        focus_areas=['root cause analysis', 'regression test', 'minimal change'],
        anti_patterns=[
            'Rewriting the entire module instead of fixing the bug.',
            'Suppressing errors instead of fixing the underlying logic.',
            'Adding broad try/except blocks to hide the bug.',
        ],
        recommended_steps=[
            '1. Read the error/stack trace carefully.',
            '2. Locate the exact source of the bug.',
            '3. Write a failing test.',
            '4. Apply the minimal fix.',
            '5. Verify the test passes and no others break.',
        ],
    ),
    'feature': IntentionProfile(
        goal_type='feature',
        guardrails=[
            'Follow existing code patterns and conventions.',
            'Do NOT touch files unrelated to the feature.',
            'Write tests alongside the implementation, not after.',
            'If the feature scope grows beyond the original description, pause and report.',
        ],
        focus_areas=['incremental delivery', 'test coverage', 'convention compliance'],
        anti_patterns=[
            'Adding unnecessary abstractions for a simple feature.',
            'Changing shared utilities without considering other callers.',
            'Implementing more than what was specified.',
        ],
        recommended_steps=[
            '1. Understand the feature specification fully.',
            '2. Identify which files need changes.',
            '3. Implement incrementally — smallest working slice first.',
            '4. Add tests for each new public function/method.',
            '5. Verify no regressions.',
        ],
    ),
    'refactor': IntentionProfile(
        goal_type='refactor',
        guardrails=[
            'Behaviour must remain identical — no functional changes.',
            'Run tests after EVERY structural change.',
            'Commit in small, logical chunks.',
            'If you break more than 3 tests in one change, rollback and split smaller.',
        ],
        focus_areas=['behaviour preservation', 'incremental commits', 'test integrity'],
        anti_patterns=[
            'Introducing new features disguised as refactoring.',
            'Changing public APIs without updating all callers.',
            'Large-scale renames without verifying all references.',
        ],
        recommended_steps=[
            '1. Ensure all existing tests pass first.',
            '2. Make one structural change at a time.',
            '3. Run tests after each change.',
            '4. Verify no functional differences.',
        ],
    ),
    'audit': IntentionProfile(
        goal_type='audit',
        guardrails=[
            'Only report — do NOT fix issues during an audit pass.',
            'Classify severity: critical / high / medium / low.',
            'Cite exact file paths and line numbers for every finding.',
            'If you find more than 20 issues, summarise by category instead.',
        ],
        focus_areas=['completeness', 'severity classification', 'actionable findings'],
        anti_patterns=[
            'Fixing issues while auditing (changes the audit scope).',
            'Reporting vague findings without file/line references.',
            'Missing entire subsystems in the audit.',
        ],
        recommended_steps=[
            '1. List all files/modules in scope.',
            '2. Scan each systematically.',
            '3. Record findings with file, line, severity, description.',
            '4. Summarise totals by severity.',
        ],
    ),
    'docs': IntentionProfile(
        goal_type='docs',
        guardrails=[
            'Do NOT change source code — only documentation files.',
            'Keep examples accurate and up to date with current API.',
            'Use the same style/format as existing docs.',
            'Verify any code examples actually compile/run.',
        ],
        focus_areas=['accuracy', 'completeness', 'style consistency'],
        anti_patterns=[
            'Documenting internal implementation details that may change.',
            'Writing docs for features that do not exist yet.',
            'Inconsistent formatting or heading levels.',
        ],
        recommended_steps=[
            '1. Identify which docs need updating.',
            '2. Read the current docs for style reference.',
            '3. Write/update documentation.',
            '4. Verify all code examples.',
        ],
    ),
    'stabilize': IntentionProfile(
        goal_type='stabilize',
        guardrails=[
            'Focus on failing tests — do NOT add new features.',
            'Fix the easiest failures first for quick wins.',
            'If a test is flaky, mark it as such — do not delete it.',
            'Aim for 100% pass rate, not 100% coverage.',
        ],
        focus_areas=['test pass rate', 'flaky test detection', 'CI reliability'],
        anti_patterns=[
            'Deleting failing tests instead of fixing them.',
            'Disabling linting rules to pass CI.',
            'Making tests pass by weakening assertions.',
        ],
        recommended_steps=[
            '1. Run the full test suite and record failures.',
            '2. Triage by severity (crash > wrong result > flaky).',
            '3. Fix one test at a time, verify others still pass.',
            '4. Re-run full suite after all fixes.',
        ],
    ),
}

# Generic fallback
_GENERIC_PROFILE = IntentionProfile(
    goal_type='generic',
    guardrails=[
        'Understand the goal fully before coding.',
        'Run existing tests before making changes.',
        'Keep changes minimal and focused.',
        'If stuck for 2 iterations, pause and report.',
    ],
    focus_areas=['clarity', 'minimalism', 'test safety'],
    anti_patterns=[
        'Making sweeping changes without understanding the codebase.',
        'Ignoring existing test failures.',
    ],
    recommended_steps=[
        '1. Read relevant source files.',
        '2. Plan the change.',
        '3. Implement.',
        '4. Test.',
    ],
)


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------

def get_intention_profile(goal_type: str) -> IntentionProfile:
    """Get the intention profile for a goal type (falls back to generic)."""
    return _PROFILES.get(goal_type, _GENERIC_PROFILE)


def all_goal_types() -> list[str]:
    """Return all known goal types."""
    return list(_PROFILES.keys())


# ---------------------------------------------------------------------------
# Rendering for prompt injection
# ---------------------------------------------------------------------------

def render_intention_guardrails(goal_type: str) -> str:
    """Render guardrails as a prompt section for a given goal type."""
    profile = get_intention_profile(goal_type)

    lines = [f'## Intention-Aware Guardrails ({profile.goal_type})', '']

    lines.append('**Focus Areas:** ' + ', '.join(profile.focus_areas))
    lines.append('')

    lines.append('**Guardrails (MUST follow):**')
    for g in profile.guardrails:
        lines.append(f'- {g}')
    lines.append('')

    lines.append('**Anti-Patterns (MUST avoid):**')
    for ap in profile.anti_patterns:
        lines.append(f'- ❌ {ap}')
    lines.append('')

    lines.append('**Recommended Steps:**')
    for step in profile.recommended_steps:
        lines.append(f'  {step}')

    return '\n'.join(lines)


def render_combined_guardrails(
    goal_type: str,
    extra_guardrails: list[str] | None = None,
) -> str:
    """Render intention guardrails plus any extra dynamic guardrails."""
    base = render_intention_guardrails(goal_type)
    if not extra_guardrails:
        return base

    lines = [base, '', '**Additional Dynamic Guardrails:**']
    for g in extra_guardrails:
        lines.append(f'- ⚡ {g}')
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Adaptive guardrails — learn from run history
# ---------------------------------------------------------------------------

def learn_guardrails_from_history(
    goal_type: str,
    history: list[dict],
) -> list[str]:
    """Extract additional guardrails from run history patterns.

    Analyses what went wrong in previous iterations and generates
    guardrails specific to this run, complementing the static profiles.
    """
    learned: list[str] = []
    if not history or len(history) < 2:
        return learned

    # Detect repeated validation failures
    val_fails: dict[str, int] = {}
    for h in history:
        for v in h.get('validation_after') or []:
            if v.get('status') in ('fail', 'timeout'):
                name = v.get('name', 'unknown')
                val_fails[name] = val_fails.get(name, 0) + 1
    for name, count in val_fails.items():
        if count >= 2:
            learned.append(
                f'The [{name}] check has failed {count} times in this run. '
                f'Fix it BEFORE making any other changes.'
            )

    # Detect score regression
    scores = [h.get('score') for h in history if h.get('score') is not None]
    if len(scores) >= 2:
        for i in range(1, len(scores)):
            if scores[i] < scores[i - 1] - 5:
                learned.append(
                    'Score regressed in a previous iteration. '
                    'Make smaller, safer changes and verify tests after each one.'
                )
                break

    # Detect scope creep (summary topic drift)
    summaries = [h.get('summary', '') for h in history if h.get('summary')]
    if len(summaries) >= 3:
        first_words = set(summaries[0].lower().split()[:10])
        last_words = set(summaries[-1].lower().split()[:10])
        stop_words = {'the', 'a', 'an', 'and', 'or', 'in', 'to', 'of', 'is', 'was', 'for', 'on', 'with'}
        meaningful_first = first_words - stop_words
        meaningful_last = last_words - stop_words
        if meaningful_first and not (meaningful_first & meaningful_last):
            learned.append(
                'The work appears to have drifted from the original task. '
                'Stay focused on the stated goal — do NOT add unrequested changes.'
            )

    return learned
