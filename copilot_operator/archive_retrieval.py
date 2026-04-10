"""Archive Retrieval — semantic-aware search over past runs.

Scoring pipeline (layered):
  1. BM25 (term frequency × inverse document frequency) — exact keyword match
  2. N-gram boost — partial word matching for fuzzy hits
  3. TF-IDF cosine similarity — semantic vector scoring across documents
  4. Context boost — same phase, overlapping files, similar goal type
  5. Relevance decay — older runs score lower (half-life = 10 runs)
  6. LLM reranking — optional neural reranking when LLM Brain is available

Final score = weighted combination capped at 1.0.

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
    'RelevanceSignal',
    'extract_keywords',
    'query_archive',
    'render_archive_hits_for_prompt',
    'compute_semantic_similarity',
]

import json
import math
import re
from collections import Counter
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
    relevance: float = 0.0   # 0.0–1.0  (combined score)
    snippet: str = ''        # the matching text (truncated)
    goal: str = ''           # original goal of the run
    outcome: str = ''        # complete | blocked | error
    score: int | None = None
    signals: RelevanceSignal | None = None  # breakdown of scoring


@dataclass(slots=True)
class RelevanceSignal:
    """Breakdown of how relevance was computed for transparency."""
    bm25: float = 0.0
    ngram: float = 0.0
    semantic: float = 0.0
    context_boost: float = 0.0
    decay: float = 0.0       # penalty (negative)
    combined: float = 0.0


@dataclass(slots=True)
class ArchiveQuery:
    """Parameters for an archive search."""
    keywords: list[str] = field(default_factory=list)
    max_results: int = 5
    min_relevance: float = 0.1
    exclude_run_id: str = ''   # skip current run
    current_phase: str = ''    # for context boost
    current_files: list[str] = field(default_factory=list)  # for file overlap boost


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
# Scoring — BM25 with n-gram boost
# ---------------------------------------------------------------------------

# BM25 parameters (standard defaults)
_BM25_K1 = 1.2
_BM25_B = 0.75


def _tokenize(text: str) -> list[str]:
    """Tokenize text into lowercase words (3+ chars)."""
    return re.findall(r'[a-zA-Z_][a-zA-Z0-9_]{2,}', text.lower())


def _compute_idf(keyword: str, doc_count: int, docs_containing: int) -> float:
    """Inverse document frequency for a keyword."""
    if doc_count == 0 or docs_containing == 0:
        return 0.0
    return math.log((doc_count - docs_containing + 0.5) / (docs_containing + 0.5) + 1.0)


def _bm25_score_single(
    text_tokens: list[str],
    keyword: str,
    avg_doc_len: float,
    idf: float,
) -> float:
    """BM25 score for a single keyword against a tokenized document."""
    tf = text_tokens.count(keyword)
    if tf == 0:
        return 0.0
    doc_len = len(text_tokens)
    numerator = tf * (_BM25_K1 + 1)
    denominator = tf + _BM25_K1 * (1 - _BM25_B + _BM25_B * doc_len / max(avg_doc_len, 1))
    return idf * (numerator / denominator)


def _ngram_boost(text: str, keywords: list[str], n: int = 3) -> float:
    """Boost score for partial matches via character n-grams.

    Catches cases where keyword = 'authentication' and text has 'auth'.
    Returns 0.0–0.3 bonus.
    """
    if not text or not keywords:
        return 0.0
    text_lower = text.lower()
    text_ngrams = {text_lower[i:i + n] for i in range(len(text_lower) - n + 1)} if len(text_lower) >= n else set()
    if not text_ngrams:
        return 0.0

    total_overlap = 0.0
    for kw in keywords:
        if len(kw) < n:
            continue
        kw_ngrams = {kw[i:i + n] for i in range(len(kw) - n + 1)}
        if kw_ngrams:
            overlap = len(kw_ngrams & text_ngrams) / len(kw_ngrams)
            total_overlap += overlap
    avg_overlap = total_overlap / len(keywords) if keywords else 0.0
    return min(avg_overlap * 0.3, 0.3)  # cap at 0.3 boost


def _score_text(text: str, keywords: list[str]) -> float:
    """Score a text blob against keywords using BM25 + n-gram boost.

    Returns 0.0–1.0.
    """
    if not text or not keywords:
        return 0.0
    text_tokens = _tokenize(text)
    if not text_tokens:
        return 0.0

    # Simplified BM25: treat each text as a single doc, use uniform IDF
    avg_doc_len = float(len(text_tokens))
    total = 0.0
    for kw in keywords:
        tf = text_tokens.count(kw)
        if tf > 0:
            # Approximate IDF (assume keyword appears in ~30% of docs)
            idf = math.log(1.0 + (1.0 / 0.3))
            total += _bm25_score_single(text_tokens, kw, avg_doc_len, idf)

    # Normalize to 0–1 range
    max_possible = len(keywords) * math.log(1.0 + (1.0 / 0.3)) * (_BM25_K1 + 1)
    bm25_normalized = min(total / max_possible, 1.0) if max_possible > 0 else 0.0

    # Add n-gram boost for partial/fuzzy matches
    ngram = _ngram_boost(text, keywords)

    return min(bm25_normalized + ngram, 1.0)


# ---------------------------------------------------------------------------
# TF-IDF cosine similarity — semantic vector scoring
# ---------------------------------------------------------------------------

def _build_tfidf_vector(tokens: list[str], idf_map: dict[str, float]) -> dict[str, float]:
    """Build a TF-IDF vector from tokens and an IDF map."""
    tf = Counter(tokens)
    total = len(tokens) or 1
    return {
        word: (count / total) * idf_map.get(word, 0.0)
        for word, count in tf.items()
        if word in idf_map
    }


def _cosine_similarity(vec_a: dict[str, float], vec_b: dict[str, float]) -> float:
    """Cosine similarity between two sparse vectors."""
    if not vec_a or not vec_b:
        return 0.0
    common = set(vec_a) & set(vec_b)
    if not common:
        return 0.0
    dot = sum(vec_a[k] * vec_b[k] for k in common)
    mag_a = math.sqrt(sum(v * v for v in vec_a.values()))
    mag_b = math.sqrt(sum(v * v for v in vec_b.values()))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def compute_semantic_similarity(query_text: str, doc_text: str, corpus_texts: list[str] | None = None) -> float:
    """Compute TF-IDF cosine similarity between query and document.

    If corpus_texts is provided, IDF is computed across the corpus.
    Otherwise, IDF is estimated from query + doc alone.
    Returns 0.0–1.0.
    """
    query_tokens = _tokenize(query_text)
    doc_tokens = _tokenize(doc_text)
    if not query_tokens or not doc_tokens:
        return 0.0

    # Build corpus for IDF
    all_docs = [query_tokens, doc_tokens]
    if corpus_texts:
        all_docs.extend(_tokenize(t) for t in corpus_texts)

    # Compute IDF for all unique terms
    vocab = set(query_tokens) | set(doc_tokens)
    doc_count = len(all_docs)
    idf_map: dict[str, float] = {}
    for word in vocab:
        docs_with = sum(1 for d in all_docs if word in d)
        idf_map[word] = _compute_idf(word, doc_count, docs_with)

    vec_q = _build_tfidf_vector(query_tokens, idf_map)
    vec_d = _build_tfidf_vector(doc_tokens, idf_map)
    return _cosine_similarity(vec_q, vec_d)


# ---------------------------------------------------------------------------
# Context-aware boosting
# ---------------------------------------------------------------------------

def _context_boost(
    hit_goal: str,
    hit_files: list[str],
    hit_outcome: str,
    query_goal: str,
    query_files: list[str],
    query_phase: str,
) -> float:
    """Boost relevance based on contextual similarity.

    Factors:
    - Goal type similarity (both 'fix bug', both 'add feature', etc.)
    - File overlap (touching the same files = very relevant)
    - Successful outcomes score higher
    """
    boost = 0.0

    # Goal type similarity — structural patterns
    goal_patterns = {
        'bug': r'\b(fix|bug|error|crash|fail|broken|issue)\b',
        'feature': r'\b(add|create|implement|build|new|feature)\b',
        'refactor': r'\b(refactor|clean|reorganize|restructure|simplify)\b',
        'test': r'\b(test|spec|coverage|assert|verify)\b',
        'docs': r'\b(doc|readme|comment|guide|tutorial)\b',
    }
    hit_types = {k for k, pat in goal_patterns.items() if re.search(pat, hit_goal.lower())}
    query_types = {k for k, pat in goal_patterns.items() if re.search(pat, query_goal.lower())}
    if hit_types & query_types:
        boost += 0.1  # same type of work

    # File overlap — most valuable signal
    if hit_files and query_files:
        hit_set = {f.rsplit('/', 1)[-1].rsplit('\\', 1)[-1] for f in hit_files}
        query_set = {f.rsplit('/', 1)[-1].rsplit('\\', 1)[-1] for f in query_files}
        overlap = len(hit_set & query_set)
        if overlap > 0:
            boost += min(overlap * 0.05, 0.15)

    # Successful outcomes are more valuable (lessons from success)
    if hit_outcome == 'complete':
        boost += 0.05

    return min(boost, 0.25)  # cap


def _relevance_decay(run_index: int, total_runs: int, half_life: int = 10) -> float:
    """Penalise older runs. Returns a negative penalty (0.0 to -0.3).

    run_index=0 is the oldest, run_index=total_runs-1 is the newest.
    """
    if total_runs <= 1:
        return 0.0
    age = total_runs - 1 - run_index  # 0 = newest, total-1 = oldest
    decay_factor = 0.5 ** (age / max(half_life, 1))
    penalty = (1.0 - decay_factor) * 0.3
    return -penalty


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
    llm_brain: object | None = None,
    current_phase: str = '',
    current_files: list[str] | None = None,
) -> list[ArchiveHit]:
    """Query the archive for runs relevant to the given goal.

    Multi-signal scoring pipeline:
      1. BM25 + n-gram (from _score_text)
      2. TF-IDF cosine semantic similarity
      3. Context boost (goal type, file overlap, outcome)
      4. Relevance decay (older runs penalised)
      5. Optional LLM reranking

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
        current_phase=current_phase,
        current_files=current_files or [],
    )

    log_dir = workspace / '.copilot-operator' / 'logs'

    # Scan both sources
    all_hits = _scan_narratives(log_dir, query, keywords)
    all_hits.extend(_scan_ledgers(log_dir, query, keywords))

    if not all_hits:
        return []

    # --- Corpus-level IDF boost ---
    doc_count = len(all_hits)
    for hit in all_hits:
        hit_tokens = set(_tokenize(hit.snippet))
        boosted = 0.0
        for kw in keywords:
            docs_with_kw = sum(1 for h in all_hits if kw in _tokenize(h.snippet))
            if kw in hit_tokens and docs_with_kw < doc_count:
                idf = _compute_idf(kw, doc_count, docs_with_kw)
                boosted += idf * 0.05
        hit.relevance = min(hit.relevance + boosted, 1.0)

    # --- Enumerate run ordering for decay ---
    run_ids_ordered = []
    for rd in sorted(log_dir.iterdir()) if log_dir.exists() else []:
        if rd.is_dir() and rd.name != exclude_run_id:
            run_ids_ordered.append(rd.name)
    run_index_map = {rid: i for i, rid in enumerate(run_ids_ordered)}
    total_runs = len(run_ids_ordered)

    # --- Multi-signal scoring ---
    corpus_snippets = [h.snippet for h in all_hits]
    for hit in all_hits:
        bm25_score = hit.relevance  # already computed by _score_text + corpus IDF

        # Semantic similarity via TF-IDF cosine
        semantic = compute_semantic_similarity(goal, hit.snippet, corpus_snippets)

        # Context boost
        hit_files: list[str] = []
        if hit.source == 'ledger':
            # Parse files from snippet (best effort)
            hit_files = re.findall(r'[\w/\\]+\.\w+', hit.snippet)
        ctx_boost = _context_boost(
            hit.goal, hit_files, hit.outcome,
            goal, query.current_files, query.current_phase,
        )

        # Relevance decay
        run_idx = run_index_map.get(hit.run_id, total_runs - 1)
        decay = _relevance_decay(run_idx, total_runs)

        # Weighted combination: BM25 40%, semantic 35%, context 25% + decay
        combined = (
            bm25_score * 0.40
            + semantic * 0.35
            + ctx_boost * 0.25
            + decay
        )
        combined = max(0.0, min(combined, 1.0))

        hit.signals = RelevanceSignal(
            bm25=bm25_score,
            ngram=0.0,  # already included in bm25_score
            semantic=semantic,
            context_boost=ctx_boost,
            decay=decay,
            combined=combined,
        )
        hit.relevance = combined

    # Filter by min relevance
    all_hits = [h for h in all_hits if h.relevance >= query.min_relevance]

    # Deduplicate: keep best hit per run_id
    best_per_run: dict[str, ArchiveHit] = {}
    for hit in all_hits:
        existing = best_per_run.get(hit.run_id)
        if not existing or hit.relevance > existing.relevance:
            best_per_run[hit.run_id] = hit

    # Sort by relevance descending
    results = sorted(best_per_run.values(), key=lambda h: h.relevance, reverse=True)

    # LLM reranking: if brain is available, rerank top candidates
    if llm_brain and len(results) > 1:
        results = _llm_rerank(results, goal, llm_brain, max_results)

    return results[:max_results]


