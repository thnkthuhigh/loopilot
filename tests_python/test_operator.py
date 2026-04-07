from __future__ import annotations

import io
import os
import tempfile
import unittest
from pathlib import Path

from copilot_operator.bootstrap import (
    cleanup_run_logs,
    initialize_workspace,
    read_operator_focus,
    read_operator_status,
    watch_operator_status,
)
from copilot_operator.config import OperatorConfig, ValidationCommand, load_config
from copilot_operator.operator import CopilotOperator
from copilot_operator.planner import build_milestone_baton, fallback_plan, merge_plan, parse_operator_plan
from copilot_operator.prompts import build_prompt_context, parse_operator_state
from copilot_operator.repo_inspector import detect_workspace_insight
from copilot_operator.session_store import (
    extract_response_text,
    get_latest_request,
    load_chat_session,
    request_completed,
    request_needs_continue,
)
from copilot_operator.validation import run_validations

FIXTURES = Path(__file__).parent / 'fixtures'


class SessionStoreTests(unittest.TestCase):
    def test_loads_single_snapshot_session(self) -> None:
        session = load_chat_session(FIXTURES / 'session_snapshot.jsonl')
        request = get_latest_request(session)
        self.assertIsNotNone(request)
        self.assertTrue(request_completed(session, request))
        text = extract_response_text(request)
        self.assertIn('OPERATOR_STATE', text)
        state = parse_operator_state(text)
        self.assertIsNotNone(state)
        self.assertEqual(state.status, 'done')
        self.assertEqual(state.score, 90)

    def test_replays_incremental_session_events(self) -> None:
        session = load_chat_session(FIXTURES / 'session_incremental.jsonl')
        request = get_latest_request(session)
        self.assertIsNotNone(request)
        self.assertTrue(request_completed(session, request))
        self.assertTrue(request_needs_continue(request))
        state = parse_operator_state(extract_response_text(request))
        self.assertIsNotNone(state)
        self.assertEqual(state.status, 'continue')
        self.assertEqual(state.next_prompt, 'Continue fixing the failing tests.')

    def test_accepts_supervisor_audit_as_fallback_machine_block(self) -> None:
        response = 'All good.\n\n<SUPERVISOR_AUDIT>{"status":"done","score":100,"tests":"not_applicable","lint":"not_applicable","blockers":[],"done_reason":"smoke test passed"}</SUPERVISOR_AUDIT>'
        state = parse_operator_state(response)
        self.assertIsNotNone(state)
        self.assertEqual(state.status, 'done')
        self.assertEqual(state.score, 100)
        self.assertEqual(state.done_reason, 'smoke test passed')


