from __future__ import annotations

__all__ = [
    'main',
]

import argparse
import json
import sys
from pathlib import Path

from .bootstrap import (
    cleanup_run_logs,
    format_operator_focus,
    initialize_workspace,
    read_operator_focus,
    read_operator_status,
    watch_operator_status,
)
from .config import load_config
from .operator import CopilotOperator
from .planner import render_plan
from .repo_inspector import as_dict
from .terminal import err as _term_err
from .terminal import ok as _term_ok
from .terminal import warn as _term_warn
from .vscode_chat import VSCodeChatError, ensure_workspace_storage, run_code_command

_MAX_GOAL_LENGTH = 5000


def _read_goal(config_path: str | None, workspace_override: str | None, goal: str | None, goal_file: str | None) -> str:
    config = load_config(config_path, workspace_override)
    if goal:
        text = goal.strip()
    elif goal_file:
        text = Path(goal_file).read_text(encoding='utf-8').strip()
    else:
        default_goal_file = config.repo_profile.default_goal_file
        if default_goal_file and default_goal_file.exists():
            text = default_goal_file.read_text(encoding='utf-8').strip()
        else:
            raise SystemExit('Provide --goal or --goal-file, or define defaultGoalFile in repo-profile.yml.')
    if not text:
        raise SystemExit('Goal cannot be empty.')
    if len(text) > _MAX_GOAL_LENGTH:
        raise SystemExit(f'Goal is too long ({len(text)} chars, max {_MAX_GOAL_LENGTH}).')
    return text


def _doctor(config_path: str | None, workspace_override: str | None) -> int:
    config = load_config(config_path, workspace_override)
    issues: list[str] = []
    checks: list[str] = []

    # Python version
    import platform
    py_ver = platform.python_version()
    py_parts = tuple(int(x) for x in py_ver.split('.')[:2])
    if py_parts >= (3, 10):
        checks.append(f'python version: {py_ver}')
    else:
        issues.append(f'Python >= 3.10 required, found {py_ver}')

    # PyYAML availability
    try:
        import yaml  # noqa: F401
        checks.append('pyyaml installed')
    except ImportError:
        issues.append('pyyaml not installed — run: pip install pyyaml')

    # Node.js availability (required for hooks)
    import shutil as _shutil
    import subprocess as _subprocess
    _node = _shutil.which('node')
    if _node:
        try:
            _node_ver = _subprocess.run(
                ['node', '--version'], capture_output=True, text=True, timeout=10, check=False
            )
            checks.append(f'node.js available: {_node_ver.stdout.strip()}')
        except Exception:
            checks.append('node.js found on PATH (version check failed)')
    else:
        issues.append('node.js not found on PATH — hooks (.github/hooks/scripts/*.cjs) will not run; install Node.js >= 18')

    try:
        version = run_code_command(['--version'], cwd=config.workspace, timeout_seconds=60)
        checks.append(f"code.cmd available: {version.stdout.splitlines()[0]}")
    except VSCodeChatError as exc:
        issues.append(f'code.cmd check failed: {exc}')

    try:
        chat_help = run_code_command(['chat', '--help'], cwd=config.workspace, timeout_seconds=60)
        checks.append(f"code chat available: {chat_help.stdout.splitlines()[0]}")
    except VSCodeChatError as exc:
        issues.append(f'code chat check failed: {exc}')

    if config.workspace.exists():
        checks.append(f'workspace exists: {config.workspace}')
    else:
        issues.append(f'workspace does not exist: {config.workspace}')

    try:
        storage = ensure_workspace_storage(config)
        checks.append(f'workspaceStorage resolved: {storage}')
    except VSCodeChatError as exc:
        issues.append(str(exc))

    config.ensure_runtime_dirs()
    checks.append(f'runtime directory ready: {config.memory_file.parent}')

    # Runtime directory writable
    try:
        test_file = config.memory_file.parent / '.doctor_test'
        test_file.write_text('ok', encoding='utf-8')
        test_file.unlink()
        checks.append('runtime directory writable')
    except OSError as exc:
        issues.append(f'runtime directory not writable: {exc}')

    # Git repository check
    git_dir = config.workspace / '.git'
    if git_dir.exists():
        checks.append('git repository detected')
    else:
        issues.append('not a git repository — snapshot/rollback features will be unavailable')

    checks.append(f'code command retries configured: {config.code_command_retries}')
    checks.append(f'goal profile: {config.goal_profile}')
    checks.append(f'log retention runs: {config.log_retention_runs}')

    insight = config.workspace_insight
    if insight.ecosystem != 'unknown':
        checks.append(f'workspace ecosystem detected: {insight.ecosystem}')
    else:
        issues.append('Workspace ecosystem could not be auto-detected yet.')
    if insight.package_manager:
        checks.append(f'package manager detected: {insight.package_manager}')
    if insight.validation_hints:
        checks.append('inferred validation hints: ' + ', '.join(f"{name}={cmd}" for name, cmd in sorted(insight.validation_hints.items())))

    if config.repo_profile.path:
        checks.append(f'repo profile loaded: {config.repo_profile.path}')
    else:
        issues.append('No repo profile file is configured yet. Create .copilot-operator/repo-profile.yml for per-repo defaults.')

    if config.repo_profile.project_memory_files:
        checks.append(f'project memory files configured: {len(config.repo_profile.project_memory_files)}')

    required_without_commands = [item.name for item in config.validation if item.required and not item.command]
    if required_without_commands:
        issues.append('Required validations are missing commands: ' + ', '.join(required_without_commands))
    else:
        checks.append(f'validation entries configured: {len(config.validation)}')

    for item in config.validation:
        if item.command:
            checks.append(f"validation {item.name}: {item.command} ({item.source})")

    # LLM brain check
    try:
        from .llm_brain import load_llm_config_from_dict, load_llm_config_from_env
        llm_config = (
            load_llm_config_from_dict(config.llm_config_raw)
            if config.llm_config_raw
            else load_llm_config_from_env()
        )
        if llm_config.is_ready:
            checks.append(f'LLM brain ready: {llm_config.provider}/{llm_config.model}')
        else:
            checks.append('LLM brain not configured (heuristic-only mode)')
    except Exception:
        checks.append('LLM brain not available')

    for line in checks:
        print(_term_ok(line))
    for line in issues:
        print(_term_warn(line))
    return 0 if not issues else 1


