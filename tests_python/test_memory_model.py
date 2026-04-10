"""Tests for 5-tier memory model: task_ledger, archive_retrieval, commitment summary."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from copilot_operator.archive_retrieval import (
    ArchiveHit,
    _score_text,
    extract_keywords,
    query_archive,
    render_archive_hits_for_prompt,
)
from copilot_operator.narrative import build_commitment_summary
from copilot_operator.task_ledger import (
    Commitments,
    LedgerEntry,
    TaskLedger,
    build_commitments,
    load_ledger,
    render_ledger_for_prompt,
    save_ledger,
    update_ledger_from_iteration,
)

# ═══════════════════════════════════════════════════════════════════
# Task Ledger
# ═══════════════════════════════════════════════════════════════════

class TestTaskLedgerDataclasses(unittest.TestCase):
    def test_empty_ledger(self):
        ledger = TaskLedger()
        self.assertEqual(ledger.entries, [])
        self.assertEqual(ledger.outcome, '')

    def test_ledger_entry(self):
        e = LedgerEntry(iteration=1, action='Fix bug', result='pass', score=85)
        self.assertEqual(e.iteration, 1)
        self.assertEqual(e.score, 85)

    def test_commitments(self):
        c = Commitments(decided=['Use pytest'], owed=['Add integration tests'],
                        next_step='Run CI', must_not_forget=['Lint is failing'])
        self.assertEqual(len(c.decided), 1)
        self.assertEqual(c.next_step, 'Run CI')


class TestTaskLedgerPersistence(unittest.TestCase):
    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            ledger = TaskLedger(
                run_id='test-run-1',
                goal='Fix auth bug',
                acceptance_criteria=['All tests pass', 'No regressions'],
                plan_milestones=['Diagnose', 'Fix', 'Verify'],
                decisions=['Use session tokens'],
                outcome='complete',
            )
            ledger.entries.append(LedgerEntry(
                iteration=1, action='Fixed login', result='pass', score=90,
            ))
            ledger.commitments = Commitments(
                decided=['Use JWT'], owed=[], next_step='Done',
                must_not_forget=['Check rate limiting'],
            )
            save_ledger(ws, ledger)
            loaded = load_ledger(ws, 'test-run-1')
            self.assertEqual(loaded.goal, 'Fix auth bug')
            self.assertEqual(loaded.outcome, 'complete')
            self.assertEqual(len(loaded.entries), 1)
            self.assertEqual(loaded.entries[0].score, 90)
            self.assertEqual(loaded.commitments.decided, ['Use JWT'])
            self.assertEqual(loaded.commitments.must_not_forget, ['Check rate limiting'])

    def test_load_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = load_ledger(Path(tmp), 'nonexistent')
            self.assertEqual(ledger.run_id, 'nonexistent')
            self.assertEqual(ledger.entries, [])


class TestUpdateLedger(unittest.TestCase):
    def test_update_from_iteration(self):
        ledger = TaskLedger(run_id='r1', goal='Test')
        record = {
            'summary': 'Fixed the auth module',
            'status': 'continue',
            'score': 75,
            'decisionCode': 'CONTINUE',
            'blockers': [{'item': 'lint failing', 'severity': 'medium'}],
            'changedFiles': ['auth.py', 'test_auth.py'],
            'validation_after': [
                {'name': 'pytest', 'status': 'pass'},
                {'name': 'ruff', 'status': 'fail'},
            ],
        }
        update_ledger_from_iteration(ledger, 1, record)
        self.assertEqual(len(ledger.entries), 1)
        self.assertEqual(ledger.entries[0].score, 75)
        self.assertEqual(ledger.entries[0].blockers, ['lint failing'])
        self.assertIn('auth.py', ledger.files_touched)
        self.assertIn('pytest:pass', ledger.validations_run)
        self.assertIn('ruff:fail', ledger.validations_run)

    def test_files_dedup(self):
        ledger = TaskLedger(run_id='r1')
        record1 = {'changedFiles': ['a.py'], 'summary': '', 'status': 'ok', 'decisionCode': 'C'}
        record2 = {'changedFiles': ['a.py', 'b.py'], 'summary': '', 'status': 'ok', 'decisionCode': 'C'}
        update_ledger_from_iteration(ledger, 1, record1)
        update_ledger_from_iteration(ledger, 2, record2)
        self.assertEqual(ledger.files_touched, ['a.py', 'b.py'])


class TestBuildCommitments(unittest.TestCase):
    def test_complete_run(self):
        ledger = TaskLedger(run_id='r1', decisions=['Use fixtures'])
        ledger.validations_run = ['pytest:pass', 'ruff:pass']
        result = {'status': 'complete', 'reasonCode': 'STOP_GATE_PASSED'}
        c = build_commitments(ledger, result)
        self.assertEqual(c.next_step, 'Task complete.')
        self.assertIn('Use fixtures', c.decided)

    def test_blocked_run(self):
        ledger = TaskLedger(run_id='r1')
        ledger.entries.append(LedgerEntry(iteration=1, blockers=['Cannot install deps']))
        ledger.validations_run = ['pytest:fail']
        result = {
            'status': 'blocked',
            'reasonCode': 'MAX_ITERATIONS_REACHED',
            'reason': 'Hit iteration limit',
            'plan': {'milestones': [{'tasks': [
                {'title': 'Fix deps', 'status': 'todo'},
                {'title': 'Run tests', 'status': 'todo'},
            ]}]},
        }
        c = build_commitments(ledger, result)
        self.assertIn('Cannot install deps', c.must_not_forget)
        self.assertIn('pytest:fail', c.must_not_forget)
        self.assertIn('Fix deps', c.owed)
        self.assertIn('MAX_ITERATIONS_REACHED', c.next_step)


class TestRenderLedger(unittest.TestCase):
    def test_empty(self):
        ledger = TaskLedger(run_id='r1')
        self.assertEqual(render_ledger_for_prompt(ledger), '')

    def test_with_entries(self):
        ledger = TaskLedger(run_id='r1', goal='Fix bug')
        ledger.entries.append(LedgerEntry(iteration=1, score=70, decision_code='CONTINUE'))
        ledger.entries.append(LedgerEntry(iteration=2, score=85, decision_code='STOP_GATE_PASSED',
                                          blockers=['lint issue']))
        ledger.commitments = Commitments(
            decided=['Use pytest'], owed=['Add docs'],
            next_step='Write docs', must_not_forget=['Lint broken'],
        )
        text = render_ledger_for_prompt(ledger)
        self.assertIn('Task Ledger', text)
        self.assertIn('Fix bug', text)
        self.assertIn('i1:70', text)
        self.assertIn('i2:85', text)
        self.assertIn('lint issue', text)
        self.assertIn('Use pytest', text)
        self.assertIn('Add docs', text)
        self.assertIn('Lint broken', text)


# ═══════════════════════════════════════════════════════════════════
# Archive Retrieval
# ═══════════════════════════════════════════════════════════════════

class TestExtractKeywords(unittest.TestCase):
    def test_basic(self):
        kws = extract_keywords('Fix authentication bug in login module')
        self.assertIn('authentication', kws)
        self.assertIn('login', kws)
        self.assertIn('module', kws)
        # Stop words removed
        self.assertNotIn('fix', kws)
        self.assertNotIn('the', kws)

    def test_short_words_skipped(self):
        kws = extract_keywords('if x is ok')
        self.assertEqual(kws, [])

    def test_dedup(self):
        kws = extract_keywords('auth auth auth module module')
        self.assertEqual(kws.count('auth'), 1)


class TestScoreText(unittest.TestCase):
    def test_all_match(self):
        self.assertEqual(_score_text('auth login module', ['auth', 'login', 'module']), 1.0)

    def test_partial(self):
        self.assertAlmostEqual(_score_text('auth works fine', ['auth', 'login']), 0.5)

    def test_none(self):
        self.assertEqual(_score_text('hello world', ['auth']), 0.0)

    def test_empty(self):
        self.assertEqual(_score_text('', ['auth']), 0.0)
        self.assertEqual(_score_text('auth', []), 0.0)


class TestArchiveQuery(unittest.TestCase):
    def test_query_empty_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            hits = query_archive(Path(tmp), 'Fix authentication bug')
            self.assertEqual(hits, [])

    def test_query_with_narratives(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            log_dir = ws / '.copilot-operator' / 'logs' / 'run-old'
            log_dir.mkdir(parents=True)
            (log_dir / 'narrative.md').write_text(
                '## Run\n**Goal:** Fix authentication module\n**Outcome:** complete\nscore: 90\n'
                'Fixed the JWT validation and added rate limiting to the auth endpoint.',
                encoding='utf-8',
            )
            hits = query_archive(ws, 'Fix authentication and JWT validation')
            self.assertTrue(len(hits) >= 1)
            self.assertEqual(hits[0].run_id, 'run-old')
            self.assertIn('complete', hits[0].outcome)

    def test_query_with_ledgers(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            log_dir = ws / '.copilot-operator' / 'logs' / 'run-prev'
            log_dir.mkdir(parents=True)
            ledger_data = {
                'goal': 'Implement rate limiting for API',
                'decisions': ['Use token bucket algorithm'],
                'files_touched': ['middleware.py'],
                'stop_reason': '',
                'commitments': {
                    'decided': ['Token bucket strategy'],
                    'owed': [],
                    'must_not_forget': ['Redis dependency required'],
                },
            }
            (log_dir / 'ledger.json').write_text(json.dumps(ledger_data), encoding='utf-8')
            hits = query_archive(ws, 'rate limiting token bucket')
            self.assertTrue(len(hits) >= 1)
            self.assertEqual(hits[0].run_id, 'run-prev')

    def test_exclude_current_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            for rid in ('run-a', 'run-b'):
                d = ws / '.copilot-operator' / 'logs' / rid
                d.mkdir(parents=True)
                (d / 'narrative.md').write_text(
                    '**Goal:** Auth fix\n**Outcome:** complete\nscore: 85\nauth module fix',
                    encoding='utf-8',
                )
            hits = query_archive(ws, 'auth module', exclude_run_id='run-a')
            run_ids = [h.run_id for h in hits]
            self.assertNotIn('run-a', run_ids)
            self.assertIn('run-b', run_ids)

    def test_dedup_per_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            d = ws / '.copilot-operator' / 'logs' / 'run-x'
            d.mkdir(parents=True)
            (d / 'narrative.md').write_text('auth validation module fix', encoding='utf-8')
            (d / 'ledger.json').write_text(
                json.dumps({'goal': 'auth validation', 'decisions': [], 'files_touched': [],
                            'stop_reason': '', 'commitments': {}}),
                encoding='utf-8',
            )
            hits = query_archive(ws, 'auth validation module')
            # Should be deduped to 1 hit per run
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0].run_id, 'run-x')


class TestRenderArchiveHits(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(render_archive_hits_for_prompt([]), '')

    def test_with_hits(self):
        hits = [
            ArchiveHit(run_id='r1', source='narrative', relevance=0.8,
                       snippet='Fixed auth module...', goal='Fix auth', outcome='complete', score=90),
            ArchiveHit(run_id='r2', source='ledger', relevance=0.5,
                       snippet='Rate limiting done', goal='Add rate limit', outcome='blocked'),
        ]
        text = render_archive_hits_for_prompt(hits)
        self.assertIn('Relevant Past Runs', text)
        self.assertIn('Fix auth', text)
        self.assertIn('score=90', text)
        self.assertIn('Add rate limit', text)


# ═══════════════════════════════════════════════════════════════════
# Commitment Summary (narrative.py)
# ═══════════════════════════════════════════════════════════════════

class TestBuildCommitmentSummary(unittest.TestCase):
    def _make_runtime(self, history, plan=None):
        return {'history': history, 'plan': plan or {}}

    def test_complete_run(self):
        history = [
            {'iteration': 1, 'score': 70, 'summary': 'Initial fix', 'blockers': [],
             'validation_after': [{'name': 'pytest', 'status': 'pass'}]},
            {'iteration': 2, 'score': 90, 'summary': 'Polished', 'blockers': [],
             'validation_after': [{'name': 'pytest', 'status': 'pass'}]},
        ]
        result = {'status': 'complete', 'reasonCode': 'STOP_GATE_PASSED', 'score': 90}
        summary = build_commitment_summary('Fix bug', self._make_runtime(history), result)
        self.assertEqual(summary['status'], 'complete')
        self.assertEqual(summary['score'], 90)
        self.assertIn('complete', summary['next_step'].lower())
        self.assertEqual(summary['owed'], [])

    def test_blocked_run(self):
        history = [
            {'iteration': 1, 'score': 50, 'summary': 'Started', 'blockers': [{'item': 'Cannot install'}],
             'validation_after': [{'name': 'ruff', 'status': 'fail'}]},
        ]
        result = {'status': 'blocked', 'reasonCode': 'MAX_ITERATIONS'}
        summary = build_commitment_summary('Fix', self._make_runtime(history), result)
        self.assertIn('MAX_ITERATIONS', summary['next_step'])
        self.assertTrue(any('Cannot install' in m for m in summary['must_not_forget']))
        self.assertTrue(any('ruff' in m for m in summary['must_not_forget']))

    def test_score_regression(self):
        history = [
            {'iteration': 1, 'score': 80, 'summary': 'Good', 'blockers': [], 'validation_after': []},
            {'iteration': 2, 'score': 60, 'summary': 'Worse', 'blockers': [], 'validation_after': []},
        ]
        result = {'status': 'blocked', 'reasonCode': 'REGRESSION'}
        summary = build_commitment_summary('Fix', self._make_runtime(history), result)
        self.assertTrue(any('regress' in m.lower() for m in summary['must_not_forget']))

    def test_owed_from_plan(self):
        history = [{'iteration': 1, 'score': 50, 'summary': 'x', 'blockers': [], 'validation_after': []}]
        plan = {'milestones': [{'tasks': [
            {'title': 'Setup', 'status': 'done'},
            {'title': 'Implement', 'status': 'todo'},
            {'title': 'Test', 'status': 'todo'},
        ]}]}
        result = {'status': 'blocked', 'reasonCode': 'MAX_ITER'}
        summary = build_commitment_summary('Build feature', self._make_runtime(history, plan), result)
        self.assertIn('Implement', summary['owed'])
        self.assertIn('Test', summary['owed'])
        self.assertNotIn('Setup', summary['owed'])


if __name__ == '__main__':
    unittest.main()