class PlannerTests(unittest.TestCase):
    def test_parse_operator_plan_reads_milestones_and_tasks(self) -> None:
        response = (
            'ok\n<OPERATOR_PLAN>{'
            '"summary":"Ship the bug fix",'
            '"current_milestone_id":"m2",'
            '"next_milestone_id":"m3",'
            '"current_task_id":"m2.t1",'
            '"next_task_id":"m2.t2",'
            '"milestones":['
            '{"id":"m1","title":"Reproduce bug","status":"done"},'
            '{"id":"m2","title":"Patch behavior","status":"in_progress","tasks":['
            '{"id":"m2.t1","title":"Patch code","status":"in_progress"},'
            '{"id":"m2.t2","title":"Add regression test","status":"pending"}]},'
            '{"id":"m3","title":"Verify tests","status":"pending"}]}</OPERATOR_PLAN>'
        )
        plan = parse_operator_plan(response)
        self.assertIsNotNone(plan)
        self.assertEqual(plan.current_milestone_id, 'm2')
        self.assertEqual(plan.next_milestone_id, 'm3')
        self.assertEqual(plan.current_task_id, 'm2.t1')
        self.assertEqual(plan.next_task_id, 'm2.t2')
        self.assertEqual(plan.milestones[1].title, 'Patch behavior')
        self.assertEqual(plan.milestones[1].tasks[0].title, 'Patch code')

    def test_fallback_plan_creates_single_in_progress_milestone_and_task(self) -> None:
        plan = fallback_plan('Fix pagination issue in preview')
        self.assertEqual(plan['currentMilestoneId'], 'm1')
        self.assertEqual(plan['currentTaskId'], 'm1.t1')
        self.assertEqual(plan['milestones'][0]['status'], 'in_progress')
        self.assertEqual(plan['milestones'][0]['tasks'][0]['status'], 'in_progress')
        self.assertIn('Fix pagination issue in preview', plan['milestones'][0]['title'])

    def test_merge_plan_marks_all_milestones_and_tasks_done_on_terminal_done(self) -> None:
        existing = {
            'summary': 'Ship feature',
            'currentMilestoneId': 'm2',
            'currentTaskId': 'm2.t1',
            'nextMilestoneId': 'm3',
            'milestones': [
                {'id': 'm1', 'title': 'Discover', 'status': 'done'},
                {
                    'id': 'm2',
                    'title': 'Implement',
                    'status': 'in_progress',
                    'currentTaskId': 'm2.t1',
                    'tasks': [
                        {'id': 'm2.t1', 'title': 'Patch code', 'status': 'in_progress'},
                        {'id': 'm2.t2', 'title': 'Update tests', 'status': 'pending'},
                    ],
                },
                {'id': 'm3', 'title': 'Verify', 'status': 'pending'},
            ],
        }
        merged = merge_plan(existing, None, 'Ship feature', terminal_status='done')
        self.assertEqual(merged['currentMilestoneId'], '')
        self.assertEqual(merged['currentTaskId'], '')
        self.assertTrue(all(item['status'] == 'done' for item in merged['milestones']))
        self.assertTrue(all(task['status'] == 'done' for task in merged['milestones'][1]['tasks']))

    def test_build_milestone_baton_uses_current_task_and_acceptance_targets(self) -> None:
        plan = {
            'summary': 'Ship feature safely',
            'currentMilestoneId': 'm2',
            'currentTaskId': 'm2.t1',
            'nextMilestoneId': 'm3',
            'nextTaskId': 'm2.t2',
            'milestones': [
                {'id': 'm1', 'title': 'Discover', 'status': 'done'},
                {
                    'id': 'm2',
                    'title': 'Implement slice',
                    'status': 'in_progress',
                    'acceptance': ['tests pass', 'no regressions'],
                    'currentTaskId': 'm2.t1',
                    'nextTaskId': 'm2.t2',
                    'tasks': [
                        {'id': 'm2.t1', 'title': 'Patch code', 'status': 'in_progress', 'summary': 'Wire the fix into the main path'},
                        {'id': 'm2.t2', 'title': 'Add regression test', 'status': 'pending'},
                    ],
                },
                {'id': 'm3', 'title': 'Verify docs', 'status': 'pending'},
            ],
        }
        baton = build_milestone_baton(plan, 'fallback')
        self.assertIn('Milestone: m2 - Implement slice', baton)
        self.assertIn('Task: m2.t1 - Patch code', baton)
        self.assertIn('tests pass', baton)
        self.assertIn('Do not move to task m2.t2', baton)


class RepoInspectorTests(unittest.TestCase):
    def test_detects_node_workspace_and_validation_hints(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / 'package.json').write_text(
                '{"name":"demo","scripts":{"test":"vitest run","lint":"eslint .","build":"vite build"}}',
                encoding='utf-8',
            )
            (workspace / 'pnpm-lock.yaml').write_text('lockfileVersion: 9', encoding='utf-8')
            insight = detect_workspace_insight(workspace)
            self.assertEqual(insight.ecosystem, 'node')
            self.assertEqual(insight.package_manager, 'pnpm')
            self.assertEqual(insight.validation_hints['tests'], 'pnpm test')
            self.assertEqual(insight.validation_hints['lint'], 'pnpm run lint')

    def test_detects_python_workspace_and_validation_hints(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / 'pyproject.toml').write_text('[tool.pytest.ini_options]\naddopts = "-q"\n[tool.ruff]\n', encoding='utf-8')
            (workspace / 'tests').mkdir()
            insight = detect_workspace_insight(workspace)
            self.assertEqual(insight.ecosystem, 'python')
            self.assertIn('pytest', insight.validation_hints['tests'])
            self.assertIn('ruff check .', insight.validation_hints['lint'])


