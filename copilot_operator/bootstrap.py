from __future__ import annotations

__all__ = [
    'initialize_workspace',
    'read_operator_status',
    'read_operator_focus',
    'format_operator_focus',
    'watch_operator_status',
    'cleanup_run_logs',
    'detect_and_hydrate',
]

import json
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any, TextIO

from .planner import build_milestone_baton, summarize_plan
from .repo_inspector import WorkspaceInsight, detect_workspace_insight


def _workspace_name(workspace: Path) -> str:
    return workspace.resolve().name or 'your-repo'


def _repo_profile_text(workspace: Path) -> str:
    repo_name = _workspace_name(workspace)
    return f"""repoName: {repo_name}
summary: One-paragraph summary of what this repo does.
architecture: Short architecture summary that tells Copilot where the important layers live.
standards:
  - Keep changes localized and reviewable.
  - Prefer existing architecture and naming patterns.
priorities:
  - Replace placeholder validation commands with the real ones for this repo.
  - Fill the repo memory docs with project-specific truth.
protectedPaths:
  - path/that/should/not/change/easily
projectMemoryFiles:
  - docs/operator/repo-profile.md
  - docs/operator/architecture-map.md
  - docs/operator/definition-of-done.md
  - docs/operator/validation-map.md
  - docs/operator/known-traps.md
defaultGoalFile: .copilot-operator/default-goal.txt
operator:
  targetScore: 85
  failOnBlockers:
    - critical
    - high
  validation:
    - name: tests
      command: npm test
      required: true
      runBeforePrompt: true
      runAfterResponse: true
    - name: lint
      command: npm run lint
      required: true
      runBeforePrompt: false
      runAfterResponse: true
    - name: build
      command: npm run build
      required: false
      runBeforePrompt: false
      runAfterResponse: true
"""


def _config_text() -> str:
    return """workspace: .
mode: agent
goalProfile: default
maxIterations: 6
pollIntervalSeconds: 2
sessionTimeoutSeconds: 900
quietPeriodPolls: 2
targetScore: 85
failOnBlockers:
  - critical
  - high
codeCommandRetries: 2
codeCommandRetryDelaySeconds: 2
logRetentionRuns: 20
memoryFile: .copilot-operator/memory.md
stateFile: .copilot-operator/state.json
summaryFile: .copilot-operator/session-summary.json
logDir: .copilot-operator/logs
validation:
  - name: tests
    command: ""
    required: false
    timeoutSeconds: 1200
    runBeforePrompt: true
    runAfterResponse: true
  - name: lint
    command: ""
    required: false
    timeoutSeconds: 1200
    runBeforePrompt: false
    runAfterResponse: true
  - name: build
    command: ""
    required: false
    timeoutSeconds: 1200
    runBeforePrompt: false
    runAfterResponse: true
"""


def _runtime_readme_text() -> str:
    return """# Copilot Operator Runtime

This folder is generated and updated by `python -m copilot_operator run` and `python -m copilot_operator resume`.

Key files:

- `repo-profile.yml`: per-repo defaults and attached memory files
- `default-goal.txt`: optional default goal if you do not pass `--goal`
- `memory.md`: rolling continuity file attached to each fresh `code chat` call
- `state.json`: machine-readable operator state, including `goalProfile`, `workspaceInsight`, milestone plan state, and reason codes
- `session-summary.json`: concise last-run summary for scripts and dashboards
- `logs/`: one subdirectory per `runId`, each containing prompts, Copilot replies, decisions, and validation artifacts per iteration
- old run directories can be pruned with `python -m copilot_operator cleanup`
"""


def _default_goal_text() -> str:
    return """Stabilize the current workspace without broad rewrites.

Constraints:
- Prefer localized changes.
- Follow the existing architecture and naming patterns.
- If you touch operator logic, update tests or docs that describe the new behavior.

Stop only when:
- score >= 85
- no critical or high blockers remain
- required validations configured for this repo pass
"""


def _repo_profile_doc(workspace: Path) -> str:
    repo_name = _workspace_name(workspace)
    return f"""# Repo Profile

## Identity

- Repo name: {repo_name}
- Primary purpose: replace this paragraph with the real business and technical goal of the repo

## Architecture summary

- Fill in the major layers, services, apps, packages, and important paths
- Explain where tests live
- Explain where build and deploy logic lives

## Current focus

- Fill in the real priorities for this repo
"""


def _architecture_map_doc() -> str:
    return """# Architecture Map

## Runtime layers

- App entrypoints:
- Core modules:
- Data layer:
- Test layer:
- Build/deploy layer:

## Important paths

- `src/`:
- `tests/`:
- `docs/`:
- `scripts/`:

## Notes for the operator

- Which areas are high risk?
- Which files should not be changed lightly?
"""


