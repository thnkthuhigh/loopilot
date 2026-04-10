"""Context Budget Manager — controls prompt size and section priorities.

With 13+ intelligence sources injected into every prompt, context window
overflow is a real risk.  This module:

  1. Assigns each prompt section a priority level
  2. Estimates token costs (4 chars ≈ 1 token)
  3. Compresses or drops low-priority sections when budget is tight
  4. Dynamically adjusts based on score pressure (declining → keep more)

Priority tiers (lower = higher priority, always included):
  P0: Mission authority, veto, drift correction
  P1: Task ledger, priority queue, score diagnosis
  P2: Brain insights, guardrails, worker context
  P3: Archive hits, cross-repo, LLM analysis
  P4: Mission context (informational), repo map
"""

from __future__ import annotations

__all__ = [
    'PromptSection',
    'ContextBudget',
    'allocate_budget',
    'compress_section',
    'build_budgeted_sections',
]

from dataclasses import dataclass

from .logging_config import get_logger

logger = get_logger(__name__)

# 4 chars ≈ 1 token (rough estimate for GPT-family models)
CHARS_PER_TOKEN = 4


@dataclass(slots=True)
class PromptSection:
    """A single section of injected prompt context."""
    name: str = ''
    content: str = ''
    priority: int = 2              # 0 = critical, 4 = optional
    compressible: bool = True      # can this section be truncated?
    tokens_estimated: int = 0      # auto-calculated
    was_compressed: bool = False
    was_dropped: bool = False

    def estimate_tokens(self) -> int:
        self.tokens_estimated = max(1, len(self.content) // CHARS_PER_TOKEN)
        return self.tokens_estimated


@dataclass(slots=True)
class ContextBudget:
    """Token budget for the intelligence portion of the prompt."""
    max_tokens: int = 6000         # intelligence budget (not full prompt)
    used_tokens: int = 0
    sections_included: int = 0
    sections_dropped: int = 0
    sections_compressed: int = 0
    pressure: str = 'normal'       # normal | tight | critical


def _estimate_total(sections: list[PromptSection]) -> int:
    """Estimate total tokens for all sections."""
    total = 0
    for s in sections:
        s.estimate_tokens()
        total += s.tokens_estimated
    return total


def compress_section(section: PromptSection, target_tokens: int) -> PromptSection:
    """Compress a section to fit within target_tokens.

    Strategies:
    - Truncate content to target length
    - Keep first and last portions (context window pattern)
    """
    if not section.compressible or section.tokens_estimated <= target_tokens:
        return section

    target_chars = target_tokens * CHARS_PER_TOKEN
    content = section.content

    if len(content) <= target_chars:
        return section

    # Keep first 60% and last 20%, add separator
    head_len = int(target_chars * 0.6)
    tail_len = int(target_chars * 0.2)
    separator = '\n\n... [compressed — middle sections removed] ...\n\n'

    if head_len + tail_len + len(separator) >= len(content):
        # Not worth compressing, just truncate
        section.content = content[:target_chars] + '\n... [truncated]'
    else:
        section.content = content[:head_len] + separator + content[-tail_len:]

    section.was_compressed = True
    section.estimate_tokens()
    return section


def allocate_budget(
    sections: list[PromptSection],
    max_tokens: int = 6000,
    score_declining: bool = False,
) -> ContextBudget:
    """Allocate token budget across sections by priority.

    If score is declining, be more generous (keep more context to help).
    If budget is tight, compress P3+ sections, drop P4 sections.

    Returns the budget with sections modified in-place.
    """
    budget = ContextBudget(max_tokens=max_tokens)
    total = _estimate_total(sections)

    # Adjust budget based on score pressure
    if score_declining:
        budget.max_tokens = int(max_tokens * 1.2)  # 20% more for declining
        budget.pressure = 'tight'

    if total <= budget.max_tokens:
        # Everything fits — no compression needed
        budget.used_tokens = total
        budget.sections_included = len([s for s in sections if s.content])
        budget.pressure = 'normal'
        return budget

    # --- Phase 1: Drop P4 sections ---
    remaining = total
    for s in sections:
        if s.priority >= 4 and remaining > budget.max_tokens:
            remaining -= s.tokens_estimated
            s.content = ''
            s.was_dropped = True
            budget.sections_dropped += 1

    if remaining <= budget.max_tokens:
        budget.used_tokens = remaining
        budget.sections_included = len([s for s in sections if s.content and not s.was_dropped])
        return budget

    # --- Phase 2: Compress P3 sections to 50% ---
    for s in sections:
        if s.priority == 3 and s.compressible and s.content and not s.was_dropped:
            old_tokens = s.tokens_estimated
            target = max(old_tokens // 2, 50)
            compress_section(s, target)
            remaining -= (old_tokens - s.tokens_estimated)
            budget.sections_compressed += 1

    if remaining <= budget.max_tokens:
        budget.used_tokens = remaining
        budget.sections_included = len([s for s in sections if s.content and not s.was_dropped])
        return budget

    # --- Phase 3: Compress P2 sections to 60% ---
    for s in sections:
        if s.priority == 2 and s.compressible and s.content and not s.was_dropped:
            old_tokens = s.tokens_estimated
            target = max(int(old_tokens * 0.6), 50)
            compress_section(s, target)
            remaining -= (old_tokens - s.tokens_estimated)
            budget.sections_compressed += 1

    if remaining <= budget.max_tokens:
        budget.used_tokens = remaining
        budget.sections_included = len([s for s in sections if s.content and not s.was_dropped])
        return budget

    # --- Phase 4: Drop P3 sections entirely ---
    for s in sections:
        if s.priority == 3 and s.content and not s.was_dropped:
            remaining -= s.tokens_estimated
            s.content = ''
            s.was_dropped = True
            budget.sections_dropped += 1

    budget.used_tokens = min(remaining, budget.max_tokens)
    budget.sections_included = len([s for s in sections if s.content and not s.was_dropped])
    budget.pressure = 'critical'
    return budget


def build_budgeted_sections(
    sections: list[PromptSection],
    max_tokens: int = 6000,
    score_declining: bool = False,
) -> tuple[str, ContextBudget]:
    """Build the combined intelligence text within token budget.

    Returns (combined_text, budget_stats).
    """
    # Sort by priority (P0 first) for allocation
    sections_sorted = sorted(sections, key=lambda s: s.priority)

    budget = allocate_budget(sections_sorted, max_tokens, score_declining)

    # Combine non-dropped sections in original order
    parts = []
    for s in sections:
        if s.content and not s.was_dropped:
            parts.append(s.content)

    combined = '\n\n'.join(parts)

    if budget.sections_dropped > 0 or budget.sections_compressed > 0:
        logger.info(
            'Context budget: %d/%d tokens, %d dropped, %d compressed (pressure=%s)',
            budget.used_tokens, budget.max_tokens,
            budget.sections_dropped, budget.sections_compressed,
            budget.pressure,
        )

    return combined, budget
