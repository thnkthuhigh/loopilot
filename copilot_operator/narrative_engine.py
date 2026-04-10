"""Narrative Engine — the voice of the AI agent.

Translates internal agent thinking into structured, human-readable output
across 4 views:

  1. **Live View** — what is it doing right now?
  2. **Decision Trace** — what did it think and decide?
  3. **Run Summary** — what did it accomplish?
  4. **Memory View** — what does it remember?

Each view has a FIXED structure so output is predictable and parseable.
This is NOT a dashboard — it's the agent's ability to explain itself.
"""

from __future__ import annotations

__all__ = [
    'LiveNarrative',
    'DecisionTrace',
    'RunSummaryView',
    'NarrativeEngine',
]

from dataclasses import dataclass, field
from typing import Any

from .logging_config import get_logger

logger = get_logger(__name__)


# ===================================================================
# (1) LIVE VIEW — "nó đang làm gì"
# ===================================================================

@dataclass(slots=True)
class LiveNarrative:
    """Real-time snapshot: what the agent is doing RIGHT NOW."""
    # [STATE]
    task: str = ''
    milestone: str = ''
    iteration: int = 0
    max_iterations: int = 0
    worker_status: str = ''
    score: int = 0
    target_score: int = 0
    strategy: str = 'default'
    escalation_level: int = 0

    # [ACTION]
    just_did: str = ''
    next_action: str = ''

    # [PRESSURE]
    urgency: str = 'normal'
    blocking_now: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines: list[str] = []

        # State block
        status_icon = '🟢' if self.score >= self.target_score else ('🟡' if self.score > 0 else '⚪')
        lines.append(f'{status_icon} [{self.worker_status.upper() or "RUNNING"}] {self.task or "initializing..."}')
        if self.milestone:
            lines.append(f'  Milestone: {self.milestone}')
        lines.append(f'  Iteration {self.iteration}/{self.max_iterations} │ Score: {self.score}/{self.target_score}')

        if self.strategy != 'default':
            lines.append(f'  Strategy: {self.strategy} (level {self.escalation_level})')

        # Action block
        if self.just_did:
            lines.append(f'  → Done: {self.just_did}')
        if self.next_action:
            lines.append(f'  → Next: {self.next_action}')

        # Pressure
        if self.urgency in ('critical', 'high'):
            icon = '🚨' if self.urgency == 'critical' else '⚠️'
            lines.append(f'  {icon} Urgency: {self.urgency}')
        if self.blocking_now:
            for b in self.blocking_now[:3]:
                lines.append(f'  🔴 Blocked: {b}')

        return '\n'.join(lines)


# ===================================================================
# (2) DECISION TRACE — "nó đã nghĩ gì"
# ===================================================================

@dataclass(slots=True)
class DecisionTrace:
    """What the agent observed, hypothesized, decided, and rejected."""
    iteration: int = 0

    # [OBSERVATION]
    observation: str = ''          # what it saw (score, validation, loops)

    # [HYPOTHESIS]
    hypothesis: str = ''           # what it thinks is happening

    # [DECISION]
    decision: str = ''             # what it chose to do
    decision_code: str = ''        # machine-readable reason code

    # [ALTERNATIVES_REJECTED]
    alternatives_rejected: list[str] = field(default_factory=list)

    # [ACTUATION]
    actions_taken: list[str] = field(default_factory=list)  # hint actuator

    # [SELF_EVAL]
    score_delta: int = 0
    what_helped: str = ''
    what_hurt: str = ''
    correction: str = ''

    def render(self) -> str:
        lines = [f'── Iteration {self.iteration} ──']

        if self.observation:
            lines.append(f'[OBSERVATION] {self.observation}')
        if self.hypothesis:
            lines.append(f'[HYPOTHESIS]  {self.hypothesis}')
        if self.decision:
            code = f' ({self.decision_code})' if self.decision_code else ''
            lines.append(f'[DECISION]    {self.decision}{code}')
        if self.alternatives_rejected:
            lines.append('[REJECTED]    ' + ' │ '.join(self.alternatives_rejected))
        if self.actions_taken:
            lines.append('[ACTUATION]   ' + ', '.join(self.actions_taken))

        # Self-eval
        if self.score_delta != 0:
            icon = '📈' if self.score_delta > 0 else '📉'
            lines.append(f'[SELF-EVAL]   {icon} score {self.score_delta:+d}')
        if self.what_helped:
            lines.append(f'              ✅ {self.what_helped}')
        if self.what_hurt:
            lines.append(f'              ❌ {self.what_hurt}')
        if self.correction:
            lines.append(f'              🔧 {self.correction}')

        return '\n'.join(lines)


