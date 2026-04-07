"""Tests for Features A-F: meta_learner, adversarial, github_integration,
snapshot, cross_repo_brain, intention_guard, and their wiring into operator.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Feature A: meta_learner
# ---------------------------------------------------------------------------
from copilot_operator.meta_learner import (
    MetaAnalysis,
    PromptRule,
    _detect_blocker_pattern,
    _detect_scope_creep,
    _detect_score_regression,
    _detect_validation_loop,
    analyse_run_for_rules,
    apply_meta_learning,
    load_rules,
    render_guardrails,
    save_rules,
)


class TestMetaLearnerDetectors(unittest.TestCase):
    def test_detect_score_regression(self):
        history = [
            {'iteration': 1, 'score': 60},
            {'iteration': 2, 'score': 40},
        ]
        rule = _detect_score_regression(history, 'run-abc')
        self.assertIsNotNone(rule)
        self.assertEqual(rule.category, 'test_regression')
        self.assertIn('score regression', rule.guardrail.lower())

    def test_no_regression_on_small_drop(self):
        history = [
            {'iteration': 1, 'score': 60},
            {'iteration': 2, 'score': 55},
        ]
        rule = _detect_score_regression(history, 'run-abc')
        self.assertIsNone(rule)

    def test_detect_validation_loop(self):
        history = [
            {'iteration': 1, 'validation_after': [{'name': 'lint', 'status': 'fail'}]},
            {'iteration': 2, 'validation_after': [{'name': 'lint', 'status': 'fail'}]},
        ]
        rule = _detect_validation_loop(history, 'run-abc')
        self.assertIsNotNone(rule)
        self.assertIn('lint', rule.guardrail.lower())

    def test_detect_scope_creep(self):
        history = [
            {'iteration': 1, 'summary': 'fixed the parser module for JSON handling'},
            {'iteration': 2, 'summary': 'updated the parser tests and edge cases'},
            {'iteration': 3, 'summary': 'completely rewrote database migration system'},
        ]
        rule = _detect_scope_creep(history, 'run-abc')
        self.assertIsNotNone(rule)
        self.assertEqual(rule.category, 'scope_creep')

    def test_detect_blocker_pattern(self):
        history = [
            {'iteration': 1, 'blockers': [{'item': 'missing auth token'}]},
            {'iteration': 2, 'blockers': [{'item': 'missing auth token'}]},
        ]
        rule = _detect_blocker_pattern(history, 'run-abc')
        self.assertIsNotNone(rule)
        self.assertIn('missing auth token', rule.guardrail)


class TestMetaLearnerPersistence(unittest.TestCase):
    def test_save_and_load_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            rules = [
                PromptRule(rule_id='r1', trigger='test', guardrail='do X', category='test', confidence=0.8),
            ]
            save_rules(workspace, rules)
            loaded = load_rules(workspace)
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].rule_id, 'r1')
            self.assertEqual(loaded[0].confidence, 0.8)

    def test_load_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            rules = load_rules(Path(tmp))
            self.assertEqual(rules, [])


class TestMetaLearnerAnalysis(unittest.TestCase):
    def test_analyse_run_for_rules(self):
        history = [
            {'iteration': 1, 'score': 70, 'validation_after': [], 'summary': 'step 1', 'blockers': []},
            {'iteration': 2, 'score': 50, 'validation_after': [], 'summary': 'step 2', 'blockers': []},
        ]
        analysis = analyse_run_for_rules(history, 'run-1', 'blocked', [])
        self.assertIsInstance(analysis, MetaAnalysis)
        self.assertGreater(len(analysis.new_rules), 0)

    def test_apply_meta_learning_cycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            history = [
                {'iteration': 1, 'score': 70, 'validation_after': [], 'summary': 'x', 'blockers': []},
                {'iteration': 2, 'score': 40, 'validation_after': [], 'summary': 'y', 'blockers': []},
            ]
            analysis = apply_meta_learning(workspace, history, 'run-1', 'blocked')
            self.assertIsInstance(analysis, MetaAnalysis)
            rules = load_rules(workspace)
            self.assertGreater(len(rules), 0)


class TestMetaLearnerRender(unittest.TestCase):
    def test_render_guardrails(self):
        rules = [PromptRule(rule_id='r1', trigger='t', guardrail='Do not break tests', category='test')]
        text = render_guardrails(rules)
        self.assertIn('Do not break tests', text)

    def test_render_empty(self):
        self.assertEqual(render_guardrails([]), '')


# ---------------------------------------------------------------------------
# Feature B: adversarial
# ---------------------------------------------------------------------------

from copilot_operator.adversarial import (
    build_critic_prompt,
    build_fix_baton_from_critic,
    parse_critic_report,
    should_run_critic,
)


class TestAdversarial(unittest.TestCase):
    def test_build_critic_prompt(self):
        prompt = build_critic_prompt(
            goal='Fix bug #42',
            coder_summary='Fixed the null pointer',
            coder_score=75,
        )
        self.assertIn('CODE CRITIC', prompt)
        self.assertIn('Fix bug #42', prompt)
        self.assertIn('CRITIC_REPORT', prompt)

    def test_parse_critic_report_valid(self):
        response = """Here is my review.
