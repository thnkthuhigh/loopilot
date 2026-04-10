"""Tests for the 3 critical memory upgrades: BM25 retrieval, priority pressure, drift guard."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from copilot_operator.archive_retrieval import (
    _bm25_score_single,
    _ngram_boost,
    _score_text,
    query_archive,
)
from copilot_operator.mission_memory import (
    DriftCheckResult,
    Mission,
    MissionObjective,
    check_mission_drift,
    render_drift_correction,
)
from copilot_operator.task_ledger import (
    LedgerEntry,
    TaskLedger,
    compute_priority_pressure,
    render_ledger_for_prompt,
)

# ═══════════════════════════════════════════════════════════════════
# 1. BM25 + N-gram Archive Retrieval
# ═══════════════════════════════════════════════════════════════════

class TestBM25Scoring(unittest.TestCase):
    def test_exact_match_scores_high(self):
        score = _score_text('authentication module login handler', ['authentication', 'login'])
        self.assertGreater(score, 0.3)

    def test_no_match_scores_very_low(self):
        score = _score_text('database migration schema', ['authentication', 'login'])
        # N-gram might give a tiny boost for shared trigrams, but score should be near zero
        self.assertLess(score, 0.1)

    def test_partial_match(self):
        partial = _score_text('authentication module with database', ['authentication', 'login'])
        full = _score_text('authentication login module handler', ['authentication', 'login'])
        self.assertGreater(full, partial)

    def test_term_frequency_matters(self):
        # More occurrences of a term → higher score (BM25 with saturation)
        low_tf = _score_text('auth works', ['auth'])
        high_tf = _score_text('auth auth auth auth works', ['auth'])
        self.assertGreaterEqual(high_tf, low_tf)

    def test_empty_inputs(self):
        self.assertEqual(_score_text('', ['auth']), 0.0)
        self.assertEqual(_score_text('auth', []), 0.0)


class TestNgramBoost(unittest.TestCase):
    def test_partial_word_match(self):
        # 'auth' is a prefix of 'authentication' — should get n-gram boost
        boost = _ngram_boost('auth module works', ['authentication'])
        self.assertGreater(boost, 0.0)

    def test_no_overlap(self):
        boost = _ngram_boost('xyz abc', ['authentication'])
        self.assertAlmostEqual(boost, 0.0, places=2)

    def test_full_overlap(self):
        boost = _ngram_boost('authentication handler', ['authentication'])
        self.assertGreater(boost, 0.1)

    def test_cap_at_03(self):
        boost = _ngram_boost('authentication authentication authentication', ['authentication'])
        self.assertLessEqual(boost, 0.3)


class TestBM25ScoreSingle(unittest.TestCase):
    def test_basic(self):
        tokens = ['auth', 'module', 'login', 'auth']
        score = _bm25_score_single(tokens, 'auth', avg_doc_len=4.0, idf=1.0)
        self.assertGreater(score, 0.0)

    def test_zero_tf(self):
        tokens = ['module', 'login']
        score = _bm25_score_single(tokens, 'auth', avg_doc_len=2.0, idf=1.0)
        self.assertEqual(score, 0.0)


class TestBM25ArchiveQuery(unittest.TestCase):
    def test_bm25_ranks_better_than_keyword(self):
        """BM25 should rank a document with rare relevant terms higher."""
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            # Run A: generic text with many common words
            d_a = ws / '.copilot-operator' / 'logs' / 'run-generic'
            d_a.mkdir(parents=True)
            (d_a / 'narrative.md').write_text(
                '**Goal:** Fix authentication\n**Outcome:** complete\n'
                'Fixed the authentication module and login handler. '
                'Also touched database and migration and schema and config.',
                encoding='utf-8',
            )
            # Run B: focused text with rare specific term
            d_b = ws / '.copilot-operator' / 'logs' / 'run-focused'
            d_b.mkdir(parents=True)
            (d_b / 'narrative.md').write_text(
                '**Goal:** Fix JWT validation\n**Outcome:** complete\n'
                'Fixed JWT token validation in auth middleware. '
                'The jsonwebtoken library had a breaking change.',
                encoding='utf-8',
            )
            # Query about JWT
            hits = query_archive(ws, 'Fix JWT token validation breaking change')
            self.assertTrue(len(hits) >= 1)
            # The focused run should rank higher for JWT-specific query
            if len(hits) >= 2:
                self.assertEqual(hits[0].run_id, 'run-focused')

    def test_ngram_catches_partial(self):
        """N-gram boost should help find 'auth' when searching for 'authentication'."""
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            d = ws / '.copilot-operator' / 'logs' / 'run-partial'
            d.mkdir(parents=True)
            (d / 'narrative.md').write_text(
                '**Goal:** Auth fix\n**Outcome:** complete\n'
                'Fixed the auth middleware. Token refresh works now.',
                encoding='utf-8',
            )
            hits = query_archive(ws, 'Fix authentication token refresh middleware')
            self.assertTrue(len(hits) >= 1)


# ═══════════════════════════════════════════════════════════════════
# 2. Priority Pressure
# ═══════════════════════════════════════════════════════════════════

class TestPriorityPressure(unittest.TestCase):
    def test_blocking_is_critical(self):
        ledger = TaskLedger(run_id='r1', goal='Fix bug')
        ledger.entries.append(LedgerEntry(
            iteration=1, score=50, blockers=['Cannot connect to DB'],
        ))
        pp = compute_priority_pressure(ledger, target_score=85)
        self.assertEqual(pp.urgency, 'critical')
        self.assertIn('Cannot connect to DB', pp.blocking_now)
        self.assertIn('Unblock', pp.focus)

    def test_failing_validation_is_high(self):
        ledger = TaskLedger(run_id='r1', goal='Fix')
        ledger.entries.append(LedgerEntry(iteration=1, score=60))
        ledger.validations_run = ['pytest:fail', 'ruff:pass']
        pp = compute_priority_pressure(ledger, target_score=85)
        self.assertEqual(pp.urgency, 'high')
        self.assertIn('pytest', pp.focus)

    def test_regression_is_critical(self):
        ledger = TaskLedger(run_id='r1', goal='Fix')
        ledger.entries.append(LedgerEntry(iteration=1, score=80))
        ledger.entries.append(LedgerEntry(iteration=2, score=60))
        pp = compute_priority_pressure(ledger, target_score=85)
        self.assertEqual(pp.urgency, 'critical')
        self.assertIn('regression', pp.focus.lower())

    def test_stuck_score(self):
        ledger = TaskLedger(run_id='r1', goal='Fix')
        ledger.entries.append(LedgerEntry(iteration=1, score=50))
        ledger.entries.append(LedgerEntry(iteration=2, score=50))
        ledger.entries.append(LedgerEntry(iteration=3, score=50))
        pp = compute_priority_pressure(ledger, target_score=85)
        self.assertIn('stall', pp.focus.lower())

    def test_normal_progress(self):
        ledger = TaskLedger(run_id='r1', goal='Fix')
        ledger.entries.append(LedgerEntry(iteration=1, score=70))
        pp = compute_priority_pressure(ledger, target_score=85)
        self.assertEqual(pp.urgency, 'normal')
        self.assertIn('gap', pp.focus.lower())

    def test_target_met(self):
        ledger = TaskLedger(run_id='r1', goal='Fix')
        ledger.entries.append(LedgerEntry(iteration=1, score=90))
        pp = compute_priority_pressure(ledger, target_score=85)
        self.assertEqual(pp.urgency, 'low')
        self.assertIn('completion', pp.focus.lower())

    def test_can_defer_docs(self):
        ledger = TaskLedger(run_id='r1', goal='Fix')
        ledger.entries.append(LedgerEntry(iteration=1, score=70))
        plan = {'milestones': [{'tasks': [
            {'title': 'Fix core bug', 'status': 'done'},
            {'title': 'Update README docs', 'status': 'todo'},
            {'title': 'Cleanup formatting', 'status': 'todo'},
            {'title': 'Add integration test', 'status': 'todo'},
        ]}]}
        pp = compute_priority_pressure(ledger, plan=plan, target_score=85)
        self.assertTrue(any('README' in d for d in pp.can_defer))
        self.assertTrue(any('Cleanup' in d or 'format' in d.lower() for d in pp.can_defer))

    def test_priority_in_rendered_prompt(self):
        ledger = TaskLedger(run_id='r1', goal='Fix auth')
        ledger.entries.append(LedgerEntry(
            iteration=1, score=40, blockers=['lint failing'],
        ))
        pp = compute_priority_pressure(ledger, target_score=85)
        text = render_ledger_for_prompt(ledger, priority=pp)
        self.assertIn('PRIORITY', text)
        self.assertIn('CRITICAL', text)
        self.assertIn('lint failing', text)


# ═══════════════════════════════════════════════════════════════════
# 3. Mission Drift Guard
# ═══════════════════════════════════════════════════════════════════

class TestMissionDriftCheck(unittest.TestCase):
    def _make_mission(self, **kwargs) -> Mission:
        defaults = {
            'project_name': 'TestProject',
            'direction': 'Build a REST API',
            'priorities': ['Fix authentication', 'Add rate limiting'],
            'hard_constraints': [
                'Do not delete production data',
                'Never deploy without tests passing',
            ],
            'objectives': [
                MissionObjective(id='OBJ-1', description='Fix authentication module', status='active'),
                MissionObjective(id='OBJ-2', description='Add rate limiting to API', status='active'),
            ],
        }
        defaults.update(kwargs)
        return Mission(**defaults)

    def test_aligned_goal(self):
        mission = self._make_mission()
        drift = check_mission_drift(mission, 'Fix authentication token validation')
        self.assertFalse(drift.drifted)

    def test_hard_constraint_violation(self):
        mission = self._make_mission()
        drift = check_mission_drift(
            mission,
            'Delete production data and redeploy',
            current_action='delete production database',
        )
        self.assertTrue(drift.drifted)
        self.assertEqual(drift.severity, 'severe')
        self.assertTrue(any('Hard constraint' in v for v in drift.violations))

    def test_goal_misaligned_with_objectives(self):
        mission = self._make_mission()
        drift = check_mission_drift(
            mission,
            'Refactor the entire CSS framework',
        )
        # This goal doesn't match any objective about auth or rate limiting
        self.assertTrue(drift.drifted)
        self.assertTrue(any('does not align' in v for v in drift.violations))

    def test_priority_misalignment(self):
        mission = self._make_mission()
        drift = check_mission_drift(
            mission,
            'Add dark mode theme to the dashboard',
        )
        self.assertTrue(drift.drifted)
        self.assertIn('mild', drift.severity)

    def test_no_objectives_no_drift(self):
        mission = Mission(direction='Build something')
        drift = check_mission_drift(mission, 'Do anything')
        # No objectives → can't drift from objectives
        self.assertFalse(any('does not align' in v for v in drift.violations))

    def test_empty_mission_no_crash(self):
        mission = Mission()
        drift = check_mission_drift(mission, 'Fix something')
        self.assertFalse(drift.drifted)

    def test_render_severe_correction(self):
        drift = DriftCheckResult(
            drifted=True,
            severity='severe',
            violations=['Hard constraint violated: "Do not delete production data"'],
            correction='STOP. Do NOT proceed.',
        )
        text = render_drift_correction(drift)
        self.assertIn('SEVERE', text)
        self.assertIn('STOP', text)
        self.assertIn('production data', text)

    def test_render_mild_correction(self):
        drift = DriftCheckResult(
            drifted=True,
            severity='mild',
            violations=['Goal does not align with active objectives'],
            correction='Consider realigning.',
            aligned_objective='Fix authentication module',
        )
        text = render_drift_correction(drift)
        self.assertIn('Warning', text)
        self.assertIn('authentication', text)

    def test_render_no_drift(self):
        drift = DriftCheckResult(drifted=False)
        text = render_drift_correction(drift)
        self.assertEqual(text, '')

    def test_hard_constraints_in_render(self):
        mission = self._make_mission()
        rendered = mission.render()
        self.assertIn('HARD CONSTRAINTS', rendered)
        self.assertIn('production data', rendered)


if __name__ == '__main__':
    unittest.main()
