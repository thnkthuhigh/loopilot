from __future__ import annotations

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
) -> int:
    config = load_config(config_path, workspace_override)
    if mode:
        config.mode = mode
    if goal_profile:
        config.goal_profile = goal_profile.strip().lower()
    if max_iterations is not None:
        config.max_iterations = max_iterations
    operator = CopilotOperator(config, dry_run=dry_run, live=live)
    goal_value = None if resume and not (goal or goal_file) else _read_goal(config_path, workspace_override, goal, goal_file)
    result = operator.run(goal_value, resume=resume)
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
) -> int:
    """Fetch a GitHub issue and run the operator loop to fix it."""
    import os

    from .github_integration import GitHubConfig, get_issue, issue_to_goal

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
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def _add_common_arguments(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument('--config', default='copilot-operator.yml', help='Path to the operator config file.')
    subparser.add_argument('--workspace', help='Override the workspace path from config.')


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
    fix_issue.set_defaults(handler=lambda args: _fix_issue(
        args.config, args.workspace, args.issue, args.repo, args.goal_profile, args.max_iterations, args.dry_run,
    ))

    version = subparsers.add_parser('version', help='Print the operator version.')
    version.set_defaults(handler=lambda args: _version())

    benchmark = subparsers.add_parser('benchmark', help='Run operator benchmark cases and score results.')
    _add_common_arguments(benchmark)
    benchmark.add_argument('--file', required=True, dest='benchmark_file', help='Path to benchmark JSON file.')
    benchmark.add_argument('--json', action='store_true', dest='output_json', help='Output results as JSON.')
    benchmark.set_defaults(handler=lambda args: _benchmark(
        args.config, args.workspace, args.benchmark_file, args.output_json,
    ))

    return parser


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding='utf-8')
    parser = build_parser()
    parser.add_argument('-V', action='version', version=f'copilot-operator {_get_version()}')
    args = parser.parse_args(argv)
    return args.handler(args)