<CRITIC_REPORT>
{
  "overall_quality": "needs_work",
  "confidence": 0.8,
  "summary": "Missing edge case handling",
  "findings": [
    {
      "severity": "high",
      "category": "edge_case",
      "file_path": "src/parser.py",
      "line_hint": "near line 42",
      "description": "No null check",
      "suggestion": "Add a null check"
    }
  ]
}
</CRITIC_REPORT>"""
        report = parse_critic_report(response)
        self.assertIsNotNone(report)
        self.assertEqual(report.overall_quality, 'needs_work')
        self.assertEqual(len(report.findings), 1)
        self.assertEqual(report.findings[0].severity, 'high')

    def test_parse_critic_report_missing(self):
        self.assertIsNone(parse_critic_report('No report here'))

    def test_should_run_critic(self):
        # Should run after iteration 3 with high enough score
        self.assertTrue(should_run_critic(4, 80, 90))
        # Not on iteration 0 (below run_critic_after=1)
        self.assertFalse(should_run_critic(0, 80, 90))

    def test_build_fix_baton(self):
        from copilot_operator.adversarial import CriticFinding, CriticReport
        report = CriticReport(
            findings=[
                CriticFinding(severity='high', description='Missing null check', file_path='x.py', suggestion='Add check'),
            ],
            summary='Needs work',
            overall_quality='needs_work',
        )
        baton = build_fix_baton_from_critic(report, 'Fix bug #42')
        self.assertIn('Missing null check', baton)
        self.assertIn('x.py', baton)


# ---------------------------------------------------------------------------
# Feature C: github_integration
# ---------------------------------------------------------------------------

from copilot_operator.github_integration import (
    GitHubConfig,
    GitHubIssue,
    build_pr_body,
    issue_to_goal,
    load_github_config,
)


class TestGitHubIntegration(unittest.TestCase):
    def test_github_config_not_configured(self):
        config = GitHubConfig()
        self.assertFalse(config.is_configured)

    def test_github_config_configured(self):
        config = GitHubConfig(token='ghp_xxx', owner='org', repo='project')
        self.assertTrue(config.is_configured)

    def test_issue_to_goal(self):
        issue = GitHubIssue(number=42, title='Fix parsing bug', body='The parser crashes on empty input', labels=['bug'])
        goal = issue_to_goal(issue)
        self.assertIn('#42', goal)
        self.assertIn('Fix parsing bug', goal)
        self.assertIn('parser crashes', goal)
        self.assertIn('bug', goal)

    def test_build_pr_body(self):
        body = build_pr_body(
            goal='Fix #42',
            run_id='abc-123',
            iterations=3,
            score=95,
            reason_code='STOP_GATE_PASSED',
        )
        self.assertIn('Fix #42', body)
        self.assertIn('abc-123', body)
        self.assertIn('95', body)
        self.assertIn('Copilot Operator', body)

    def test_load_github_config_from_env(self):
        with patch.dict('os.environ', {'GITHUB_TOKEN': 'test-token'}):
            config = load_github_config({'owner': 'myorg', 'repo': 'myrepo'})
            self.assertEqual(config.token, 'test-token')
            self.assertEqual(config.owner, 'myorg')


# ---------------------------------------------------------------------------
# Feature D: snapshot
# ---------------------------------------------------------------------------

from copilot_operator.snapshot import (
    SnapshotManager,
    find_best_snapshot,
    should_rollback,
    should_snapshot,
    snapshot_summary,
)


class TestSnapshot(unittest.TestCase):
    def test_should_snapshot_default(self):
        self.assertTrue(should_snapshot(1))
        self.assertTrue(should_snapshot(5))

    def test_should_snapshot_interval(self):
        self.assertTrue(should_snapshot(2, interval=2))
        self.assertFalse(should_snapshot(3, interval=2))

    def test_should_rollback_regression(self):
        self.assertTrue(should_rollback(40, 60, regression_threshold=10))
        self.assertFalse(should_rollback(55, 60, regression_threshold=10))

    def test_should_rollback_none_scores(self):
        self.assertFalse(should_rollback(None, 60))
        self.assertFalse(should_rollback(40, None))

    def test_find_best_snapshot(self):
        from copilot_operator.snapshot import Snapshot
        mgr = SnapshotManager(snapshots=[
            Snapshot(snapshot_id='s1', iteration=1, score=50),
            Snapshot(snapshot_id='s2', iteration=2, score=80),
            Snapshot(snapshot_id='s3', iteration=3, score=60),
        ])
        best = find_best_snapshot(mgr)
        self.assertIsNotNone(best)
        self.assertEqual(best.snapshot_id, 's2')
        self.assertEqual(best.score, 80)

    def test_find_best_snapshot_none(self):
        mgr = SnapshotManager()
        self.assertIsNone(find_best_snapshot(mgr))

    def test_snapshot_summary_empty(self):
        mgr = SnapshotManager()
        text = snapshot_summary(mgr)
        self.assertIn('No snapshots', text)

    def test_snapshot_summary_with_data(self):
        from copilot_operator.snapshot import Snapshot
        mgr = SnapshotManager(
            snapshots=[Snapshot(snapshot_id='s1', iteration=1, score=70, reason='test')],
            last_good_score=70,
            last_good_iteration=1,
        )
        text = snapshot_summary(mgr)
        self.assertIn('s1', text)
        self.assertIn('70', text)


# ---------------------------------------------------------------------------
# Feature E: cross_repo_brain
# ---------------------------------------------------------------------------

from copilot_operator.cross_repo_brain import (
    SharedBrain,
    SharedInsight,
    contribute_insight,
    export_rules_as_insights,
    load_shared_brain,
    query_guardrails,
    query_insights,
    render_cross_repo_insights,
    save_shared_brain,
)


class TestCrossRepoBrain(unittest.TestCase):
    def test_contribute_insight(self):
        brain = SharedBrain()
        insight = contribute_insight(brain, 'repo-a', 'guardrail', 'Always run tests first')
        self.assertEqual(insight.source_repo, 'repo-a')
        self.assertEqual(len(brain.insights), 1)

    def test_contribute_duplicate(self):
        brain = SharedBrain()
        contribute_insight(brain, 'repo-a', 'guardrail', 'Always run tests first')
        contribute_insight(brain, 'repo-a', 'guardrail', 'Always run tests first')
        self.assertEqual(len(brain.insights), 1)
        self.assertEqual(brain.insights[0].hit_count, 2)

    def test_query_insights_filter_by_category(self):
        brain = SharedBrain(insights=[
            SharedInsight(insight_id='i1', source_repo='a', category='guardrail', text='g1', confidence=0.5, hit_count=1),
            SharedInsight(insight_id='i2', source_repo='a', category='trap', text='t1', confidence=0.5, hit_count=1),
        ])
        results = query_insights(brain, category='guardrail')
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].category, 'guardrail')

    def test_query_exclude_repo(self):
        brain = SharedBrain(insights=[
            SharedInsight(insight_id='i1', source_repo='a', category='guardrail', text='g1', confidence=0.5, hit_count=1),
            SharedInsight(insight_id='i2', source_repo='b', category='guardrail', text='g2', confidence=0.5, hit_count=1),
        ])
        results = query_insights(brain, exclude_repo='a')
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].source_repo, 'b')

    def test_query_guardrails(self):
        brain = SharedBrain(insights=[
            SharedInsight(insight_id='i1', source_repo='a', category='guardrail', text='g1', confidence=0.5, hit_count=1),
        ])
        results = query_guardrails(brain)
        self.assertEqual(len(results), 1)

    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            brain = SharedBrain(store_path=tmp)
            contribute_insight(brain, 'repo-x', 'trap', 'Watch for race conditions')
            save_shared_brain(brain)
            loaded = load_shared_brain(tmp)
            self.assertEqual(len(loaded.insights), 1)
            self.assertEqual(loaded.insights[0].text, 'Watch for race conditions')

    def test_render_cross_repo_insights(self):
        brain = SharedBrain(insights=[
            SharedInsight(insight_id='i1', source_repo='other-repo', category='guardrail', text='Run lint', confidence=0.7, hit_count=3),
        ])
        text = render_cross_repo_insights(brain, current_repo='my-repo')
        self.assertIn('Run lint', text)
        self.assertIn('other-repo', text)

    def test_render_empty(self):
        brain = SharedBrain()
        text = render_cross_repo_insights(brain)
        self.assertEqual(text, '')

    def test_export_rules_as_insights(self):
        brain = SharedBrain()
        rules = [
            {'trigger': 'score_regression', 'guardrail': 'Do not break tests', 'category': 'guardrail'},
            {'trigger': 'scope_creep', 'guardrail': 'Stay on task', 'category': 'guardrail'},
        ]
        count = export_rules_as_insights(brain, 'test-repo', rules)
        self.assertEqual(count, 2)
        self.assertEqual(len(brain.insights), 2)


# ---------------------------------------------------------------------------
# Feature F: intention_guard
# ---------------------------------------------------------------------------

from copilot_operator.intention_guard import (
    all_goal_types,
    get_intention_profile,
    render_combined_guardrails,
    render_intention_guardrails,
)


class TestIntentionGuard(unittest.TestCase):
    def test_all_goal_types(self):
        types = all_goal_types()
        self.assertIn('bug', types)
        self.assertIn('feature', types)
        self.assertIn('refactor', types)
        self.assertIn('audit', types)
        self.assertIn('docs', types)
        self.assertIn('stabilize', types)

    def test_get_known_profile(self):
        profile = get_intention_profile('bug')
        self.assertEqual(profile.goal_type, 'bug')
        self.assertGreater(len(profile.guardrails), 0)
        self.assertGreater(len(profile.anti_patterns), 0)

    def test_get_unknown_profile(self):
        profile = get_intention_profile('unknown_type_xyz')
        self.assertEqual(profile.goal_type, 'generic')

    def test_render_intention_guardrails(self):
        text = render_intention_guardrails('bug')
        self.assertIn('bug', text)
        self.assertIn('Guardrails', text)
        self.assertIn('Anti-Patterns', text)
        self.assertIn('Reproduce', text)

    def test_render_combined_guardrails(self):
        text = render_combined_guardrails('feature', ['Extra rule 1', 'Extra rule 2'])
        self.assertIn('feature', text)
        self.assertIn('Extra rule 1', text)
        self.assertIn('Dynamic Guardrails', text)

    def test_render_combined_no_extras(self):
        text = render_combined_guardrails('refactor')
        self.assertIn('refactor', text)
        self.assertNotIn('Dynamic', text)

    def test_each_profile_has_content(self):
        for goal_type in all_goal_types():
            profile = get_intention_profile(goal_type)
            self.assertGreater(len(profile.guardrails), 0, f'{goal_type} missing guardrails')
            self.assertGreater(len(profile.focus_areas), 0, f'{goal_type} missing focus areas')
            self.assertGreater(len(profile.anti_patterns), 0, f'{goal_type} missing anti-patterns')
            self.assertGreater(len(profile.recommended_steps), 0, f'{goal_type} missing steps')


# ---------------------------------------------------------------------------
# Integration: prompt context with new fields
# ---------------------------------------------------------------------------

from copilot_operator.prompts import PromptContext, build_prompt_context


class TestPromptContextExtended(unittest.TestCase):
    def test_prompt_context_has_new_fields(self):
        ctx = PromptContext(
            recent_history='',
            validation_snapshot='',
            repo_profile_text='',
            workspace_insight_text='',
            goal_profile_text='',
            plan_text='',
            intention_text='some guardrails',
            cross_repo_text='cross-repo data',
        )
        self.assertEqual(ctx.intention_text, 'some guardrails')
        self.assertEqual(ctx.cross_repo_text, 'cross-repo data')

    def test_build_prompt_context_new_params(self):
        from copilot_operator.config import RepoProfile
        from copilot_operator.repo_inspector import WorkspaceInsight
        ctx = build_prompt_context(
            history=[],
            validation_results=[],
            repo_profile=RepoProfile(),
            workspace=Path('.'),
            workspace_insight=WorkspaceInsight(),
            goal_profile='default',
            plan=None,
            intention_text='intention data',
            cross_repo_text='cross-repo data',
        )
        self.assertEqual(ctx.intention_text, 'intention data')
        self.assertEqual(ctx.cross_repo_text, 'cross-repo data')


# ---------------------------------------------------------------------------
# Integration: operator imports compile
# ---------------------------------------------------------------------------

class TestOperatorImportsCompile(unittest.TestCase):
    def test_imports(self):
        """Verify all new imports in operator.py actually resolve."""
        from copilot_operator import operator  # noqa: F401
        self.assertTrue(hasattr(operator, 'CopilotOperator'))


if __name__ == '__main__':
    unittest.main()