def _definition_of_done_doc() -> str:
    return """# Definition of Done

A run may stop as `complete` only when all of the following are true:

- Copilot reports `status=done`
- score is at least the configured `targetScore`
- no blocker remains at a severity listed in `failOnBlockers`
- every required operator-side validation has `status=pass`
- the stop reason is understandable by a human reading `state.json`

Add repo-specific rules below.
"""


def _validation_map_doc() -> str:
    return """# Validation Map

## Validation lanes

- `tests`: behavior correctness gate
- `lint`: code quality and style gate
- `build`: integration / compile gate
- `smoke`: lightweight product-path verification gate

## Real commands for this repo

- tests:
- lint:
- build:
- smoke:
"""


def _known_traps_doc() -> str:
    return """# Known Traps

Document the repo-specific things that often make AI coding runs go sideways.

Examples:

- hidden environment requirements
- long-running tests
- generated files that should not be edited directly
- brittle legacy modules
- commands that must be run from a specific subdirectory
"""


def _goal_templates_doc() -> str:
    return """# Copilot Operator Goal Templates

## Bug fix

```text
Fix [bug name] in [area].

Constraints:
- Keep the fix minimal and localized.
- Do not change public API unless required.
- Add or update tests close to the behavior gap if needed.

Stop only when:
- relevant tests pass
- lint passes
- no high or critical blockers remain
- score >= 85
```

## Feature implementation

```text
Implement [feature] in [area].

Constraints:
- Follow existing architecture and naming patterns.
- Prefer incremental changes over broad rewrites.
- Update tests and docs affected by the feature.

Stop only when:
- feature works in the intended path
- tests pass
- lint/build pass
- no high or critical blockers remain
- score >= 85
```

## Refactor

```text
Refactor [area] without changing behavior.

Constraints:
- Keep the behavior stable.
- Prefer small reviewable slices.
- Strengthen tests or checks around risky edges if needed.

Stop only when:
- behavior is preserved
- tests/lint pass
- no high or critical blockers remain
- score >= 85
```
"""


def _checklist_doc() -> str:
    return """# Copilot Operator Quick Checklist

## Day 0

- [ ] Run `python -m copilot_operator doctor`
- [ ] Fill real commands into `copilot-operator.yml` or `.copilot-operator/repo-profile.yml`
- [ ] Fill `docs/operator/*` with project-specific truth
- [ ] Run one smoke goal that does not edit files
- [ ] Run one real bug-fix or lint-fix goal

## Before each run

- [ ] Goal is specific and has stop conditions
- [ ] Goal profile is chosen correctly (`bug`, `feature`, `refactor`, `audit`, `docs`, or `default`)
- [ ] VS Code is open on the correct repo
- [ ] Required validations are configured
- [ ] There are no risky uncommitted local edits that the operator could overwrite
"""


def _vscode_settings_text() -> str:
    return """{
  "chat.tools.global.autoApprove": true,
  "chat.autopilot.enabled": true,
  "chat.tools.terminal.enableAutoApprove": true,
  "chat.tools.edits.autoApprove": {
    "**": true
  }
}
"""


def _agent_operator_text() -> str:
    return """---
name: Operator
description: "Autonomous coding agent for the loopilot operator loop. Directly implements goals, runs tests, and emits OPERATOR_STATE output. Use this as the default mode for loopilot runs."
tools: [read, search, edit, execute, todo, web]
model:
  - Claude Opus 4.5 (copilot)
  - Claude Sonnet 4.5 (copilot)
  - gpt-4o (copilot)
---
You are an autonomous coding agent being driven by an external operator loop.

## Mission

Complete the goal given in the prompt. Make real code changes, run tests, and report results.

## Rules

1. Read files before editing. Understand existing code first.
2. Make only the changes needed for the current goal.
3. Run tests after changes to verify correctness.
4. Do not merge, deploy, publish, or push remote changes.
5. **AUTONOMOUS MODE**: Never ask the user a question. Never request confirmation. When uncertain, read files or search, then decide.

## Required Output Format

You MUST end every reply with EXACTLY these two blocks filled in with real values:

<OPERATOR_STATE>
{"status": "continue", "score": 0, "summary": "what changed", "next_prompt": "next step if needed", "tests": "pass|fail|not_run|not_applicable", "lint": "pass|fail|not_run|not_applicable", "blockers": [], "needs_continue": false, "done_reason": "explanation"}
</OPERATOR_STATE>

<OPERATOR_PLAN>
{"summary": "plan summary", "current_milestone_id": "m1", "next_milestone_id": "", "current_task_id": "m1.t1", "next_task_id": "", "milestones": [{"id": "m1", "title": "complete goal", "status": "done", "summary": "", "acceptance": [], "current_task_id": "", "next_task_id": "", "tasks": []}]}
</OPERATOR_PLAN>
"""


