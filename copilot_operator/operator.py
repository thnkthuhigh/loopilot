from __future__ import annotations

import sys
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
from .meta_learner import apply_meta_learning, load_rules, render_guardrails
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
from .repo_ops import check_protected_paths, create_operator_commit, get_diff_summary, is_git_repo, pre_run_safety_check
from .session_store import extract_response_text, get_latest_request, request_needs_continue
from .snapshot import (
    SnapshotManager,
    cleanup_operator_stashes,
    should_rollback,
    should_snapshot,
    snapshot_summary,
    take_snapshot,
)
from .terminal import bold, cyan, dim, score_color, status_badge
from .validation import dump_json, run_validations
from .vscode_chat import (
    ensure_workspace_storage,
    send_chat_prompt,
    snapshot_chat_sessions,
    wait_for_completed_session,
    wait_for_session_file,
)

logger = get_logger('operator')


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
        _merge_vscode_settings(config.workspace)
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

    def _live_print(self, msg: str) -> None:
        """Print a live progress line to stderr (only when live=True)."""
        if self.live:
            print(msg, file=sys.stderr, flush=True)

    def run(self, goal: str | None = None, resume: bool = False) -> dict[str, Any]:
        storage = ensure_workspace_storage(self.config)
        chat_dir = storage / 'chatSessions'
        chat_dir.mkdir(parents=True, exist_ok=True)

        goal_text, decision, iteration_start = self._prepare_run(goal, resume)
        logger.info('Starting run: goal=%r dry_run=%s iterations=%d-%d',
                     goal_text[:80], self.dry_run, iteration_start, self.config.max_iterations)

        # Clean up any leftover operator stashes from a previous interrupted run
        if not resume:
            dropped = cleanup_operator_stashes()
            if dropped:
                logger.info('Cleaned up %d leftover operator stash(es)', dropped)

        error_retry_count = 0
        _MAX_ERROR_RETRIES = 3

        for iteration in range(iteration_start, self.config.max_iterations + 1):
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
                raise
            except Exception as exc:
                logger.error('Error in iteration %d: %s', iteration, exc, exc_info=True)
                self.runtime.setdefault('errors', []).append({
                    'iteration': iteration,
                    'error': str(exc),
                    'type': type(exc).__name__,
                    'timestamp': self._now(),
                })
                self._write_runtime()
                # Allow up to _MAX_ERROR_RETRIES consecutive errors before stopping
                error_retry_count += 1
                if error_retry_count >= _MAX_ERROR_RETRIES:
                    logger.error('Stopping after %d consecutive errors', error_retry_count)
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
                    return {
                        'status': 'error',
                        'reason': str(exc),
                        'reasonCode': 'CONSECUTIVE_ERRORS',
                        'iterations': iteration,
                        'stateFile': str(self.config.state_file),
                    }
                # Not yet at limit — try to continue without consuming another progress iteration
                logger.info('Attempting to continue after error (%d/%d)', error_retry_count, _MAX_ERROR_RETRIES)
                decision = Decision(
                    action='continue',
                    reason=f'Retrying after error: {exc}',
                    next_prompt=decision.next_prompt,
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
        send_chat_prompt(self.config, prompt, add_files=attachment_paths)
        session_path = wait_for_session_file(chat_dir, baseline, self.config.session_timeout_seconds, self.config.poll_interval_seconds)
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
            # Attempt rollback
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
        self.runtime['lastResponsePath'] = str(response_path)
        self.runtime['lastPromptPath'] = str(prompt_path)
        self.runtime['updatedAt'] = self._now()
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
        self._snapshot_mgr = SnapshotManager()

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

        # LLM Brain: enrich intelligence with real AI analysis when available
        llm_text = ''
        if self._llm_brain and self._llm_brain.is_ready and history and dx.risk_level in ('medium', 'high', 'critical'):
            llm_text = self._ask_llm_for_guidance(goal, history, dx)

        # Combine intelligence sources
        full_intelligence = intelligence_text
        if llm_text:
            full_intelligence = f'{intelligence_text}\n\n## LLM Brain Analysis\n{llm_text}' if intelligence_text else llm_text

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

    def _sync_runtime_paths(self) -> None:
        self.runtime['logRootDir'] = str(self.config.log_dir)
        self.runtime['logDir'] = str(self._run_log_dir())

    def _render_memory(self) -> str:
        history = self.runtime.get('history', [])
        plan_snapshot = summarize_plan(self.runtime.get('plan'))
        lines = [
            '# Copilot Operator Memory',
            '',
            f"- Goal: {self.runtime.get('goal', '')}",
            f"- Goal profile: {self.runtime.get('goalProfile', self.config.goal_profile)}",
            f"- Workspace: {self.runtime.get('workspace', '')}",
            f"- Mode: {self.runtime.get('mode', '')}",
            f"- Target score: {self.runtime.get('targetScore', '')}",
            f"- Current status: {self.runtime.get('status', '')}",
            f"- Run ID: {self.runtime.get('runId', '')}",
            f"- Run log directory: {self.runtime.get('logDir', '')}",
            f"- Workspace ecosystem: {self.config.workspace_insight.ecosystem}",
            f"- Package manager: {self.config.workspace_insight.package_manager or 'unknown'}",
            f"- Current milestone: {plan_snapshot.get('currentMilestoneId', '')}",
            f"- Current task: {plan_snapshot.get('currentTaskId', '')}",
        ]
        if plan_snapshot.get('summary'):
            lines.append(f"- Plan summary: {plan_snapshot['summary']}")
        if self.config.repo_profile.repo_name:
            lines.append(f"- Repo profile: {self.config.repo_profile.repo_name}")
        lines.append(render_brain_status(self._llm_brain))
        if self.config.repo_profile.summary:
            lines.append(f"- Repo summary: {self.config.repo_profile.summary}")
        lines.extend(['', '## Milestones'])
        milestones = plan_snapshot.get('milestones', []) or []
        if milestones:
            for milestone in milestones:
                lines.append(
                    f"- [{milestone.get('status', 'pending')}] {milestone.get('id', '')}: {milestone.get('title', '')}"
                )
        else:
            lines.append('- No milestone plan recorded yet.')
        lines.extend(['', '## Recent Iterations'])
        if not history:
            lines.append('- No iterations have completed yet.')
            # Add project brain insights if available
            if hasattr(self, '_project_insights'):
                brain_text = render_insights_for_memory(self._project_insights)
                if brain_text:
                    lines.append(brain_text)
            return '\n'.join(lines) + '\n'

        for item in history[-5:]:
            summary = str(item.get('summary', ''))[:200]
            next_prompt = str(item.get('next_prompt', ''))[:200]
            decision_next = str(item.get('decisionNextPrompt', ''))[:200]
            lines.extend(
                [
                    '',
                    f"### Iteration {item.get('iteration', 'n/a')}",
                    f"- Session: {item.get('sessionId', 'n/a')}",
                    f"- Status: {item.get('status', 'unknown')}",
                    f"- Score: {item.get('score', 'n/a')}",
                    f"- Needs continue: {item.get('needs_continue', False)}",
                    f"- Decision code: {item.get('decisionCode', '')}",
                    f"- Current milestone: {item.get('currentMilestoneId', '')}",
                    f"- Current task: {item.get('currentTaskId', '')}",
                    f"- Summary: {summary}",
                    f"- Next prompt from Copilot: {next_prompt}",
                    f"- Next prompt from operator: {decision_next}",
                    f"- Tests: {item.get('tests', 'not_run')}",
                    f"- Lint: {item.get('lint', 'not_run')}",
                ]
            )
            blockers = item.get('blockers') or []
            if blockers:
                lines.append('- Blockers:')
                for blocker in blockers:
                    lines.append(f"  - {blocker.get('severity', 'unknown')}: {blocker.get('item', '')}")
            validations = item.get('validation_after') or []
            if validations:
                lines.append('- Operator validations after response:')
                for validation in validations:
                    source = validation.get('source', '')
                    source_text = f"; source={source}" if source else ''
                    lines.append(
                        f"  - {validation['name']}: {validation['status']} ({validation.get('summary', '')}){source_text}"
                    )

        # Add project brain insights
        if hasattr(self, '_project_insights'):
            brain_text = render_insights_for_memory(self._project_insights)
            if brain_text:
                lines.append(brain_text)

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
            repo_map = build_repo_map(self.config.workspace, goal=goal)
            text = render_repo_map_for_prompt(repo_map)
            self._cached_repo_map_text: str = text
            return text
        except Exception as exc:
            logger.debug('Repo map build failed: %s', exc)
            self._cached_repo_map_text = ''
            return ''

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

            # Auto-create PR on successful completion
            if run_status == 'complete' and self.config.auto_create_pr and self.config.github_token:
                self._try_create_pr(result)
        except Exception:
            pass  # Post-run hooks are best-effort

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