# ===================================================================
# (3) RUN SUMMARY — "nó đã làm được gì"
# ===================================================================

@dataclass(slots=True)
class RunSummaryView:
    """Concise post-run summary answering 'is it really done?'"""
    # [GOAL]
    goal: str = ''
    goal_profile: str = ''

    # [OUTCOME]
    outcome: str = ''              # success / partial / fail / blocked
    score: int = 0
    target_score: int = 0
    iterations: int = 0

    # [CHANGES]
    files_changed: list[str] = field(default_factory=list)

    # [VALIDATION]
    tests_passed: int = 0
    tests_failed: int = 0
    lint_status: str = ''

    # [WHY_DONE]
    stop_reason: str = ''
    stop_reason_code: str = ''
    conditions_met: list[str] = field(default_factory=list)
    conditions_unmet: list[str] = field(default_factory=list)

    # [RISKS]
    risks: list[str] = field(default_factory=list)

    # [NEXT]
    commitments_owed: list[str] = field(default_factory=list)
    next_step: str = ''

    def render(self) -> str:
        lines: list[str] = []

        # Goal
        outcome_icon = {'success': '✅', 'partial': '🟡', 'fail': '❌', 'blocked': '🔴'}.get(self.outcome, '⚪')
        lines.append(f'{outcome_icon} {self.goal}')
        lines.append(f'   Profile: {self.goal_profile} │ Score: {self.score}/{self.target_score} │ Iterations: {self.iterations}')

        # Changes
        if self.files_changed:
            count = len(self.files_changed)
            preview = ', '.join(self.files_changed[:5])
            if count > 5:
                preview += f' (+{count - 5} more)'
            lines.append(f'   Files: {preview}')

        # Validation
        if self.tests_passed or self.tests_failed:
            lines.append(f'   Tests: {self.tests_passed} passed, {self.tests_failed} failed')
        if self.lint_status:
            lines.append(f'   Lint: {self.lint_status}')

        # Why done
        lines.append(f'   Stop: {self.stop_reason} [{self.stop_reason_code}]')
        if self.conditions_met:
            for c in self.conditions_met:
                lines.append(f'   ✅ {c}')
        if self.conditions_unmet:
            for c in self.conditions_unmet:
                lines.append(f'   ❌ {c}')

        # Risks
        if self.risks:
            lines.append('   Risks:')
            for r in self.risks[:5]:
                lines.append(f'     ⚠ {r}')

        # Next
        if self.next_step:
            lines.append(f'   Next: {self.next_step}')
        if self.commitments_owed:
            for c in self.commitments_owed[:3]:
                lines.append(f'   📌 Owed: {c}')

        return '\n'.join(lines)


# ===================================================================
# (4) MEMORY VIEW — "nó nhớ gì"
# ===================================================================