def _agent_supervisor_text() -> str:
    return """---
name: Supervisor
description: "Use when you want an autonomous local supervisor loop for GitHub Copilot agent mode: research, implement, audit, continue, score progress, and decide the next directive."
argument-hint: "State the goal, constraints, and current progress"
tools: [agent, read, search, web, todo, edit, execute]
agents: [Researcher, Implementer, Auditor]
model:
  - Claude Opus 4.5 (copilot)
  - Claude Sonnet 4.5 (copilot)
  - gpt-4o (copilot)
---
You are the top-level local supervisor for this workspace.

## Mission

Drive a repeating `Research -> Implement -> Audit` loop until the task is genuinely ready to stop or the work is clearly blocked.

## Operating Rules

1. Do not make direct code edits yourself. Delegate implementation to `Implementer`.
2. Start with `Researcher` when requirements, repo patterns, or market comparisons are unclear or stale.
3. Always end a loop with `Auditor`.
4. If the latest audit says the score is below target or blockers remain, continue immediately using `nextDirective`.
5. Never finish with a generic "done" if the audit still shows real gaps.
6. **AUTONOMOUS MODE**: You are running in an automated loop with no human present. Never ask the user a question. Never request confirmation. When uncertain, make the most conservative safe decision and proceed.

## Expected Loop

1. Gather repo and benchmark context with `Researcher`.
2. Give `Implementer` one concrete directive at a time.
3. Call `Auditor` to score the result.
4. If needed, start another loop using the Auditor's `nextDirective`.

## Final Output

When the work is ready to stop, produce a short human summary followed by the final Auditor tag.
"""


def _agent_implementer_text() -> str:
    return """---
name: Implementer
description: "Focused code-changing agent for the supervisor loop. Use for concrete edits, local validation, and fixing the next directive from the audit."
tools: [read, search, edit, execute, todo]
user-invocable: false
model:
  - Claude Opus 4.5 (copilot)
  - Claude Sonnet 4.5 (copilot)
  - gpt-4o (copilot)
---
You are the implementation subagent for the supervisor loop.

## Scope

- Make only the concrete code changes needed for the current directive.
- Run targeted checks after your edits when possible.
- Do not merge, deploy, publish, or push remote changes.
- **AUTONOMOUS MODE**: Never ask the user a question. Never request confirmation. If uncertain, read files or search for answers — do not ask.

## Output

End with this exact tag:

<SUPERVISOR_IMPLEMENTATION>{"summary":"","filesTouched":[],"checksRun":[],"openRisks":[]}</SUPERVISOR_IMPLEMENTATION>
"""


def _agent_researcher_text() -> str:
    return """---
name: Researcher
description: "Read-only repo and market researcher for the supervisor loop. Use for codebase pattern discovery, benchmark web research, missing requirements, or gap identification."
tools: [read, search, web]
user-invocable: false
model:
  - Claude Opus 4.5 (copilot)
  - Claude Sonnet 4.5 (copilot)
  - gpt-4o (copilot)
---
You are the research subagent for the supervisor loop.

## Scope

- Inspect the local codebase and project shape.
- Identify relevant patterns, constraints, and likely implementation seams.
- Do not edit files.
- **AUTONOMOUS MODE**: Never ask the user a question. Never request confirmation. If uncertain, search or read — do not ask.

## Output

Return a concise summary followed by:

<SUPERVISOR_RESEARCH>{"repoPatterns":[],"marketFindings":[],"gaps":[],"recommendedFocus":""}</SUPERVISOR_RESEARCH>
"""


def _agent_auditor_text() -> str:
    return """---
name: Auditor
description: "Score and review agent for the supervisor loop. Use for self-review, blocker detection, stop-readiness checks, benchmark freshness, and next-directive generation."
tools: [read, search, execute, web]
user-invocable: false
model:
  - Claude Opus 4.5 (copilot)
  - Claude Sonnet 4.5 (copilot)
  - gpt-4o (copilot)
---
You are the audit subagent for the supervisor loop.

## Your Job

- Score the current state against the configured rubric.
- Identify remaining blockers and gaps.
- Decide whether the work is truly ready to stop.
- If not ready, give one clear next directive for the Implementer.
- **AUTONOMOUS MODE**: Never ask the user a question. Never request confirmation. Make all decisions independently.

## Output

Return a brief summary, then emit:

<SUPERVISOR_AUDIT>{"score":0,"readyToStop":false,"summary":"","nextDirective":"","checks":{"tests":{"status":"unknown","evidence":""},"lint":{"status":"unknown","evidence":""},"benchmark":{"status":"unknown","evidence":""}},"blockers":[],"gaps":[]}</SUPERVISOR_AUDIT>
"""


