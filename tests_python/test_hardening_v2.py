"""Tests for hardening features: runtime guard, stop controller, worker contract, CI integration."""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Runtime Guard tests
# ---------------------------------------------------------------------------
from copilot_operator.runtime_guard import (
    IterationCheckpoint,
    acquire_lock,
    build_continuity_context,
    clear_checkpoint,
    detect_window_conflict,
    load_checkpoint,
    read_lock,
    refresh_lock,
    release_lock,
    save_checkpoint,
)


class TestLockFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.workspace = Path(self.tmp)

    def test_acquire_and_release(self):
        lock = acquire_lock(self.workspace, 'run-1')
        self.assertIsNotNone(lock)
        self.assertEqual(lock.run_id, 'run-1')
        self.assertEqual(lock.pid, os.getpid())

        # Same process can re-acquire (e.g. new run in same process)
        lock2 = acquire_lock(self.workspace, 'run-2')
        self.assertIsNotNone(lock2)

        # Release
        self.assertTrue(release_lock(self.workspace, 'run-2'))

        # Now can acquire
        lock3 = acquire_lock(self.workspace, 'run-3')
        self.assertIsNotNone(lock3)
        release_lock(self.workspace, 'run-3')

    def test_different_process_blocked(self):
        """Lock held by a different PID blocks acquisition."""
        lock_file = self.workspace / '.copilot-operator' / 'operator.lock'
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        lock_file.write_text(json.dumps({
            'pid': 99999,
            'runId': 'other-run',
            'startedAt': time.time(),
            'workspace': str(self.workspace),
        }), encoding='utf-8')

        # Mock _is_process_alive to return True for the fake PID
        with patch('copilot_operator.runtime_guard._is_process_alive', return_value=True):
            lock = acquire_lock(self.workspace, 'my-run')
        # Should fail because other process is alive
        self.assertIsNone(lock)

    def test_stale_lock_taken_over(self):
        # Write a lock with old timestamp and dead PID
        lock_file = self.workspace / '.copilot-operator' / 'operator.lock'
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        lock_file.write_text(json.dumps({
            'pid': 99999999,  # Almost certainly not running
            'runId': 'old-run',
            'startedAt': time.time() - 9999,
            'workspace': str(self.workspace),
        }), encoding='utf-8')

        lock = acquire_lock(self.workspace, 'new-run')
        self.assertIsNotNone(lock)
        self.assertEqual(lock.run_id, 'new-run')
        release_lock(self.workspace, 'new-run')

    def test_read_lock_empty(self):
        self.assertIsNone(read_lock(self.workspace))

    def test_refresh_lock(self):
        lock = acquire_lock(self.workspace, 'run-1')
        self.assertIsNotNone(lock)
        old_time = lock.started_at

        time.sleep(0.01)
        refresh_lock(self.workspace, 'run-1')

        updated = read_lock(self.workspace)
        self.assertIsNotNone(updated)
        self.assertGreater(updated.started_at, old_time)
        release_lock(self.workspace, 'run-1')

    def test_release_unowned_lock(self):
        lock = acquire_lock(self.workspace, 'run-1')
        self.assertIsNotNone(lock)
        # Try to release with wrong run_id + different PID
        with patch('copilot_operator.runtime_guard.os.getpid', return_value=0):
            self.assertFalse(release_lock(self.workspace, 'wrong-run'))
        release_lock(self.workspace, 'run-1')


