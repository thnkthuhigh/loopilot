"""Tests for memory system v2 upgrades:
  - Semantic retrieval (TF-IDF cosine + context boost + decay)
  - Priority system (TaskPriority, PriorityQueue, escalation)
  - Mission authority (veto, directives, override)
"""

from __future__ import annotations

import unittest

from copilot_operator.archive_retrieval import (
    RelevanceSignal,
    _context_boost,
    _cosine_similarity,
    _relevance_decay,
    compute_semantic_similarity,
)
from copilot_operator.mission_memory import (
    Mission,
    MissionAuthority,
    MissionDirective,
    MissionObjective,
    MissionVeto,
)
from copilot_operator.task_ledger import (
    LedgerEntry,
    PrioritizedTask,
    PriorityQueue,
    TaskLedger,
    TaskPriority,
    build_priority_queue,
    escalate_priorities,
    render_ledger_for_prompt,
)

# ===================================================================
# Semantic Retrieval Tests
# ===================================================================

class TestCosineSimilarity(unittest.TestCase):
    def test_identical_vectors(self):
        vec = {'auth': 0.5, 'login': 0.3}
        self.assertAlmostEqual(_cosine_similarity(vec, vec), 1.0, places=5)

    def test_orthogonal_vectors(self):
        a = {'auth': 1.0}
        b = {'database': 1.0}
        self.assertEqual(_cosine_similarity(a, b), 0.0)

    def test_partial_overlap(self):
        a = {'auth': 0.5, 'login': 0.3, 'user': 0.2}
        b = {'auth': 0.4, 'database': 0.6}
        sim = _cosine_similarity(a, b)
        self.assertGreater(sim, 0.0)
        self.assertLess(sim, 1.0)

    def test_empty_vectors(self):
        self.assertEqual(_cosine_similarity({}, {'a': 1.0}), 0.0)
        self.assertEqual(_cosine_similarity({'a': 1.0}, {}), 0.0)
        self.assertEqual(_cosine_similarity({}, {}), 0.0)


class TestSemanticSimilarity(unittest.TestCase):
    def test_identical_text(self):
        sim = compute_semantic_similarity('fix authentication bug', 'fix authentication bug')
        self.assertGreater(sim, 0.8)

    def test_related_text(self):
        sim = compute_semantic_similarity(
            'fix authentication login issue',
            'auth login module broken needs repair',
        )
        self.assertGreater(sim, 0.0)

    def test_unrelated_text(self):
        sim = compute_semantic_similarity(
            'database migration schema upgrade',
            'react component styling css colors',
        )
        self.assertLess(sim, 0.3)

    def test_empty_text(self):
        self.assertEqual(compute_semantic_similarity('', 'something'), 0.0)
        self.assertEqual(compute_semantic_similarity('something', ''), 0.0)

    def test_with_corpus(self):
        sim = compute_semantic_similarity(
            'fix authentication login bug',
            'authentication login module has error and needs repair',
            corpus_texts=['database migration schema', 'ui component styling colors'],
        )
        self.assertGreater(sim, 0.0)


class TestContextBoost(unittest.TestCase):
    def test_same_goal_type(self):
        boost = _context_boost(
            'fix login bug', [], 'complete',
            'fix auth error', [], '',
        )
        self.assertGreater(boost, 0.0)  # both are "bug fix" type

    def test_file_overlap(self):
        boost = _context_boost(
            'some goal', ['src/auth.py', 'src/login.py'], 'complete',
            'other goal', ['src/auth.py', 'src/db.py'], '',
        )
        self.assertGreater(boost, 0.0)  # auth.py overlap

    def test_successful_outcome_bonus(self):
        boost_complete = _context_boost('goal', [], 'complete', 'goal', [], '')
        boost_error = _context_boost('goal', [], 'error', 'goal', [], '')
        self.assertGreater(boost_complete, boost_error)

    def test_no_overlap(self):
        boost = _context_boost(
            'database migration', [], 'error',
            'ui styling', [], '',
        )
        self.assertEqual(boost, 0.0)

    def test_capped_at_025(self):
        boost = _context_boost(
            'fix bug error crash', ['a.py', 'b.py', 'c.py', 'd.py'], 'complete',
            'fix bug error crash', ['a.py', 'b.py', 'c.py', 'd.py'], '',
        )
        self.assertLessEqual(boost, 0.25)


class TestRelevanceDecay(unittest.TestCase):
    def test_newest_no_decay(self):
        decay = _relevance_decay(9, 10)
        self.assertAlmostEqual(decay, 0.0, places=2)

    def test_oldest_has_decay(self):
        decay = _relevance_decay(0, 10)
        self.assertLess(decay, 0.0)

    def test_single_run_no_decay(self):
        self.assertEqual(_relevance_decay(0, 1), 0.0)

    def test_decay_is_negative(self):
        decay = _relevance_decay(0, 20)
        self.assertLess(decay, 0.0)
        self.assertGreaterEqual(decay, -0.3)


