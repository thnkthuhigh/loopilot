"""Cross-Repo Learning — share learned insights across workspaces.

When the operator runs in different repositories, each repo accumulates
its own brain (run digests, patterns, learned rules).  This module lets
the operator export/import insights so knowledge from Repo A can benefit
Repo B.

Storage: a shared directory (default ``~/.copilot-operator/shared-brain/``)
holds JSON files keyed by category.  Each repo can contribute entries and
query the shared pool during planning or diagnosis.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_SHARED_PATH = os.path.join(os.path.expanduser('~'), '.copilot-operator', 'shared-brain')


def shared_brain_dir(override: str = '') -> Path:
    """Return the shared brain directory, creating it if needed."""
    p = Path(override) if override else Path(DEFAULT_SHARED_PATH)
    p.mkdir(parents=True, exist_ok=True)
    return p


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class SharedInsight:
    """A single transferable insight."""
    insight_id: str = ''
    source_repo: str = ''
    category: str = ''          # 'pattern' | 'guardrail' | 'strategy' | 'trap'
    tags: list[str] = field(default_factory=list)
    text: str = ''
    confidence: float = 0.5     # 0.0–1.0
    hit_count: int = 0          # how many times it was useful


@dataclass(slots=True)
class SharedBrain:
    """Aggregated cross-repo knowledge pool."""
    insights: list[SharedInsight] = field(default_factory=list)
    store_path: str = ''


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _insights_file(brain_dir: Path) -> Path:
    return brain_dir / 'insights.json'


def load_shared_brain(store_path: str = '') -> SharedBrain:
    """Load the shared brain from disk."""
    brain_dir = shared_brain_dir(store_path)
    fp = _insights_file(brain_dir)
    items: list[SharedInsight] = []
    if fp.exists():
        try:
            raw = json.loads(fp.read_text(encoding='utf-8'))
            for entry in raw:
                items.append(SharedInsight(**{
                    k: v for k, v in entry.items()
                    if k in SharedInsight.__dataclass_fields__
                }))
        except (OSError, json.JSONDecodeError, TypeError, KeyError):
            pass  # Start fresh on corrupt data
    return SharedBrain(insights=items, store_path=str(brain_dir))


def save_shared_brain(brain: SharedBrain) -> None:
    """Persist the shared brain to disk."""
    brain_dir = shared_brain_dir(brain.store_path)
    fp = _insights_file(brain_dir)
    data = [asdict(insight) for insight in brain.insights]
    fp.write_text(json.dumps(data, indent=2, default=str), encoding='utf-8')


# ---------------------------------------------------------------------------
# Contribute (export from local repo)
# ---------------------------------------------------------------------------

def contribute_insight(
    brain: SharedBrain,
    source_repo: str,
    category: str,
    text: str,
    tags: list[str] | None = None,
    confidence: float = 0.5,
) -> SharedInsight:
    """Add a new insight to the shared brain pool.

    De-duplicates by matching category + text prefix (first 80 chars).
    """
    prefix = text[:80]
    for existing in brain.insights:
        if existing.category == category and existing.text[:80] == prefix:
            existing.hit_count += 1
            existing.confidence = min(1.0, existing.confidence + 0.05)
            return existing

    insight_id = f'{source_repo}-{category}-{len(brain.insights)}'
    insight = SharedInsight(
        insight_id=insight_id,
        source_repo=source_repo,
        category=category,
        tags=tags or [],
        text=text,
        confidence=confidence,
        hit_count=1,
    )
    brain.insights.append(insight)
    return insight


# ---------------------------------------------------------------------------
# Query (import into current repo)
# ---------------------------------------------------------------------------

def query_insights(
    brain: SharedBrain,
    category: str = '',
    tags: list[str] | None = None,
    min_confidence: float = 0.3,
    exclude_repo: str = '',
    limit: int = 10,
) -> list[SharedInsight]:
    """Query the shared brain for relevant insights.

    Filters by category, tags, and confidence.  Optionally exclude the
    current repo to get only *external* knowledge.
    """
    results = brain.insights[:]

    if category:
        results = [i for i in results if i.category == category]
    if tags:
        tag_set = set(tags)
        results = [i for i in results if tag_set & set(i.tags)]
    if exclude_repo:
        results = [i for i in results if i.source_repo != exclude_repo]
    results = [i for i in results if i.confidence >= min_confidence]

    # Sort by confidence * hit_count (relevance score)
    results.sort(key=lambda i: i.confidence * i.hit_count, reverse=True)
    return results[:limit]


def query_guardrails(brain: SharedBrain, exclude_repo: str = '') -> list[SharedInsight]:
    """Get all guardrail insights from the shared brain."""
    return query_insights(brain, category='guardrail', exclude_repo=exclude_repo)


def query_traps(brain: SharedBrain, exclude_repo: str = '') -> list[SharedInsight]:
    """Get known traps/pitfalls from other repos."""
    return query_insights(brain, category='trap', exclude_repo=exclude_repo)


# ---------------------------------------------------------------------------
# Render for prompt injection
# ---------------------------------------------------------------------------

def render_cross_repo_insights(
    brain: SharedBrain,
    current_repo: str = '',
    max_items: int = 5,
) -> str:
    """Render external insights for injection into the operator prompt."""
    relevant = query_insights(brain, exclude_repo=current_repo, limit=max_items)
    if not relevant:
        return ''

    lines = ['## Cross-Repo Insights', '']
    for insight in relevant:
        tag_str = f' [{", ".join(insight.tags)}]' if insight.tags else ''
        lines.append(
            f'- **[{insight.category}]** (from {insight.source_repo}{tag_str}, '
            f'confidence {insight.confidence:.0%}): {insight.text}'
        )
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Auto-export from meta_learner rules
# ---------------------------------------------------------------------------

def export_rules_as_insights(
    brain: SharedBrain,
    source_repo: str,
    rules: list[dict[str, str]],
) -> int:
    """Batch-export meta-learner rules as shared insights.

    ``rules`` should be a list of dicts with 'trigger', 'guardrail', 'category'.
    Returns number of new insights contributed.
    """
    count = 0
    for rule in rules:
        trigger = rule.get('trigger', '')
        guardrail = rule.get('guardrail', '')
        category = rule.get('category', 'guardrail')
        if trigger and guardrail:
            text = f'When "{trigger}" → {guardrail}'
            insight = contribute_insight(
                brain, source_repo, category, text,
                tags=['auto-exported', 'meta-learner'],
            )
            if insight.hit_count == 1:
                count += 1
    return count
