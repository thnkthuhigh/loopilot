"""Memory Promotion — rules for promoting facts between memory layers.

Memory layers (bottom → top):
  1. Working memory  — current run state, transient (in runtime dict)
  2. Task memory     — per-run decision/artifacts (in logs/{runId}/)
  3. Project memory  — repo-level facts (in brain, learned-rules, repo-profile)
  4. Mission memory  — project direction (in mission.yml)
  5. Cross-repo      — shared insights (in shared-brain/)

Promotion rules decide when a fact should move UP from a lower layer to
a higher one, and when a fact should be DEMOTED (cleaned out) to prevent
context bloat.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class PromotionCandidate:
    """A fact being considered for promotion."""
    fact: str
    source_layer: str        # working | task | project
    target_layer: str        # task | project | mission | cross_repo
    category: str            # trap | convention | validation_fact | strategy | lesson
    confidence: float = 0.5
    occurrences: int = 1
    source_runs: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PromotionResult:
    """Result of running promotion rules on a run's outputs."""
    promoted: list[PromotionCandidate] = field(default_factory=list)
    demoted: list[str] = field(default_factory=list)      # fact descriptions removed


# ---------------------------------------------------------------------------
# Threshold configuration
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class PromotionThresholds:
    """Configurable thresholds for memory promotion."""
    # Promote to project memory when a trap/pattern appears N times
    trap_repeat_threshold: int = 2
    # Promote to project memory when a validation fact is confirmed N times
    validation_confirm_threshold: int = 2
    # Promote a strategy to project when it succeeds N times
    strategy_success_threshold: int = 3
    # Promote to mission memory when a lesson is cross-task relevant
    lesson_cross_task_threshold: int = 2
    # Max project-level lessons before trimming oldest
    max_project_lessons: int = 30
    # Max mission-level lessons
    max_mission_lessons: int = 20
    # Confidence threshold to promote to cross-repo
    cross_repo_confidence: float = 0.8


DEFAULT_THRESHOLDS = PromotionThresholds()


# ---------------------------------------------------------------------------
# Core promotion logic
# ---------------------------------------------------------------------------

def evaluate_promotions(
    run_history: list[dict[str, Any]],
    run_result: dict[str, Any],
    existing_rules: list[dict[str, Any]],
    existing_lessons: list[str],
    thresholds: PromotionThresholds | None = None,
) -> PromotionResult:
    """Analyse a completed run and determine which facts should be promoted.

    Args:
        run_history: The runtime['history'] list from the operator.
        run_result: The final result dict from the run.
        existing_rules: Current learned-rules (as dicts with 'trigger', 'hit_count', etc.)
        existing_lessons: Current project-level lessons (from brain insights).
        thresholds: Promotion thresholds (uses defaults if None).

    Returns:
        PromotionResult with candidates and demotions.
    """
    th = thresholds or DEFAULT_THRESHOLDS
    promoted: list[PromotionCandidate] = []
    demoted: list[str] = []

    # --- 1. Trap promotion: working/task → project ---
    # If a blocker or validation failure appeared in this run AND was seen before
    all_blockers: list[str] = []
    all_val_failures: list[str] = []
    for h in run_history:
        for b in h.get('blockers') or []:
            item = b.get('item', '')
            if item and item not in all_blockers:
                all_blockers.append(item)
        for v in h.get('validation_after') or []:
            if v.get('status') in ('fail', 'timeout'):
                name = v.get('name', '')
                if name and name not in all_val_failures:
                    all_val_failures.append(name)

    # Check against existing rules for repeat patterns
    rule_triggers = {r.get('trigger', ''): r.get('hit_count', 0) for r in existing_rules}

    for blocker in all_blockers:
        count = rule_triggers.get(blocker, 0) + 1
        if count >= th.trap_repeat_threshold:
            promoted.append(PromotionCandidate(
                fact=f'Recurring blocker: {blocker}',
                source_layer='task',
                target_layer='project',
                category='trap',
                confidence=min(0.5 + count * 0.1, 1.0),
                occurrences=count,
            ))

    # --- 2. Validation fact promotion: task → project ---
    for vf in all_val_failures:
        count = rule_triggers.get(vf, 0) + 1
        if count >= th.validation_confirm_threshold:
            promoted.append(PromotionCandidate(
                fact=f'Persistent validation failure: {vf}',
                source_layer='task',
                target_layer='project',
                category='validation_fact',
                confidence=min(0.5 + count * 0.15, 1.0),
                occurrences=count,
            ))

    # --- 3. Strategy promotion: project → cross_repo ---
    # Rules that succeeded many times should be shared
    for rule in existing_rules:
        hit = rule.get('hit_count', 0)
        success = rule.get('success_after_hit', 0)
        confidence = rule.get('confidence', 0)
        if (hit >= th.strategy_success_threshold
                and success >= th.strategy_success_threshold
                and confidence >= th.cross_repo_confidence):
            promoted.append(PromotionCandidate(
                fact=f'Strategy: {rule.get("guardrail", "")}',
                source_layer='project',
                target_layer='cross_repo',
                category='strategy',
                confidence=confidence,
                occurrences=hit,
            ))

    # --- 4. Lesson promotion: task → mission ---
    # If the run had a significant outcome (error/blocked), this is a mission-level lesson
    status = run_result.get('status', '')
    reason_code = run_result.get('reasonCode', '')

    if status in ('error', 'blocked') and reason_code:
        # Check if this reason code appeared before in existing lessons
        matching = [ls for ls in existing_lessons if reason_code.lower() in ls.lower()]
        if len(matching) >= th.lesson_cross_task_threshold:
            promoted.append(PromotionCandidate(
                fact=f'Recurring issue: {reason_code} — appears across multiple tasks',
                source_layer='project',
                target_layer='mission',
                category='lesson',
                confidence=0.7,
                occurrences=len(matching) + 1,
            ))

    # --- 5. Demotion: trim stale project lessons ---
    if len(existing_lessons) > th.max_project_lessons:
        excess = len(existing_lessons) - th.max_project_lessons
        for old_lesson in existing_lessons[:excess]:
            demoted.append(old_lesson)

    return PromotionResult(promoted=promoted, demoted=demoted)