def _run(
    config_path: str | None,
    workspace_override: str | None,
    goal: str | None,
    goal_file: str | None,
    mode: str | None,
    goal_profile: str | None,
    max_iterations: int | None,
    resume: bool,
    dry_run: bool = False,
    live: bool = False,
    fresh: bool = False,
) -> int:
    config = load_config(config_path, workspace_override)
    if mode:
        config.mode = mode
    if goal_profile:
        config.goal_profile = goal_profile.strip().lower()
    if max_iterations is not None:
        config.max_iterations = max_iterations

    # Auto-resume: detect pending state when not explicitly resuming or starting fresh
    if not resume and not fresh and config.state_file.exists():
        try:
            state = json.loads(config.state_file.read_text(encoding='utf-8'))
            status = str(state.get('status', '')).strip().lower()
            pending = state.get('pendingDecision') or {}
            if status in ('blocked', 'error', 'running') and pending.get('action') == 'continue':
                old_goal = str(state.get('goal', '')).strip()
                new_goal = _read_goal(config_path, workspace_override, goal, goal_file) if (goal or goal_file) else ''
                if new_goal and old_goal and new_goal != old_goal:
                    print(f'⚠ Goal changed from previous run. Starting fresh.\n  Old: {old_goal[:80]}\n  New: {new_goal[:80]}', file=sys.stderr)
                else:
                    last_iter = len(state.get('history', []))
                    last_score = (state.get('history', [{}])[-1] if state.get('history') else {}).get('score', '?')
                    print(f'Auto-resuming previous run (iteration {last_iter}, score {last_score})...', file=sys.stderr)
                    resume = True
        except (json.JSONDecodeError, OSError):
            pass

    operator = CopilotOperator(config, dry_run=dry_run, live=live)
    goal_value = None if resume and not (goal or goal_file) else _read_goal(config_path, workspace_override, goal, goal_file)
    result = operator.run(goal_value, resume=resume)
    # Remove non-serializable internal objects before printing
    result.pop('_narrativeEngine', None)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def _init(config_path: str | None, workspace_override: str | None, force: bool, detect_hints: bool = True) -> int:
    config = load_config(config_path, workspace_override)
    result = initialize_workspace(config.workspace, force=force, detect_hints=detect_hints)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def _detect_hints(config_path: str | None, workspace_override: str | None) -> int:
    from .bootstrap import detect_and_hydrate
    config = load_config(config_path, workspace_override)
    result = detect_and_hydrate(config.workspace)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def _approve_escalation(config_path: str | None, workspace_override: str | None) -> int:
    config = load_config(config_path, workspace_override)
    if not config.state_file.exists():
        print('No state file found.', file=sys.stderr)
        return 1
    state = json.loads(config.state_file.read_text(encoding='utf-8'))
    escalation = state.get('pendingEscalation')
    if not escalation:
        print('No pending escalation found.', file=sys.stderr)
        return 1
    print(f"Escalation type: {escalation.get('type')}")
    print(f"Paths: {', '.join(escalation.get('paths', []))}")
    print(f"Diff summary:\n{escalation.get('diffSummary', '(none)')}")
    # Clear escalation, set pending decision to continue
    state['pendingEscalation'] = None
    state['status'] = 'blocked'
    state['pendingDecision'] = {
        'action': 'continue',
        'reason': 'Escalation approved — continuing with changes intact.',
        'reasonCode': 'ESCALATION_APPROVED',
        'nextPrompt': (state.get('pendingDecision') or {}).get('nextPrompt', state.get('goal', '')),
    }
    config.state_file.write_text(json.dumps(state, indent=2), encoding='utf-8')
    print('Escalation approved. Run `run` or `resume` to continue.')
    return 0


def _reject_escalation(config_path: str | None, workspace_override: str | None) -> int:
    config = load_config(config_path, workspace_override)
    if not config.state_file.exists():
        print('No state file found.', file=sys.stderr)
        return 1
    state = json.loads(config.state_file.read_text(encoding='utf-8'))
    escalation = state.get('pendingEscalation')
    if not escalation:
        print('No pending escalation found.', file=sys.stderr)
        return 1
    violated_paths = escalation.get('paths', [])
    paths_str = ', '.join(violated_paths)
    # Rollback via snapshot if available
    try:
        from .snapshot import SnapshotManager, rollback_to_last_good
        mgr = SnapshotManager()
        rollback_to_last_good(mgr)
        print('Changes rolled back.')
    except Exception:
        print('Warning: could not rollback changes automatically.', file=sys.stderr)
    # Set pending decision with adjusted prompt
    state['pendingEscalation'] = None
    state['status'] = 'blocked'
    original_prompt = (state.get('pendingDecision') or {}).get('nextPrompt', state.get('goal', ''))
    adjusted_prompt = f"{original_prompt}\n\nDo NOT modify these protected paths: {paths_str}"
    state['pendingDecision'] = {
        'action': 'continue',
        'reason': f'Escalation rejected — rolled back, avoiding paths: {paths_str}',
        'reasonCode': 'ESCALATION_REJECTED',
        'nextPrompt': adjusted_prompt,
    }
    config.state_file.write_text(json.dumps(state, indent=2), encoding='utf-8')
    print(f'Escalation rejected. Protected paths: {paths_str}')
    print('Run `run` or `resume` to continue with adjusted prompt.')
    return 0


def _status(config_path: str | None, workspace_override: str | None) -> int:
    config = load_config(config_path, workspace_override)
    result = read_operator_status(config.state_file, config.summary_file)
    result['configuredGoalProfile'] = config.goal_profile
    result['workspaceInsightFromConfig'] = as_dict(config.workspace_insight)
    result['validationConfig'] = [
        {
            'name': item.name,
            'command': item.command,
            'required': item.required,
            'source': item.source,
        }
        for item in config.validation
    ]
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def _focus(config_path: str | None, workspace_override: str | None, as_json: bool) -> int:
    config = load_config(config_path, workspace_override)
    result = read_operator_focus(config.state_file, config.summary_file)
    if as_json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(format_operator_focus(result))
    return 0


def _plan(config_path: str | None, workspace_override: str | None, as_json: bool) -> int:
    config = load_config(config_path, workspace_override)
    status = read_operator_status(config.state_file, config.summary_file)
    plan = status.get('plan') or {}
    if as_json:
        print(json.dumps(plan, indent=2, ensure_ascii=False))
    else:
        print(render_plan(plan))
    return 0


