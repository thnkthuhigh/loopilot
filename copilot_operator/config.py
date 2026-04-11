from __future__ import annotations

__all__ = [
    'ValidationCommand',
    'RepoProfile',
    'OperatorConfig',
    'load_config',
]

import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as _yaml_err:
    raise RuntimeError(
        'PyYAML is required: pip install pyyaml'
    ) from _yaml_err

from .logging_config import get_logger
from .repo_inspector import WorkspaceInsight, detect_workspace_insight

logger = get_logger('config')


@dataclass(slots=True)
class ValidationCommand:
    name: str
    command: str = ""
    required: bool = False
    timeout_seconds: int = 300
    run_before_prompt: bool = True
    run_after_response: bool = True
    source: str = 'configured'


@dataclass(slots=True)
class RepoProfile:
    path: Path | None = None
    repo_name: str = ""
    summary: str = ""
    architecture: str = ""
    standards: list[str] = field(default_factory=list)
    priorities: list[str] = field(default_factory=list)
    protected_paths: list[str] = field(default_factory=list)
    project_memory_files: list[Path] = field(default_factory=list)
    prompt_appendix: str = ""
    default_goal_file: Path | None = None


@dataclass(slots=True)
class OperatorConfig:
    workspace: Path
    mode: str = 'agent'
    goal_profile: str = 'default'
    max_iterations: int = 6
    poll_interval_seconds: float = 2.0
    session_timeout_seconds: int = 900
    quiet_period_polls: int = 2
    target_score: int = 85
    fail_on_blockers: list[str] = field(default_factory=lambda: ['critical', 'high'])
    code_command_retries: int = 2
    code_command_retry_delay_seconds: float = 2.0
    focus_settle_seconds: float = 2.0
    max_error_retries: int = 3
    error_backoff_base_seconds: float = 2.0
    log_retention_runs: int = 20
    memory_file: Path = Path('.copilot-operator/memory.md')
    state_file: Path = Path('.copilot-operator/state.json')
    summary_file: Path = Path('.copilot-operator/session-summary.json')
    log_dir: Path = Path('.copilot-operator/logs')
    validation: list[ValidationCommand] = field(default_factory=list)
    repo_profile: RepoProfile = field(default_factory=RepoProfile)
    workspace_insight: WorkspaceInsight = field(default_factory=WorkspaceInsight)
    llm_config_raw: dict[str, Any] = field(default_factory=dict)
    escalation_policy: str = 'escalate'  # 'escalate' | 'block' | 'warn'
    large_diff_threshold: int = 200
    max_llm_cost_usd: float = 0.0         # 0 = unlimited; stop run if LLM cost exceeds this
    max_task_seconds: int = 0              # 0 = unlimited; wall-clock timeout per run
    auto_create_pr: bool = False
    github_token: str = field(default_factory=lambda: os.environ.get('GITHUB_TOKEN', ''))
    # Chat model override — sets github.copilot.chat.preferredModel in VS Code settings
    # If empty, the bootstrap default list is used (currently Claude Sonnet 4.5).
    chat_model: str = ''
    # SLA enforcement
    sla_max_blocked_seconds: int = 0     # 0 = no SLA; alert if blocked longer than this
    sla_max_cost_per_hour_usd: float = 0.0  # 0 = no limit
    # Output expectations (declarative guards)
    expect_tests_added: bool = False     # require at least one new test file/function
    expect_docs_updated: bool = False    # require docs change when code changes
    expect_max_files_changed: int = 0    # 0 = no limit; block if more files changed
    # Stop controller
    stop_no_progress_iterations: int = 2   # stop after N iterations with no diff
    stop_score_floor: int = 20             # hard stop if score stays below this
    stop_score_floor_iterations: int = 3   # ... for this many consecutive iterations
    # Repo map
    repo_map_max_chars: int = 6000         # max chars in repo map context
    repo_map_max_files: int = 60           # max files to include in repo map
    # Snapshot
    max_snapshots: int = 10                # max git stash snapshots to keep
    # Memory promotion thresholds
    promotion_trap_repeat: int = 2         # recurring blocker count → project trap
    promotion_validation_confirm: int = 2  # persistent validation failure → project
    promotion_strategy_success: int = 3    # strategy success count → cross-repo

    def ensure_runtime_dirs(self) -> None:
        self.memory_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.summary_file.parent.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)


def _resolve_config_path(base: Path, value: str | Path | None, default: str | Path) -> Path:
    raw = Path(value or default)
    if raw.is_absolute():
        return raw
    return (base / raw).resolve()


def _resolve_workspace_path(config_dir: Path, value: str | None) -> Path:
    raw = Path(value or '.')
    if raw.is_absolute():
        return raw.resolve()
    return (config_dir / raw).resolve()


