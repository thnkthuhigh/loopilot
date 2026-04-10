from __future__ import annotations

__all__ = [
    'CopilotOperator',
    'Decision',
]

import json as _json
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .adversarial import build_critic_prompt, should_run_critic
from .bootstrap import _merge_vscode_settings, cleanup_run_logs  # noqa: F401
from .brain import (
    ProjectInsights,
    analyse_runs,
    extract_run_digest,
    render_insights_for_memory,
    render_insights_for_prompt,
)
from .config import OperatorConfig
from .cross_repo_brain import (
    export_rules_as_insights,
    load_shared_brain,
    render_cross_repo_insights,
    save_shared_brain,
)
from .goal_decomposer import build_replan_prompt, classify_goal, decompose_goal, decompose_goal_with_llm, should_replan
from .intention_guard import learn_guardrails_from_history, render_combined_guardrails
from .llm_brain import LLMBrain, load_llm_config_from_dict, load_llm_config_from_env, render_brain_status
from .logging_config import get_logger
from .memory_promotion import PromotionThresholds, run_promotion_cycle
from .meta_learner import apply_meta_learning, load_rules, render_guardrails
from .mission_memory import (
    load_mission,
    render_mission_for_prompt,
    save_mission,
    update_mission_from_run,
)
from .narrative import (
    build_run_narrative,
    write_done_explanation_file,
    write_narrative_file,
)
from .planner import (
    build_milestone_baton,
    is_generic_baton,
    merge_plan,
    parse_operator_plan,
    summarize_plan,
)
from .prompts import (
    Assessment,
    build_follow_up_prompt,
    build_initial_prompt,
    build_prompt_context,
    fallback_assessment,
    parse_operator_state,
)
from .reasoning import Diagnosis, diagnose, format_diagnosis_for_prompt
from .repo_inspector import as_dict
from .repo_ops import (
    check_protected_paths,
    create_operator_commit,
    get_all_changed_files,
    get_diff_summary,
    is_git_repo,
    pre_run_safety_check,
)
from .runtime_guard import (
    IterationCheckpoint,
    acquire_lock,
    clear_checkpoint,
    detect_window_conflict,
    refresh_lock,
    release_lock,
    save_checkpoint,
)
from .scheduler import (
    create_scheduler_plan,
    get_next_runnable_slot,
    is_plan_complete,
    mark_slot_complete,
    mark_slot_running,
    render_scheduler_plan,
    update_plan_status,
)
from .session_store import extract_response_text, get_latest_request, request_needs_continue
from .snapshot import (
    SnapshotManager,
    cleanup_operator_stashes,
    should_rollback,
    should_snapshot,
    snapshot_summary,
    take_snapshot,
)
from .stop_controller import StopController, build_stop_controller_config
from .terminal import bold, cyan, dim, score_color, status_badge
from .validation import dump_json, run_validations
from .vscode_chat import (
    VSCodeChatError,
    VSCodeChatRetryableError,
    ensure_workspace_storage,
    send_chat_prompt,
    snapshot_chat_sessions,
    wait_for_completed_session,
    wait_for_session_file,
)
from .worker import Worker, WorkerStatus

logger = get_logger('operator')

# --- Chat directory resolution ---

# VS Code stores chat sessions in different locations across versions.
# Search these paths (in priority order) under the workspaceStorage folder.
_CHAT_DIR_CANDIDATES = [
    Path('GitHub.copilot-chat') / 'transcripts',       # VS Code ~1.114+
    Path('GitHub.copilot-chat') / 'chat-session-resources',  # Older versions
    Path('chatSessions'),                                # Legacy
]


def _resolve_chat_dir(storage: Path) -> Path:
    """Find the chat session directory under workspaceStorage, trying known locations."""
    for candidate in _CHAT_DIR_CANDIDATES:
        full = storage / candidate
        if full.exists() and any(full.glob('*.json*')):
            return full
    # Fallback: check if any candidate dir exists even if empty
    for candidate in _CHAT_DIR_CANDIDATES:
        full = storage / candidate
        if full.exists():
            return full
    # Default to the newest known path
    return storage / _CHAT_DIR_CANDIDATES[0]


# --- Error classification ---

_FATAL_EXCEPTIONS = (FileNotFoundError, PermissionError, NotADirectoryError)


def _is_retryable(exc: Exception) -> bool:
    """Return True if the error is transient and worth retrying."""
    if isinstance(exc, _FATAL_EXCEPTIONS):
        return False
    if isinstance(exc, VSCodeChatRetryableError):
        return True
    if isinstance(exc, VSCodeChatError):
        # Non-retryable VSCode errors (config, missing binary)
        return False
    # json parse errors from LLM output quirks are retryable
    if isinstance(exc, (_json.JSONDecodeError, ValueError, KeyError)):
        return True
    # Default: treat unknown errors as retryable
    return True


@dataclass(slots=True)
class Decision:
    action: str
    reason: str
    next_prompt: str = ''
    reason_code: str = ''