class TestWindowConflict(unittest.TestCase):
    def test_no_conflict_without_storage(self):
        tmp = tempfile.mkdtemp()
        with patch('copilot_operator.session_store.find_workspace_storage', return_value=None), \
             patch('copilot_operator.runtime_guard.find_workspace_storage', return_value=None):
            result = detect_window_conflict(Path(tmp))
        self.assertIsNone(result)

    def test_detects_recent_session(self):
        tmp = tempfile.mkdtemp()
        storage = Path(tmp)
        # Create a recent session file
        chat_dir = storage / 'GitHub.copilot-chat' / 'transcripts'
        chat_dir.mkdir(parents=True, exist_ok=True)
        session = chat_dir / 'recent-session.json'
        session.write_text('{}', encoding='utf-8')

        with patch('copilot_operator.runtime_guard.find_workspace_storage', return_value=storage):
            result = detect_window_conflict(Path(tmp))
        self.assertIsNotNone(result)
        self.assertIn('Active Copilot session', result)


class TestCheckpoint(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.workspace = Path(self.tmp)

    def test_save_and_load(self):
        cp = IterationCheckpoint(
            run_id='test-run',
            iteration=3,
            goal='Fix the bug',
            goal_profile='bug',
            status='running',
            last_decision_action='continue',
            last_decision_reason_code='WORK_REMAINS',
            last_decision_next_prompt='Fix the test',
            score=75,
            timestamp=time.time(),
            all_changed_files=['src/main.py', 'tests/test.py'],
        )
        save_checkpoint(self.workspace, cp)
        loaded = load_checkpoint(self.workspace)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.run_id, 'test-run')
        self.assertEqual(loaded.iteration, 3)
        self.assertEqual(loaded.goal, 'Fix the bug')
        self.assertEqual(loaded.score, 75)
        self.assertEqual(loaded.all_changed_files, ['src/main.py', 'tests/test.py'])

    def test_load_missing(self):
        self.assertIsNone(load_checkpoint(self.workspace))

    def test_clear(self):
        cp = IterationCheckpoint(run_id='test', iteration=1)
        save_checkpoint(self.workspace, cp)
        clear_checkpoint(self.workspace)
        self.assertIsNone(load_checkpoint(self.workspace))


class TestContinuityContext(unittest.TestCase):
    def test_empty_history(self):
        self.assertEqual(build_continuity_context([], [], None, ''), '')

    def test_builds_summary(self):
        history = [
            {'iteration': 1, 'score': 60, 'summary': 'Added tests', 'decisionCode': 'WORK_REMAINS'},
            {'iteration': 2, 'score': 85, 'summary': 'Fixed linting', 'decisionCode': 'STOP_GATE_PASSED'},
        ]
        result = build_continuity_context(history, ['src/app.py'], 85, 'Worker healthy')
        self.assertIn('Session Continuity Context', result)
        self.assertIn('2 prior iterations', result)
        self.assertIn('60 → 85', result)
        self.assertIn('src/app.py', result)
        self.assertIn('Worker healthy', result)


# ---------------------------------------------------------------------------
# Stop Controller tests
# ---------------------------------------------------------------------------
from copilot_operator.stop_controller import (
    StopController,
    StopControllerConfig,
    _hash_diff,
    build_stop_controller_config,
)