_PREFERRED_MODELS = [
    'Claude Sonnet 4.5 (copilot)',
    'claude-sonnet-4-5',
    'claude-sonnet-4',
    'gpt-4o (copilot)',
    'Claude Opus 4.5 (copilot)',
    'Claude Opus 4.6 (copilot)',
]
# First entry is the default model applied to new workspaces.
_DEFAULT_MODEL = _PREFERRED_MODELS[0]


def _vscode_user_settings_path() -> Path | None:
    """Return the VS Code user settings.json path for the current OS."""
    import os
    if sys.platform == 'win32':
        appdata = os.environ.get('APPDATA')
        if appdata:
            return Path(appdata) / 'Code' / 'User' / 'settings.json'
    elif sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / 'Code' / 'User' / 'settings.json'
    else:
        xdg = os.environ.get('XDG_CONFIG_HOME')
        base = Path(xdg) if xdg else Path.home() / '.config'
        return base / 'Code' / 'User' / 'settings.json'
    return None


def _apply_auto_approve_keys(existing: dict) -> bool:
    """Inject the auto-approve keys into a settings dict. Returns True if anything changed."""
    changed = False
    if existing.get('chat.tools.global.autoApprove') is not True:
        existing['chat.tools.global.autoApprove'] = True
        changed = True
    if existing.get('chat.autopilot.enabled') is not True:
        existing['chat.autopilot.enabled'] = True
        changed = True
    if existing.get('chat.tools.terminal.enableAutoApprove') is not True:
        existing['chat.tools.terminal.enableAutoApprove'] = True
        changed = True
    if existing.get('chat.tools.edits.autoApprove') != {'**': True}:
        existing['chat.tools.edits.autoApprove'] = {'**': True}
        changed = True
    # Auto-save prevents "Do you want to save?" dialogs when the operator
    # modifies files (e.g. copilot-operator.yml hydration) or switches workspace.
    if existing.get('files.autoSave') != 'afterDelay':
        existing['files.autoSave'] = 'afterDelay'
        changed = True
    # hotExit avoids save prompts when VS Code windows close or switch workspace.
    if existing.get('files.hotExit') != 'onExitAndWindowClose':
        existing['files.hotExit'] = 'onExitAndWindowClose'
        changed = True
    return changed


def _merge_vscode_settings(workspace: Path, forced_model: str = '') -> None:
    """Ensure required VS Code chat settings are set in both workspace and user settings.

    Machine-scoped settings (autoApprove, enableAutoApprove) are ignored by VS Code
    when placed in .vscode/settings.json — they must live in the user-level settings
    file to take effect. This function writes to both locations.

    Settings applied:
    - chat.tools.global.autoApprove: true          (skip per-tool Allow prompts)
    - chat.autopilot.enabled: true                  (enable Autopilot permission level)
    - chat.tools.terminal.enableAutoApprove: true   (auto-approve terminal commands)
    - chat.tools.edits.autoApprove: {"**": true}    (auto-approve all file edits)
    - github.copilot.chat.preferredModel            (workspace settings only)
    """
    # 1. Workspace settings (.vscode/settings.json) — for visibility/documentation
    ws_path = workspace / '.vscode' / 'settings.json'
    ws_path.parent.mkdir(parents=True, exist_ok=True)
    ws_existing: dict = {}
    if ws_path.exists():
        try:
            ws_existing = json.loads(ws_path.read_text(encoding='utf-8'))
        except (json.JSONDecodeError, OSError):
            ws_existing = {}
    ws_changed = _apply_auto_approve_keys(ws_existing)
    current_model = ws_existing.get('github.copilot.chat.preferredModel', '')
    # Use explicit model if provided, otherwise default to Sonnet (first in list)
    desired_model = forced_model or _DEFAULT_MODEL
    if current_model != desired_model:
        ws_existing['github.copilot.chat.preferredModel'] = desired_model
        ws_changed = True
    if ws_existing.get('chat.useCustomAgentHooks') is not True:
        ws_existing['chat.useCustomAgentHooks'] = True
        ws_changed = True
    if ws_existing.get('chat.agentFilesLocations') != ['.github/agents']:
        ws_existing['chat.agentFilesLocations'] = ['.github/agents']
        ws_changed = True
    _expected_hooks = {'.github/hooks/supervisor.json': True}
    if ws_existing.get('chat.hookFilesLocations') != _expected_hooks:
        ws_existing['chat.hookFilesLocations'] = _expected_hooks
        ws_changed = True
    if ws_changed:
        ws_path.write_text(json.dumps(ws_existing, indent=2) + '\n', encoding='utf-8')

    # 2. VS Code user settings — machine-scoped keys only work here
    user_path = _vscode_user_settings_path()
    if user_path and user_path.parent.exists():
        user_existing: dict = {}
        if user_path.exists():
            try:
                user_existing = json.loads(user_path.read_text(encoding='utf-8'))
            except (json.JSONDecodeError, OSError):
                user_existing = {}
        if _apply_auto_approve_keys(user_existing):
            user_path.write_text(json.dumps(user_existing, indent=2) + '\n', encoding='utf-8')