class TestRelevanceSignal(unittest.TestCase):
    def test_signal_fields(self):
        sig = RelevanceSignal(bm25=0.5, semantic=0.3, context_boost=0.1, decay=-0.05)
        self.assertEqual(sig.bm25, 0.5)
        self.assertEqual(sig.semantic, 0.3)
        self.assertEqual(sig.context_boost, 0.1)
        self.assertEqual(sig.decay, -0.05)


# ===================================================================
# Priority System Tests
# ===================================================================

class TestTaskPriority(unittest.TestCase):
    def test_priority_values(self):
        self.assertEqual(TaskPriority.P0_CRITICAL, 0)
        self.assertEqual(TaskPriority.P1_HIGH, 1)
        self.assertEqual(TaskPriority.P2_MEDIUM, 2)
        self.assertEqual(TaskPriority.P3_LOW, 3)

    def test_labels(self):
        self.assertEqual(TaskPriority.label(0), 'P0-CRITICAL')
        self.assertEqual(TaskPriority.label(3), 'P3-LOW')

    def test_icons(self):
        self.assertEqual(TaskPriority.icon(0), '🔴')
        self.assertEqual(TaskPriority.icon(3), '⚪')


class TestPriorityQueue(unittest.TestCase):
    def _make_queue(self):
        return PriorityQueue(tasks=[
            PrioritizedTask(id='t0', title='Fix tests', priority=1, status='pending'),
            PrioritizedTask(id='t1', title='Add docs', priority=3, status='pending'),
            PrioritizedTask(id='t2', title='Implement feature', priority=2, status='pending'),
            PrioritizedTask(id='t3', title='Critical bug', priority=0, status='pending'),
        ])

    def test_ordered_by_priority(self):
        q = self._make_queue()
        ordered = q.ordered()
        self.assertEqual(ordered[0].id, 't3')  # P0
        self.assertEqual(ordered[-1].id, 't1')  # P3

    def test_next_actionable(self):
        q = self._make_queue()
        nxt = q.next_actionable()
        self.assertEqual(nxt.id, 't3')  # P0 first

    def test_next_actionable_skips_done(self):
        q = self._make_queue()
        q.mark_done('t3')
        nxt = q.next_actionable()
        self.assertEqual(nxt.id, 't0')  # P1

    def test_next_actionable_respects_blockers(self):
        q = PriorityQueue(tasks=[
            PrioritizedTask(id='t0', title='A', priority=0, status='pending', blocked_by=['t1']),
            PrioritizedTask(id='t1', title='B', priority=1, status='pending'),
        ])
        nxt = q.next_actionable()
        self.assertEqual(nxt.id, 't1')  # t0 is blocked by t1

    def test_blocked_resolved(self):
        q = PriorityQueue(tasks=[
            PrioritizedTask(id='t0', title='A', priority=0, status='pending', blocked_by=['t1']),
            PrioritizedTask(id='t1', title='B', priority=1, status='done'),
        ])
        nxt = q.next_actionable()
        self.assertEqual(nxt.id, 't0')  # t1 is done, t0 unblocked

    def test_mark_done(self):
        q = self._make_queue()
        q.mark_done('t2')
        task = next(t for t in q.tasks if t.id == 't2')
        self.assertEqual(task.status, 'done')

    def test_defer(self):
        q = self._make_queue()
        q.defer('t1')
        task = next(t for t in q.tasks if t.id == 't1')
        self.assertEqual(task.status, 'deferred')

    def test_all_done_returns_none(self):
        q = PriorityQueue(tasks=[
            PrioritizedTask(id='t0', title='A', priority=0, status='done'),
        ])
        self.assertIsNone(q.next_actionable())