class TestStopController(unittest.TestCase):
    def test_no_stop_initially(self):
        ctrl = StopController()
        signal = ctrl.evaluate(1, [])
        self.assertFalse(signal.should_stop)

    def test_no_progress_detection(self):
        config = StopControllerConfig(no_progress_max_iterations=2)
        ctrl = StopController(config)
        ctrl.record_diff('')  # first empty (prev was '', so _no_progress_count=1)
        ctrl.record_diff('')  # same empty again (_no_progress_count=2)
        ctrl.record_diff('')  # same empty again (_no_progress_count=3)
        signal = ctrl.evaluate(4, [])
        self.assertTrue(signal.should_stop)
        self.assertEqual(signal.reason_code, 'NO_PROGRESS')

    def test_progress_resets_counter(self):
        config = StopControllerConfig(no_progress_max_iterations=3)
        ctrl = StopController(config)
        ctrl.record_diff('+ some change')
        ctrl.record_diff('+ some change')  # same (_no_progress_count=1)
        ctrl.record_diff('+ different change')  # different! resets (_no_progress_count=0)
        signal = ctrl.evaluate(4, [])
        self.assertFalse(signal.should_stop)

    def test_diff_dedup_detection(self):
        config = StopControllerConfig(diff_dedup_max_repeats=2)
        ctrl = StopController(config)
        ctrl.record_diff('+ same code')
        ctrl.record_diff('+ same code')  # repeated
        signal = ctrl.evaluate(3, [])
        self.assertTrue(signal.should_stop)
        self.assertEqual(signal.reason_code, 'DIFF_DEDUP')

    def test_task_timeout(self):
        config = StopControllerConfig(task_timeout_seconds=1)
        ctrl = StopController(config)
        ctrl._start_time = time.monotonic() - 2  # Fake 2 seconds elapsed
        signal = ctrl.evaluate(1, [])
        self.assertTrue(signal.should_stop)
        self.assertEqual(signal.reason_code, 'TASK_TIMEOUT')
        self.assertFalse(signal.is_hard_stop)  # Timeout is soft

    def test_score_floor_breach(self):
        config = StopControllerConfig(score_floor=20, score_floor_max_iterations=3)
        ctrl = StopController(config)
        history = [
            {'score': 10},
            {'score': 15},
            {'score': 5},
        ]
        signal = ctrl.evaluate(4, history)
        self.assertTrue(signal.should_stop)
        self.assertEqual(signal.reason_code, 'SCORE_FLOOR_BREACH')
        self.assertTrue(signal.is_hard_stop)

    def test_score_floor_not_breached(self):
        config = StopControllerConfig(score_floor=20, score_floor_max_iterations=3)
        ctrl = StopController(config)
        history = [
            {'score': 10},
            {'score': 15},
            {'score': 25},  # Above floor
        ]
        signal = ctrl.evaluate(4, history)
        self.assertFalse(signal.should_stop)

    def test_hard_vs_soft_escalation(self):
        # Hard escalation — need 2 empty diffs to trigger (first sets the hash, second increments)
        config = StopControllerConfig(no_progress_max_iterations=1, default_escalation='hard')
        ctrl = StopController(config)
        ctrl.record_diff('')  # sets prev hash
        ctrl.record_diff('')  # same → _no_progress_count=2, triggers at >=1
        signal = ctrl.evaluate(3, [])
        self.assertTrue(signal.should_stop)
        self.assertTrue(signal.is_hard_stop)

        # Soft escalation
        config2 = StopControllerConfig(no_progress_max_iterations=1, default_escalation='soft')
        ctrl2 = StopController(config2)
        ctrl2.record_diff('')
        ctrl2.record_diff('')
        signal2 = ctrl2.evaluate(3, [])
        self.assertTrue(signal2.should_stop)
        self.assertFalse(signal2.is_hard_stop)


class TestDiffHash(unittest.TestCase):
    def test_empty_diff(self):
        self.assertEqual(_hash_diff(''), 'empty')
        self.assertEqual(_hash_diff('  \n  '), 'empty')

    def test_same_content_same_hash(self):
        h1 = _hash_diff('+ new line\n- old line')
        h2 = _hash_diff('+ new line\n- old line')
        self.assertEqual(h1, h2)

    def test_different_content_different_hash(self):
        h1 = _hash_diff('+ line A')
        h2 = _hash_diff('+ line B')
        self.assertNotEqual(h1, h2)


class TestBuildStopControllerConfig(unittest.TestCase):
    def test_from_operator_config(self):
        mock_config = MagicMock()
        mock_config.max_task_seconds = 300
        mock_config.escalation_policy = 'hard'
        result = build_stop_controller_config(mock_config)
        self.assertEqual(result.task_timeout_seconds, 300)
        self.assertEqual(result.default_escalation, 'hard')

    def test_defaults_when_missing(self):
        mock_config = MagicMock(spec=[])  # No attributes
        result = build_stop_controller_config(mock_config)
        self.assertEqual(result.task_timeout_seconds, 0)
        self.assertEqual(result.default_escalation, 'soft')