@dataclass(slots=True)
class MemorySnapshot:
    """What the agent remembers about the project."""
    # Mission
    mission_direction: str = ''
    mission_phase: str = ''
    mission_priorities: list[str] = field(default_factory=list)
    mission_constraints: list[str] = field(default_factory=list)

    # Project memory
    success_rate: float = 0.0
    total_runs: int = 0
    common_blockers: list[str] = field(default_factory=list)
    architecture_insights: list[str] = field(default_factory=list)

    # Rules/traps
    active_rules: int = 0
    top_rules: list[str] = field(default_factory=list)

    # Recent lessons
    recent_lessons: list[str] = field(default_factory=list)

    # Strategy memory
    effective_strategies: list[str] = field(default_factory=list)
    failed_strategies: list[str] = field(default_factory=list)

    # Telemetry
    top_intel_sources: list[str] = field(default_factory=list)
    wasteful_sources: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines: list[str] = []

        # Mission
        if self.mission_direction:
            lines.append(f'🎯 Mission: {self.mission_direction}')
            if self.mission_phase:
                lines.append(f'   Phase: {self.mission_phase}')
            if self.mission_priorities:
                lines.append(f'   Priorities: {", ".join(self.mission_priorities[:3])}')
            if self.mission_constraints:
                lines.append(f'   Constraints: {", ".join(self.mission_constraints[:3])}')

        # Project knowledge
        if self.total_runs > 0:
            lines.append(f'📊 Project: {self.total_runs} runs, {self.success_rate:.0%} success')
            if self.common_blockers:
                lines.append(f'   Common blockers: {", ".join(self.common_blockers[:3])}')

        # Rules
        if self.active_rules > 0:
            lines.append(f'📏 Rules: {self.active_rules} active')
            for r in self.top_rules[:3]:
                lines.append(f'   • {r}')

        # Lessons
        if self.recent_lessons:
            lines.append('💡 Recent lessons:')
            for lesson in self.recent_lessons[:5]:
                lines.append(f'   • {lesson}')

        # Strategy memory
        if self.effective_strategies:
            lines.append(f'✅ Effective strategies: {", ".join(self.effective_strategies[:3])}')
        if self.failed_strategies:
            lines.append(f'❌ Failed strategies: {", ".join(self.failed_strategies[:3])}')

        # Telemetry
        if self.top_intel_sources:
            lines.append(f'🔬 Top intel: {", ".join(self.top_intel_sources[:3])}')
        if self.wasteful_sources:
            lines.append(f'🗑️ Wasteful: {", ".join(self.wasteful_sources[:3])}')

        return '\n'.join(lines) if lines else 'No memory data available.'


# ===================================================================
# NARRATIVE ENGINE — aggregates all 4 views
# ===================================================================