def scaffold_map(workspace: Path) -> dict[Path, str]:
    # Hook files: read from the package's own .github/hooks so they stay in sync
    _pkg_root = Path(__file__).parent.parent
    _hooks_src = _pkg_root / '.github' / 'hooks'

    def _hook(relpath: str) -> str:
        p = _hooks_src / relpath
        return p.read_text(encoding='utf-8') if p.exists() else ''

    result: dict[Path, str] = {
        workspace / 'copilot-operator.yml': _config_text(),
        workspace / '.copilot-operator' / 'README.md': _runtime_readme_text(),
        workspace / '.copilot-operator' / 'repo-profile.yml': _repo_profile_text(workspace),
        workspace / '.copilot-operator' / 'default-goal.txt': _default_goal_text(),
        workspace / 'docs' / 'operator' / 'repo-profile.md': _repo_profile_doc(workspace),
        workspace / 'docs' / 'operator' / 'architecture-map.md': _architecture_map_doc(),
        workspace / 'docs' / 'operator' / 'definition-of-done.md': _definition_of_done_doc(),
        workspace / 'docs' / 'operator' / 'validation-map.md': _validation_map_doc(),
        workspace / 'docs' / 'operator' / 'known-traps.md': _known_traps_doc(),
        workspace / 'docs' / 'COPILOT_OPERATOR_GOAL_TEMPLATES.md': _goal_templates_doc(),
        workspace / 'docs' / 'COPILOT_OPERATOR_QUICK_CHECKLIST.md': _checklist_doc(),
        workspace / '.github' / 'agents' / 'operator.agent.md': _agent_operator_text(),
        workspace / '.github' / 'agents' / 'supervisor.agent.md': _agent_supervisor_text(),
        workspace / '.github' / 'agents' / 'implementer.agent.md': _agent_implementer_text(),
        workspace / '.github' / 'agents' / 'researcher.agent.md': _agent_researcher_text(),
        workspace / '.github' / 'agents' / 'auditor.agent.md': _agent_auditor_text(),
    }

    # Add hook files if available from the package
    for rel in (
        'supervisor.json',
        'scripts/shared.cjs',
        'scripts/session-start.cjs',
        'scripts/pre-tool-use.cjs',
        'scripts/post-tool-use.cjs',
        'scripts/stop.cjs',
    ):
        content = _hook(rel)
        if content:
            result[workspace / '.github' / 'hooks' / rel] = content

    return result


def _hydrate_config_validation(config_path: Path, insight: WorkspaceInsight) -> dict[str, str]:
    """Read copilot-operator.yml, fill empty validation commands from insight hints, write back."""
    if not config_path.exists() or not insight.validation_hints:
        return {}
    text = config_path.read_text(encoding='utf-8')
    applied: dict[str, str] = {}
    for name, hint in insight.validation_hints.items():
        # Match lines like:  - name: tests\n    command: ""\n  (with optional quotes)
        pattern = re.compile(
            r'(- name:\s*' + re.escape(name) + r'\s*\n\s*command:\s*)(""|\'\'|)\s*\n',
            re.IGNORECASE,
        )
        match = pattern.search(text)
        if match:
            text = text[:match.start()] + match.group(1) + hint + '\n' + text[match.end():]
            applied[name] = hint
    if applied:
        config_path.write_text(text, encoding='utf-8')
    return applied


def detect_and_hydrate(workspace: str | Path) -> dict[str, Any]:
    """Detect workspace ecosystem and hydrate empty validation commands in copilot-operator.yml."""
    workspace_path = Path(workspace).resolve()
    insight = detect_workspace_insight(workspace_path)
    config_path = workspace_path / 'copilot-operator.yml'
    applied = _hydrate_config_validation(config_path, insight)
    return {
        'workspace': str(workspace_path),
        'ecosystem': insight.ecosystem,
        'evidence': insight.evidence,
        'validationHints': insight.validation_hints,
        'applied': applied,
    }