def _watch(
    config_path: str | None,
    workspace_override: str | None,
    interval: float,
    count: int | None,
    clear_screen: bool,
) -> int:
    config = load_config(config_path, workspace_override)
    result = watch_operator_status(
        config.state_file,
        config.summary_file,
        interval_seconds=interval,
        max_updates=count,
        clear_screen=clear_screen,
    )
    if clear_screen:
        print('')
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def _cleanup(config_path: str | None, workspace_override: str | None, keep_runs: int | None, dry_run: bool) -> int:
    config = load_config(config_path, workspace_override)
    result = cleanup_run_logs(config.log_dir, keep_runs or config.log_retention_runs, dry_run=dry_run)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def _brain(config_path: str | None, workspace_override: str | None, test_prompt: str | None) -> int:
    """Show LLM brain status and optionally test it."""
    from .llm_brain import LLMBrain, load_llm_config_from_dict, load_llm_config_from_env

    config = load_config(config_path, workspace_override)
    llm_config = (
        load_llm_config_from_dict(config.llm_config_raw)
        if config.llm_config_raw
        else load_llm_config_from_env()
    )

    info = {
        'provider': llm_config.provider or '(none)',
        'model': llm_config.model or '(default)',
        'enabled': llm_config.enabled,
        'ready': llm_config.is_ready,
        'api_url': llm_config.api_url or '(default)',
        'has_api_key': bool(llm_config.api_key),
        'max_tokens': llm_config.max_tokens,
        'temperature': llm_config.temperature,
    }

    if not llm_config.is_ready:
        print('[brain] LLM brain is NOT configured. Running in heuristic-only mode.')
        print('[brain] To enable, set COPILOT_OPERATOR_LLM_PROVIDER and the matching API key.')
        print('[brain] Supported providers: openai, anthropic, gemini, local')
        print(json.dumps(info, indent=2, ensure_ascii=False))
        return 1

    print(f'[brain] Provider: {llm_config.provider}')
    print(f'[brain] Model: {llm_config.model}')
    print(f'[brain] Ready: {llm_config.is_ready}')

    if test_prompt:
        brain = LLMBrain(llm_config)
        print(f'\n[brain] Testing with prompt: {test_prompt[:80]}...')
        response = brain.ask(test_prompt, system='You are a helpful assistant. Be concise.')
        if response.success:
            print(f'[brain] Response ({response.tokens_used} tokens):')
            print(response.content[:500])
        else:
            print(f'[brain] Error: {response.error}')
            return 1

    print(json.dumps(info, indent=2, ensure_ascii=False))
    return 0


def _version() -> int:
    try:
        from importlib.metadata import version
        v = version('copilot-operator')
    except Exception:
        v = '1.0.0-dev'
    print(f'copilot-operator {v}')
    return 0


def _get_version() -> str:
    try:
        from importlib.metadata import version
        return version('copilot-operator')
    except Exception:
        return '1.0.0-dev'