class NarrativeEngine:
    """Aggregates all intelligence sources into structured narrative views.

    This is the agent's voice — translating internal state into
    human-understandable output.
    """

    def __init__(self) -> None:
        self._decision_traces: list[DecisionTrace] = []

    def record_decision(self, trace: DecisionTrace) -> None:
        """Record a decision trace for the current run."""
        self._decision_traces.append(trace)

    def build_live(
        self,
        runtime: dict[str, Any],
        config: Any,
        adaptive_state: dict[str, Any] | None = None,
        pressure: Any | None = None,
    ) -> LiveNarrative:
        """Build live view from current runtime state."""
        history = runtime.get('history', [])
        latest = history[-1] if history else {}
        plan = runtime.get('plan') or {}
        pending = runtime.get('pendingDecision') or {}

        # Find current milestone/task
        current_milestone = ''
        current_task = ''
        for ms in plan.get('milestones', []):
            if ms.get('status') != 'done':
                current_milestone = ms.get('title', '')
                for task in ms.get('tasks', []):
                    if task.get('status') != 'done':
                        current_task = task.get('title', '')
                        break
                break

        live = LiveNarrative(
            task=current_task or str(runtime.get('goal', ''))[:80],
            milestone=current_milestone,
            iteration=len(history),
            max_iterations=getattr(config, 'max_iterations', 6),
            worker_status=str(runtime.get('status', 'running')),
            score=latest.get('score', 0) or 0,
            target_score=getattr(config, 'target_score', 85),
            just_did=latest.get('summary', '')[:120] if latest else '',
            next_action=str(pending.get('nextPrompt', ''))[:120],
        )

        # Adaptive strategy state
        if adaptive_state:
            live.strategy = adaptive_state.get('strategy', 'default')
            live.escalation_level = adaptive_state.get('escalation_level', 0)

        # Priority pressure
        if pressure:
            live.urgency = getattr(pressure, 'urgency', 'normal')
            live.blocking_now = list(getattr(pressure, 'blocking_now', []))[:3]

        return live

    def build_decision_trace(
        self,
        iteration: int,
        diagnosis: Any | None = None,
        eval_result: Any | None = None,
        actuation: Any | None = None,
        decision: Any | None = None,
    ) -> DecisionTrace:
        """Build decision trace from one iteration's intelligence."""
        trace = DecisionTrace(iteration=iteration)

        # From diagnosis
        if diagnosis:
            parts = []
            trend = getattr(diagnosis, 'score_trend', None)
            if trend:
                parts.append(f'Score {trend.direction} (Δ{trend.delta:+d})')
            loops = getattr(diagnosis, 'loops', [])
            detected = [lp for lp in loops if lp.detected]
            if detected:
                parts.append(f'Loop detected: {detected[0].kind}')
            parts.append(f'Risk: {getattr(diagnosis, "risk_level", "?")}')
            trace.observation = ' │ '.join(parts)

            hints = getattr(diagnosis, 'hints', [])
            if hints:
                chosen = [h for h in hints if h.action != 'none']
                if chosen:
                    trace.hypothesis = chosen[0].reason
                    trace.decision = f'{chosen[0].action}'
                    if chosen[0].suggested_profile:
                        trace.decision += f' → {chosen[0].suggested_profile}'

        # From self-eval
        if eval_result:
            trace.score_delta = getattr(eval_result, 'score', None) and eval_result.score.delta or 0
            trace.what_helped = getattr(eval_result, 'what_helped', '')
            trace.what_hurt = getattr(eval_result, 'what_hurt', '')
            trace.correction = getattr(eval_result, 'correction', '')

        # From actuation
        if actuation:
            trace.actions_taken = list(getattr(actuation, 'actions_taken', []))

        # From decision
        if decision:
            if not trace.decision:
                trace.decision = getattr(decision, 'reason', '')
            trace.decision_code = getattr(decision, 'reason_code', '')

        self._decision_traces.append(trace)
        return trace

    def build_summary(
        self,
        runtime: dict[str, Any],
        result: dict[str, Any],
        config: Any,
    ) -> RunSummaryView:
        """Build run summary from completed run data."""
        history = runtime.get('history', [])
        last = history[-1] if history else {}
        commitment = result.get('commitmentSummary') or {}

        # Parse validation from last iteration
        tests_pass = 0
        tests_fail = 0
        lint_status = ''
        for v in last.get('validation_after', []):
            name = v.get('name', '')
            status = v.get('status', '')
            if 'test' in name.lower():
                if status == 'pass':
                    tests_pass += 1
                else:
                    tests_fail += 1
            if 'lint' in name.lower():
                lint_status = status

        # Done explanation
        narrative_obj = result.get('_narrativeObj')
        conditions_met: list[str] = []
        conditions_unmet: list[str] = []
        risks: list[str] = []
        if narrative_obj and hasattr(narrative_obj, 'done_explanation') and narrative_obj.done_explanation:
            de = narrative_obj.done_explanation
            conditions_met = list(getattr(de, 'conditions_met', []))
            conditions_unmet = list(getattr(de, 'conditions_unmet', []))
            risks = list(getattr(de, 'risks', []))

        outcome = str(result.get('status', 'unknown'))
        if outcome == 'complete':
            outcome = 'success'
        elif outcome in ('blocked', 'error', 'escalated'):
            pass
        elif last.get('score', 0) and last['score'] >= getattr(config, 'target_score', 85):
            outcome = 'success'
        else:
            outcome = 'partial'

        return RunSummaryView(
            goal=str(runtime.get('goal', '')),
            goal_profile=str(runtime.get('goalProfile', '')),
            outcome=outcome,
            score=last.get('score', 0) or 0,
            target_score=getattr(config, 'target_score', 85),
            iterations=len(history),
            files_changed=list(runtime.get('allChangedFiles', [])),
            tests_passed=tests_pass,
            tests_failed=tests_fail,
            lint_status=lint_status,
            stop_reason=result.get('reason', ''),
            stop_reason_code=result.get('reasonCode', ''),
            conditions_met=conditions_met,
            conditions_unmet=conditions_unmet,
            risks=risks,
            commitments_owed=commitment.get('owed', []),
            next_step=commitment.get('next_step', ''),
        )

    def build_memory(
        self,
        mission: Any | None = None,
        insights: Any | None = None,
        rules: list[Any] | None = None,
        adaptive_state: dict[str, Any] | None = None,
        telemetry_report: Any | None = None,
    ) -> MemorySnapshot:
        """Build memory view from all memory sources."""
        mem = MemorySnapshot()

        # Mission
        if mission:
            mem.mission_direction = getattr(mission, 'direction', '')
            mem.mission_phase = getattr(mission, 'current_phase', '')
            mem.mission_priorities = list(getattr(mission, 'priorities', []))[:5]
            mem.mission_constraints = list(getattr(mission, 'hard_constraints', []))[:5]
            mem.recent_lessons = list(getattr(mission, 'lessons_learned', []))[-5:]

        # Project insights
        if insights:
            mem.success_rate = getattr(insights, 'success_rate', 0.0)
            mem.total_runs = getattr(insights, 'total_runs', 0)
            mem.common_blockers = list(getattr(insights, 'common_blockers', []))[:5]
            learnings = getattr(insights, 'learnings', [])
            if learnings:
                mem.architecture_insights = [
                    item.detail for item in learnings
                    if getattr(item, 'category', '') == 'architecture'
                ][:3]

        # Meta-learner rules
        if rules:
            mem.active_rules = len(rules)
            high_conf = sorted(rules, key=lambda r: getattr(r, 'confidence', 0), reverse=True)
            mem.top_rules = [
                f'[{getattr(r, "category", "?")}] {getattr(r, "guardrail", "")[:60]}'
                for r in high_conf[:3]
            ]

        # Adaptive strategy
        if adaptive_state:
            mem.effective_strategies = list(adaptive_state.get('effective', []))
            mem.failed_strategies = list(adaptive_state.get('failed', []))

        # Telemetry
        if telemetry_report:
            mem.top_intel_sources = list(getattr(telemetry_report, 'top_sources', []))
            mem.wasteful_sources = list(getattr(telemetry_report, 'wasteful_sources', []))

        return mem

    def get_decision_history(self) -> list[DecisionTrace]:
        """Return all decision traces from this run."""
        return list(self._decision_traces)

    def serialize_traces(self) -> list[dict[str, Any]]:
        """Serialize all decision traces to a list of dicts for JSON persistence."""
        return [
            {
                'iteration': t.iteration,
                'observation': t.observation,
                'hypothesis': t.hypothesis,
                'decision': t.decision,
                'decision_code': t.decision_code,
                'alternatives_rejected': t.alternatives_rejected,
                'actions_taken': t.actions_taken,
                'score_delta': t.score_delta,
                'what_helped': t.what_helped,
                'what_hurt': t.what_hurt,
                'correction': t.correction,
            }
            for t in self._decision_traces
        ]

    @classmethod
    def load_traces(cls, data: list[dict[str, Any]]) -> list[DecisionTrace]:
        """Load decision traces from serialized dicts."""
        return [
            DecisionTrace(
                iteration=d.get('iteration', 0),
                observation=d.get('observation', ''),
                hypothesis=d.get('hypothesis', ''),
                decision=d.get('decision', ''),
                decision_code=d.get('decision_code', ''),
                alternatives_rejected=d.get('alternatives_rejected', []),
                actions_taken=d.get('actions_taken', []),
                score_delta=d.get('score_delta', 0),
                what_helped=d.get('what_helped', ''),
                what_hurt=d.get('what_hurt', ''),
                correction=d.get('correction', ''),
            )
            for d in data
        ]

    def render_full_trace(self) -> str:
        """Render the complete decision trace for the run."""
        if not self._decision_traces:
            return 'No decisions recorded.'
        return '\n\n'.join(t.render() for t in self._decision_traces)

    def render_all_views(
        self,
        runtime: dict[str, Any],
        result: dict[str, Any],
        config: Any,
        mission: Any | None = None,
        insights: Any | None = None,
        rules: list[Any] | None = None,
        adaptive_state: dict[str, Any] | None = None,
        telemetry_report: Any | None = None,
    ) -> str:
        """Render all 4 views into a single comprehensive narrative."""
        sections = []

        # Summary
        summary = self.build_summary(runtime, result, config)
        sections.append('═══ RUN SUMMARY ═══')
        sections.append(summary.render())

        # Decision trace
        if self._decision_traces:
            sections.append('\n═══ DECISION TRACE ═══')
            sections.append(self.render_full_trace())

        # Memory
        mem = self.build_memory(mission, insights, rules, adaptive_state, telemetry_report)
        mem_text = mem.render()
        if mem_text and mem_text != 'No memory data available.':
            sections.append('\n═══ MEMORY ═══')
            sections.append(mem_text)

        return '\n'.join(sections)