def _llm_rerank(
    hits: list[ArchiveHit],
    goal: str,
    llm_brain: object,
    max_results: int,
) -> list[ArchiveHit]:
    """Use LLM Brain to rerank archive hits by semantic relevance.

    Falls back to original order if LLM is unavailable or fails.
    """
    try:
        brain = llm_brain  # type: ignore[assignment]
        if not getattr(brain, 'is_ready', False):
            return hits

        # Build a compact prompt for reranking
        candidates = []
        for i, hit in enumerate(hits[:max_results * 2]):  # rerank top 2x
            candidates.append(f'{i}: goal="{hit.goal[:60]}" outcome={hit.outcome} snippet="{hit.snippet[:80]}"')

        prompt = (
            f'Current goal: "{goal[:100]}"\n\n'
            f'Past runs:\n' + '\n'.join(candidates) + '\n\n'
            'Rank the past runs by relevance to the current goal. '
            'Return ONLY a comma-separated list of indices, most relevant first. '
            'Example: 2,0,1'
        )

        response = brain.ask(prompt, max_tokens=100)
        if not response:
            return hits

        # Parse response: extract indices
        indices = []
        for token in re.findall(r'\d+', response):
            idx = int(token)
            if 0 <= idx < len(hits) and idx not in indices:
                indices.append(idx)

        if not indices:
            return hits

        # Reorder hits by LLM ranking
        reranked = [hits[i] for i in indices if i < len(hits)]
        # Append any remaining hits not mentioned
        seen = set(indices)
        for i, hit in enumerate(hits):
            if i not in seen:
                reranked.append(hit)

        logger.info('Archive LLM reranked: %s', indices[:5])
        return reranked

    except Exception:
        return hits  # reranking is best-effort


def render_archive_hits_for_prompt(hits: list[ArchiveHit]) -> str:
    """Render archive hits as a concise block for prompt injection.

    Returns empty string if no hits.
    """
    if not hits:
        return ''

    lines = ['## Relevant Past Runs']
    for i, hit in enumerate(hits, 1):
        score_str = f', score={hit.score}' if hit.score is not None else ''
        rel_pct = f'{hit.relevance:.0%}'
        lines.append(f'**[{i}]** {hit.goal[:80]} → {hit.outcome}{score_str} (relevance: {rel_pct})')
        lines.append(f'  _{hit.snippet[:150]}_')
        # Show signal breakdown for transparency (compact)
        if hit.signals and hit.relevance >= 0.3:
            s = hit.signals
            parts = []
            if s.bm25 > 0:
                parts.append(f'keyword:{s.bm25:.2f}')
            if s.semantic > 0:
                parts.append(f'semantic:{s.semantic:.2f}')
            if s.context_boost > 0:
                parts.append(f'context:{s.context_boost:.2f}')
            if parts:
                lines.append(f'  _Signals: {", ".join(parts)}_')
    return '\n'.join(lines)
