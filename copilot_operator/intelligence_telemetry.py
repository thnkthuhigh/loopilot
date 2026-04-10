"""Intelligence Telemetry — track which prompt injections actually help.

13+ intelligence sources inject context into every prompt, but we have
no measurement of which sources correlate with score improvement.  This
module tracks:

  1. Which sections were injected (name, priority, token cost)
  2. Whether that iteration improved the score
  3. Aggregated effectiveness per source over time

This enables data-driven pruning: drop sections that never help,
boost budget for sections that do.
"""

from __future__ import annotations

__all__ = [
    'InjectionRecord',
    'TelemetryAggregator',
    'IntelligenceReport',
]

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .logging_config import get_logger

logger = get_logger(__name__)


@dataclass(slots=True)
class InjectionRecord:
    """Record of a single intelligence injection in one iteration."""
    source: str = ''               # section name (e.g. 'archive', 'brain', 'mission')
    tokens_used: int = 0
    was_compressed: bool = False
    was_dropped: bool = False
    iteration: int = 0
    score_before: int = 0
    score_after: int = 0

    @property
    def score_delta(self) -> int:
        return self.score_after - self.score_before

    @property
    def helped(self) -> bool:
        """Did the score improve in the iteration this source was injected?"""
        return self.score_delta > 0


@dataclass(slots=True)
class SourceStats:
    """Aggregated stats for a single intelligence source."""
    source: str = ''
    total_injections: int = 0
    total_tokens: int = 0
    times_helped: int = 0          # injections where score improved
    times_neutral: int = 0         # injections where score unchanged
    times_hurt: int = 0            # injections where score regressed
    times_dropped: int = 0         # times dropped by budget manager
    times_compressed: int = 0

    @property
    def help_rate(self) -> float:
        if self.total_injections == 0:
            return 0.0
        return self.times_helped / self.total_injections

    @property
    def avg_tokens(self) -> int:
        if self.total_injections == 0:
            return 0
        return self.total_tokens // self.total_injections


@dataclass(slots=True)
class IntelligenceReport:
    """Summary of intelligence source effectiveness."""
    total_iterations: int = 0
    total_tokens_spent: int = 0
    source_stats: dict[str, SourceStats] = field(default_factory=dict)
    top_sources: list[str] = field(default_factory=list)        # best help_rate
    wasteful_sources: list[str] = field(default_factory=list)   # tokens spent, never helped

    def render_text(self) -> str:
        """Human-readable report."""
        if not self.source_stats:
            return 'No intelligence telemetry data.'

        lines = ['## 🔬 Intelligence Effectiveness Report']
        lines.append(f'Iterations tracked: {self.total_iterations}')
        lines.append(f'Total tokens spent: {self.total_tokens_spent:,}')
        lines.append('')
        lines.append('| Source | Injections | Helped | Neutral | Hurt | Help% | Avg Tokens |')
        lines.append('|--------|-----------|--------|---------|------|-------|------------|')

        for name in sorted(self.source_stats, key=lambda s: self.source_stats[s].help_rate, reverse=True):
            st = self.source_stats[name]
            rate = f'{st.help_rate:.0%}'
            lines.append(
                f'| {name} | {st.total_injections} | {st.times_helped} | '
                f'{st.times_neutral} | {st.times_hurt} | {rate} | {st.avg_tokens} |'
            )

        if self.top_sources:
            lines.append(f'\n**Top sources:** {", ".join(self.top_sources)}')
        if self.wasteful_sources:
            lines.append(f'**Wasteful (consider dropping):** {", ".join(self.wasteful_sources)}')

        return '\n'.join(lines)


class TelemetryAggregator:
    """Collects injection records and produces effectiveness reports.

    Usage::

        agg = TelemetryAggregator()
        # After each iteration:
        agg.record_iteration(sections, score_before, score_after, iteration)
        # After run:
        report = agg.build_report()
    """

    def __init__(self) -> None:
        self._records: list[InjectionRecord] = []
        self._iterations_seen: set[int] = set()

    def record_iteration(
        self,
        sections: list[dict[str, Any]],
        score_before: int,
        score_after: int,
        iteration: int,
    ) -> None:
        """Record which sections were injected and their outcomes.

        ``sections`` should be a list of dicts with keys:
        name, tokens_estimated, was_compressed, was_dropped.
        """
        self._iterations_seen.add(iteration)

        for sec in sections:
            name = sec.get('name', '')
            if not name:
                continue
            self._records.append(InjectionRecord(
                source=name,
                tokens_used=sec.get('tokens_estimated', 0),
                was_compressed=sec.get('was_compressed', False),
                was_dropped=sec.get('was_dropped', False),
                iteration=iteration,
                score_before=score_before,
                score_after=score_after,
            ))

    def build_report(self) -> IntelligenceReport:
        """Aggregate all records into an effectiveness report."""
        report = IntelligenceReport(
            total_iterations=len(self._iterations_seen),
        )

        stats: dict[str, SourceStats] = {}

        for rec in self._records:
            if rec.source not in stats:
                stats[rec.source] = SourceStats(source=rec.source)
            st = stats[rec.source]
            st.total_injections += 1
            st.total_tokens += rec.tokens_used

            if rec.was_dropped:
                st.times_dropped += 1
            if rec.was_compressed:
                st.times_compressed += 1

            if not rec.was_dropped:
                if rec.score_delta > 0:
                    st.times_helped += 1
                elif rec.score_delta < 0:
                    st.times_hurt += 1
                else:
                    st.times_neutral += 1

        report.source_stats = stats
        report.total_tokens_spent = sum(st.total_tokens for st in stats.values())

        # Identify top and wasteful sources
        for name, st in stats.items():
            if st.total_injections >= 2 and st.help_rate >= 0.5:
                report.top_sources.append(name)
            if st.total_injections >= 3 and st.times_helped == 0 and st.total_tokens > 100:
                report.wasteful_sources.append(name)

        return report

    def get_priority_adjustment(self, source_name: str) -> int:
        """Suggest priority adjustment for a source based on telemetry.

        Returns: negative = boost priority, positive = lower priority, 0 = no change.
        """
        recs = [r for r in self._records if r.source == source_name and not r.was_dropped]
        if len(recs) < 2:
            return 0

        help_rate = sum(1 for r in recs if r.helped) / len(recs)
        if help_rate >= 0.6:
            return -1  # boost
        if help_rate == 0 and len(recs) >= 3:
            return 1   # demote
        return 0

    def save(self, path: Path) -> None:
        """Persist telemetry records to JSON."""
        path.parent.mkdir(parents=True, exist_ok=True)
        data = [
            {
                'source': r.source,
                'tokens_used': r.tokens_used,
                'was_compressed': r.was_compressed,
                'was_dropped': r.was_dropped,
                'iteration': r.iteration,
                'score_before': r.score_before,
                'score_after': r.score_after,
            }
            for r in self._records
        ]
        path.write_text(json.dumps(data, indent=2), encoding='utf-8')

    @classmethod
    def load(cls, path: Path) -> TelemetryAggregator:
        """Load telemetry from a previous run."""
        agg = cls()
        if not path.exists():
            return agg
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            for item in data:
                agg._records.append(InjectionRecord(**item))
                agg._iterations_seen.add(item.get('iteration', 0))
        except (OSError, json.JSONDecodeError, TypeError):
            logger.debug('Failed to load telemetry from %s', path)
        return agg