# ---------------------------------------------------------------------------
# Worker Contract tests
# ---------------------------------------------------------------------------
from copilot_operator.worker import (
    HealthSignal,
    RecyclePolicy,
    RequiredArtifacts,
    TaskInput,
    Worker,
    WorkerHealth,
    WorkerStatus,
)


class TestHealthSignal(unittest.TestCase):
    def test_healthy(self):
        h = WorkerHealth()
        self.assertEqual(h.signal, HealthSignal.HEALTHY)

    def test_degraded_on_error(self):
        h = WorkerHealth()
        h.record_error('test error')
        self.assertEqual(h.signal, HealthSignal.DEGRADED)

    def test_dead_on_multiple_errors(self):
        h = WorkerHealth()
        h.record_error('error 1')
        h.record_error('error 2')
        h.record_error('error 3')
        self.assertEqual(h.signal, HealthSignal.DEAD)

    def test_degraded_on_declining_score(self):
        h = WorkerHealth()
        h.record_success(80)
        h.record_success(60)
        h.record_success(40)
        self.assertEqual(h.signal, HealthSignal.DEGRADED)


class TestRecyclePolicy(unittest.TestCase):
    def test_no_recycle_when_healthy(self):
        policy = RecyclePolicy()
        health = WorkerHealth()
        health.record_success(80)
        should, reason = policy.should_recycle(health)
        self.assertFalse(should)

    def test_recycle_on_dead(self):
        policy = RecyclePolicy()
        health = WorkerHealth()
        for _ in range(3):
            health.record_error('error')
        should, reason = policy.should_recycle(health)
        self.assertTrue(should)
        self.assertIn('consecutive errors', reason)

    def test_recycle_on_score_floor(self):
        policy = RecyclePolicy(max_score_floor_iterations=3, score_floor=20)
        health = WorkerHealth()
        health.record_success(10)
        health.record_success(15)
        health.record_success(5)
        should, reason = policy.should_recycle(health)
        self.assertTrue(should)
        self.assertIn('Score below', reason)

    def test_recycle_on_idle(self):
        policy = RecyclePolicy(max_idle_seconds=1)
        health = WorkerHealth()
        health.last_activity = time.time() - 2
        should, reason = policy.should_recycle(health)
        self.assertTrue(should)
        self.assertIn('idle', reason)


class TestRequiredArtifacts(unittest.TestCase):
    def test_all_present(self):
        arts = RequiredArtifacts()
        produced = {
            'prompt_file': True,
            'response_file': True,
            'decision_file': True,
            'validation_result': True,
        }
        self.assertEqual(arts.check(produced), [])

    def test_missing_artifacts(self):
        arts = RequiredArtifacts()
        produced = {'prompt_file': True}
        missing = arts.check(produced)
        self.assertIn('response_file', missing)
        self.assertIn('decision_file', missing)
        self.assertIn('validation_result', missing)

    def test_optional_diff(self):
        arts = RequiredArtifacts(diff_summary=True)
        produced = {
            'prompt_file': True,
            'response_file': True,
            'decision_file': True,
            'validation_result': True,
        }
        missing = arts.check(produced)
        self.assertIn('diff_summary', missing)