class TestBuildPriorityQueue(unittest.TestCase):
    def test_from_plan(self):
        plan = {
            'milestones': [{
                'tasks': [
                    {'title': 'Write tests', 'status': 'pending'},
                    {'title': 'Update README', 'status': 'pending'},
                    {'title': 'Implement feature', 'status': 'done'},
                ],
            }],
        }
        ledger = TaskLedger(entries=[LedgerEntry(iteration=1, score=50)])
        q = build_priority_queue(plan, ledger)
        self.assertEqual(len(q.tasks), 3)
        # Tests task → P1
        test_task = next(t for t in q.tasks if 'tests' in t.title.lower())
        self.assertEqual(test_task.priority, TaskPriority.P1_HIGH)
        # Docs task → P3
        doc_task = next(t for t in q.tasks if 'README' in t.title)
        self.assertEqual(doc_task.priority, TaskPriority.P3_LOW)

    def test_mission_priority_boost(self):
        plan = {
            'milestones': [{
                'tasks': [
                    {'title': 'Fix authentication module', 'status': 'pending'},
                    {'title': 'Add logging', 'status': 'pending'},
                ],
            }],
        }
        ledger = TaskLedger(entries=[LedgerEntry(iteration=1, score=50)])
        q = build_priority_queue(plan, ledger, mission_priorities=['Fix authentication module'])
        auth_task = next(t for t in q.tasks if 'authentication' in t.title.lower())
        self.assertLessEqual(auth_task.priority, TaskPriority.P1_HIGH)
        self.assertTrue(auth_task.mission_override)

    def test_empty_plan(self):
        q = build_priority_queue(None, TaskLedger())
        self.assertEqual(len(q.tasks), 0)


class TestEscalatePriorities(unittest.TestCase):
    def test_regression_escalates_blockers(self):
        q = PriorityQueue(tasks=[
            PrioritizedTask(id='t0', title='Fix lint error', priority=2, status='pending'),
        ])
        ledger = TaskLedger(entries=[
            LedgerEntry(iteration=1, score=80),
            LedgerEntry(iteration=2, score=70, blockers=['lint error']),
        ])
        changes = escalate_priorities(q, ledger)
        self.assertEqual(q.tasks[0].priority, TaskPriority.P0_CRITICAL)
        self.assertTrue(any('regression' in c.lower() for c in changes))

    def test_stuck_escalates_next(self):
        q = PriorityQueue(tasks=[
            PrioritizedTask(id='t0', title='Next task', priority=2, status='pending'),
        ])
        ledger = TaskLedger(entries=[
            LedgerEntry(iteration=1, score=60),
            LedgerEntry(iteration=2, score=60),
            LedgerEntry(iteration=3, score=60),
        ])
        changes = escalate_priorities(q, ledger)
        self.assertEqual(q.tasks[0].priority, TaskPriority.P1_HIGH)
        self.assertTrue(any('stuck' in c.lower() for c in changes))

    def test_dependency_cascade(self):
        q = PriorityQueue(tasks=[
            PrioritizedTask(id='t0', title='Core task', priority=2, status='pending',
                            blocks=['t1', 't2', 't3']),
        ])
        ledger = TaskLedger(entries=[LedgerEntry(iteration=1, score=50)])
        escalate_priorities(q, ledger)
        self.assertEqual(q.tasks[0].priority, TaskPriority.P1_HIGH)

    def test_time_pressure(self):
        q = PriorityQueue(tasks=[
            PrioritizedTask(id='t0', title='Some task', priority=2, status='pending'),
        ])
        ledger = TaskLedger(entries=[
            LedgerEntry(iteration=i, score=50) for i in range(1, 5)
        ])
        changes = escalate_priorities(q, ledger)
        self.assertEqual(q.tasks[0].priority, TaskPriority.P1_HIGH)
        self.assertGreater(len(changes), 0)

    def test_no_escalation_when_done(self):
        q = PriorityQueue(tasks=[
            PrioritizedTask(id='t0', title='Done task', priority=2, status='done'),
        ])
        ledger = TaskLedger(entries=[
            LedgerEntry(iteration=i, score=50) for i in range(1, 5)
        ])
        changes = escalate_priorities(q, ledger)
        self.assertEqual(q.tasks[0].priority, 2)  # unchanged
        self.assertEqual(len(changes), 0)


class TestRenderLedgerWithQueue(unittest.TestCase):
    def test_queue_in_rendered_output(self):
        ledger = TaskLedger(
            goal='Test goal',
            entries=[LedgerEntry(iteration=1, score=60, decision_code='CONTINUE')],
        )
        q = PriorityQueue(tasks=[
            PrioritizedTask(id='t0', title='Critical fix', priority=0, status='pending'),
            PrioritizedTask(id='t1', title='Add docs', priority=3, status='pending'),
        ])
        rendered = render_ledger_for_prompt(ledger, queue=q)
        self.assertIn('Task Queue', rendered)
        self.assertIn('Critical fix', rendered)
        self.assertIn('P0-CRITICAL', rendered)
        self.assertIn('P3-LOW', rendered)


# ===================================================================
# Mission Authority Tests
# ===================================================================

