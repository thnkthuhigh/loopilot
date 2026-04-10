"""Archive Retrieval — keyword search over past runs for relevant context.

The archive layer stores:
  - run narratives (prose + done explanation)
  - task ledgers (structured state)
  - run digests (metrics)

Instead of replaying full history, this module retrieves only the facts,
lessons, and decisions relevant to the current goal.  This is **retrieval,
not replay** — the key mechanism that prevents context bloat.

Memory model position:
  Working → Task → Project → Mission → Cross-repo
  Archive sits alongside all layers as a queryable store.
"""

from __future__ import annotations

__all__ = [
    'ArchiveHit',
    'ArchiveQuery',
    'query_archive',
    'render_archive_hits_for_prompt',
]

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .logging_config import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class ArchiveHit:
    """A single relevant result from the archive."""
    run_id: str = ''
    source: str = ''         # narrative | ledger | digest
    relevance: float = 0.0   # 0.0–1.0
    snippet: str = ''        # the matching text (truncated)
    goal: str = ''           # original goal of the run
    outcome: str = ''        # complete | blocked | error
    score: int | None = None


@dataclass(slots=True)
class ArchiveQuery:
    """Parameters for an archive search."""
    keywords: list[str] = field(default_factory=list)
    max_results: int = 5
    min_relevance: float = 0.1
    exclude_run_id: str = ''   # skip current run


# ---------------------------------------------------------------------------
# Keyword extraction
# ---------------------------------------------------------------------------

_STOP_WORDS = frozenset({
    'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
    'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
    'should', 'may', 'might', 'shall', 'can', 'need', 'it', 'its', 'this',
    'that', 'these', 'those', 'i', 'you', 'he', 'she', 'we', 'they', 'me',
    'him', 'her', 'us', 'them', 'my', 'your', 'his', 'our', 'their',
    'what', 'which', 'who', 'whom', 'where', 'when', 'why', 'how',
    'not', 'no', 'nor', 'but', 'and', 'or', 'if', 'then', 'else',
    'for', 'of', 'to', 'from', 'by', 'on', 'at', 'in', 'with', 'as',
    'into', 'through', 'during', 'before', 'after', 'above', 'below',
    'up', 'down', 'out', 'off', 'over', 'under', 'again', 'further',
    'all', 'each', 'every', 'both', 'few', 'more', 'most', 'other',
    'some', 'such', 'only', 'own', 'same', 'so', 'than', 'too', 'very',
    'just', 'because', 'about', 'between', 'also', 'here', 'there',
    'fix', 'run', 'test', 'file', 'code', 'make', 'add', 'get', 'set',
})


def extract_keywords(text: str) -> list[str]:
    """Extract meaningful keywords from a goal or query string."""
    words = re.findall(r'[a-zA-Z_][a-zA-Z0-9_]{2,}', text.lower())
    return [w for w in dict.fromkeys(words) if w not in _STOP_WORDS]


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _score_text(text: str, keywords: list[str]) -> float:
    """Score a text blob against keywords. Returns 0.0–1.0."""
    if not text or not keywords:
        return 0.0
    text_lower = text.lower()
    hits = sum(1 for kw in keywords if kw in text_lower)
    return hits / len(keywords)


def _extract_snippet(text: str, keywords: list[str], max_len: int = 200) -> str:
    """Extract the most relevant snippet from text around the first keyword hit."""
    text_lower = text.lower()
    best_pos = -1
    for kw in keywords:
        pos = text_lower.find(kw)
        if pos >= 0:
            best_pos = pos
            break
    if best_pos < 0:
        return text[:max_len]
    start = max(0, best_pos - 60)
    end = min(len(text), start + max_len)
    snippet = text[start:end].strip()
    if start > 0:
        snippet = '...' + snippet
    if end < len(text):
        snippet = snippet + '...'
    return snippet


# ---------------------------------------------------------------------------
# Archive scanning
# ---------------------------------------------------------------------------