class TestWorkerContract(unittest.TestCase):
    def test_worker_has_recycle_policy(self):
        w = Worker(role='coder')
        self.assertIsInstance(w.recycle_policy, RecyclePolicy)

    def test_worker_has_required_artifacts(self):
        w = Worker(role='coder')
        self.assertIsInstance(w.required_artifacts, RequiredArtifacts)

    def test_worker_to_dict_includes_signal(self):
        w = Worker(role='coder')
        d = w.to_dict()
        self.assertIn('signal', d['health'])
        self.assertEqual(d['health']['signal'], 'healthy')

    def test_should_stop_recycle(self):
        w = Worker(role='coder')
        w.recycle_policy = RecyclePolicy(max_consecutive_errors=2)
        w.health.record_error('e1')
        w.health.record_error('e2')
        w.health.record_error('e3')
        should, reason = w.should_stop()
        self.assertTrue(should)

    def test_check_artifacts(self):
        w = Worker(role='coder')
        missing = w.check_artifacts({'prompt_file': True})
        self.assertTrue(len(missing) > 0)

    def test_recycled_status(self):
        self.assertEqual(WorkerStatus.RECYCLED, 'recycled')

    def test_task_input_defaults(self):
        ti = TaskInput(goal='Fix bug', target_score=90)
        self.assertEqual(ti.goal, 'Fix bug')
        self.assertEqual(ti.target_score, 90)
        self.assertEqual(ti.constraints, [])


# ---------------------------------------------------------------------------
# CI Integration tests
# ---------------------------------------------------------------------------
from copilot_operator.ci_integration import (
    CIConfig,
    CIResult,
    build_ci_fix_prompt,
    load_ci_config,
    render_ci_summary,
)


class TestCIConfig(unittest.TestCase):
    def test_not_configured_without_token(self):
        c = CIConfig()
        self.assertFalse(c.is_configured)

    def test_configured_with_all_fields(self):
        c = CIConfig(token='ghp_test', owner='user', repo='repo')
        self.assertTrue(c.is_configured)


class TestCIResult(unittest.TestCase):
    def test_success_result(self):
        r = CIResult(success=True, conclusion='success', run_id=123)
        self.assertTrue(r.success)

    def test_failure_result(self):
        r = CIResult(success=False, conclusion='failure', run_id=456,
                      failed_jobs=['build'], failed_steps=['build → test'])
        self.assertFalse(r.success)
        self.assertEqual(r.failed_jobs, ['build'])


class TestBuildCIFixPrompt(unittest.TestCase):
    def test_generates_prompt(self):
        result = CIResult(
            success=False,
            conclusion='failure',
            failed_jobs=['test'],
            failed_steps=['test → Run unit tests'],
            log_excerpt='Error: expected true, got false',
        )
        prompt = build_ci_fix_prompt(result)
        self.assertIn('CI pipeline has failed', prompt)
        self.assertIn('test', prompt)
        self.assertIn('Run unit tests', prompt)
        self.assertIn('Error: expected true', prompt)

    def test_empty_failures(self):
        result = CIResult(success=True, conclusion='success')
        prompt = build_ci_fix_prompt(result)
        self.assertIn('CI pipeline', prompt)


class TestRenderCISummary(unittest.TestCase):
    def test_success_summary(self):
        r = CIResult(success=True, conclusion='success', run_id=100)
        text = render_ci_summary(r)
        self.assertIn('PASS', text)

    def test_failure_summary(self):
        r = CIResult(success=False, conclusion='failure', run_id=200,
                      failed_jobs=['build'], url='https://example.com')
        text = render_ci_summary(r)
        self.assertIn('FAIL', text)
        self.assertIn('build', text)
        self.assertIn('https://example.com', text)


class TestLoadCIConfig(unittest.TestCase):
    @patch.dict(os.environ, {'GITHUB_TOKEN': 'ghp_test'}, clear=False)
    @patch('copilot_operator.ci_integration._infer_owner_repo', return_value=('owner', 'repo'))
    def test_loads_from_env_and_git(self, mock_infer):
        config = load_ci_config()
        self.assertEqual(config.token, 'ghp_test')
        self.assertEqual(config.owner, 'owner')
        self.assertEqual(config.repo, 'repo')

    def test_explicit_overrides(self):
        config = load_ci_config(github_token='token', owner='o', repo='r')
        self.assertEqual(config.token, 'token')
        self.assertEqual(config.owner, 'o')
        self.assertEqual(config.repo, 'r')


if __name__ == '__main__':
    unittest.main()