class TestMissionAuthority(unittest.TestCase):
    def _make_mission(self):
        return Mission(
            project_name='TestProject',
            direction='Build a secure authentication system',
            priorities=['Fix security vulnerabilities', 'Add test coverage'],
            hard_constraints=[
                'Do not deploy to production without review',
                'Never delete user data',
                'Must not bypass authentication checks',
            ],
            objectives=[
                MissionObjective(
                    id='obj1',
                    description='Implement OAuth2 login',
                    status='active',
                    success_criteria=['All tests pass', 'OAuth flow works'],
                ),
                MissionObjective(
                    id='obj2',
                    description='Add rate limiting',
                    status='active',
                ),
            ],
        )

    def test_directives_from_constraints(self):
        auth = MissionAuthority(self._make_mission())
        directives = auth.get_directives()
        constraint_directives = [d for d in directives if d.source == 'constraint']
        self.assertEqual(len(constraint_directives), 3)

    def test_directives_from_priorities(self):
        auth = MissionAuthority(self._make_mission())
        directives = auth.get_directives()
        priority_directives = [d for d in directives if d.source == 'mission' and 'PRIORITY' in d.instruction]
        self.assertGreater(len(priority_directives), 0)

    def test_directives_from_objectives(self):
        auth = MissionAuthority(self._make_mission())
        directives = auth.get_directives()
        obj_directives = [d for d in directives if 'OBJECTIVE' in d.instruction]
        self.assertEqual(len(obj_directives), 2)

    def test_veto_constraint_violation(self):
        auth = MissionAuthority(self._make_mission())
        veto = auth.evaluate_action('deploy to production now')
        self.assertTrue(veto.vetoed)
        self.assertIn('production', veto.reason.lower())

    def test_veto_delete(self):
        auth = MissionAuthority(self._make_mission())
        veto = auth.evaluate_action('delete all user data from the database')
        self.assertTrue(veto.vetoed)

    def test_no_veto_allowed_action(self):
        auth = MissionAuthority(self._make_mission())
        veto = auth.evaluate_action('add unit tests for login module')
        self.assertFalse(veto.vetoed)

    def test_force_priority(self):
        auth = MissionAuthority(self._make_mission())
        auth.force_priority('security', 0, 'Critical vulnerability')
        priorities = auth.get_forced_priorities()
        self.assertEqual(priorities['security'], 0)

    def test_authority_block_rendering(self):
        auth = MissionAuthority(self._make_mission())
        block = auth.render_authority_block()
        self.assertIn('MISSION AUTHORITY', block)
        self.assertIn('MUST NOT', block)
        self.assertIn('must follow', block.lower())

    def test_override_large_gap(self):
        auth = MissionAuthority(self._make_mission())
        override = auth.override_decision(
            'refactor the CSS styles',
            current_score=40,
            target_score=85,
            iteration=4,
        )
        self.assertIsNotNone(override)
        self.assertIn('OVERRIDE', override.instruction)
        self.assertIn('security', override.instruction.lower())

    def test_no_override_when_on_track(self):
        auth = MissionAuthority(self._make_mission())
        # Use action that matches the mission direction
        override = auth.override_decision(
            'fix security vulnerability in authentication system',
            current_score=80,
            target_score=85,
            iteration=2,
        )
        self.assertIsNone(override)

    def test_override_misaligned_action(self):
        auth = MissionAuthority(self._make_mission())
        override = auth.override_decision(
            'refactor database schema for performance',
            current_score=80,
            target_score=85,
            iteration=2,
        )
        # Direction is "Build a secure authentication system"
        # Action about database schema doesn't match
        self.assertIsNotNone(override)
        self.assertIn('REDIRECT', override.instruction)

    def test_veto_with_alternative(self):
        auth = MissionAuthority(self._make_mission())
        veto = auth.evaluate_action('deploy to production')
        self.assertTrue(veto.vetoed)
        self.assertTrue(veto.alternative)  # should suggest alternative

    def test_empty_mission_no_crash(self):
        auth = MissionAuthority(Mission())
        directives = auth.get_directives()
        self.assertEqual(len(directives), 0)
        veto = auth.evaluate_action('anything')
        self.assertFalse(veto.vetoed)
        block = auth.render_authority_block()
        self.assertEqual(block, '')


class TestMissionDirective(unittest.TestCase):
    def test_directive_fields(self):
        d = MissionDirective(
            id='test',
            instruction='Do X',
            reason='Because Y',
            priority=0,
            source='constraint',
        )
        self.assertEqual(d.id, 'test')
        self.assertEqual(d.priority, 0)


class TestMissionVeto(unittest.TestCase):
    def test_no_veto_default(self):
        v = MissionVeto()
        self.assertFalse(v.vetoed)

    def test_veto_with_details(self):
        v = MissionVeto(vetoed=True, reason='Blocked', constraint='No X', alternative='Do Y')
        self.assertTrue(v.vetoed)
        self.assertEqual(v.constraint, 'No X')


if __name__ == '__main__':
    unittest.main()