class BootstrapTests(unittest.TestCase):
    def test_initialize_workspace_creates_scaffold(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            result = initialize_workspace(workspace)
            self.assertTrue((workspace / '.copilot-operator' / 'repo-profile.yml').exists())
            self.assertTrue((workspace / 'docs' / 'operator' / 'repo-profile.md').exists())
            self.assertTrue((workspace / 'copilot-operator.yml').exists())
            self.assertTrue(result['created'])

    def test_cleanup_run_logs_keeps_latest_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            log_dir = Path(temp_dir)
            older = log_dir / 'older-run'
            middle = log_dir / 'middle-run'
            newest = log_dir / 'newest-run'
            older.mkdir()
            middle.mkdir()
            newest.mkdir()
            os.utime(older, (100, 100))
            os.utime(middle, (200, 200))
            os.utime(newest, (300, 300))

            result = cleanup_run_logs(log_dir, keep_runs=1)
            self.assertEqual(result['kept'], [str(newest)])
            self.assertIn(str(older), result['removed'])
            self.assertIn(str(middle), result['removed'])
            self.assertTrue(newest.exists())
            self.assertFalse(older.exists())

    def test_read_operator_status_handles_missing_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            status = read_operator_status(workspace / 'state.json', workspace / 'summary.json')
            self.assertEqual(status['status'], 'not_started')
            self.assertEqual(status['iterationsCompleted'], 0)

    def test_read_operator_status_exposes_plan_and_reason_codes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            state_file = workspace / 'state.json'
            summary_file = workspace / 'summary.json'
            state_file.write_text(
                '{\n'
                '  "status": "running",\n'
                '  "goal": "demo",\n'
                '  "goalProfile": "bug",\n'
                '  "runId": "run-123",\n'
                '  "logRootDir": ".copilot-operator/logs",\n'
                '  "logDir": ".copilot-operator/logs/run-123",\n'
                '  "workspaceInsight": {"ecosystem": "node", "packageManager": "npm"},\n'
                '  "plan": {\n'
                '    "summary": "Fix bug safely",\n'
                '    "currentMilestoneId": "m2",\n'
                '    "nextMilestoneId": "m3",\n'
                '    "currentTaskId": "m2.t1",\n'
                '    "nextTaskId": "m2.t2",\n'
                '    "counts": {"pending": 1, "in_progress": 1, "done": 1, "blocked": 0},\n'
                '    "taskCounts": {"pending": 1, "in_progress": 1, "done": 0, "blocked": 0},\n'
                '    "milestones": [\n'
                '      {"id": "m1", "title": "Reproduce", "status": "done"},\n'
                '      {"id": "m2", "title": "Patch", "status": "in_progress", "currentTaskId": "m2.t1", "nextTaskId": "m2.t2", "tasks": [{"id": "m2.t1", "title": "Patch code", "status": "in_progress"}, {"id": "m2.t2", "title": "Add test", "status": "pending"}]},\n'
                '      {"id": "m3", "title": "Verify", "status": "pending"}\n'
                '    ]\n'
                '  },\n'
                '  "history": [\n'
                '    {\n'
                '      "iteration": 1,\n'
                '      "sessionId": "session-1",\n'
                '      "score": 88,\n'
                '      "summary": "did work",\n'
                '      "decisionCode": "WORK_REMAINS",\n'
                '      "currentMilestoneId": "m2",\n'
                '      "currentTaskId": "m2.t1",\n'
                '      "artifacts": {"prompt": "prompt.md", "response": "response.md"}\n'
                '    }\n'
                '  ]\n'
                '}',
                encoding='utf-8',
            )
            summary_file.write_text('{"status": "running", "reasonCode": "WORK_REMAINS"}', encoding='utf-8')
            status = read_operator_status(state_file, summary_file)
            self.assertEqual(status['runLogDir'], '.copilot-operator/logs/run-123')
            self.assertEqual(status['goalProfile'], 'bug')
            self.assertEqual(status['currentMilestoneId'], 'm2')
            self.assertEqual(status['currentTaskId'], 'm2.t1')
            self.assertEqual(status['taskCounts']['pending'], 1)
            self.assertEqual(status['milestoneCounts']['done'], 1)
            self.assertEqual(status['lastDecisionCode'], 'WORK_REMAINS')
            self.assertEqual(status['workspaceInsight']['ecosystem'], 'node')

    def test_watch_operator_status_renders_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            state_file = workspace / 'state.json'
            summary_file = workspace / 'summary.json'
            state_file.write_text(
                '{"status": "running", "goal": "demo", "goalProfile": "feature", "runId": "run-abc", "workspaceInsight": {"ecosystem": "node"}, "plan": {"summary": "Deliver feature", "currentMilestoneId": "m1", "currentTaskId": "m1.t1", "counts": {"pending": 1, "in_progress": 1, "done": 0, "blocked": 0}, "taskCounts": {"pending": 1, "in_progress": 1, "done": 0, "blocked": 0}, "milestones": [{"id": "m1", "title": "Build slice", "status": "in_progress", "tasks": [{"id": "m1.t1", "title": "Implement slice", "status": "in_progress"}]}]}, "updatedAt": "2026-04-06T00:00:00Z"}',
                encoding='utf-8',
            )
            output = io.StringIO()
            result = watch_operator_status(
                state_file,
                summary_file,
                interval_seconds=0,
                max_updates=1,
                stream=output,
                sleep_fn=lambda _: None,
            )
            rendered = output.getvalue()
            self.assertEqual(result['status'], 'running')
            self.assertIn('Status: running', rendered)
            self.assertIn('Goal profile: feature', rendered)
            self.assertIn('Current milestone: m1', rendered)
            self.assertIn('Current task: m1.t1', rendered)
            self.assertIn('Run ID: run-abc', rendered)

    def test_read_operator_focus_prefers_pending_decision_baton(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            state_file = workspace / 'state.json'
            summary_file = workspace / 'summary.json'
            state_file.write_text(
                '{"status": "running", "goal": "demo", "goalProfile": "feature", "runId": "run-focus", "plan": {"summary": "Deliver feature", "currentMilestoneId": "m2", "nextMilestoneId": "m3", "currentTaskId": "m2.t1", "nextTaskId": "m2.t2", "milestones": [{"id": "m1", "title": "Discover", "status": "done"}, {"id": "m2", "title": "Patch", "status": "in_progress", "currentTaskId": "m2.t1", "nextTaskId": "m2.t2", "acceptance": ["tests pass"], "tasks": [{"id": "m2.t1", "title": "Patch code", "status": "in_progress"}, {"id": "m2.t2", "title": "Add regression test", "status": "pending"}]}, {"id": "m3", "title": "Verify", "status": "pending"}]}, "pendingDecision": {"action": "continue", "reasonCode": "WORK_REMAINS", "nextPrompt": "Keep patching the active task."}}',
                encoding='utf-8',
            )
            focus = read_operator_focus(state_file, summary_file)
            self.assertEqual(focus['currentMilestoneId'], 'm2')
            self.assertEqual(focus['currentTaskId'], 'm2.t1')
            self.assertEqual(focus['nextPrompt'], 'Keep patching the active task.')


class ConfigTests(unittest.TestCase):
    def test_load_config_merges_repo_profile_defaults_and_infers_validations(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            profile_dir = workspace / '.copilot-operator'
            profile_dir.mkdir(parents=True)
            (profile_dir / 'repo-memory.md').write_text('# memory\n', encoding='utf-8')
            (profile_dir / 'goal.txt').write_text('default goal', encoding='utf-8')
            (profile_dir / 'repo-profile.yml').write_text(
                '\n'.join(
                    [
                        'repoName: temp-repo',
                        'summary: temp summary',
                        'projectMemoryFiles:',
                        '  - .copilot-operator/repo-memory.md',
                        'defaultGoalFile: .copilot-operator/goal.txt',
                        'operator:',
                        '  targetScore: 91',
                        '  validation:',
                        '    - name: tests',
                        '      command: ""',
                        '      required: true',
                        '      runBeforePrompt: false',
                        '      runAfterResponse: true',
                    ]
                ),
                encoding='utf-8',
            )
            (workspace / 'package.json').write_text('{"scripts":{"test":"vitest run"}}', encoding='utf-8')
            (workspace / 'package-lock.json').write_text('{}', encoding='utf-8')
            config_path = workspace / 'copilot-operator.yml'
            config_path.write_text('workspace: .\nmaxIterations: 4\ngoalProfile: bug\ncodeCommandRetries: 5\n', encoding='utf-8')

            config = load_config(config_path)
            self.assertEqual(config.repo_profile.repo_name, 'temp-repo')
            self.assertEqual(config.target_score, 91)
            self.assertEqual(config.goal_profile, 'bug')
            self.assertEqual(config.max_iterations, 4)
            self.assertEqual(config.code_command_retries, 5)
            self.assertEqual(config.workspace_insight.package_manager, 'npm')
            self.assertEqual(len(config.repo_profile.project_memory_files), 1)
            self.assertTrue(config.repo_profile.project_memory_files[0].exists())
            self.assertIsNotNone(config.repo_profile.default_goal_file)
            self.assertEqual(config.validation[0].command, 'npm test')
            self.assertEqual(config.validation[0].source, 'inferred')
            self.assertFalse(config.validation[0].run_before_prompt)


class PromptTests(unittest.TestCase):
    def test_build_prompt_context_includes_goal_profile_workspace_insight_and_plan(self) -> None:
        config = OperatorConfig(workspace=Path.cwd())
        context = build_prompt_context(
            history=[],
            validation_results=[],
            repo_profile=config.repo_profile,
            workspace=config.workspace,
            workspace_insight=config.workspace_insight,
            goal_profile='refactor',
            plan=fallback_plan('Refactor the preview pipeline'),
        )
        self.assertIn('Goal profile: refactor', context.goal_profile_text)
        self.assertIn('Current milestone', context.plan_text)
        self.assertIn('Current task', context.plan_text)
        self.assertTrue(context.workspace_insight_text)


class ValidationTests(unittest.TestCase):
    def test_validation_respects_phase_flags(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            validations = [
                ValidationCommand(name='before-only', command='echo before', run_before_prompt=True, run_after_response=False),
                ValidationCommand(name='after-only', command='echo after', run_before_prompt=False, run_after_response=True),
            ]
            before = run_validations(validations, workspace, phase='before_prompt')
            after = run_validations(validations, workspace, phase='after_response')
            self.assertEqual([item['name'] for item in before], ['before-only'])
            self.assertEqual([item['name'] for item in after], ['after-only'])


class OperatorDecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        workspace = Path(self.temp_dir.name)
        self.config = OperatorConfig(
            workspace=workspace,
            target_score=85,
            max_iterations=3,
            goal_profile='feature',
            validation=[ValidationCommand(name='tests', command='', required=True)],
            memory_file=workspace / '.copilot-operator' / 'memory.md',
            state_file=workspace / '.copilot-operator' / 'state.json',
            summary_file=workspace / '.copilot-operator' / 'session-summary.json',
            log_dir=workspace / '.copilot-operator' / 'logs',
        )
        self.operator = CopilotOperator(self.config)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_done_is_reopened_when_required_validation_failed(self) -> None:
        assessment = parse_operator_state(
            'ok\n<OPERATOR_STATE>{"status":"done","score":91,"summary":"done","next_prompt":"","tests":"pass","lint":"pass","blockers":[],"needs_continue":false,"done_reason":"ready"}</OPERATOR_STATE>'
        )
        decision = self.operator._decide(
            assessment,
            [{'name': 'tests', 'status': 'fail', 'required': True, 'summary': 'one test failed'}],
            iteration=1,
        )
        self.assertEqual(decision.action, 'continue')
        self.assertEqual(decision.reason_code, 'VALIDATION_FAILED')
        self.assertIn('validation failed', decision.reason.lower())

    def test_done_stops_when_gate_passes(self) -> None:
        assessment = parse_operator_state(
            'ok\n<OPERATOR_STATE>{"status":"done","score":92,"summary":"done","next_prompt":"","tests":"pass","lint":"pass","blockers":[],"needs_continue":false,"done_reason":"ready"}</OPERATOR_STATE>'
        )
        decision = self.operator._decide(
            assessment,
            [{'name': 'tests', 'status': 'pass', 'required': True, 'summary': 'all green'}],
            iteration=1,
        )
        self.assertEqual(decision.action, 'stop')
        self.assertEqual(decision.reason_code, 'STOP_GATE_PASSED')
        self.assertEqual(decision.reason, 'ready')

    def test_continue_uses_task_aware_baton_when_copilot_prompt_is_generic(self) -> None:
        self.operator.runtime = {
            'plan': {
                'summary': 'Ship feature safely',
                'currentMilestoneId': 'm2',
                'currentTaskId': 'm2.t1',
                'nextMilestoneId': 'm3',
                'milestones': [
                    {'id': 'm1', 'title': 'Discover', 'status': 'done'},
                    {
                        'id': 'm2',
                        'title': 'Implement slice',
                        'status': 'in_progress',
                        'acceptance': ['tests pass'],
                        'currentTaskId': 'm2.t1',
                        'tasks': [{'id': 'm2.t1', 'title': 'Patch code', 'status': 'in_progress'}],
                    },
                    {'id': 'm3', 'title': 'Verify docs', 'status': 'pending'},
                ],
            }
        }
        assessment = parse_operator_state(
            'ok\n<OPERATOR_STATE>{"status":"continue","score":60,"summary":"working","next_prompt":"Continue","tests":"not_run","lint":"not_run","blockers":[],"needs_continue":false,"done_reason":""}</OPERATOR_STATE>'
        )
        decision = self.operator._decide(assessment, [], iteration=1)
        self.assertEqual(decision.action, 'continue')
        self.assertIn('Milestone: m2 - Implement slice', decision.next_prompt)
        self.assertIn('Task: m2.t1 - Patch code', decision.next_prompt)
        self.assertIn('tests pass', decision.next_prompt)

    def test_prepare_fresh_run_creates_run_specific_log_directory_and_plan(self) -> None:
        goal, decision, iteration = self.operator._prepare_fresh_run('Ship the bug fix')
        self.assertEqual(goal, 'Ship the bug fix')
        self.assertEqual(iteration, 1)
        self.assertEqual(decision.action, 'continue')
        self.assertEqual(decision.reason_code, 'INITIAL_EXECUTION')
        run_id = self.operator.runtime['runId']
        expected_dir = self.config.log_dir / run_id
        self.assertEqual(self.operator.runtime['goalProfile'], 'feature')
        self.assertEqual(self.operator.runtime['plan']['currentMilestoneId'], 'm1')
        self.assertEqual(self.operator.runtime['plan']['currentTaskId'], 'm1.t1')
        self.assertEqual(self.operator.runtime['logDir'], str(expected_dir))
        self.assertEqual(self.operator._artifact_path(1, 'prompt.md'), expected_dir / 'iteration-01-prompt.md')

    def test_prepare_resume_run_uses_pending_decision_and_preserves_plan(self) -> None:
        self.config.ensure_runtime_dirs()
        self.config.state_file.write_text(
            '{\n'
            '  "goal": "Resume goal",\n'
            '  "goalProfile": "bug",\n'
            '  "runId": "resume-run",\n'
            '  "plan": {"summary": "Existing plan", "currentMilestoneId": "m2", "currentTaskId": "m2.t1", "milestones": [{"id": "m1", "title": "Discover", "status": "done"}, {"id": "m2", "title": "Patch", "status": "in_progress", "currentTaskId": "m2.t1", "tasks": [{"id": "m2.t1", "title": "Patch code", "status": "in_progress"}]}], "counts": {"pending": 0, "in_progress": 1, "done": 1, "blocked": 0}, "taskCounts": {"pending": 0, "in_progress": 1, "done": 0, "blocked": 0}},\n'
            '  "status": "blocked",\n'
            '  "history": [\n'
            '    {"iteration": 1, "next_prompt": "Continue from iteration one."}\n'
            '  ],\n'
            '  "pendingDecision": {\n'
            '    "action": "continue",\n'
            '    "reason": "Need another pass",\n'
            '    "reasonCode": "WORK_REMAINS",\n'
            '    "nextPrompt": "Continue from the saved baton."\n'
            '  }\n'
            '}',
            encoding='utf-8',
        )
        goal, decision, iteration = self.operator._prepare_resume_run(None)
        self.assertEqual(goal, 'Resume goal')
        self.assertEqual(iteration, 2)
        self.assertEqual(decision.reason, 'Need another pass')
        self.assertEqual(decision.reason_code, 'WORK_REMAINS')
        self.assertEqual(decision.next_prompt, 'Continue from the saved baton.')
        self.assertEqual(self.operator.runtime['plan']['currentMilestoneId'], 'm2')
        self.assertEqual(self.operator.runtime['plan']['currentTaskId'], 'm2.t1')
        self.assertEqual(self.operator.runtime['logDir'], str(self.config.log_dir / 'resume-run'))


if __name__ == '__main__':
    unittest.main()