def _scan_narratives(log_dir: Path, query: ArchiveQuery, keywords: list[str]) -> list[ArchiveHit]:
    """Scan narrative.md and done-explanation.md files."""
    hits: list[ArchiveHit] = []
    if not log_dir.exists():
        return hits

    for run_dir in log_dir.iterdir():
        if not run_dir.is_dir():
            continue
        run_id = run_dir.name
        if run_id == query.exclude_run_id:
            continue

        for filename in ('narrative.md', 'done-explanation.md'):
            path = run_dir / filename
            if not path.exists():
                continue
            try:
                text = path.read_text(encoding='utf-8')
            except OSError:
                continue

            score = _score_text(text, keywords)
            if score >= query.min_relevance:
                # Try to extract goal from narrative
                goal = ''
                goal_match = re.search(r'\*\*Goal:\*\*\s*(.+)', text)
                if goal_match:
                    goal = goal_match.group(1).strip()
                outcome = ''
                outcome_match = re.search(r'\*\*Outcome:\*\*\s*(\w+)', text)
                if outcome_match:
                    outcome = outcome_match.group(1).strip()
                score_match = re.search(r'score[:\s]+(\d+)', text, re.IGNORECASE)
                run_score = int(score_match.group(1)) if score_match else None

                hits.append(ArchiveHit(
                    run_id=run_id,
                    source=f'narrative:{filename}',
                    relevance=score,
                    snippet=_extract_snippet(text, keywords),
                    goal=goal,
                    outcome=outcome,
                    score=run_score,
                ))
    return hits


def _scan_ledgers(log_dir: Path, query: ArchiveQuery, keywords: list[str]) -> list[ArchiveHit]:
    """Scan ledger.json files for relevant task context."""
    hits: list[ArchiveHit] = []
    if not log_dir.exists():
        return hits

    for run_dir in log_dir.iterdir():
        if not run_dir.is_dir():
            continue
        run_id = run_dir.name
        if run_id == query.exclude_run_id:
            continue

        path = run_dir / 'ledger.json'
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            continue

        # Build searchable text from ledger fields
        parts = [
            data.get('goal', ''),
            ' '.join(data.get('decisions', [])),
            ' '.join(data.get('files_touched', [])),
            data.get('stop_reason', ''),
        ]
        commitments = data.get('commitments', {})
        parts.extend(commitments.get('decided', []))
        parts.extend(commitments.get('owed', []))
        parts.extend(commitments.get('must_not_forget', []))

        text = ' '.join(parts)
        score = _score_text(text, keywords)
        if score >= query.min_relevance:
            hits.append(ArchiveHit(
                run_id=run_id,
                source='ledger',
                relevance=score,
                snippet=_extract_snippet(text, keywords),
                goal=data.get('goal', ''),
                outcome=data.get('outcome', ''),
                score=None,
            ))
    return hits


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def query_archive(
    workspace: Path,
    goal: str,
    *,
    extra_keywords: list[str] | None = None,
    max_results: int = 5,
    exclude_run_id: str = '',
) -> list[ArchiveHit]:
    """Query the archive for runs relevant to the given goal.

    Searches narratives and ledgers using keyword matching.
    Returns hits sorted by relevance, deduplicated per run_id.
    """
    keywords = extract_keywords(goal)
    if extra_keywords:
        keywords.extend(kw.lower() for kw in extra_keywords if kw.lower() not in keywords)

    if not keywords:
        return []

    query = ArchiveQuery(
        keywords=keywords,
        max_results=max_results,
        exclude_run_id=exclude_run_id,
    )

    log_dir = workspace / '.copilot-operator' / 'logs'

    # Scan both sources
    all_hits = _scan_narratives(log_dir, query, keywords)
    all_hits.extend(_scan_ledgers(log_dir, query, keywords))

    # Deduplicate: keep best hit per run_id
    best_per_run: dict[str, ArchiveHit] = {}
    for hit in all_hits:
        existing = best_per_run.get(hit.run_id)
        if not existing or hit.relevance > existing.relevance:
            best_per_run[hit.run_id] = hit

    # Sort by relevance descending, cap at max_results
    results = sorted(best_per_run.values(), key=lambda h: h.relevance, reverse=True)
    return results[:max_results]


def render_archive_hits_for_prompt(hits: list[ArchiveHit]) -> str:
    """Render archive hits as a concise block for prompt injection.

    Returns empty string if no hits.
    """
    if not hits:
        return ''

    lines = ['## Relevant Past Runs']
    for i, hit in enumerate(hits, 1):
        score_str = f', score={hit.score}' if hit.score is not None else ''
        lines.append(f'**[{i}]** {hit.goal[:80]} → {hit.outcome}{score_str}')
        lines.append(f'  _{hit.snippet[:150]}_')
    return '\n'.join(lines)
