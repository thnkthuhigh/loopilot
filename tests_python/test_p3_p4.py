"""Tests for P3-P4 features: dashboard, nightly, ROI, policy, issue sync."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from copilot_operator.dashboard import (
    DashboardSnapshot,
    load_dashboard_snapshot,
    render_dashboard,
)
from copilot_operator.github_integration import (
    GitHubIssue,
    build_pr_body,
    issue_to_goal,
)
from copilot_operator.nightly import (
    NightlyConfig,
    NightlyReport,
    collect_nightly_goals,
)
from copilot_operator.policy import (
    PolicyEngine,
    PolicyRule,
    PolicyVerdict,
    load_policy_rules_from_config,
)
from copilot_operator.roi import ROIMetrics, analyse_roi

# ---------------------------------------------------------------------------
# Dashboard tests
# ---------------------------------------------------------------------------

class TestDashboardSnapshot(unittest.TestCase):

    def test_load_from_state_file(self):
        with tempfile.NamedTemporaryFile(suffix='.json', mode='w', delete=False, encoding='utf-8') as f:
            json.dump({
                'status': 'running',
                'goal': 'fix bugs',
                'goalProfile': 'bug',
                'runId': 'abc-123',
                'targetScore': 85,
                'history': [
                    {'iteration': 1, 'score': 60, 'status': 'in_progress'},
                    {'iteration': 2, 'score': 75, 'status': 'in_progress'},
                ],
                'plan': {'summary': 'Fix all bugs'},
            }, f)
            path = Path(f.name)

        snap = load_dashboard_snapshot(path)
        self.assertIsNotNone(snap)
        self.assertEqual(snap.status, 'running')
        self.assertEqual(snap.goal, 'fix bugs')
        self.assertEqual(snap.score, 75)
        self.assertEqual(len(snap.score_history), 2)
        path.unlink()

    def test_load_missing_file_returns_none(self):
        snap = load_dashboard_snapshot(Path('/nonexistent/state.json'))
        self.assertIsNone(snap)

    def test_render_dashboard(self):
        snap = DashboardSnapshot(
            status='running',
            goal='Implement feature X',
            goal_profile='feature',
            run_id='abc-123',
            iteration=3,
            max_iterations=6,
            score=75,
            target_score=85,
            score_history=[50, 65, 75],
        )
        output = render_dashboard(snap)
        self.assertIn('Dashboard', output)
        self.assertIn('running', output.lower() or snap.status)
        self.assertIn('feature', output)

    def test_render_with_milestones(self):
        snap = DashboardSnapshot(
            status='running',
            goal='test',
            milestones=[{'title': 'Setup', 'status': 'done'}, {'title': 'Code', 'status': 'in_progress'}],
        )
        output = render_dashboard(snap)
        self.assertIn('Setup', output)
        self.assertIn('Code', output)


# ---------------------------------------------------------------------------
# Nightly tests
# ---------------------------------------------------------------------------

class TestNightlyConfig(unittest.TestCase):

    def test_collect_goals_from_file(self):
        with tempfile.NamedTemporaryFile(suffix='.txt', mode='w', delete=False, encoding='utf-8') as f:
            f.write('# Comment\nFix auth bug\nAdd pagination\n\n# Another comment\nUpdate docs\n')
            path = Path(f.name)

        cfg = NightlyConfig(goals_file=path)
        goals = collect_nightly_goals(cfg, Path('.'))
        self.assertEqual(len(goals), 3)
        self.assertEqual(goals[0], 'Fix auth bug')
        self.assertEqual(goals[2], 'Update docs')
        path.unlink()

    def test_collect_empty_file(self):
        with tempfile.NamedTemporaryFile(suffix='.txt', mode='w', delete=False, encoding='utf-8') as f:
            f.write('# Only comments\n')
            path = Path(f.name)

        cfg = NightlyConfig(goals_file=path)
        goals = collect_nightly_goals(cfg, Path('.'))
        self.assertEqual(len(goals), 0)
        path.unlink()

    def test_no_file_returns_empty(self):
        cfg = NightlyConfig()
        goals = collect_nightly_goals(cfg, Path('.'))
        self.assertEqual(len(goals), 0)


class TestNightlyReport(unittest.TestCase):

    def test_success_rate(self):
        report = NightlyReport(total_tasks=10, completed=8, failed=2)
        self.assertAlmostEqual(report.success_rate, 80.0)

    def test_render_text(self):
        report = NightlyReport(
            total_tasks=3, completed=2, failed=1,
            total_iterations=12, total_llm_cost_usd=0.05,
            total_duration_seconds=180.0,
            tasks=[
                {'goal': 'Fix bug A', 'status': 'complete', 'score': 95},
                {'goal': 'Fix bug B', 'status': 'complete', 'score': 90},
                {'goal': 'Fix bug C', 'status': 'error', 'score': None},
            ],
        )
        text = report.render_text()
        self.assertIn('2✓', text)
        self.assertIn('1✗', text)
        self.assertIn('Fix bug A', text)


# ---------------------------------------------------------------------------
# ROI tests
# ---------------------------------------------------------------------------

class TestROIMetrics(unittest.TestCase):

    def test_roi_ratio_with_cost(self):
        metrics = ROIMetrics(
            total_llm_cost_usd=0.10,
            estimated_dev_minutes_saved=60,
        )
        # 60min = 1hr at $100/hr = $100 saved / $0.10 cost = 1000x
        self.assertAlmostEqual(metrics.roi_ratio, 1000.0)

    def test_roi_ratio_zero_cost(self):
        metrics = ROIMetrics(estimated_dev_minutes_saved=60)
        self.assertEqual(metrics.roi_ratio, float('inf'))

    def test_render_text(self):
        metrics = ROIMetrics(
            total_runs=10, successful_runs=8, failed_runs=2,
            success_rate=80.0, avg_score=85.0,
            total_iterations=30, total_llm_cost_usd=0.50,
            estimated_dev_minutes_saved=360,
        )
        text = metrics.render_text()
        self.assertIn('ROI', text)
        self.assertIn('80%', text)

    def test_analyse_empty_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            metrics = analyse_roi(Path(tmpdir))
            self.assertEqual(metrics.total_runs, 0)

    def test_analyse_with_runs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir) / 'run-1'
            run_dir.mkdir()
            decision = {
                'reasonCode': 'STOP_GATE_PASSED',
                'assessment': {'score': 95},
                'plan': {'profile': 'bug'},
            }
            (run_dir / 'iteration-01-decision.json').write_text(
                json.dumps(decision), encoding='utf-8'
            )

            metrics = analyse_roi(Path(tmpdir))
            self.assertEqual(metrics.total_runs, 1)
            self.assertEqual(metrics.successful_runs, 1)
            self.assertEqual(metrics.avg_score, 95.0)


# ---------------------------------------------------------------------------
# Policy engine tests
# ---------------------------------------------------------------------------

class TestPolicyEngine(unittest.TestCase):

    def test_allow_when_no_rules_triggered(self):
        engine = PolicyEngine()
        decision = engine.evaluate('test', {'changed_files_count': 5})
        self.assertEqual(decision.verdict, PolicyVerdict.ALLOW)
        self.assertEqual(len(decision.triggered_rules), 0)

    def test_block_on_mass_delete(self):
        engine = PolicyEngine()
        decision = engine.evaluate('test', {'deleted_files_count': 20})
        self.assertEqual(decision.verdict, PolicyVerdict.BLOCK)
        self.assertIn('mass_delete', decision.triggered_rules)

    def test_warn_on_high_cost(self):
        engine = PolicyEngine()
        decision = engine.evaluate('test', {'llm_cost_usd': 5.0})
        self.assertEqual(decision.verdict, PolicyVerdict.WARN)
        self.assertIn('high_cost', decision.triggered_rules)

    def test_require_approval_on_large_diff(self):
        engine = PolicyEngine()
        decision = engine.evaluate('test', {'changed_files_count': 100})
        self.assertEqual(decision.verdict, PolicyVerdict.REQUIRE_APPROVAL)
        self.assertIn('large_diff', decision.triggered_rules)

    def test_block_low_score_pr(self):
        engine = PolicyEngine()
        decision = engine.evaluate('test', {'score_below': 50})
        self.assertEqual(decision.verdict, PolicyVerdict.BLOCK)
        self.assertIn('low_score_pr', decision.triggered_rules)

    def test_custom_rule(self):
        custom = PolicyRule(name='custom_test', condition='custom_metric', threshold=10, verdict=PolicyVerdict.WARN)
        engine = PolicyEngine(custom_rules=[custom])
        decision = engine.evaluate('test', {'custom_metric': 15})
        self.assertIn('custom_test', decision.triggered_rules)

    def test_render_rules(self):
        engine = PolicyEngine()
        text = engine.render_rules()
        self.assertIn('large_diff', text)
        self.assertIn('mass_delete', text)

    def test_load_from_config(self):
        config = {
            'policyRules': [
                {'name': 'my_rule', 'condition': 'x', 'threshold': 5, 'verdict': 'block'},
            ]
        }
        rules = load_policy_rules_from_config(config)
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0].name, 'my_rule')

    def test_audit_trail(self):
        with tempfile.NamedTemporaryFile(suffix='.jsonl', delete=False) as f:
            audit_path = Path(f.name)

        engine = PolicyEngine(audit_file=audit_path)
        engine.evaluate('action1', {'changed_files_count': 100})
        engine.evaluate('action2', {'llm_cost_usd': 5.0})

        trail = engine.get_audit_trail()
        self.assertEqual(len(trail), 2)
        self.assertEqual(trail[0]['action'], 'action1')
        audit_path.unlink()


# ---------------------------------------------------------------------------
# GitHub issue sync tests
# ---------------------------------------------------------------------------

class TestIssueSync(unittest.TestCase):

    def test_issue_to_goal_format(self):
        issue = GitHubIssue(number=42, title='Login fails', body='Users cannot login', labels=['bug'])
        goal = issue_to_goal(issue)
        self.assertIn('#42', goal)
        self.assertIn('Login fails', goal)
        self.assertIn('Users cannot login', goal)
        self.assertIn('bug', goal)

    def test_build_pr_body(self):
        body = build_pr_body('fix login', 'run-123', 3, 95, 'STOP_GATE_PASSED')
        self.assertIn('fix login', body)
        self.assertIn('95', body)
        self.assertIn('run-123', body)


if __name__ == '__main__':
    unittest.main()