def _queue(
    config_path: str | None,
    workspace_override: str | None,
    goals: list[str] | None,
    goals_file: str | None,
    max_iterations: int,
    dry_run: bool,
    live: bool,
) -> int:
    """Run multiple goals sequentially with a persistent worker."""
    from .worker import Task, TaskQueue

    # Collect goals
    goal_list: list[str] = []
    if goals:
        goal_list.extend(goals)
    if goals_file:
        path = Path(goals_file)
        if not path.exists():
            print(_term_err(f'Goals file not found: {path}'), file=sys.stderr)
            return 1
        for line in path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if line and not line.startswith('#'):
                goal_list.append(line)
    if not goal_list:
        print(_term_err('No goals provided. Use --goals or --file.'), file=sys.stderr)
        return 1

    # Build task queue
    queue = TaskQueue()
    for goal_text in goal_list:
        queue.enqueue(Task(goal=goal_text, source='queue'))

    config = load_config(config_path, workspace_override)
    if max_iterations:
        config.max_iterations = max_iterations

    results: list[dict] = []
    total = len(goal_list)
    completed = 0
    failed = 0

    print(f'[queue] {total} tasks queued', file=sys.stderr)
    while True:
        task = queue.dequeue()
        if task is None:
            break
        idx = len(results) + 1
        print(f'\n[queue] [{idx}/{total}] {task.goal[:80]}', file=sys.stderr)

        operator = CopilotOperator(config, dry_run=dry_run, live=live)
        try:
            result = operator.run(task.goal)
            queue.mark_complete(task.task_id, result)
            status = result.get('status', 'unknown')
            score = result.get('score', 'N/A')
            print(f'[queue] [{idx}/{total}] {status} (score={score})', file=sys.stderr)
            if status == 'complete':
                completed += 1
            else:
                failed += 1
            results.append({'taskId': task.task_id, 'goal': task.goal[:80], **result})
        except Exception as exc:
            queue.mark_failed(task.task_id, str(exc))
            failed += 1
            print(f'[queue] [{idx}/{total}] FAILED: {exc}', file=sys.stderr)
            results.append({'taskId': task.task_id, 'goal': task.goal[:80], 'status': 'error', 'error': str(exc)})

    summary = {
        'total': total,
        'completed': completed,
        'failed': failed,
        'results': results,
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if failed == 0 else 1


def _multi(
    config_path: str | None,
    workspace_override: str | None,
    goal: str | None,
    goal_file: str | None,
    roles: list[str],
    max_iterations: int,
    dry_run: bool,
    live: bool,
) -> int:
    """Run a multi-session plan with different roles."""
    config = load_config(config_path, workspace_override)
    goal_text = _read_goal(config_path, workspace_override, goal, goal_file)
    if max_iterations:
        config.max_iterations = max_iterations

    operator = CopilotOperator(config, dry_run=dry_run, live=live)
    result = operator.run_multi_session(goal_text, roles=roles)

    print(json.dumps(result, indent=2, ensure_ascii=False))

    all_ok = all(s.get('status') == 'complete' for s in result.get('slots', []))
    return 0 if all_ok else 1


def _dashboard(
    config_path: str | None,
    workspace_override: str | None,
    interval: float,
    count: int | None,
    no_clear: bool,
) -> int:
    """Run the live terminal dashboard."""
    from .dashboard import run_dashboard

    config = load_config(config_path, workspace_override)
    run_dashboard(config.state_file, interval=interval, count=count, clear=not no_clear)
    return 0


def _nightly(
    config_path: str | None,
    workspace_override: str | None,
    goals_file: str | None,
    max_iterations: int,
    dry_run: bool,
) -> int:
    """Run overnight delivery loop."""
    from .nightly import NightlyConfig, collect_nightly_goals, run_nightly

    config = load_config(config_path, workspace_override)
    nightly_cfg = NightlyConfig(
        goals_file=Path(goals_file) if goals_file else None,
        max_iterations_per_task=max_iterations,
        dry_run=dry_run,
    )
    goals = collect_nightly_goals(nightly_cfg, config.workspace)
    if not goals:
        print(_term_err('No goals found. Provide --file with one goal per line.'), file=sys.stderr)
        return 1

    print(f'[nightly] {len(goals)} task(s) queued', file=sys.stderr)
    report = run_nightly(config.workspace, goals, max_iterations=max_iterations, dry_run=dry_run)
    print(report.render_text())
    return 0 if report.failed == 0 else 1


def _ci(
    config_path: str | None,
    workspace_override: str | None,
    action: str,
    workflow_id: str,
    run_id: int,
    timeout: int,
    fix: bool,
    max_iterations: int,
    dry_run: bool,
    live: bool,
) -> int:
    """CI integration — trigger, check, wait, and optionally fix CI failures."""
    from .ci_integration import (
        analyse_run,
        get_latest_run,
        list_workflows,
        load_ci_config,
        trigger_workflow,
        wait_for_run,
    )

    ci_config = load_ci_config()
    if not ci_config.is_configured:
        print(_term_err('CI not configured. Set GITHUB_TOKEN and ensure git remote points to GitHub.'), file=sys.stderr)
        return 1

    if action == 'list':
        workflows = list_workflows(ci_config)
        if not workflows:
            print('No workflows found.')
            return 0
        for w in workflows:
            state_icon = '✅' if w['state'] == 'active' else '⏸'
            print(f"  {state_icon} {w['name']} (id={w['id']}, path={w['path']})")
        return 0

    if action == 'trigger':
        if not workflow_id:
            print(_term_err('--workflow-id is required for trigger.'), file=sys.stderr)
            return 1
        ok = trigger_workflow(ci_config, workflow_id)
        if ok:
            print(_term_ok('Workflow triggered.'))
            return 0
        print(_term_err('Failed to trigger workflow.'), file=sys.stderr)
        return 1

    if action == 'check':
        run = get_latest_run(ci_config, workflow_id=workflow_id) if workflow_id else get_latest_run(ci_config)
        if not run:
            print('No runs found.')
            return 0
        icon = '✅' if run.conclusion == 'success' else '❌' if run.conclusion == 'failure' else '⏳'
        print(f'{icon} {run.name} (#{run.run_id})')
        print(f'  Status: {run.status}, Conclusion: {run.conclusion}')
        print(f'  Branch: {run.head_branch}, SHA: {run.head_sha[:8]}')
        print(f'  URL: {run.url}')
        if run.conclusion == 'failure':
            result = analyse_run(ci_config, run.run_id)
            if result.failed_jobs:
                print(f'  Failed jobs: {", ".join(result.failed_jobs)}')
            if result.failed_steps:
                print('  Failed steps:')
                for step in result.failed_steps:
                    print(f'    ❌ {step}')
            if result.log_excerpt:
                print(f'  Log excerpt:\n    {result.log_excerpt[:500]}')
        return 0 if run.conclusion == 'success' else 1

    if action == 'wait':
        target_run_id = run_id
        if not target_run_id:
            run = get_latest_run(ci_config, workflow_id=workflow_id) if workflow_id else get_latest_run(ci_config)
            if not run:
                print(_term_err('No runs found to wait for.'), file=sys.stderr)
                return 1
            target_run_id = run.run_id
        print(f'Waiting for run #{target_run_id} (timeout={timeout}s)...', file=sys.stderr)
        completed = wait_for_run(ci_config, target_run_id, timeout_seconds=timeout)
        if not completed:
            print(_term_err(f'Timed out waiting for run #{target_run_id}.'), file=sys.stderr)
            return 1
        icon = '✅' if completed.conclusion == 'success' else '❌'
        print(f'{icon} Run #{completed.run_id}: {completed.conclusion}')
        return 0 if completed.conclusion == 'success' else 1

    if action == 'fix':
        # Full CI loop: check latest → if fail → build fix prompt → run operator
        run = get_latest_run(ci_config, workflow_id=workflow_id) if workflow_id else get_latest_run(ci_config)
        if not run:
            print(_term_err('No CI runs found.'), file=sys.stderr)
            return 1
        if run.conclusion == 'success':
            print(_term_ok(f'CI is green (#{run.run_id}). Nothing to fix.'))
            return 0
        if run.status != 'completed':
            print(f'Run #{run.run_id} still {run.status}. Waiting...', file=sys.stderr)
            run = wait_for_run(ci_config, run.run_id, timeout_seconds=timeout)
            if not run or run.conclusion == 'success':
                print(_term_ok('CI passed.') if run else _term_err('Timed out.'))
                return 0 if run else 1

        result = analyse_run(ci_config, run.run_id)
        print(f'❌ CI failed (#{run.run_id}): {", ".join(result.failed_jobs)}', file=sys.stderr)

        # Build fix goal from CI failure analysis
        fix_goal = _build_ci_fix_goal(result)
        print(f'Fix goal: {fix_goal[:200]}...', file=sys.stderr)

        if dry_run:
            print(json.dumps({'goal': fix_goal, 'ci_result': {'run_id': result.run_id, 'failed_jobs': result.failed_jobs}}, indent=2))
            return 0

        config = load_config(config_path, workspace_override)
        if max_iterations:
            config.max_iterations = max_iterations
        operator = CopilotOperator(config, dry_run=False, live=live)
        op_result = operator.run(goal=fix_goal)
        op_result.pop('_narrativeEngine', None)
        print(json.dumps(op_result, indent=2, ensure_ascii=False))
        return 0 if op_result.get('status') == 'complete' else 1

    print(_term_err(f'Unknown CI action: {action}'), file=sys.stderr)
    return 1


def _build_ci_fix_goal(result: object) -> str:
    """Build an operator goal from a CI failure analysis."""
    lines = ['Fix the CI pipeline failure.', '']
    if result.failed_jobs:
        lines.append(f'Failed jobs: {", ".join(result.failed_jobs)}')
    if result.failed_steps:
        lines.append('Failed steps:')
        for step in result.failed_steps[:10]:
            lines.append(f'  - {step}')
    if result.log_excerpt:
        lines.append('')
        lines.append('Error log excerpt:')
        lines.append(f'```\n{result.log_excerpt[:1500]}\n```')
    lines.append('')
    lines.append('Analyse the error, fix the root cause, and ensure all tests pass.')
    return '\n'.join(lines)


def _roi(
    config_path: str | None,
    workspace_override: str | None,
    output_json: bool,
) -> int:
    """Show ROI analytics."""
    from .roi import analyse_roi

    config = load_config(config_path, workspace_override)
    metrics = analyse_roi(config.log_dir)

    if output_json:
        print(json.dumps(metrics.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(metrics.render_text())
    return 0


def _policy(
    config_path: str | None,
    workspace_override: str | None,
    show_audit: bool,
    limit: int,
) -> int:
    """View policy rules or audit trail."""
    from .policy import PolicyEngine

    engine = PolicyEngine()
    if show_audit:
        trail = engine.get_audit_trail(limit=limit)
        if not trail:
            print('No audit trail entries found.')
        else:
            for entry in trail:
                print(json.dumps(entry, ensure_ascii=False))
    else:
        print(engine.render_rules())
    return 0


def _benchmark(
    config_path: str | None,
    workspace_override: str | None,
    benchmark_file: str,
    output_json: bool,
) -> int:
    """Run operator benchmark cases and report results."""
    from .benchmark import render_benchmark_result, run_benchmark

    path = Path(benchmark_file)
    if not path.exists():
        print(_term_err(f'Benchmark file not found: {path}'), file=sys.stderr)
        return 1

    print(f'Running benchmark: {path}', file=sys.stderr)
    result = run_benchmark(config_path, workspace_override, path)

    if output_json:
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(render_benchmark_result(result))

    return 0 if result.cases_failed == 0 else 1


def _fix_issue(
    config_path: str | None,
    workspace_override: str | None,
    issue_number: int,
    repo_override: str | None,
    goal_profile: str,
    max_iterations: int | None,
    dry_run: bool,
    sync: bool = False,
    auto_close: bool = False,
) -> int:
    """Fetch a GitHub issue and run the operator loop to fix it."""
    import os

    from .github_integration import GitHubConfig, get_issue, issue_to_goal, sync_issue_status

    config = load_config(config_path, workspace_override)

    repo = repo_override or config.repo_profile.repo_name
    if not repo or '/' not in repo:
        raise SystemExit(
            'Provide --repo owner/repo or set repoName=owner/repo in .copilot-operator/repo-profile.yml'
        )
    owner, repo_name = repo.split('/', 1)
    token = config.github_token or os.environ.get('GITHUB_TOKEN', '')
    if not token:
        raise SystemExit('GITHUB_TOKEN environment variable required for fix-issue command.')

    gh_config = GitHubConfig(token=token, owner=owner, repo=repo_name)
    print(f'[fix-issue] Fetching issue #{issue_number} from {repo}...', file=sys.stderr)
    issue = get_issue(gh_config, issue_number)
    print(f'[fix-issue] Issue: {issue.title}', file=sys.stderr)

    goal = issue_to_goal(issue)

    if goal_profile:
        config.goal_profile = goal_profile
    if max_iterations is not None:
        config.max_iterations = max_iterations

    operator = CopilotOperator(config, dry_run=dry_run)
    result = operator.run(goal)

    # Sync result back to GitHub issue
    if sync and not dry_run:
        print(f'[fix-issue] Syncing result to issue #{issue_number}...', file=sys.stderr)
        try:
            sync_issue_status(gh_config, issue_number, result, auto_close=auto_close)
            print(f'[fix-issue] Issue #{issue_number} updated.', file=sys.stderr)
        except Exception as exc:
            print(f'[fix-issue] Warning: sync failed: {exc}', file=sys.stderr)

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def _narrative(config_path: str | None, workspace_override: str | None, run_id: str | None, as_json: bool) -> int:
    """Show the run narrative for the latest or specified run."""
    config = load_config(config_path, workspace_override)

    # Find the run to read
    if run_id:
        narrative_path = config.log_dir / run_id / 'narrative.md'
    else:
        # Find latest run that has a narrative
        state = _read_json(config.state_file)
        run_id = state.get('runId', '')
        narrative_path = config.log_dir / run_id / 'narrative.md' if run_id else None

    if narrative_path and narrative_path.exists():
        text = narrative_path.read_text(encoding='utf-8')
        if as_json:
            print(json.dumps({'runId': run_id, 'narrative': text}, indent=2, ensure_ascii=False))
        else:
            print(text)
    else:
        # Build from state.json if no narrative file
        state = _read_json(config.state_file)
        summary = _read_json(config.summary_file)
        if state and summary:
            from .narrative import build_run_narrative
            goal = state.get('goal', '')
            narrative = build_run_narrative(goal, state, summary, config.target_score)
            text = narrative.render()
            if as_json:
                print(json.dumps({'runId': state.get('runId', ''), 'narrative': text}, indent=2, ensure_ascii=False))
            else:
                print(text)
        else:
            print('[narrative] No run data found.', file=sys.stderr)
            return 1
    return 0


def _mission(config_path: str | None, workspace_override: str | None, as_json: bool, set_direction: str | None, add_goal: str | None) -> int:
    """View or update the mission memory."""
    config = load_config(config_path, workspace_override)
    from .mission_memory import load_mission, save_mission

    mission = load_mission(config.workspace)

    # Auto-detect project name if missing
    if not mission.project_name:
        mission.project_name = config.repo_profile.repo_name or config.workspace.name

    # Apply updates
    changed = False
    if set_direction:
        mission.direction = set_direction
        changed = True
    if add_goal:
        if add_goal not in mission.active_goals:
            mission.active_goals.append(add_goal)
            changed = True

    if changed:
        save_mission(config.workspace, mission)
        print('[mission] Updated.', file=sys.stderr)

    if as_json:
        from .mission_memory import _mission_to_dict
        print(json.dumps(_mission_to_dict(mission), indent=2, ensure_ascii=False))
    else:
        rendered = mission.render()
        if rendered:
            print(rendered)
        else:
            print('[mission] No mission configured. Use --set-direction or --add-goal to get started.', file=sys.stderr)
    return 0


def _read_json(path: object) -> dict:
    """Helper: read a JSON file or return empty dict."""
    from pathlib import Path
    p = Path(str(path))
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        return {}


def _explain(
    config_path: str | None,
    workspace_override: str | None,
    run_id: str | None,
    view: str | None,
    as_json: bool,
    short: bool = False,
) -> int:
    """Show the structured AI Narrative UI (4 views) for a run."""
    config = load_config(config_path, workspace_override)

    # Resolve run ID
    if not run_id:
        state = _read_json(config.state_file)
        run_id = state.get('runId', '')
    if not run_id:
        print('[explain] No run found. Specify --run-id or run the operator first.', file=sys.stderr)
        return 1

    run_log_dir = config.workspace / '.copilot-operator' / 'logs' / run_id

    # --- Short mode: ultra-compact summary ---
    if short:
        state = _read_json(config.state_file)
        summary = _read_json(config.summary_file)
        if not state:
            print('[explain] No state data found.', file=sys.stderr)
            return 1
        from .narrative_engine import NarrativeEngine
        from .narrative_formats import format_summary_short
        engine = NarrativeEngine()
        summary_view = engine.build_summary(state, summary or {}, config)
        short_text = format_summary_short(summary_view)
        if as_json:
            print(json.dumps({
                'runId': run_id, 'goal': summary_view.goal,
                'outcome': summary_view.outcome, 'score': summary_view.score,
                'target_score': summary_view.target_score,
                'iterations': summary_view.iterations,
                'stop_reason': summary_view.stop_reason,
                'risks': summary_view.risks, 'next_step': summary_view.next_step,
            }, indent=2, ensure_ascii=False))
        else:
            print(short_text)
        return 0

    # Try reading pre-rendered narrative-ui.txt (fast path for full view)
    ui_path = run_log_dir / 'narrative-ui.txt'
    if ui_path.exists() and not view:
        text = ui_path.read_text(encoding='utf-8')
        # Append decision traces from persisted file if available
        traces_path = run_log_dir / 'decision-traces.json'
        if traces_path.exists() and 'DECISION TRACE' not in text:
            try:
                from .narrative_engine import NarrativeEngine
                traces_data = json.loads(traces_path.read_text(encoding='utf-8'))
                traces = NarrativeEngine.load_traces(traces_data)
                if traces:
                    trace_text = '\n\n'.join(t.render() for t in traces)
                    text += f'\n\n═══ DECISION TRACE ═══\n{trace_text}'
            except Exception:
                pass
        if as_json:
            print(json.dumps({'runId': run_id, 'narrativeUi': text}, indent=2, ensure_ascii=False))
        else:
            print(text)
        return 0

    # Build views from state + summary
    state = _read_json(config.state_file)
    summary = _read_json(config.summary_file)
    if not state:
        print('[explain] No state data found.', file=sys.stderr)
        return 1

    from .narrative_engine import NarrativeEngine

    engine = NarrativeEngine()

    # Load persisted decision traces for past runs
    traces_path = run_log_dir / 'decision-traces.json'
    if traces_path.exists():
        try:
            traces_data = json.loads(traces_path.read_text(encoding='utf-8'))
            for trace in NarrativeEngine.load_traces(traces_data):
                engine.record_decision(trace)
        except Exception:
            pass

    # Build requested view(s)
    if view == 'live':
        print(engine.build_live(state, config).render())
        return 0

    if view == 'trace':
        text = engine.render_full_trace()
        if as_json:
            print(json.dumps({'runId': run_id, 'traces': engine.serialize_traces()}, indent=2, ensure_ascii=False))
        else:
            print(text)
        return 0

    if view == 'summary':
        print(engine.build_summary(state, summary or {}, config).render())
        return 0

    if view == 'memory':
        mission = None
        try:
            from .mission_memory import load_mission
            mission = load_mission(config.workspace)
        except Exception:
            pass
        insights = None
        try:
            from .brain import analyse_runs
            insights = analyse_runs([])
        except Exception:
            pass
        rules = None
        try:
            from .meta_learner import load_rules
            rules = load_rules(config.workspace)
        except Exception:
            pass
        print(engine.build_memory(mission=mission, insights=insights, rules=rules).render())
        return 0

    # Render all views (default)
    mission = None
    try:
        from .mission_memory import load_mission
        mission = load_mission(config.workspace)
    except Exception:
        pass
    rules = None
    try:
        from .meta_learner import load_rules
        rules = load_rules(config.workspace)
    except Exception:
        pass
    full = engine.render_all_views(state, summary or {}, config, mission=mission, rules=rules)
    if as_json:
        print(json.dumps({'runId': run_id, 'narrativeUi': full}, indent=2, ensure_ascii=False))
    else:
        print(full)
    return 0


def _issues(
    config_path: str | None,
    workspace_override: str | None,
    repo_override: str | None,
    labels: list[str] | None,
    limit: int,
    run_all: bool,
    max_iterations: int | None,
    dry_run: bool,
    live: bool,
    sync: bool,
) -> int:
    """List or batch-process GitHub issues."""
    import os

    from .github_integration import (
        GitHubConfig,
        batch_process_issues,
        sync_issue_status,
    )

    config = load_config(config_path, workspace_override)
    repo = repo_override or config.repo_profile.repo_name
    if not repo or '/' not in repo:
        raise SystemExit('Provide --repo owner/repo or set repoName in repo-profile.yml')
    owner, repo_name = repo.split('/', 1)
    token = config.github_token or os.environ.get('GITHUB_TOKEN', '')
    if not token:
        raise SystemExit('GITHUB_TOKEN required.')

    gh_config = GitHubConfig(token=token, owner=owner, repo=repo_name)
    issues = batch_process_issues(gh_config, labels=labels, limit=limit)

    if not issues:
        print('[issues] No open unassigned issues found.', file=sys.stderr)
        return 0

    if not run_all:
        # List mode — just show the issues
        for item in issues:
            labels_str = ', '.join(item['labels']) if item['labels'] else 'none'
            print(f"  #{item['number']:>5}  [{labels_str}]  {item['title']}")
        print(f'\n{len(issues)} issue(s). Use --run-all to process them.', file=sys.stderr)
        return 0

    # Batch processing mode
    if max_iterations:
        config.max_iterations = max_iterations

    results = []
    for idx, item in enumerate(issues, 1):
        print(f"\n[issues] [{idx}/{len(issues)}] #{item['number']}: {item['title']}", file=sys.stderr)
        operator = CopilotOperator(config, dry_run=dry_run, live=live)
        try:
            result = operator.run(item['goal'])
            if sync and not dry_run:
                try:
                    sync_issue_status(gh_config, item['number'], result)
                except Exception:
                    pass
            results.append({'issue': item['number'], **result})
        except Exception as exc:
            results.append({'issue': item['number'], 'status': 'error', 'error': str(exc)})

    print(json.dumps(results, indent=2, ensure_ascii=False))
    return 0


def _add_common_arguments(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument('--config', default='copilot-operator.yml', help='Path to the operator config file.')
    subparser.add_argument('--workspace', help='Override the workspace path from config.')
    subparser.add_argument('--lang', choices=['en', 'vi'], default='en', help='Display language (en=English, vi=Tiếng Việt).')


def _add_run_shared_arguments(subparser: argparse.ArgumentParser) -> None:
    _add_common_arguments(subparser)
    subparser.add_argument('--goal', help='High-level goal for Copilot.')
    subparser.add_argument('--goal-file', help='Read the goal from a UTF-8 text file.')
    subparser.add_argument('--mode', help='Override chat mode (ask, edit, agent, or custom mode id).')
    subparser.add_argument('--goal-profile', choices=['default', 'bug', 'feature', 'refactor', 'audit', 'docs'], help='Prompt guidance profile for this run.')
    subparser.add_argument('--max-iterations', type=int, help='Override the configured max iteration count.')


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='External local operator for GitHub Copilot in VS Code.')
    subparsers = parser.add_subparsers(dest='command', required=True)

    doctor = subparsers.add_parser('doctor', help='Verify VS Code chat integration and workspace discovery.')
    _add_common_arguments(doctor)
    doctor.set_defaults(handler=lambda args: _doctor(args.config, args.workspace))

    init = subparsers.add_parser('init', help='Scaffold operator files into the target workspace.')
    _add_common_arguments(init)
    init.add_argument('--force', action='store_true', help='Overwrite existing scaffold files.')
    init.add_argument('--no-detect-hints', action='store_true', help='Skip auto-detection of validation commands.')
    init.set_defaults(handler=lambda args: _init(args.config, args.workspace, args.force, not args.no_detect_hints))

    detect_hints = subparsers.add_parser('detect-hints', help='Re-detect ecosystem and hydrate empty validation commands.')
    _add_common_arguments(detect_hints)
    detect_hints.set_defaults(handler=lambda args: _detect_hints(args.config, args.workspace))

    approve = subparsers.add_parser('approve-escalation', help='Approve a pending escalation and resume the run.')
    _add_common_arguments(approve)
    approve.set_defaults(handler=lambda args: _approve_escalation(args.config, args.workspace))

    reject = subparsers.add_parser('reject-escalation', help='Reject a pending escalation, rollback, and adjust prompt.')
    _add_common_arguments(reject)
    reject.set_defaults(handler=lambda args: _reject_escalation(args.config, args.workspace))

    status = subparsers.add_parser('status', help='Read the latest operator state and summary.')
    _add_common_arguments(status)
    status.set_defaults(handler=lambda args: _status(args.config, args.workspace))

    plan = subparsers.add_parser('plan', help='Render only the current milestone plan.')
    _add_common_arguments(plan)
    plan.add_argument('--json', action='store_true', help='Print the raw milestone plan JSON.')
    plan.set_defaults(handler=lambda args: _plan(args.config, args.workspace, args.json))

    focus = subparsers.add_parser('focus', help='Show the active milestone/task baton at a glance.')
    _add_common_arguments(focus)
    focus.add_argument('--json', action='store_true', help='Print the raw focus payload as JSON.')
    focus.set_defaults(handler=lambda args: _focus(args.config, args.workspace, args.json))

    watch = subparsers.add_parser('watch', help='Poll and render operator status until completion or Ctrl+C.')
    _add_common_arguments(watch)
    watch.add_argument('--interval', type=float, default=2.0, help='Polling interval in seconds.')
    watch.add_argument('--count', type=int, help='Optional number of updates before exiting.')
    watch.add_argument('--clear', action='store_true', help='Clear the terminal before each rendered status snapshot.')
    watch.set_defaults(handler=lambda args: _watch(args.config, args.workspace, args.interval, args.count, args.clear))

    cleanup = subparsers.add_parser('cleanup', help='Prune old run directories under the operator log root.')
    _add_common_arguments(cleanup)
    cleanup.add_argument('--keep-runs', type=int, help='Number of recent run directories to keep.')
    cleanup.add_argument('--dry-run', action='store_true', help='Show what would be removed without deleting anything.')
    cleanup.set_defaults(handler=lambda args: _cleanup(args.config, args.workspace, args.keep_runs, args.dry_run))

    run = subparsers.add_parser('run', help='Run the external Copilot operator loop.')
    _add_run_shared_arguments(run)
    run.add_argument('--dry-run', action='store_true', help='Generate prompt only — skip VS Code interaction.')
    run.add_argument('--live', action='store_true', help='Print real-time iteration progress to stderr.')
    run.add_argument('--fresh', action='store_true', help='Force a fresh start, ignoring any pending state.')
    run.set_defaults(
        handler=lambda args: _run(
            args.config,
            args.workspace,
            args.goal,
            args.goal_file,
            args.mode,
            args.goal_profile,
            args.max_iterations,
            False,
            getattr(args, 'dry_run', False),
            getattr(args, 'live', False),
            getattr(args, 'fresh', False),
        )
    )

    resume = subparsers.add_parser('resume', help='Resume from the last operator state file.')
    _add_run_shared_arguments(resume)
    resume.add_argument('--dry-run', action='store_true', help='Generate prompt only — skip VS Code interaction.')
    resume.add_argument('--live', action='store_true', help='Print real-time iteration progress to stderr.')
    resume.set_defaults(
        handler=lambda args: _run(
            args.config,
            args.workspace,
            args.goal,
            args.goal_file,
            args.mode,
            args.goal_profile,
            args.max_iterations,
            True,
            getattr(args, 'dry_run', False),
            getattr(args, 'live', False),
        )
    )

    brain = subparsers.add_parser('brain', help='Show LLM brain status and optionally test it.')
    _add_common_arguments(brain)
    brain.add_argument('--test', dest='test_prompt', help='Send a test prompt to the LLM brain.')
    brain.set_defaults(handler=lambda args: _brain(args.config, args.workspace, args.test_prompt))

    fix_issue = subparsers.add_parser('fix-issue', help='Fetch a GitHub issue and run the operator to fix it.')
    _add_common_arguments(fix_issue)
    fix_issue.add_argument('--issue', type=int, required=True, help='GitHub issue number to fix.')
    fix_issue.add_argument('--repo', help='GitHub repo as owner/repo. Falls back to repoName in repo-profile.yml.')
    fix_issue.add_argument('--goal-profile', choices=['default', 'bug', 'feature', 'refactor', 'audit', 'docs'], default='bug')
    fix_issue.add_argument('--max-iterations', type=int, help='Override max iterations.')
    fix_issue.add_argument('--dry-run', action='store_true', help='Generate prompt without executing.')
    fix_issue.add_argument('--sync', action='store_true', help='Sync result back to GitHub issue (labels + comment).')
    fix_issue.add_argument('--auto-close', action='store_true', help='Close the issue if operator completes successfully.')
    fix_issue.set_defaults(handler=lambda args: _fix_issue(
        args.config, args.workspace, args.issue, args.repo, args.goal_profile,
        args.max_iterations, args.dry_run, args.sync, args.auto_close,
    ))

    # --- Issues: list or batch-process GitHub issues ---
    issues_cmd = subparsers.add_parser('issues', help='List or batch-process open GitHub issues.')
    _add_common_arguments(issues_cmd)
    issues_cmd.add_argument('--repo', help='GitHub repo as owner/repo.')
    issues_cmd.add_argument('--labels', nargs='+', help='Filter by label (e.g. bug enhancement).')
    issues_cmd.add_argument('--limit', type=int, default=10, help='Max issues to list/process.')
    issues_cmd.add_argument('--run-all', action='store_true', help='Process all listed issues sequentially.')
    issues_cmd.add_argument('--max-iterations', type=int, help='Max iterations per issue.')
    issues_cmd.add_argument('--dry-run', action='store_true')
    issues_cmd.add_argument('--live', action='store_true')
    issues_cmd.add_argument('--sync', action='store_true', help='Sync results back to GitHub.')
    issues_cmd.set_defaults(handler=lambda args: _issues(
        args.config, args.workspace, args.repo, args.labels, args.limit,
        args.run_all, args.max_iterations, args.dry_run, args.live, args.sync,
    ))

    version = subparsers.add_parser('version', help='Print the operator version.')
    version.set_defaults(handler=lambda args: _version())

    # --- Narrative: show run narrative / done explanation ---
    narrative = subparsers.add_parser('narrative', help='Show the run narrative for the latest or a specific run.')
    _add_common_arguments(narrative)
    narrative.add_argument('--run-id', help='Show narrative for a specific run ID.')
    narrative.add_argument('--json', action='store_true', dest='output_json', help='Output as JSON.')
    narrative.set_defaults(handler=lambda args: _narrative(args.config, args.workspace, args.run_id, args.output_json))

    # --- Explain: structured AI Narrative UI (4 views) ---
    explain = subparsers.add_parser('explain', help='Show the structured AI Narrative UI: Live, Decision Trace, Summary, Memory.')
    _add_common_arguments(explain)
    explain.add_argument('--run-id', help='Show narrative for a specific run ID.')
    explain.add_argument('--view', choices=['live', 'trace', 'summary', 'memory'], help='Show only a specific view.')
    explain.add_argument('--json', action='store_true', dest='output_json', help='Output as JSON.')
    explain.add_argument('--short', action='store_true', help='Ultra-compact summary (5 lines).')
    explain.set_defaults(handler=lambda args: _explain(args.config, args.workspace, args.run_id, args.view, args.output_json, args.short))

    # --- Mission: view/update mission memory ---
    mission = subparsers.add_parser('mission', help='View or update the project mission memory.')
    _add_common_arguments(mission)
    mission.add_argument('--json', action='store_true', dest='output_json', help='Output as JSON.')
    mission.add_argument('--set-direction', help='Set the project direction (one-line).')
    mission.add_argument('--add-goal', help='Add an active goal to the mission.')
    mission.set_defaults(handler=lambda args: _mission(
        args.config, args.workspace, args.output_json, args.set_direction, args.add_goal,
    ))

    benchmark = subparsers.add_parser('benchmark', help='Run operator benchmark cases and score results.')
    _add_common_arguments(benchmark)
    benchmark.add_argument('--file', required=True, dest='benchmark_file', help='Path to benchmark JSON file.')
    benchmark.add_argument('--json', action='store_true', dest='output_json', help='Output results as JSON.')
    benchmark.set_defaults(handler=lambda args: _benchmark(
        args.config, args.workspace, args.benchmark_file, args.output_json,
    ))

    # --- Queue: run multiple goals sequentially ---
    queue = subparsers.add_parser('queue', help='Run multiple goals sequentially from a file or arguments.')
    _add_common_arguments(queue)
    queue.add_argument('--goals', nargs='+', help='List of goals to run sequentially.')
    queue.add_argument('--file', dest='goals_file', help='Path to file with one goal per line.')
    queue.add_argument('--max-iterations', type=int, default=0, help='Max iterations per goal (0 = use config default).')
    queue.add_argument('--dry-run', action='store_true')
    queue.add_argument('--live', action='store_true')
    queue.set_defaults(handler=lambda args: _queue(
        args.config, args.workspace, args.goals, args.goals_file,
        args.max_iterations, args.dry_run, args.live,
    ))

    # --- Multi: run multi-session plan with roles (coder, reviewer, tester...) ---
    multi = subparsers.add_parser('multi', help='Run a multi-session plan with different roles.')
    _add_common_arguments(multi)
    multi.add_argument('--goal', help='The goal for the multi-session plan.')
    multi.add_argument('--goal-file', help='Read goal from file.')
    multi.add_argument('--roles', nargs='+', default=['coder', 'reviewer'],
                       help='Roles to run (default: coder reviewer). Options: coder, reviewer, tester, fixer, docs.')
    multi.add_argument('--max-iterations', type=int, default=0, help='Max iterations per role.')
    multi.add_argument('--dry-run', action='store_true')
    multi.add_argument('--live', action='store_true')
    multi.set_defaults(handler=lambda args: _multi(
        args.config, args.workspace, args.goal, args.goal_file,
        args.roles, args.max_iterations, args.dry_run, args.live,
    ))

    # --- Dashboard: live terminal UI ---
    dash = subparsers.add_parser('dashboard', help='Live terminal dashboard for monitoring operator runs.')
    _add_common_arguments(dash)
    dash.add_argument('--interval', type=float, default=2.0, help='Refresh interval in seconds.')
    dash.add_argument('--count', type=int, help='Number of updates before exiting.')
    dash.add_argument('--no-clear', action='store_true', help='Do not clear screen between frames.')
    dash.set_defaults(handler=lambda args: _dashboard(args.config, args.workspace, args.interval, args.count, args.no_clear))

    # --- Nightly: overnight delivery loop ---
    nightly = subparsers.add_parser('nightly', help='Run overnight delivery loop with goals from file.')
    _add_common_arguments(nightly)
    nightly.add_argument('--file', dest='goals_file', help='Path to file with one goal per line.')
    nightly.add_argument('--max-iterations', type=int, default=6, help='Max iterations per task.')
    nightly.add_argument('--dry-run', action='store_true')
    nightly.set_defaults(handler=lambda args: _nightly(args.config, args.workspace, args.goals_file, args.max_iterations, args.dry_run))

    # --- ROI: analytics and metrics ---
    roi_cmd = subparsers.add_parser('roi', help='Show ROI analytics from past operator runs.')
    _add_common_arguments(roi_cmd)
    roi_cmd.add_argument('--json', action='store_true', dest='output_json', help='Output as JSON.')
    roi_cmd.set_defaults(handler=lambda args: _roi(args.config, args.workspace, args.output_json))

    # --- Policy: view and audit policy rules ---
    policy_cmd = subparsers.add_parser('policy', help='View policy rules and audit trail.')
    _add_common_arguments(policy_cmd)
    policy_cmd.add_argument('--audit', action='store_true', help='Show recent audit trail.')
    policy_cmd.add_argument('--limit', type=int, default=20, help='Number of audit entries to show.')
    policy_cmd.set_defaults(handler=lambda args: _policy(args.config, args.workspace, args.audit, args.limit))

    # --- CI: trigger, check, wait, and fix CI failures ---
    ci_cmd = subparsers.add_parser('ci', help='CI integration — trigger, check, wait for, and fix CI failures.')
    _add_common_arguments(ci_cmd)
    ci_cmd.add_argument('action', choices=['list', 'trigger', 'check', 'wait', 'fix'],
                        help='list: show workflows. trigger: start a workflow. check: show latest run. wait: poll until done. fix: auto-fix CI failure.')
    ci_cmd.add_argument('--workflow-id', default='', help='Workflow ID or filename (e.g. "ci.yml").')
    ci_cmd.add_argument('--run-id', type=int, default=0, help='Specific run ID (for wait).')
    ci_cmd.add_argument('--timeout', type=int, default=600, help='Timeout in seconds for wait/fix (default: 600).')
    ci_cmd.add_argument('--max-iterations', type=int, default=3, help='Max operator iterations for fix (default: 3).')
    ci_cmd.add_argument('--dry-run', action='store_true', help='Show fix goal without running operator.')
    ci_cmd.add_argument('--live', action='store_true', help='Live output during fix.')
    ci_cmd.set_defaults(handler=lambda args: _ci(
        args.config, args.workspace, args.action, args.workflow_id,
        args.run_id, args.timeout, args.action == 'fix',
        args.max_iterations, args.dry_run, getattr(args, 'live', False),
    ))

    return parser


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding='utf-8')
    parser = build_parser()
    parser.add_argument('-V', action='version', version=f'copilot-operator {_get_version()}')
    args = parser.parse_args(argv)
    # Apply locale setting
    lang = getattr(args, 'lang', 'en')
    if lang and lang != 'en':
        from .locale import set_locale
        set_locale(lang)
    return args.handler(args)