def apply_promotions(
    result: PromotionResult,
    brain_learnings: list[dict[str, Any]],
    mission_lessons: list[str],
    shared_brain_insights: list[dict[str, Any]],
) -> dict[str, int]:
    """Apply promotion candidates to the appropriate memory layers.

    Returns a summary dict: {'promoted_to_project': N, 'promoted_to_mission': N, ...}

    Note: This modifies the input lists in-place.
    """
    counts: dict[str, int] = {
        'promoted_to_project': 0,
        'promoted_to_mission': 0,
        'promoted_to_cross_repo': 0,
        'demoted': 0,
    }

    for candidate in result.promoted:
        if candidate.target_layer == 'project':
            # Add to brain learnings if not already present
            existing_facts = {entry.get('detail', '') for entry in brain_learnings}
            if candidate.fact not in existing_facts:
                brain_learnings.append({
                    'category': candidate.category,
                    'detail': candidate.fact,
                    'source_run': ','.join(candidate.source_runs) if candidate.source_runs else 'promotion',
                    'confidence': candidate.confidence,
                    'timestamp': '',
                })
                counts['promoted_to_project'] += 1

        elif candidate.target_layer == 'mission':
            if candidate.fact not in mission_lessons:
                mission_lessons.append(candidate.fact)
                counts['promoted_to_mission'] += 1

        elif candidate.target_layer == 'cross_repo':
            existing_details = {i.get('detail', '') for i in shared_brain_insights}
            if candidate.fact not in existing_details:
                shared_brain_insights.append({
                    'category': candidate.category,
                    'detail': candidate.fact,
                    'confidence': candidate.confidence,
                    'tags': [candidate.category],
                    'hit_count': candidate.occurrences,
                })
                counts['promoted_to_cross_repo'] += 1

    # Apply demotions
    for old_fact in result.demoted:
        brain_learnings[:] = [entry for entry in brain_learnings if entry.get('detail') != old_fact]
        counts['demoted'] += 1

    return counts


# ---------------------------------------------------------------------------
# Convenience: full promotion cycle
# ---------------------------------------------------------------------------

def run_promotion_cycle(
    run_history: list[dict[str, Any]],
    run_result: dict[str, Any],
    existing_rules: list[dict[str, Any]],
    brain_learnings: list[dict[str, Any]],
    mission_lessons: list[str],
    shared_brain_insights: list[dict[str, Any]],
    thresholds: PromotionThresholds | None = None,
) -> dict[str, int]:
    """Evaluate and apply promotions in one call.

    Returns summary counts.
    """
    existing_lesson_texts = [entry.get('detail', '') for entry in brain_learnings]
    result = evaluate_promotions(
        run_history, run_result, existing_rules, existing_lesson_texts, thresholds,
    )
    return apply_promotions(result, brain_learnings, mission_lessons, shared_brain_insights)