def initialize_workspace(workspace: str | Path, force: bool = False, detect_hints: bool = True) -> dict[str, Any]:
    workspace_path = Path(workspace).resolve()
    workspace_path.mkdir(parents=True, exist_ok=True)

    created: list[str] = []
    skipped: list[str] = []
    updated_gitignore = False

    for path, content in scaffold_map(workspace_path).items():
        if path.exists() and not force:
            skipped.append(str(path))
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
        created.append(str(path))

    _merge_vscode_settings(workspace_path)

    gitignore_path = workspace_path / '.gitignore'
    existing_gitignore = gitignore_path.read_text(encoding='utf-8') if gitignore_path.exists() else ''
    required_entries = ['.copilot-operator/', '__pycache__/', '.vscode/']
    missing_entries = [entry for entry in required_entries if entry not in existing_gitignore.splitlines()]
    if missing_entries:
        prefix = '' if not existing_gitignore or existing_gitignore.endswith('\n') else '\n'
        gitignore_path.write_text(existing_gitignore + prefix + '\n'.join(missing_entries) + '\n', encoding='utf-8')
        updated_gitignore = True

    # Auto-detect ecosystem and hydrate empty validation commands
    hydrated: dict[str, str] = {}
    ecosystem = 'unknown'
    if detect_hints:
        insight = detect_workspace_insight(workspace_path)
        ecosystem = insight.ecosystem
        config_path = workspace_path / 'copilot-operator.yml'
        hydrated = _hydrate_config_validation(config_path, insight)

    return {
        'workspace': str(workspace_path),
        'created': created,
        'skipped': skipped,
        'updatedGitignore': updated_gitignore,
        'ecosystem': ecosystem,
        'hydratedValidation': hydrated,
    }