def _merge_dicts(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged


def _read_yaml_file(path: Path | None) -> dict[str, Any]:
    if not path or not path.exists():
        return {}
    try:
        loaded = yaml.safe_load(path.read_text(encoding='utf-8'))
        return loaded if isinstance(loaded, dict) else {}
    except (OSError, yaml.YAMLError) as exc:
        logger.error('Failed to parse %s: %s', path, exc)
        return {}


def _load_validation(items: list[dict[str, Any]] | None) -> list[ValidationCommand]:
    commands: list[ValidationCommand] = []
    for item in items or []:
        commands.append(
            ValidationCommand(
                name=str(item.get('name', 'unnamed')),
                command=str(item.get('command', '')).strip(),
                required=bool(item.get('required', False)),
                timeout_seconds=int(item.get('timeoutSeconds', item.get('timeout_seconds', 1200))),
                run_before_prompt=bool(item.get('runBeforePrompt', item.get('run_before_prompt', True))),
                run_after_response=bool(item.get('runAfterResponse', item.get('run_after_response', True))),
                source='configured' if str(item.get('command', '')).strip() else 'unset',
            )
        )
    return commands


def _apply_validation_hints(validation: list[ValidationCommand], insight: WorkspaceInsight) -> list[ValidationCommand]:
    hydrated: list[ValidationCommand] = []
    for item in validation:
        if item.command:
            hydrated.append(item)
            continue
        hint = insight.validation_hints.get(item.name.lower())
        if hint:
            hydrated.append(replace(item, command=hint, source='inferred'))
        else:
            hydrated.append(item)
    return hydrated


def _resolve_profile_path(config_dir: Path, workspace: Path, explicit_value: str | Path | None) -> Path | None:
    if explicit_value:
        return _resolve_config_path(config_dir, explicit_value, explicit_value)
    candidate = workspace / '.copilot-operator' / 'repo-profile.yml'
    return candidate.resolve() if candidate.exists() else None


def _load_repo_profile(raw: dict[str, Any], profile_path: Path | None, workspace: Path) -> RepoProfile:
    if not raw and not profile_path:
        return RepoProfile()
    base_dir = workspace
    memory_files = [
        _resolve_config_path(base_dir, item, item)
        for item in raw.get('projectMemoryFiles', raw.get('project_memory_files', []))
    ]
    default_goal_value = raw.get('defaultGoalFile', raw.get('default_goal_file'))
    default_goal_file = _resolve_config_path(base_dir, default_goal_value, default_goal_value) if default_goal_value else None
    return RepoProfile(
        path=profile_path,
        repo_name=str(raw.get('repoName', raw.get('repo_name', ''))).strip(),
        summary=str(raw.get('summary', '')).strip(),
        architecture=str(raw.get('architecture', '')).strip(),
        standards=[str(item).strip() for item in raw.get('standards', []) if str(item).strip()],
        priorities=[str(item).strip() for item in raw.get('priorities', []) if str(item).strip()],
        protected_paths=[str(item).strip() for item in raw.get('protectedPaths', raw.get('protected_paths', [])) if str(item).strip()],
        project_memory_files=memory_files,
        prompt_appendix=str(raw.get('promptAppendix', raw.get('prompt_appendix', ''))).strip(),
        default_goal_file=default_goal_file,
    )


def _load_dotenv(workspace: Path) -> None:
    """Load .env file from workspace if python-dotenv is available."""
    env_file = workspace / '.env'
    if not env_file.exists():
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(env_file, override=False)
    except ImportError:
        # Manual fallback: parse KEY=VALUE lines (no interpolation)
        try:
            for line in env_file.read_text(encoding='utf-8').splitlines():
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if '=' not in line:
                    continue
                key, _, value = line.partition('=')
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
        except OSError:
            pass


def load_config(path: str | Path | None = None, workspace_override: str | Path | None = None) -> OperatorConfig:
    raw_path = Path(path or 'copilot-operator.yml')
    # When --workspace is given and no explicit --config was provided,
    # look for the config file inside the target workspace first.
    if workspace_override and not path:
        ws_candidate = Path(workspace_override).resolve() / raw_path
        if ws_candidate.exists():
            raw_path = ws_candidate
    config_path = raw_path.resolve()
    raw = _read_yaml_file(config_path)
    config_dir = config_path.parent if config_path.exists() else Path.cwd()
    initial_workspace = Path(workspace_override).resolve() if workspace_override else _resolve_workspace_path(config_dir, raw.get('workspace'))

    profile_path = _resolve_profile_path(config_dir, initial_workspace, raw.get('repoProfile', raw.get('repo_profile')))
    profile_raw = _read_yaml_file(profile_path)
    profile_operator_raw = profile_raw.get('operator', {}) if isinstance(profile_raw.get('operator', {}), dict) else {}
    merged_raw = _merge_dicts(profile_operator_raw, raw)

    workspace = Path(workspace_override).resolve() if workspace_override else _resolve_workspace_path(config_dir, merged_raw.get('workspace'))

    # Load .env before detecting anything that depends on env vars
    _load_dotenv(workspace)

    repo_profile = _load_repo_profile(profile_raw, profile_path, workspace)
    workspace_insight = detect_workspace_insight(workspace)
    validation = _apply_validation_hints(_load_validation(merged_raw.get('validation')), workspace_insight)

    # LLM brain config (keys come from env only, never from YAML)
    llm_raw = merged_raw.get('llm', merged_raw.get('llmBrain', {}))
    llm_config_raw = llm_raw if isinstance(llm_raw, dict) else {}

    return OperatorConfig(
        workspace=workspace,
        mode=str(merged_raw.get('mode', 'agent')),
        goal_profile=str(merged_raw.get('goalProfile', merged_raw.get('goal_profile', 'default'))).strip().lower() or 'default',
        max_iterations=int(merged_raw.get('maxIterations', merged_raw.get('max_iterations', 6))),
        poll_interval_seconds=float(merged_raw.get('pollIntervalSeconds', merged_raw.get('poll_interval_seconds', 2.0))),
        session_timeout_seconds=int(merged_raw.get('sessionTimeoutSeconds', merged_raw.get('session_timeout_seconds', 900))),
        quiet_period_polls=int(merged_raw.get('quietPeriodPolls', merged_raw.get('quiet_period_polls', 2))),
        target_score=int(merged_raw.get('targetScore', merged_raw.get('target_score', 85))),
        fail_on_blockers=[str(item).lower() for item in merged_raw.get('failOnBlockers', merged_raw.get('fail_on_blockers', ['critical', 'high']))],
        code_command_retries=int(merged_raw.get('codeCommandRetries', merged_raw.get('code_command_retries', 2))),
        code_command_retry_delay_seconds=float(merged_raw.get('codeCommandRetryDelaySeconds', merged_raw.get('code_command_retry_delay_seconds', 2.0))),
        focus_settle_seconds=float(merged_raw.get('focusSettleSeconds', merged_raw.get('focus_settle_seconds', 2.0))),
        max_error_retries=int(merged_raw.get('maxErrorRetries', merged_raw.get('max_error_retries', 3))),
        error_backoff_base_seconds=float(merged_raw.get('errorBackoffBaseSeconds', merged_raw.get('error_backoff_base_seconds', 2.0))),
        log_retention_runs=int(merged_raw.get('logRetentionRuns', merged_raw.get('log_retention_runs', 20))),
        memory_file=_resolve_config_path(workspace, merged_raw.get('memoryFile'), '.copilot-operator/memory.md'),
        state_file=_resolve_config_path(workspace, merged_raw.get('stateFile'), '.copilot-operator/state.json'),
        summary_file=_resolve_config_path(workspace, merged_raw.get('summaryFile'), '.copilot-operator/session-summary.json'),
        log_dir=_resolve_config_path(workspace, merged_raw.get('logDir'), '.copilot-operator/logs'),
        validation=validation,
        repo_profile=repo_profile,
        workspace_insight=workspace_insight,
        llm_config_raw=llm_config_raw,
        escalation_policy=str(merged_raw.get('escalationPolicy', merged_raw.get('escalation_policy', 'escalate'))).strip().lower() or 'escalate',
        large_diff_threshold=int(merged_raw.get('largeDiffThreshold', merged_raw.get('large_diff_threshold', 200))),
        auto_create_pr=bool(merged_raw.get('autoCreatePr', merged_raw.get('auto_create_pr', False))),
        github_token=os.environ.get('GITHUB_TOKEN', ''),
        chat_model=str(merged_raw.get('chatModel', merged_raw.get('chat_model', ''))).strip(),
        stop_no_progress_iterations=int(merged_raw.get('stopNoProgressIterations', merged_raw.get('stop_no_progress_iterations', 2))),
        stop_score_floor=int(merged_raw.get('stopScoreFloor', merged_raw.get('stop_score_floor', 20))),
        stop_score_floor_iterations=int(merged_raw.get('stopScoreFloorIterations', merged_raw.get('stop_score_floor_iterations', 3))),
        repo_map_max_chars=int(merged_raw.get('repoMapMaxChars', merged_raw.get('repo_map_max_chars', 6000))),
        repo_map_max_files=int(merged_raw.get('repoMapMaxFiles', merged_raw.get('repo_map_max_files', 60))),
        max_snapshots=int(merged_raw.get('maxSnapshots', merged_raw.get('max_snapshots', 10))),
        promotion_trap_repeat=int(merged_raw.get('promotionTrapRepeat', merged_raw.get('promotion_trap_repeat', 2))),
        promotion_validation_confirm=int(merged_raw.get('promotionValidationConfirm', merged_raw.get('promotion_validation_confirm', 2))),
        promotion_strategy_success=int(merged_raw.get('promotionStrategySuccess', merged_raw.get('promotion_strategy_success', 3))),
    )