class CopilotOperator:
    def __init__(self, config: OperatorConfig, dry_run: bool = False, live: bool = False) -> None:
        self.config = config
        self.config.ensure_runtime_dirs()
        _merge_vscode_settings(config.workspace, forced_model=config.chat_model)
        self.runtime: dict[str, Any] = {}
        self.dry_run = dry_run
        self.live = live

        # Initialise LLM brain (if configured)
        llm_config = (
            load_llm_config_from_dict(config.llm_config_raw)
            if config.llm_config_raw
            else load_llm_config_from_env()
        )
        self._llm_brain: LLMBrain | None = LLMBrain(llm_config) if llm_config.is_ready else None

        # Worker runtime — maintains context across iterations
        self._worker = Worker(role='coder', max_iterations=config.max_iterations)
        self._lock_run_id: str = ''
        self._stop_ctrl = StopController(build_stop_controller_config(config))

    def _cleanup_guard(self) -> None:
        """Release lock + clear checkpoint on run exit."""
        if getattr(self, '_lock_run_id', ''):
            release_lock(self.config.workspace, self._lock_run_id)
        try:
            clear_checkpoint(self.config.workspace)
        except (OSError, TypeError, AttributeError):
            pass  # Workspace may be a mock in tests

    def _live_print(self, msg: str) -> None:
        """Print a live progress line to stderr (only when live=True)."""
        if self.live:
            print(msg, file=sys.stderr, flush=True)

    def run(self, goal: str | None = None, resume: bool = False) -> dict[str, Any]:
        storage = ensure_workspace_storage(self.config)
        chat_dir = _resolve_chat_dir(storage)
        chat_dir.mkdir(parents=True, exist_ok=True)

        goal_text, decision, iteration_start = self._prepare_run(goal, resume)

        # --- Runtime guard: acquire workspace lock ---
        run_id = str(self.runtime.get('runId', ''))
        if not self.dry_run:
            try:
                lock = acquire_lock(self.config.workspace, run_id)
                if lock is None:
                    raise RuntimeError(
                        'Another operator process is already running on this workspace. '
                        'Use --force to override or wait for the other process to finish.'
                    )
                self._lock_run_id = run_id
            except (OSError, TypeError):
                self._lock_run_id = ''

            # Same-window conflict detection
            try:
                conflict = detect_window_conflict(self.config.workspace)
                if conflict:
                    logger.warning('Window conflict: %s', conflict)
                    self._live_print(f'⚠ {conflict}')
            except (OSError, TypeError):
                pass
        else:
            self._lock_run_id = ''

        logger.info('Starting run: goal=%r dry_run=%s iterations=%d-%d',
                     goal_text[:80], self.dry_run, iteration_start, self.config.max_iterations)

        # Clean up any leftover operator stashes from a previous interrupted run
        if not resume:
            dropped = cleanup_operator_stashes()
            if dropped:
                logger.info('Cleaned up %d leftover operator stash(es)', dropped)

        error_retry_count = 0
        _MAX_ERROR_RETRIES = self.config.max_error_retries
        _BACKOFF_BASE = self.config.error_backoff_base_seconds
        try:
            _run_start_time = time.monotonic()
        except (TypeError, AttributeError):
            _run_start_time = 0
        # Track previous diff hash for no-progress detection
        _prev_diff_hash: str = ''

        for iteration in range(iteration_start, self.config.max_iterations + 1):
            # --- Refresh lock + save checkpoint ---
            if getattr(self, '_lock_run_id', ''):
                refresh_lock(self.config.workspace, self._lock_run_id)
            try:
                save_checkpoint(self.config.workspace, IterationCheckpoint(
                    run_id=run_id,
                    iteration=iteration,
                    goal=goal_text,
                    goal_profile=self.config.goal_profile,
                    status='running',
                    last_decision_action=decision.action,
                    last_decision_reason_code=decision.reason_code,
                    last_decision_next_prompt=decision.next_prompt[:500],
                    score=self.runtime.get('history', [{}])[-1].get('score') if self.runtime.get('history') else None,
                    timestamp=time.time(),
                    all_changed_files=self.runtime.get('allChangedFiles', []),
                ))
            except (OSError, TypeError):
                pass  # Best-effort checkpoint

            # --- Cost ceiling check ---
            _raw_cost = getattr(self.config, 'max_llm_cost_usd', None)
            _cost_limit = _raw_cost if isinstance(_raw_cost, (int, float)) else 0.0
            if _cost_limit > 0 and self._llm_brain and hasattr(self._llm_brain, 'stats'):
                cost = self._llm_brain.stats.get('estimated_cost_usd', 0)
                if cost >= self.config.max_llm_cost_usd:
                    self.runtime['status'] = 'blocked'
                    self.runtime['finishedAt'] = self._now()
                    self.runtime['finalReason'] = f'LLM cost ceiling reached: ${cost:.4f} >= ${_cost_limit:.4f}'
                    self.runtime['finalReasonCode'] = 'COST_CEILING_REACHED'
                    self.runtime['pendingDecision'] = {
                        'action': 'continue',
                        'reason': 'Cost ceiling reached — can be resumed with higher limit.',
                        'reasonCode': 'COST_CEILING_REACHED',
                        'nextPrompt': decision.next_prompt,
                    }
                    self._write_runtime()
                    result = {
                        'status': 'blocked',
                        'reason': f'LLM cost ceiling reached: ${cost:.4f}',
                        'reasonCode': 'COST_CEILING_REACHED',
                        'iterations': iteration - 1,
                        'stateFile': str(self.config.state_file),
                    }
                    dump_json(self.config.summary_file, result)
                    self._post_run_hooks(result)
                    return result

            # --- Time ceiling check ---
            _raw_time = getattr(self.config, 'max_task_seconds', None)
            _time_limit = _raw_time if isinstance(_raw_time, (int, float)) else 0
            if _time_limit > 0:
                try:
                    elapsed = float(time.monotonic() - _run_start_time)
                except (TypeError, AttributeError, ValueError):
                    elapsed = 0.0
                if elapsed >= _time_limit:
                    self.runtime['status'] = 'blocked'
                    self.runtime['finishedAt'] = self._now()
                    self.runtime['finalReason'] = f'Time ceiling reached: {elapsed:.0f}s >= {_time_limit}s'
                    self.runtime['finalReasonCode'] = 'TIME_CEILING_REACHED'
                    self.runtime['pendingDecision'] = {
                        'action': 'continue',
                        'reason': 'Time ceiling reached — can be resumed.',
                        'reasonCode': 'TIME_CEILING_REACHED',
                        'nextPrompt': decision.next_prompt,
                    }
                    self._write_runtime()
                    result = {
                        'status': 'blocked',
                        'reason': f'Time ceiling reached: {elapsed:.0f}s',
                        'reasonCode': 'TIME_CEILING_REACHED',
                        'iterations': iteration - 1,
                        'stateFile': str(self.config.state_file),
                    }
                    dump_json(self.config.summary_file, result)
                    self._post_run_hooks(result)
                    return result

            try:
                result = self._run_iteration(iteration, goal_text, decision, chat_dir)
                if result is not None:
                    return result
                error_retry_count = 0  # reset on successful iteration
                decision = self.runtime['_last_decision']
            except KeyboardInterrupt:
                logger.warning('Interrupted at iteration %d', iteration)
                self.runtime['status'] = 'interrupted'
                self.runtime['finishedAt'] = self._now()
                self.runtime['finalReason'] = f'Interrupted at iteration {iteration}'
                self.runtime['finalReasonCode'] = 'INTERRUPTED'
                self._write_runtime()
                self._cleanup_guard()
                raise
            except Exception as exc:
                retryable = _is_retryable(exc)
                error_type = type(exc).__name__
                logger.error('Error in iteration %d (%s, retryable=%s): %s', iteration, error_type, retryable, exc, exc_info=True)
                self.runtime.setdefault('errors', []).append({
                    'iteration': iteration,
                    'error': str(exc),
                    'type': error_type,
                    'retryable': retryable,
                    'timestamp': self._now(),
                })
                self._write_runtime()

                # Fatal errors stop immediately
                if not retryable:
                    logger.error('Fatal error (non-retryable), stopping run.')
                    self.runtime['status'] = 'error'
                    self.runtime['finishedAt'] = self._now()
                    self.runtime['finalReason'] = f'Fatal error: {exc}'
                    self.runtime['finalReasonCode'] = 'FATAL_ERROR'
                    self.runtime['pendingDecision'] = None
                    self._write_runtime()
                    result = {
                        'status': 'error',
                        'reason': str(exc),
                        'reasonCode': 'FATAL_ERROR',
                        'errorType': error_type,
                        'retryable': False,
                        'iterations': iteration,
                        'stateFile': str(self.config.state_file),
                        'summaryFile': str(self.config.summary_file),
                    }
                    dump_json(self.config.summary_file, result)
                    self._cleanup_guard()
                    return result

                # Retryable error — apply backoff
                error_retry_count += 1
                if error_retry_count >= _MAX_ERROR_RETRIES:
                    logger.error('Stopping after %d consecutive retryable errors', error_retry_count)
                    self.runtime['status'] = 'error'
                    self.runtime['finishedAt'] = self._now()
                    self.runtime['finalReason'] = f'Stopped after {error_retry_count} consecutive errors: {exc}'
                    self.runtime['finalReasonCode'] = 'CONSECUTIVE_ERRORS'
                    self.runtime['pendingDecision'] = {
                        'action': 'continue',
                        'reason': f'Stopped after {error_retry_count} consecutive errors — can be auto-resumed.',
                        'reasonCode': 'CONSECUTIVE_ERRORS',
                        'nextPrompt': decision.next_prompt,
                    }
                    self._write_runtime()
                    result = {
                        'status': 'error',
                        'reason': str(exc),
                        'reasonCode': 'CONSECUTIVE_ERRORS',
                        'iterations': iteration,
                        'stateFile': str(self.config.state_file),
                        'summaryFile': str(self.config.summary_file),
                    }
                    dump_json(self.config.summary_file, result)
                    self._cleanup_guard()
                    return result

                # Backoff delay: 2, 4, 8, ... capped at 30s
                delay = min(_BACKOFF_BASE ** error_retry_count, 30.0)
                logger.info('Backoff %.1fs before retry (%d/%d)', delay, error_retry_count, _MAX_ERROR_RETRIES)
                time.sleep(delay)

                # Session reset hint after 2+ consecutive errors
                session_hint = ''
                if error_retry_count >= 2:
                    session_hint = '\n\nPrevious session had errors. Start fresh analysis of the workspace.'

                decision = Decision(
                    action='continue',
                    reason=f'Retrying after error: {exc}',
                    next_prompt=decision.next_prompt + session_hint,
                    reason_code='RETRY_AFTER_ERROR',
                )
                continue

        self.runtime['status'] = 'blocked'
        self.runtime['finishedAt'] = self._now()
        self.runtime['finalReason'] = 'Maximum iteration count reached.'
        self.runtime['finalReasonCode'] = 'MAX_ITERATIONS_REACHED'
        self.runtime['pendingDecision'] = {
            'action': 'continue',
            'reason': 'Maximum iteration count reached — can be auto-resumed.',
            'reasonCode': 'MAX_ITERATIONS_REACHED',
            'nextPrompt': decision.next_prompt,
        }
        self._write_runtime()
        result = {
            'status': 'blocked',
            'reason': 'Maximum iteration count reached.',
            'reasonCode': 'MAX_ITERATIONS_REACHED',
            'iterations': self.config.max_iterations,
            'plan': self.runtime.get('plan', {}),
            'stateFile': str(self.config.state_file),
            'memoryFile': str(self.config.memory_file),
            'summaryFile': str(self.config.summary_file),
            'logDir': str(self._run_log_dir()),
        }
        dump_json(self.config.summary_file, result)
        self._post_run_hooks(result)
        return result

    def _run_iteration(self, iteration: int, goal_text: str, decision: Decision, chat_dir: Path) -> dict[str, Any] | None:
        """Execute a single iteration. Returns a result dict if the run should stop, or None to continue."""
        logger.info('Iteration %d: action=%s reason_code=%s', iteration, decision.action, decision.reason_code)
        self._live_print(
            f'\n{cyan(f"[{iteration}/{self.config.max_iterations}]")}'  # noqa: E501
            f' Starting iteration {dim("—")} {bold(decision.reason_code)}'
        )

        # Feature D: Snapshot before each iteration
        if hasattr(self, '_snapshot_mgr') and should_snapshot(iteration):
            prev_score = self.runtime.get('history', [])[-1].get('score') if self.runtime.get('history') else None
            take_snapshot(self._snapshot_mgr, iteration, score=prev_score, reason=f'Pre-iteration {iteration}')

        before_validation_results = run_validations(self.config.validation, self.config.workspace, phase='before_prompt')
        prompt = self._build_prompt(goal_text, decision, before_validation_results)
        prompt_path = self._artifact_path(iteration, 'prompt.md')
        prompt_path.write_text(prompt, encoding='utf-8')
        before_validation_path = self._artifact_path(iteration, 'validation.before.json')
        dump_json(before_validation_path, before_validation_results)

        # Dry-run: write prompt but skip VS Code interaction
        if self.dry_run:
            logger.info('DRY RUN: prompt written to %s — skipping VS Code chat', prompt_path)
            self.runtime['_last_decision'] = Decision(
                action='stop', reason='Dry-run mode — prompt generated only.',
                reason_code='DRY_RUN',
            )
            return {
                'status': 'dry_run',
                'reason': 'Dry-run mode — prompt generated, no VS Code interaction.',
                'reasonCode': 'DRY_RUN',
                'iterations': iteration,
                'promptPath': str(prompt_path),
                'stateFile': str(self.config.state_file),
            }

        baseline = snapshot_chat_sessions(chat_dir)
        attachment_paths = [self.config.memory_file, *self._existing_project_memory_files()]

        # On iteration 2+, attach previously changed files so Copilot has fresh context
        if iteration > 1:
            for rel_path in self.runtime.get('allChangedFiles', [])[:20]:
                abs_path = self.config.workspace / rel_path
                if abs_path.exists() and abs_path.is_file() and abs_path.stat().st_size < 50_000:
                    attachment_paths.append(abs_path)

        send_chat_prompt(self.config, prompt, add_files=attachment_paths)
        try:
            session_path = wait_for_session_file(chat_dir, baseline, self.config.session_timeout_seconds, self.config.poll_interval_seconds)
        except VSCodeChatRetryableError:
            # Session file not detected, but Copilot may have completed the work.
            # Check if workspace files changed (git diff) as a fallback signal.
            try:
                diff_result = subprocess.run(
                    ['git', 'diff', '--stat', 'HEAD'],
                    cwd=str(self.config.workspace), capture_output=True, text=True, timeout=10, check=False,
                )
                if diff_result.stdout.strip():
                    logger.warning('Session file timeout but workspace has changes — running validation fallback.')
                else:
                    raise  # No changes detected, re-raise the timeout
            except (FileNotFoundError, OSError):
                raise  # git not available, re-raise the timeout
            # Fallback: generate a synthetic response for validation
            response_text = (
                'Session file not captured, but workspace changes detected.\n'
                '<OPERATOR_STATE>{"status": "needs_validation", "summary": "Changes detected via git diff after session timeout.", '
                '"blockers": [], "score": 0, "next_prompt": ""}</OPERATOR_STATE>\n'
                '<OPERATOR_PLAN>{}</OPERATOR_PLAN>'
            )
            response_path = self._artifact_path(iteration, 'response.md')
            response_path.write_text(response_text, encoding='utf-8')
            assessment = parse_operator_state(response_text)
            if assessment is None:
                assessment = fallback_assessment(response_text, False)
            # Skip the normal session parsing and go directly to validation
            plan_update = parse_operator_plan(response_text)
            self.runtime['plan'] = merge_plan(self.runtime.get('plan'), plan_update, goal_text, terminal_status=assessment.status)
            # Continue to validation below — assessment.score will be 0 so validation decides
        else:
            session = wait_for_completed_session(session_path, self.config)
            request = get_latest_request(session)
            response_text = extract_response_text(request)
        response_path = self._artifact_path(iteration, 'response.md')
        response_path.write_text(response_text, encoding='utf-8')

        assessment = parse_operator_state(response_text)
        needs_continue = request_needs_continue(request)
        if assessment is None:
            assessment = fallback_assessment(response_text, needs_continue)
        else:
            assessment.needs_continue = assessment.needs_continue or needs_continue

        plan_update = parse_operator_plan(response_text)
        self.runtime['plan'] = merge_plan(self.runtime.get('plan'), plan_update, goal_text, terminal_status=assessment.status)
        plan_snapshot = summarize_plan(self.runtime.get('plan'))

        # Protected paths enforcement — check BEFORE validation so we can rollback first
        protected_violations = self._check_protected_paths()
        if protected_violations:
            violated_str = ', '.join(protected_violations[:5])
            policy = self.config.escalation_policy

            if policy == 'warn':
                # Warn only — log and continue
                logger.warning('Protected path modified (warn policy) at iteration %d: %s', iteration, violated_str)
                self._live_print(
                    f'{cyan(f"[{iteration}/{self.config.max_iterations}]")} '
                    f'⚠ Protected path warning: {violated_str}'
                )
            elif policy == 'escalate':
                # Escalate — pause for human decision
                logger.warning('Protected path escalation at iteration %d: %s', iteration, violated_str)
                self._live_print(
                    f'{cyan(f"[{iteration}/{self.config.max_iterations}]")} '
                    f'{status_badge("error")} Protected path escalation: {violated_str}'
                )
                diff_summary = ''
                try:
                    diff_summary = get_diff_summary(self.config.workspace)
                except Exception:
                    pass
                self.runtime['status'] = 'escalated'
                self.runtime['finishedAt'] = self._now()
                self.runtime['finalReason'] = f'Protected path(s) modified — escalation required: {violated_str}'
                self.runtime['finalReasonCode'] = 'ESCALATION_REQUIRED'
                self.runtime['pendingEscalation'] = {
                    'type': 'protected_path',
                    'paths': protected_violations,
                    'iteration': iteration,
                    'diffSummary': diff_summary,
                    'timestamp': self._now(),
                }
                self.runtime['pendingDecision'] = {
                    'action': 'escalate',
                    'reason': f'Protected path(s) modified: {violated_str}',
                    'reasonCode': 'ESCALATION_REQUIRED',
                    'nextPrompt': decision.next_prompt,
                }
                self._write_runtime()
                result = {
                    'status': 'escalated',
                    'reason': f'Protected path(s) modified — human approval required: {violated_str}',
                    'reasonCode': 'ESCALATION_REQUIRED',
                    'violations': protected_violations,
                    'iterations': iteration,
                    'stateFile': str(self.config.state_file),
                }
                dump_json(self.config.summary_file, result)
                self._post_run_hooks(result)
                return result
            else:
                # Block (original behavior)
                logger.warning('Protected path violation at iteration %d: %s', iteration, violated_str)
                self._live_print(
                    f'{cyan(f"[{iteration}/{self.config.max_iterations}]")} '
                    f'{status_badge("error")} Protected path violation: {violated_str}'
                )
                self.runtime['status'] = 'error'
                self.runtime['finishedAt'] = self._now()
                self.runtime['finalReason'] = f'Copilot modified protected path(s): {violated_str}'
                self.runtime['finalReasonCode'] = 'PROTECTED_PATH_VIOLATION'
                self.runtime['pendingDecision'] = None
                self._write_runtime()
                if hasattr(self, '_snapshot_mgr'):
                    from .snapshot import rollback_to_last_good
                    rollback_to_last_good(self._snapshot_mgr)
                result = {
                    'status': 'error',
                    'reason': f'Copilot modified protected path(s): {violated_str}',
                    'reasonCode': 'PROTECTED_PATH_VIOLATION',
                    'violations': protected_violations,
                    'iterations': iteration,
                    'stateFile': str(self.config.state_file),
                }
                dump_json(self.config.summary_file, result)
                self._post_run_hooks(result)
                return result

        after_validation_results = run_validations(self.config.validation, self.config.workspace, phase='after_response')
        after_validation_path = self._artifact_path(iteration, 'validation.after.json')
        dump_json(after_validation_path, after_validation_results)

        decision = self._decide(assessment, after_validation_results, iteration)
        decision_path = self._artifact_path(iteration, 'decision.json')
        dump_json(
            decision_path,
            {
                'action': decision.action,
                'reason': decision.reason,
                'reasonCode': decision.reason_code,
                'nextPrompt': decision.next_prompt,
                'assessment': assessment.raw,
                'plan': self.runtime.get('plan', {}),
            },
        )

        record = {
            'iteration': iteration,
            'timestamp': self._now(),
            'sessionId': session.get('sessionId') or session_path.stem,
            'sessionPath': str(session_path),
            'status': assessment.status,
            'score': assessment.score,
            'summary': assessment.summary,
            'next_prompt': assessment.next_prompt,
            'needs_continue': assessment.needs_continue,
            'tests': assessment.tests,
            'lint': assessment.lint,
            'blockers': assessment.blockers,
            'done_reason': assessment.done_reason,
            'decisionCode': decision.reason_code,
            'decisionNextPrompt': decision.next_prompt,
            'planSummary': plan_snapshot.get('summary', ''),
            'currentMilestoneId': plan_snapshot.get('currentMilestoneId', ''),
            'nextMilestoneId': plan_snapshot.get('nextMilestoneId', ''),
            'currentTaskId': plan_snapshot.get('currentTaskId', ''),
            'nextTaskId': plan_snapshot.get('nextTaskId', ''),
            'taskCounts': plan_snapshot.get('taskCounts', {}),
            'artifacts': {
                'prompt': str(prompt_path),
                'response': str(response_path),
                'decision': str(decision_path),
                'validationBefore': str(before_validation_path),
                'validationAfter': str(after_validation_path),
            },
            'validation_before': before_validation_results,
            'validation_after': after_validation_results,
        }
        self.runtime['history'].append(record)
        self._compact_history()  # keep runtime memory bounded
        self.runtime['lastResponsePath'] = str(response_path)
        self.runtime['lastPromptPath'] = str(prompt_path)
        self.runtime['updatedAt'] = self._now()

        # Record iteration in worker for context continuity
        self._worker.record_iteration(
            iteration=iteration,
            score=assessment.score,
            decision_code=decision.reason_code,
            summary=assessment.summary or '',
            changed_files=record.get('changedFiles', []),
            error=None,
        )
        self._worker.status = WorkerStatus.RUNNING

        # Track changed files for context enrichment in subsequent iterations
        try:
            changed = get_all_changed_files(self.config.workspace)
            record['changedFiles'] = changed
            all_changed = set(self.runtime.get('allChangedFiles', []))
            all_changed.update(changed)
            self.runtime['allChangedFiles'] = sorted(all_changed)
        except Exception:
            pass

        # --- Stop controller: record diff + evaluate ---
        try:
            diff_for_stop = get_diff_summary(self.config.workspace)
            if hasattr(self, '_stop_ctrl'):
                self._stop_ctrl.record_diff(diff_for_stop or '')
        except Exception:
            pass
        if hasattr(self, '_stop_ctrl'):
            stop_signal = self._stop_ctrl.evaluate(iteration, self.runtime.get('history', []))
        else:
            from .stop_controller import StopSignal
            stop_signal = StopSignal()
        if stop_signal.should_stop and decision.action != 'stop':
            logger.warning('Stop controller triggered: %s (hard=%s)', stop_signal.reason_code, stop_signal.is_hard_stop)
            self._live_print(f'⚠ Stop controller: {stop_signal.reason}')
            if stop_signal.is_hard_stop:
                decision = Decision(
                    action='stop',
                    reason=f'[HARD STOP] {stop_signal.reason}',
                    reason_code=stop_signal.reason_code,
                )
            else:
                decision = Decision(
                    action='stop',
                    reason=f'[SOFT STOP → escalate] {stop_signal.reason}',
                    next_prompt=decision.next_prompt,
                    reason_code=stop_signal.reason_code,
                )

        self.runtime['pendingDecision'] = {
            'action': decision.action,
            'reason': decision.reason,
            'reasonCode': decision.reason_code,
            'nextPrompt': decision.next_prompt,
        }
        self._write_runtime()

        logger.info('Iteration %d result: score=%s status=%s decision=%s',
                     iteration, assessment.score, assessment.status, decision.action)

        # Live progress summary line — includes diff summary when available
        validation_summary = ''
        pass_count = sum(1 for v in after_validation_results if v.get('status') == 'pass')
        fail_count = sum(1 for v in after_validation_results if v.get('status') != 'pass')
        if after_validation_results:
            validation_summary = f' | validations: {pass_count}✓/{fail_count}✗'
        diff_summary = ''
        if is_git_repo(self.config.workspace):
            raw_diff = get_diff_summary(self.config.workspace)
            if raw_diff:
                # Show only the last summary line e.g. "3 files changed, 42 insertions(+), 5 deletions(-)"
                last_line = raw_diff.strip().splitlines()[-1]
                diff_summary = f' | {dim(last_line)}'
        self._live_print(
            f'{cyan(f"[{iteration}/{self.config.max_iterations}]")} '
            f'score={score_color(assessment.score)} '
            f'status={status_badge(assessment.status)} {dim("→")} {bold(decision.reason_code)}{validation_summary}{diff_summary}'
        )

        if decision.action == 'stop':
            self.runtime['status'] = 'complete' if assessment.status == 'done' else 'blocked'
            self.runtime['finishedAt'] = self._now()
            self.runtime['finalReason'] = decision.reason
            self.runtime['finalReasonCode'] = decision.reason_code
            self.runtime['pendingDecision'] = None
            self._write_runtime()
            result = {
                'status': self.runtime['status'],
                'reason': decision.reason,
                'reasonCode': decision.reason_code,
                'iterations': iteration,
                'score': assessment.score,
                'sessionId': record['sessionId'],
                'nextPrompt': assessment.next_prompt,
                'plan': self.runtime.get('plan', {}),
                'stateFile': str(self.config.state_file),
                'memoryFile': str(self.config.memory_file),
                'summaryFile': str(self.config.summary_file),
                'logDir': str(self._run_log_dir()),
            }
            dump_json(self.config.summary_file, result)
            self._post_run_hooks(result)
            return result

        self.runtime['_last_decision'] = decision
        return None

    def _prepare_run(self, goal: str | None, resume: bool) -> tuple[str, Decision, int]:
        if resume:
            return self._prepare_resume_run(goal)
        return self._prepare_fresh_run(goal)

    def _prepare_fresh_run(self, goal: str | None) -> tuple[str, Decision, int]:
        goal_text = (goal or '').strip()
        if not goal_text:
            raise ValueError('A non-empty goal is required for a fresh run.')
        run_id = str(uuid4())

        # Smart plan: use goal decomposer instead of dumb single-milestone fallback
        inferred_profile = classify_goal(goal_text)
        plan_profile = self.config.goal_profile if self.config.goal_profile != 'default' else inferred_profile
        initial_plan = decompose_goal(goal_text, plan_profile)

        # Try LLM-powered decomposition for a smarter plan
        if self._llm_brain and self._llm_brain.is_ready:
            llm_plan = decompose_goal_with_llm(goal_text, plan_profile, self._llm_brain)
            if llm_plan:
                initial_plan = llm_plan

        # Git safety check
        git_safety = pre_run_safety_check(self.config.workspace) if is_git_repo(self.config.workspace) else {}

        # Project brain: load insights from past runs
        self._project_insights = self._load_project_insights()

        # Feature A: Meta-learner — load learned prompt rules
        self._learned_rules = load_rules(self.config.workspace)

        # Feature D: Snapshot manager — git-based rollback safety net
        self._snapshot_mgr = SnapshotManager(max_snapshots=self.config.max_snapshots)

        # Feature E: Cross-repo shared brain
        self._shared_brain = load_shared_brain()

        # Feature F: Intention-aware guardrails for this goal type
        self._intention_goal_type = plan_profile

        self.runtime = {
            'runId': run_id,
            'goal': goal_text,
            'goalProfile': self.config.goal_profile,
            'inferredProfile': inferred_profile,
            'startedAt': self._now(),
            'updatedAt': self._now(),
            'workspace': str(self.config.workspace),
            'mode': self.config.mode,
            'targetScore': self.config.target_score,
            'repoProfile': {
                'path': str(self.config.repo_profile.path) if self.config.repo_profile.path else '',
                'repoName': self.config.repo_profile.repo_name,
            },
            'workspaceInsight': as_dict(self.config.workspace_insight),
            'gitSafety': git_safety,
            'plan': initial_plan,
            'history': [],
            'status': 'running',
            'resumeCount': 0,
            'pendingDecision': {
                'action': 'continue',
                'reason': 'Initial execution',
                'reasonCode': 'INITIAL_EXECUTION',
                'nextPrompt': goal_text,
            },
        }
        self._write_runtime()
        cleanup_result = cleanup_run_logs(self.config.log_dir, self.config.log_retention_runs, current_run_id=run_id)
        self.runtime['logCleanup'] = cleanup_result
        self._write_runtime()
        return goal_text, Decision(action='continue', reason='Initial execution', next_prompt=goal_text, reason_code='INITIAL_EXECUTION'), 1

    def _prepare_resume_run(self, goal: str | None) -> tuple[str, Decision, int]:
        if not self.config.state_file.exists():
            raise ValueError(f'Cannot resume because state file does not exist: {self.config.state_file}')
        runtime = self._read_runtime_state()
        history = runtime.get('history', [])
        status = str(runtime.get('status', '')).strip().lower()
        if status == 'complete':
            raise ValueError('Cannot resume a completed run.')
        if not history and not runtime.get('goal') and not goal:
            raise ValueError('Cannot resume without a previous goal.')

        goal_text = (goal or runtime.get('goal') or '').strip()
        if not goal_text:
            raise ValueError('Cannot resume because the goal is empty.')

        pending = runtime.get('pendingDecision') or {}
        last_record = history[-1] if history else {}
        next_prompt = str(
            pending.get('nextPrompt')
            or last_record.get('decisionNextPrompt')
            or last_record.get('next_prompt')
            or goal_text
        ).strip()
        reason = str(
            pending.get('reason')
            or f"Resumed after iteration {last_record.get('iteration', 0)}"
        ).strip()
        reason_code = str(pending.get('reasonCode') or 'RESUMED_RUN').strip() or 'RESUMED_RUN'

        runtime['runId'] = str(runtime.get('runId') or uuid4())
        runtime['goal'] = goal_text
        runtime['goalProfile'] = str(runtime.get('goalProfile') or self.config.goal_profile)
        runtime['status'] = 'running'
        runtime['updatedAt'] = self._now()
        runtime['resumeCount'] = int(runtime.get('resumeCount', 0)) + 1
        if runtime['resumeCount'] > 10:
            raise ValueError(f"Resume limit exceeded ({runtime['resumeCount']} resumes). The run may be stuck in a loop. Start a new run instead.")
        runtime['workspaceInsight'] = as_dict(self.config.workspace_insight)
        runtime['plan'] = merge_plan(runtime.get('plan'), None, goal_text)
        runtime['pendingDecision'] = {
            'action': 'continue',
            'reason': reason,
            'reasonCode': reason_code,
            'nextPrompt': next_prompt,
        }
        self.runtime = runtime
        self._write_runtime()
        return goal_text, Decision(action='continue', reason=reason, next_prompt=next_prompt, reason_code=reason_code), len(history) + 1

    def _build_prompt(self, goal: str, decision: Decision, validation_results: list[dict[str, Any]]) -> str:
        history = self.runtime.get('history', [])

        # Intelligence: analyse current state for prompt enrichment
        dx = self._diagnose(len(history) + 1)
        intelligence_text = format_diagnosis_for_prompt(dx) if history else ''

        # Brain: project-level insights from past runs
        brain_text = ''
        if hasattr(self, '_project_insights'):
            brain_text = render_insights_for_prompt(self._project_insights)

        # Feature F: Intention-aware guardrails + Feature A: meta-learner guardrails
        intention_text = ''
        goal_type = getattr(self, '_intention_goal_type', 'generic')
        meta_guardrails = render_guardrails(self._learned_rules) if hasattr(self, '_learned_rules') else ''
        extra_guardrails = [meta_guardrails] if meta_guardrails else []
        # Adaptive: learn guardrails from this run's history
        adaptive_guardrails = learn_guardrails_from_history(goal_type, history)
        extra_guardrails.extend(adaptive_guardrails)
        intention_text = render_combined_guardrails(goal_type, extra_guardrails)

        # Feature E: Cross-repo brain insights
        cross_repo_text = ''
        if hasattr(self, '_shared_brain'):
            repo_name = self.config.repo_profile.repo_name or ''
            cross_repo_text = render_cross_repo_insights(self._shared_brain, current_repo=repo_name)

        # Mission context: inject high-level project direction
        mission_text = ''
        try:
            mission = load_mission(self.config.workspace)
            mission_text = render_mission_for_prompt(mission)
        except Exception:
            pass

        # LLM Brain: enrich intelligence with real AI analysis when available
        llm_text = ''
        if self._llm_brain and self._llm_brain.is_ready and history and dx.risk_level in ('medium', 'high', 'critical'):
            llm_text = self._ask_llm_for_guidance(goal, history, dx)

        # Combine intelligence sources
        full_intelligence = intelligence_text
        if llm_text:
            full_intelligence = f'{intelligence_text}\n\n## LLM Brain Analysis\n{llm_text}' if intelligence_text else llm_text

        # Worker context — condensed history for session continuity
        worker_context = self._worker.build_context_summary()
        if worker_context:
            full_intelligence = f'{full_intelligence}\n\n{worker_context}' if full_intelligence else worker_context

        # Mission context — project direction to prevent goal drift
        if mission_text:
            full_intelligence = f'{mission_text}\n\n{full_intelligence}' if full_intelligence else mission_text

        context = build_prompt_context(
            history,
            validation_results,
            self.config.repo_profile,
            self.config.workspace,
            self.config.workspace_insight,
            self.config.goal_profile,
            self.runtime.get('plan'),
            intelligence_text=full_intelligence,
            brain_text=brain_text,
            intention_text=intention_text,
            cross_repo_text=cross_repo_text,
            repo_map_text=self._get_repo_map_text(goal),
        )
        if not history:
            return build_initial_prompt(goal, self.config.target_score, context)
        return build_follow_up_prompt(
            goal=goal,
            baton=decision.next_prompt,
            target_score=self.config.target_score,
            context=context,
            reason=decision.reason,
        )

    def _decide(self, assessment: Assessment, validation_results: list[dict[str, Any]], iteration: int) -> Decision:
        failing_blockers = [
            item for item in assessment.blockers if item.get('severity', '').lower() in self.config.fail_on_blockers
        ]
        required_validation_failures = [
            item for item in validation_results if item.get('required') and item.get('status') != 'pass'
        ]

        if assessment.status == 'blocked':
            if iteration >= self.config.max_iterations:
                return Decision(
                    action='stop',
                    reason=assessment.done_reason or 'Copilot reported a blocking condition.',
                    reason_code='COPILOT_BLOCKED',
                )
            return Decision(
                action='continue',
                reason='Copilot did not emit a structured OPERATOR_STATE block — retrying with explicit format reminder.',
                next_prompt=(
                    'You must end your response with a valid OPERATOR_STATE block in exactly this format:\n\n'
                    '<OPERATOR_STATE>\n{"status":"done","score":90,"summary":"...","blockers":[],"tests":"pass","lint":"pass"}\n</OPERATOR_STATE>\n\n'
                    'If the task is already complete, report status="done" with what you did. '
                    'If there is still work to do, status="in_progress". Do not omit the block.'
                ),
                reason_code='COPILOT_BLOCKED',
            )

        if assessment.status == 'done':
            if assessment.score is None or assessment.score < self.config.target_score:
                reason = f"Copilot declared done but score {assessment.score} is below target {self.config.target_score}."
                prompt = 'You marked the task done too early. Re-open the work, inspect the remaining gaps, fix them, and end with a fresh OPERATOR_STATE block.'
                return Decision(action='continue', reason=reason, next_prompt=prompt, reason_code='DONE_BELOW_TARGET')
            if failing_blockers:
                blocker_text = '; '.join(item['item'] for item in failing_blockers if item.get('item'))
                reason = f'Copilot declared done but high-severity blockers remain: {blocker_text}'
                prompt = 'You marked the task done too early. Resolve every critical/high blocker, then rerun the relevant checks and emit OPERATOR_STATE again.'
                return Decision(action='continue', reason=reason, next_prompt=prompt, reason_code='DONE_HAS_BLOCKERS')
            if required_validation_failures:
                failed_names = ', '.join(item['name'] for item in required_validation_failures)
                reason = f'Copilot declared done but required operator-side validation failed: {failed_names}'
                prompt = f'The operator-side validation failed for: {failed_names}. Inspect those failures, fix the workspace, rerun checks, and end with OPERATOR_STATE again.'
                return Decision(action='continue', reason=reason, next_prompt=prompt, reason_code='VALIDATION_FAILED')
            return Decision(action='stop', reason=assessment.done_reason or 'Stop gate passed.', reason_code='STOP_GATE_PASSED')

        if iteration >= self.config.max_iterations:
            return Decision(action='stop', reason='Maximum iteration count reached before completion.', reason_code='MAX_ITERATIONS_REACHED')

        if assessment.needs_continue:
            return Decision(
                action='continue',
                reason='Copilot signaled that another continuation turn is required.',
                next_prompt=self._resolve_continue_baton(
                    assessment.next_prompt,
                    'Continue from the first unfinished task. Do not repeat prior summary.',
                ),
                reason_code='CONTINUE_REQUIRED',
            )

        # --- Intelligence: detect loops and adapt ---
        dx = self._diagnose(iteration)
        if should_replan(dx):
            replan_prompt = build_replan_prompt(dx, self.runtime.get('plan'), self.runtime.get('goal', ''))
            return Decision(
                action='continue',
                reason=f'Intelligence detected stuck loop (risk={dx.risk_level}). Re-planning.',
                next_prompt=replan_prompt,
                reason_code='REPLAN_TRIGGERED',
            )

        # Feature D: Snapshot rollback on score regression
        if hasattr(self, '_snapshot_mgr') and len(self.runtime.get('history', [])) >= 2:
            prev_score = self.runtime['history'][-2].get('score')
            curr_score = assessment.score
            if should_rollback(curr_score, prev_score):
                from .snapshot import rollback_to_last_good
                rolled_back = rollback_to_last_good(self._snapshot_mgr)
                if rolled_back:
                    return Decision(
                        action='continue',
                        reason=f'Score regressed from {prev_score} to {curr_score}. Rolled back to snapshot iter {rolled_back.iteration}.',
                        next_prompt='The workspace was rolled back to a previous good state because the last change caused a score regression. '
                        'Inspect the current workspace carefully and retry with a different approach.',
                        reason_code='SNAPSHOT_ROLLBACK',
                    )

        # Feature B: Adversarial critic check
        if should_run_critic(iteration, assessment.score, self.config.target_score):
            critic_prompt = build_critic_prompt(
                goal=self.runtime.get('goal', ''),
                coder_summary=assessment.summary,
                coder_score=assessment.score,
            )
            # Store critic prompt for the next iteration to use
            self.runtime['_pending_critic'] = critic_prompt

        # Use intelligence hints to enrich the baton if available
        base_baton = self._resolve_continue_baton(
            assessment.next_prompt,
            'Continue from the first unfinished task in the workspace.',
        )
        if dx.hints:
            hint_text = format_diagnosis_for_prompt(dx)
            if hint_text:
                base_baton = f'{hint_text}\n\n{base_baton}'

        return Decision(
            action='continue',
            reason='Copilot reported that more work remains.',
            next_prompt=base_baton,
            reason_code='WORK_REMAINS',
        )

    def _resolve_continue_baton(self, candidate: str, fallback: str) -> str:
        plan_baton = build_milestone_baton(self.runtime.get('plan'), fallback)
        candidate_text = str(candidate or '').strip()
        if candidate_text and not is_generic_baton(candidate_text):
            return candidate_text
        return plan_baton or candidate_text or fallback

    def _write_runtime(self) -> None:
        self._sync_runtime_paths()
        # '_last_decision' holds a Decision dataclass — strip it before serialization
        last_decision = self.runtime.pop('_last_decision', None)
        try:
            dump_json(self.config.state_file, self.runtime)
        finally:
            if last_decision is not None:
                self.runtime['_last_decision'] = last_decision
        self.config.memory_file.write_text(self._render_memory(), encoding='utf-8')

    def _compact_history(self, keep_full: int = 5) -> None:
        """Compact old history entries to reduce baton/state size.

        Keeps the last *keep_full* entries intact. Older entries are trimmed
        to just the essential fields (iteration, score, status, decisionCode).
        """
        history = self.runtime.get('history', [])
        if len(history) <= keep_full:
            return
        _COMPACT_KEYS = {'iteration', 'score', 'status', 'decisionCode', 'summary', 'tests', 'lint'}
        for i in range(len(history) - keep_full):
            entry = history[i]
            history[i] = {k: entry[k] for k in _COMPACT_KEYS if k in entry}
            # Truncate summary in compacted entries
            if 'summary' in history[i]:
                history[i]['summary'] = str(history[i]['summary'])[:120]

    def _sync_runtime_paths(self) -> None:
        self.runtime['logRootDir'] = str(self.config.log_dir)
        self.runtime['logDir'] = str(self._run_log_dir())

    def _render_memory(self) -> str:
        history = self.runtime.get('history', [])
        plan_snapshot = summarize_plan(self.runtime.get('plan'))

        # --- Layer 1: Repo Memory (permanent / cross-run context) ---
        lines = [
            '# Copilot Operator Memory',
            '',
            '## Layer 1 — Repo Context (persistent)',
            f"- Workspace: {self.runtime.get('workspace', '')}",
            f"- Ecosystem: {self.config.workspace_insight.ecosystem}",
            f"- Package manager: {self.config.workspace_insight.package_manager or 'unknown'}",
            f"- Mode: {self.runtime.get('mode', '')}",
        ]
        if self.config.repo_profile.repo_name:
            lines.append(f"- Repo: {self.config.repo_profile.repo_name}")
        if self.config.repo_profile.summary:
            lines.append(f"- Repo summary: {self.config.repo_profile.summary}")
        lines.append(render_brain_status(self._llm_brain))

        # Add project brain insights (cross-run learnings)
        if hasattr(self, '_project_insights'):
            brain_text = render_insights_for_memory(self._project_insights)
            if brain_text:
                lines.append(brain_text)

        # --- Layer 2: Task Memory (current run context) ---
        lines.extend([
            '',
            '## Layer 2 — Task Context (this run)',
            f"- Goal: {self.runtime.get('goal', '')}",
            f"- Goal profile: {self.runtime.get('goalProfile', self.config.goal_profile)}",
            f"- Target score: {self.runtime.get('targetScore', '')}",
            f"- Current status: {self.runtime.get('status', '')}",
            f"- Run ID: {self.runtime.get('runId', '')}",
            f"- Run log directory: {self.runtime.get('logDir', '')}",
            f"- Current milestone: {plan_snapshot.get('currentMilestoneId', '')}",
            f"- Current task: {plan_snapshot.get('currentTaskId', '')}",
        ])
        if plan_snapshot.get('summary'):
            lines.append(f"- Plan summary: {plan_snapshot['summary']}")

        lines.extend(['', '### Milestones'])
        milestones = plan_snapshot.get('milestones', []) or []
        if milestones:
            for milestone in milestones:
                lines.append(
                    f"- [{milestone.get('status', 'pending')}] {milestone.get('id', '')}: {milestone.get('title', '')}"
                )
        else:
            lines.append('- No milestone plan recorded yet.')

        # Changed files summary (task-level)
        all_changed = self.runtime.get('allChangedFiles', [])
        if all_changed:
            lines.extend(['', '### Files Changed Across Iterations'])
            for f in all_changed[:30]:
                lines.append(f'- {f}')
            if len(all_changed) > 30:
                lines.append(f'- ... and {len(all_changed) - 30} more')

        if not history:
            lines.append('')
            lines.append('- No iterations have completed yet.')
            return '\n'.join(lines) + '\n'

        # --- Layer 3: Delta Memory (last iteration only — volatile) ---
        last = history[-1]
        summary_text = str(last.get('summary', ''))[:300]
        next_prompt = str(last.get('next_prompt', ''))[:200]
        decision_next = str(last.get('decisionNextPrompt', ''))[:200]
        lines.extend([
            '',
            '## Layer 3 — Last Iteration Delta (volatile)',
            f"- Iteration: {last.get('iteration', 'n/a')}",
            f"- Score: {last.get('score', 'n/a')}",
            f"- Status: {last.get('status', 'unknown')}",
            f"- Decision code: {last.get('decisionCode', '')}",
            f"- Summary: {summary_text}",
            f"- Next prompt from Copilot: {next_prompt}",
            f"- Next prompt from operator: {decision_next}",
            f"- Tests: {last.get('tests', 'not_run')}",
            f"- Lint: {last.get('lint', 'not_run')}",
        ])
        last_blockers = last.get('blockers') or []
        if last_blockers:
            lines.append('- Blockers:')
            for blocker in last_blockers:
                lines.append(f"  - {blocker.get('severity', 'unknown')}: {blocker.get('item', '')}")
        last_changed = last.get('changedFiles') or []
        if last_changed:
            lines.append(f'- Changed files: {", ".join(last_changed[:10])}')
        last_validations = last.get('validation_after') or []
        if last_validations:
            lines.append('- Operator validations:')
            for v in last_validations:
                source = v.get('source', '')
                source_text = f"; source={source}" if source else ''
                lines.append(f"  - {v['name']}: {v['status']} ({v.get('summary', '')}){source_text}")

        # --- Layer 4: Failure Lessons (patterns that failed — cross-iteration) ---
        # Extract iterations where score decreased or stayed stuck
        failure_lessons: list[str] = []
        for i in range(1, len(history)):
            prev_score = history[i - 1].get('score')
            curr_score = history[i].get('score')
            if prev_score is not None and curr_score is not None and curr_score <= prev_score:
                iter_num = history[i].get('iteration', i)
                summary = str(history[i].get('summary', ''))[:120]
                code = history[i].get('decisionCode', '')
                failure_lessons.append(
                    f"- Iter {iter_num} (score {prev_score}→{curr_score}, {code}): {summary}"
                )
        if failure_lessons:
            lines.extend([
                '',
                '## Layer 4 — Failure Lessons (avoid repeating)',
                'The following iterations did NOT improve the score — avoid their approach:',
            ])
            lines.extend(failure_lessons[-5:])  # Keep last 5 failures

        # Brief score trajectory for context
        if len(history) >= 2:
            trajectory = [f"{h.get('iteration')}:{h.get('score', '?')}" for h in history[-5:]]
            lines.extend(['', f"**Score trajectory**: {' → '.join(trajectory)}"])

        lines.append('')
        return '\n'.join(lines)

    def _artifact_path(self, iteration: int, suffix: str) -> Path:
        return self._run_log_dir() / f'iteration-{iteration:02d}-{suffix}'

    def _run_log_dir(self) -> Path:
        run_id = str(self.runtime.get('runId', '')).strip()
        run_log_dir = self.config.log_dir / run_id if run_id else self.config.log_dir
        run_log_dir.mkdir(parents=True, exist_ok=True)
        return run_log_dir

    def _existing_project_memory_files(self) -> list[Path]:
        return [path for path in self.config.repo_profile.project_memory_files if path.exists()]

    def _check_protected_paths(self) -> list[str]:
        """Return list of protected paths that Copilot has modified this iteration."""
        protected = self.config.repo_profile.protected_paths
        if not protected:
            return []
        try:
            return check_protected_paths(self.config.workspace, protected)
        except Exception as exc:
            logger.debug('Protected path check failed: %s', exc)
            return []

    def _diagnose(self, iteration: int) -> Diagnosis:
        """Run intelligence engine on current history."""
        return diagnose(
            history=self.runtime.get('history', []),
            current_profile=self.config.goal_profile,
            target_score=self.config.target_score,
            iteration=iteration,
            max_iterations=self.config.max_iterations,
        )

    def _load_project_insights(self) -> ProjectInsights:
        """Load and analyse past run digests for project brain."""
        try:
            digests = []
            # Scan existing log directories for decision files
            if self.config.log_dir.exists():
                for run_dir in self.config.log_dir.iterdir():
                    if not run_dir.is_dir():
                        continue
                    decision_files = sorted(run_dir.glob('iteration-*-decision.json'))
                    if decision_files:
                        import json as _json
                        try:
                            last = _json.loads(decision_files[-1].read_text(encoding='utf-8'))
                            assessment = last.get('assessment', {})
                            digests.append(extract_run_digest({
                                'runId': run_dir.name,
                                'status': 'complete' if last.get('reasonCode') == 'STOP_GATE_PASSED' else 'blocked',
                                'finalReasonCode': last.get('reasonCode', ''),
                                'history': [{'score': assessment.get('score')}],
                                'plan': last.get('plan', {}),
                            }))
                        except (ValueError, OSError):
                            continue
            return analyse_runs(digests)
        except Exception:
            return ProjectInsights()

    def _try_operator_commit(self, iteration: int, summary: str, score: int | None) -> None:
        """Attempt to create an operator commit if workspace is a git repo."""
        if not is_git_repo(self.config.workspace):
            return
        try:
            run_id = self.runtime.get('runId', '')
            result = create_operator_commit(self.config.workspace, run_id, iteration, summary, score)
            if result.success:
                self.runtime.setdefault('commits', []).append({
                    'iteration': iteration,
                    'stdout': result.stdout[:200],
                })
        except Exception:
            pass  # Git commit is best-effort, never block the run

    def _read_runtime_state(self) -> dict[str, Any]:
        import json

        return json.loads(self.config.state_file.read_text(encoding='utf-8'))

    def _get_repo_map_text(self, goal: str) -> str:
        """Build and cache a compact codebase symbol map for this run."""
        if hasattr(self, '_cached_repo_map_text'):
            return self._cached_repo_map_text  # type: ignore[return-value]
        try:
            from .repo_map import build_repo_map, render_repo_map_for_prompt
            repo_map = build_repo_map(
                self.config.workspace,
                goal=goal,
                max_files=self.config.repo_map_max_files,
                max_chars=self.config.repo_map_max_chars,
            )
            text = render_repo_map_for_prompt(repo_map, max_chars=self.config.repo_map_max_chars)
            self._cached_repo_map_text: str = text
            return text
        except Exception as exc:
            logger.debug('Repo map build failed: %s', exc)
            self._cached_repo_map_text = ''
            return ''

    def run_multi_session(
        self,
        goal: str,
        roles: list[str] | None = None,
    ) -> dict[str, Any]:
        """Run a multi-session plan with different roles (coder, reviewer, tester, etc.).

        Each role runs sequentially, respecting dependency order.
        Returns aggregate result with per-slot outcomes.
        """
        plan = create_scheduler_plan(goal, roles=roles or ['coder', 'reviewer'])
        plan.status = 'running'
        logger.info('Multi-session plan created: %d slots, roles=%s',
                     len(plan.slots), [s.role for s in plan.slots])
        self._live_print(f'\n{bold("Multi-session plan")}:\n{render_scheduler_plan(plan)}\n')

        while not is_plan_complete(plan):
            slot = get_next_runnable_slot(plan)
            if slot is None:
                logger.warning('No runnable slot found — remaining slots may be blocked by dependencies.')
                break

            mark_slot_running(slot, str(uuid4()))
            logger.info('Running slot: role=%s goal_profile=%s', slot.role, slot.goal_profile)
            self._live_print(f'\n{cyan(f"[{slot.role}]")} Starting session...')

            # Create a fresh operator for each slot with slot-specific config
            from dataclasses import replace as dc_replace
            slot_config = dc_replace(
                self.config,
                goal_profile=slot.goal_profile,
            )
            slot_operator = CopilotOperator(slot_config, dry_run=self.dry_run, live=self.live)
            try:
                result = slot_operator.run(goal=slot.goal)
            except Exception as exc:
                result = {'status': 'error', 'reason': str(exc), 'reasonCode': 'SLOT_ERROR'}

            mark_slot_complete(slot, result)
            self._live_print(
                f'{cyan(f"[{slot.role}]")} '
                f'Finished: status={result.get("status")}, score={result.get("score", "n/a")}'
            )

        update_plan_status(plan)
        logger.info('Multi-session plan complete: status=%s', plan.status)
        self._live_print(f'\n{bold("Final plan status")}:\n{render_scheduler_plan(plan)}\n')

        return {
            'status': plan.status,
            'planId': plan.plan_id,
            'slots': [
                {
                    'role': s.role,
                    'status': s.status,
                    'score': s.final_score,
                    'reasonCode': s.final_reason_code,
                }
                for s in plan.slots
            ],
        }

    def _post_run_hooks(self, result: dict[str, Any]) -> None:
        """Run after every completed/blocked run: meta-learning, cross-repo export, snapshot summary, PR creation."""
        try:
            # Feature A: Meta-learner — full cycle: analyse → add/retire rules → save
            history = self.runtime.get('history', [])
            run_id = str(self.runtime.get('runId', ''))
            run_status = str(result.get('status', 'blocked'))
            if history:
                apply_meta_learning(self.config.workspace, history, run_id, run_status)
                self._learned_rules = load_rules(self.config.workspace)

            # Feature E: Cross-repo brain — export learned rules as shared insights
            if hasattr(self, '_shared_brain') and self._learned_rules:
                from dataclasses import asdict
                rule_dicts = [asdict(r) for r in self._learned_rules]
                repo_name = self.config.repo_profile.repo_name or 'unknown'
                export_rules_as_insights(self._shared_brain, repo_name, rule_dicts)
                save_shared_brain(self._shared_brain)

            # Feature D: Log snapshot summary
            if hasattr(self, '_snapshot_mgr'):
                result['snapshotSummary'] = snapshot_summary(self._snapshot_mgr)

            # LLM cost tracking
            if self._llm_brain:
                stats = self._llm_brain.stats
                result['llmCost'] = {
                    'calls': stats['calls'],
                    'totalTokens': stats['total_tokens'],
                    'estimatedUsd': stats['estimated_cost_usd'],
                }
                if stats['total_tokens'] > 0:
                    logger.info(
                        'LLM usage: %d calls, %d tokens, ~$%.4f',
                        stats['calls'], stats['total_tokens'], stats['estimated_cost_usd'],
                    )

            # Worker status
            self._worker.status = WorkerStatus.DONE if run_status == 'complete' else WorkerStatus.PAUSED
            result['worker'] = self._worker.to_dict()

            # Policy engine evaluation before PR creation
            try:
                from .policy import PolicyEngine, PolicyVerdict
                engine = PolicyEngine()
                changed_count = len(self.runtime.get('allChangedFiles', []))
                policy_context = {
                    'changed_files_count': changed_count,
                    'iteration_count': len(history),
                    'score_below': result.get('score'),
                }
                if self._llm_brain:
                    policy_context['llm_cost_usd'] = self._llm_brain.stats.get('estimated_cost_usd', 0)
                policy_decision = engine.evaluate('post_run', policy_context)
                result['policyVerdict'] = policy_decision.verdict
                if policy_decision.triggered_rules:
                    result['policyTriggered'] = policy_decision.triggered_rules
                    logger.info('Policy verdict: %s (rules: %s)', policy_decision.verdict,
                                ', '.join(policy_decision.triggered_rules))
                # Block PR creation if policy says so
                if policy_decision.verdict == PolicyVerdict.BLOCK:
                    logger.warning('Policy blocked PR creation')
                    result['prBlocked'] = True
            except Exception:
                pass  # Policy is best-effort

            # Auto-create PR on successful completion (unless policy blocked)
            if (run_status == 'complete' and self.config.auto_create_pr
                    and self.config.github_token and not result.get('prBlocked')):
                self._try_create_pr(result)

            # --- Run Narrative: generate prose summary + done explanation ---
            try:
                goal_text = str(self.runtime.get('goal', ''))
                narrative = build_run_narrative(
                    goal_text, self.runtime, result,
                    config_target_score=self.config.target_score,
                )
                result['narrative'] = narrative.summary
                result['doneExplanation'] = narrative.done_explanation.render() if narrative.done_explanation else ''
                # Write to disk
                if run_id:
                    write_narrative_file(self.config.workspace, run_id, narrative)
                    if narrative.done_explanation:
                        write_done_explanation_file(self.config.workspace, run_id, narrative.done_explanation)
                logger.info('Narrative: %s', narrative.summary[:120])
            except Exception:
                pass  # Narrative is best-effort

            # --- Mission Memory: update project direction ---
            try:
                mission = load_mission(self.config.workspace)
                goal_text = str(self.runtime.get('goal', ''))
                # Build lesson from narrative
                lesson = ''
                if run_status != 'complete' and result.get('reasonCode'):
                    lesson = f'[{result["reasonCode"]}] {goal_text[:60]}'
                elif run_status == 'complete':
                    lesson = f'Completed: {goal_text[:60]}'
                update_mission_from_run(mission, goal_text, result, lesson=lesson)
                save_mission(self.config.workspace, mission)
            except Exception:
                pass  # Mission update is best-effort

            # --- Memory Promotion: evaluate and apply promotions ---
            try:
                from dataclasses import asdict
                rule_dicts = [asdict(r) for r in self._learned_rules] if self._learned_rules else []
                brain_learnings: list[dict[str, Any]] = []  # Placeholder — actual brain learnings loaded in-memory
                mission = load_mission(self.config.workspace)
                shared_insights: list[dict[str, Any]] = []
                if hasattr(self, '_shared_brain') and self._shared_brain:
                    shared_insights = [
                        {'category': i.category, 'detail': i.detail, 'confidence': i.confidence,
                         'tags': list(i.tags), 'hit_count': i.hit_count}
                        for i in getattr(self._shared_brain, 'insights', [])
                    ]
                promo_counts = run_promotion_cycle(
                    history, result, rule_dicts,
                    brain_learnings, mission.lessons_learned, shared_insights,
                    thresholds=PromotionThresholds(
                        trap_repeat_threshold=self.config.promotion_trap_repeat,
                        validation_confirm_threshold=self.config.promotion_validation_confirm,
                        strategy_success_threshold=self.config.promotion_strategy_success,
                    ),
                )
                if any(v > 0 for v in promo_counts.values()):
                    logger.info('Memory promotion: %s', promo_counts)
                    save_mission(self.config.workspace, mission)
                result['memoryPromotion'] = promo_counts
            except Exception:
                pass  # Promotion is best-effort
        except Exception:
            pass  # Post-run hooks are best-effort

        # Always release guard resources
        self._cleanup_guard()

    def _try_create_pr(self, result: dict[str, Any]) -> None:
        """Attempt to create a draft PR after a successful run."""
        try:
            from .github_integration import GitHubConfig, build_pr_body, create_pull_request
            from .repo_ops import create_operator_commit, is_git_repo

            if not is_git_repo(self.config.workspace):
                return

            repo_name = self.config.repo_profile.repo_name
            if '/' not in repo_name:
                logger.info('auto_create_pr requires repoName=owner/repo in repo-profile.yml')
                return

            owner, repo = repo_name.split('/', 1)
            gh_config = GitHubConfig(token=self.config.github_token, owner=owner, repo=repo)
            if not gh_config.is_configured:
                return

            goal = str(self.runtime.get('goal', ''))
            run_id = str(self.runtime.get('runId', ''))
            score = result.get('score')
            reason_code = str(result.get('reasonCode', ''))
            plan = self.runtime.get('plan', {})
            plan_summary = plan.get('summary', '') if isinstance(plan, dict) else ''

            # Auto-commit any uncommitted changes
            create_operator_commit(self.config.workspace, goal, run_id)

            # Detect current branch
            import subprocess
            branch_result = subprocess.run(
                ['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
                cwd=self.config.workspace,
                capture_output=True,
                text=True,
                timeout=10,
            )
            head_branch = branch_result.stdout.strip() if branch_result.returncode == 0 else 'main'

            title = f'[Copilot Operator] {goal[:72]}'
            body = build_pr_body(goal, run_id, result.get('iterations', 0), score, reason_code, plan_summary)
            pr = create_pull_request(gh_config, title, body, head=head_branch, draft=True)
            result['pullRequest'] = {'url': pr.url, 'number': pr.number, 'title': pr.title}
            logger.info('Created draft PR #%d: %s', pr.number, pr.url)
        except Exception as exc:
            logger.warning('Failed to create PR: %s', exc)

    def _ask_llm_for_guidance(self, goal: str, history: list[dict[str, Any]], dx: Diagnosis) -> str:
        """Ask the LLM brain for strategic guidance when the operator is stuck."""
        if not self._llm_brain or not self._llm_brain.is_ready:
            return ''
        try:
            history_summary = '\n'.join(
                f"  Iter {h.get('iteration')}: score={h.get('score')}, status={h.get('status')}, summary={h.get('summary', '')[:120]}"
                for h in history[-4:]
            )
            current_error = '; '.join(
                f"{b.get('severity')}: {b.get('item')}"
                for h in history[-1:]
                for b in (h.get('blockers') or [])
            ) or f'Risk level: {dx.risk_level}'

            result, response = self._llm_brain.analyse_failure(goal, history_summary, current_error)
            if result and response.success:
                lines = [f"**Root Cause:** {result.get('root_cause', 'unknown')}"]
                if result.get('strategy'):
                    lines.append(f"**Strategy:** {result['strategy']}")
                if result.get('suggested_prompt'):
                    lines.append(f"**Suggested prompt:** {result['suggested_prompt']}")
                return '\n'.join(lines)
        except Exception:
            pass  # LLM guidance is best-effort
        return ''

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