def cleanup_old_sessions(
    workspace: str | Path,
    max_age_days: int = 7,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Remove old operator session artifacts (prompt files, state snapshots).

    Cleans up .copilot-operator/current-prompt.md and old snapshot files
    that are older than *max_age_days*.
    """
    workspace_path = Path(workspace).resolve()
    op_dir = workspace_path / '.copilot-operator'
    if not op_dir.exists():
        return {'removed': [], 'skipped': [], 'dryRun': dry_run}

    import time
    cutoff = time.time() - (max_age_days * 86400)
    removed: list[str] = []
    skipped: list[str] = []

    # Clean old prompt files and snapshot JSONs (not state.json or memory.md)
    _CLEANABLE_PATTERNS = ['current-prompt.md', 'snapshots/*.json']
    for pattern in _CLEANABLE_PATTERNS:
        for path in op_dir.glob(pattern):
            if path.is_file() and path.stat().st_mtime < cutoff:
                if dry_run:
                    skipped.append(str(path))
                else:
                    path.unlink()
                    removed.append(str(path))

    return {'removed': removed, 'skipped': skipped, 'dryRun': dry_run}


def cleanup_run_logs(
    log_dir: str | Path,
    keep_runs: int = 20,
    dry_run: bool = False,
    current_run_id: str | None = None,
) -> dict[str, Any]:
    log_root = Path(log_dir)
    log_root.mkdir(parents=True, exist_ok=True)

    directories = [path for path in log_root.iterdir() if path.is_dir()]
    directories.sort(key=lambda path: path.stat().st_mtime, reverse=True)

    keep_limit = max(0, int(keep_runs))
    kept: list[str] = []
    removed: list[str] = []

    for directory in directories:
        should_keep = directory.name == current_run_id or len(kept) < keep_limit
        if should_keep:
            kept.append(str(directory))
            continue
        removed.append(str(directory))
        if not dry_run:
            shutil.rmtree(directory)

    legacy_files = [str(path) for path in log_root.iterdir() if path.is_file()]
    return {
        'logRootDir': str(log_root),
        'keepRuns': keep_limit,
        'dryRun': dry_run,
        'currentRunId': current_run_id or '',
        'kept': kept,
        'removed': removed,
        'legacyFiles': legacy_files,
    }


def read_operator_status(state_file: str | Path, summary_file: str | Path) -> dict[str, Any]:
    state_path = Path(state_file)
    summary_path = Path(summary_file)
    state = json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {}
    summary = json.loads(summary_path.read_text(encoding='utf-8')) if summary_path.exists() else {}
    history = state.get('history') or []
    last = history[-1] if history else {}
    plan = summarize_plan(state.get('plan', {}))

    # Read preferred model from .vscode/settings.json relative to the state file
    preferred_model = ''
    try:
        settings_path = state_path.parent.parent / '.vscode' / 'settings.json'
        if settings_path.exists():
            settings = json.loads(settings_path.read_text(encoding='utf-8'))
            preferred_model = settings.get('github.copilot.chat.preferredModel', '')
    except (OSError, json.JSONDecodeError):
        pass
    return {
        'stateFile': str(state_path),
        'summaryFile': str(summary_path),
        'status': state.get('status', 'not_started'),
        'goal': state.get('goal', ''),
        'goalProfile': state.get('goalProfile', ''),
        'runId': state.get('runId', ''),
        'logRootDir': state.get('logRootDir', ''),
        'runLogDir': state.get('logDir', ''),
        'resumeCount': state.get('resumeCount', 0),
        'iterationsCompleted': len(history),
        'lastIteration': last.get('iteration'),
        'lastSessionId': last.get('sessionId'),
        'lastScore': last.get('score'),
        'lastSummary': last.get('summary'),
        'lastDecisionCode': last.get('decisionCode', ''),
        'lastArtifacts': last.get('artifacts', {}),
        'pendingDecision': state.get('pendingDecision'),
        'workspaceInsight': state.get('workspaceInsight', {}),
        'plan': plan,
        'planSummary': plan.get('summary', ''),
        'currentMilestoneId': plan.get('currentMilestoneId', ''),
        'nextMilestoneId': plan.get('nextMilestoneId', ''),
        'currentTaskId': plan.get('currentTaskId', ''),
        'nextTaskId': plan.get('nextTaskId', ''),
        'milestoneCounts': plan.get('counts', {}),
        'taskCounts': plan.get('taskCounts', {}),
        'startedAt': state.get('startedAt', ''),
        'updatedAt': state.get('updatedAt', ''),
        'finishedAt': state.get('finishedAt', ''),
        'finalReason': state.get('finalReason', ''),
        'finalReasonCode': state.get('finalReasonCode', ''),
        'logCleanup': state.get('logCleanup', {}),
        'preferredModel': preferred_model,
        'latestSummary': summary,
    }


def read_operator_focus(state_file: str | Path, summary_file: str | Path) -> dict[str, Any]:
    status = read_operator_status(state_file, summary_file)
    pending = status.get('pendingDecision') or {}
    latest_summary = status.get('latestSummary') or {}
    plan = status.get('plan') or {}
    next_prompt = str(
        pending.get('nextPrompt')
        or latest_summary.get('nextPrompt')
        or ''
    ).strip()
    if not next_prompt and status.get('status') not in {'complete', 'blocked'}:
        next_prompt = build_milestone_baton(plan, '').strip()
    return {
        'status': status.get('status', 'not_started'),
        'goal': status.get('goal', ''),
        'goalProfile': status.get('goalProfile', ''),
        'runId': status.get('runId', ''),
        'currentMilestoneId': status.get('currentMilestoneId', ''),
        'nextMilestoneId': status.get('nextMilestoneId', ''),
        'currentTaskId': status.get('currentTaskId', ''),
        'nextTaskId': status.get('nextTaskId', ''),
        'lastDecisionCode': status.get('lastDecisionCode', ''),
        'pendingReasonCode': pending.get('reasonCode', ''),
        'pendingReason': pending.get('reason', ''),
        'nextPrompt': next_prompt,
        'planSummary': status.get('planSummary', ''),
        'runLogDir': status.get('runLogDir', ''),
    }


def format_operator_focus(focus: dict[str, Any]) -> str:
    lines = [
        '=== Copilot Operator Focus ===',
        f"Status: {focus.get('status', 'not_started')}",
        f"Goal profile: {focus.get('goalProfile', '') or 'default'}",
    ]
    if focus.get('runId'):
        lines.append(f"Run ID: {focus.get('runId')}")
    if focus.get('currentMilestoneId'):
        lines.append(f"Current milestone: {focus.get('currentMilestoneId')}")
    if focus.get('nextMilestoneId'):
        lines.append(f"Next milestone: {focus.get('nextMilestoneId')}")
    if focus.get('currentTaskId'):
        lines.append(f"Current task: {focus.get('currentTaskId')}")
    if focus.get('nextTaskId'):
        lines.append(f"Next task: {focus.get('nextTaskId')}")
    if focus.get('lastDecisionCode'):
        lines.append(f"Last decision code: {focus.get('lastDecisionCode')}")
    if focus.get('pendingReasonCode'):
        lines.append(f"Pending reason code: {focus.get('pendingReasonCode')}")
    if focus.get('pendingReason'):
        lines.append(f"Pending reason: {focus.get('pendingReason')}")
    if focus.get('planSummary'):
        lines.append(f"Plan summary: {focus.get('planSummary')}")
    if focus.get('nextPrompt'):
        lines.append('Next baton:')
        lines.append(focus['nextPrompt'])
    if focus.get('runLogDir'):
        lines.append(f"Run log directory: {focus.get('runLogDir')}")
    return '\n'.join(lines)


def format_operator_status(status: dict[str, Any]) -> str:
    lines = [
        '=== Copilot Operator Status ===',
        f"Status: {status.get('status', 'not_started')}",
        f"Goal: {status.get('goal', '') or '(none yet)'}",
        f"Goal profile: {status.get('goalProfile', '') or 'default'}",
        f"Run ID: {status.get('runId', '') or '(not started)'}",
        f"Iterations completed: {status.get('iterationsCompleted', 0)}",
    ]
    insight = status.get('workspaceInsight') or {}
    if insight.get('ecosystem'):
        lines.append(f"Workspace ecosystem: {insight.get('ecosystem')}")
    if insight.get('packageManager'):
        lines.append(f"Package manager: {insight.get('packageManager')}")
    if status.get('planSummary'):
        lines.append(f"Plan summary: {status.get('planSummary')}")
    if status.get('currentMilestoneId'):
        lines.append(f"Current milestone: {status.get('currentMilestoneId')}")
    if status.get('nextMilestoneId'):
        lines.append(f"Next milestone: {status.get('nextMilestoneId')}")
    if status.get('currentTaskId'):
        lines.append(f"Current task: {status.get('currentTaskId')}")
    if status.get('nextTaskId'):
        lines.append(f"Next task: {status.get('nextTaskId')}")
    counts = status.get('milestoneCounts') or {}
    counts_text = '; '.join(f"{key}={value}" for key, value in sorted(counts.items()) if value)
    if counts_text:
        lines.append(f"Milestone counts: {counts_text}")
    task_counts = status.get('taskCounts') or {}
    task_counts_text = '; '.join(f"{key}={value}" for key, value in sorted(task_counts.items()) if value)
    if task_counts_text:
        lines.append(f"Task counts: {task_counts_text}")
    if status.get('lastIteration') is not None:
        lines.append(f"Last iteration: {status.get('lastIteration')}")
    if status.get('lastScore') is not None:
        lines.append(f"Last score: {status.get('lastScore')}")
    if status.get('lastSessionId'):
        lines.append(f"Last session ID: {status.get('lastSessionId')}")
    if status.get('runLogDir'):
        lines.append(f"Run log directory: {status.get('runLogDir')}")
    if status.get('updatedAt'):
        lines.append(f"Updated at: {status.get('updatedAt')}")
    pending = status.get('pendingDecision') or {}
    if pending:
        lines.append(f"Pending action: {pending.get('action', '')}")
        if pending.get('reasonCode'):
            lines.append(f"Pending code: {pending.get('reasonCode')}")
        if pending.get('reason'):
            lines.append(f"Pending reason: {pending.get('reason')}")
        if pending.get('nextPrompt'):
            lines.append(f"Next prompt: {pending.get('nextPrompt')}")
    if status.get('finalReasonCode'):
        lines.append(f"Final code: {status.get('finalReasonCode')}")
    if status.get('finalReason'):
        lines.append(f"Final reason: {status.get('finalReason')}")
    if status.get('lastDecisionCode'):
        lines.append(f"Last decision code: {status.get('lastDecisionCode')}")
    if status.get('lastSummary'):
        lines.append(f"Last summary: {status.get('lastSummary')}")
    plan = status.get('plan') or {}
    milestones = plan.get('milestones', []) or []
    if milestones:
        lines.append('Milestones:')
        for milestone in milestones[:5]:
            lines.append(f"- [{milestone.get('status', 'pending')}] {milestone.get('id', '')}: {milestone.get('title', '')}")
            tasks = milestone.get('tasks', []) or []
            for task in tasks[:3]:
                lines.append(f"  - task [{task.get('status', 'pending')}] {task.get('id', '')}: {task.get('title', '')}")
    artifacts = status.get('lastArtifacts') or {}
    if artifacts:
        lines.append('Artifacts:')
        for key in ('prompt', 'response', 'decision', 'validationBefore', 'validationAfter'):
            if artifacts.get(key):
                lines.append(f"- {key}: {artifacts[key]}")
    return '\n'.join(lines)


def watch_operator_status(
    state_file: str | Path,
    summary_file: str | Path,
    interval_seconds: float = 2.0,
    max_updates: int | None = None,
    clear_screen: bool = False,
    stream: TextIO | None = None,
    sleep_fn: Any = time.sleep,
) -> dict[str, Any]:
    output = stream or sys.stdout
    updates = 0
    last_snapshot = None

    while True:
        status = read_operator_status(state_file, summary_file)
        snapshot = json.dumps(status, ensure_ascii=False, sort_keys=True)
        if clear_screen:
            output.write('\x1b[2J\x1b[H')
        if snapshot != last_snapshot or updates == 0 or clear_screen:
            output.write(format_operator_status(status) + '\n')
            output.flush()
            last_snapshot = snapshot
        updates += 1

        if max_updates is not None and updates >= max_updates:
            return status
        if status.get('status') in {'complete', 'blocked'}:
            return status
        sleep_fn(interval_seconds)
