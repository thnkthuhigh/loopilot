"""Repo operations — git branch, commit, and PR automation.

Provides safe, auditable git operations that the operator can invoke between
iterations or at the end of a run.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class GitStatus:
    """Snapshot of the current git state."""
    branch: str = ''
    is_clean: bool = True
    staged_files: list[str] = field(default_factory=list)
    unstaged_files: list[str] = field(default_factory=list)
    untracked_files: list[str] = field(default_factory=list)
    ahead: int = 0
    behind: int = 0
    has_remote: bool = False


@dataclass(slots=True)
class GitResult:
    """Result of a git operation."""
    success: bool = False
    command: str = ''
    stdout: str = ''
    stderr: str = ''
    returncode: int = -1


# ---------------------------------------------------------------------------
# Low-level git helpers
# ---------------------------------------------------------------------------

def _run_git(args: list[str], cwd: Path, timeout: int = 60) -> GitResult:
    """Run a git command and return structured result."""
    command = ['git'] + args
    try:
        result = subprocess.run(
            command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return GitResult(
            success=result.returncode == 0,
            command=' '.join(command),
            stdout=result.stdout.strip(),
            stderr=result.stderr.strip(),
            returncode=result.returncode,
        )
    except subprocess.TimeoutExpired:
        return GitResult(
            success=False,
            command=' '.join(command),
            stderr='Git command timed out.',
            returncode=-1,
        )
    except FileNotFoundError:
        return GitResult(
            success=False,
            command=' '.join(command),
            stderr='git not found on PATH.',
            returncode=-1,
        )


def is_git_repo(workspace: Path) -> bool:
    """Check if the workspace is inside a git repository."""
    return _run_git(['rev-parse', '--is-inside-work-tree'], workspace).success


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

def get_git_status(workspace: Path) -> GitStatus:
    """Get the current git status of the workspace."""
    if not is_git_repo(workspace):
        return GitStatus()

    branch_result = _run_git(['branch', '--show-current'], workspace)
    branch = branch_result.stdout.strip() if branch_result.success else ''

    status_result = _run_git(['status', '--porcelain=v1'], workspace)
    staged: list[str] = []
    unstaged: list[str] = []
    untracked: list[str] = []
    if status_result.success:
        for line in status_result.stdout.splitlines():
            if len(line) < 4:
                continue
            index_status = line[0]
            work_status = line[1]
            filepath = line[3:].strip()
            if index_status == '?':
                untracked.append(filepath)
            elif index_status != ' ':
                staged.append(filepath)
            if work_status not in (' ', '?'):
                unstaged.append(filepath)

    # Check ahead/behind
    ahead = 0
    behind = 0
    has_remote = False
    remote_result = _run_git(['rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}'], workspace)
    if remote_result.success:
        has_remote = True
        count_result = _run_git(['rev-list', '--count', '--left-right', '@{u}...HEAD'], workspace)
        if count_result.success:
            parts = count_result.stdout.strip().split('\t')
            if len(parts) == 2:
                behind = int(parts[0])
                ahead = int(parts[1])

    return GitStatus(
        branch=branch,
        is_clean=not staged and not unstaged and not untracked,
        staged_files=staged,
        unstaged_files=unstaged,
        untracked_files=untracked,
        ahead=ahead,
        behind=behind,
        has_remote=has_remote,
    )


# ---------------------------------------------------------------------------
# Branch operations
# ---------------------------------------------------------------------------

def create_branch(workspace: Path, branch_name: str, from_branch: str = '') -> GitResult:
    """Create and checkout a new branch."""
    _sanitise_branch_name(branch_name)
    args = ['checkout', '-b', branch_name]
    if from_branch:
        args.append(from_branch)
    return _run_git(args, workspace)


def checkout_branch(workspace: Path, branch_name: str) -> GitResult:
    """Checkout an existing branch."""
    return _run_git(['checkout', branch_name], workspace)


def _sanitise_branch_name(name: str) -> str:
    """Validate a branch name for safety."""
    if not name or not re.match(r'^[a-zA-Z0-9_/.-]+$', name):
        raise ValueError(f'Invalid branch name: {name!r}')
    if '..' in name or name.startswith('-') or name.endswith('.lock'):
        raise ValueError(f'Unsafe branch name: {name!r}')
    return name


# ---------------------------------------------------------------------------
# Commit operations
# ---------------------------------------------------------------------------

def stage_all(workspace: Path) -> GitResult:
    """Stage all changes (new, modified, deleted)."""
    return _run_git(['add', '-A'], workspace)


def stage_files(workspace: Path, files: list[str]) -> GitResult:
    """Stage specific files."""
    if not files:
        return GitResult(success=True, command='git add (no files)')
    return _run_git(['add', '--'] + files, workspace)


def commit(workspace: Path, message: str, allow_empty: bool = False) -> GitResult:
    """Create a commit with the given message."""
    if not message.strip():
        return GitResult(success=False, stderr='Commit message cannot be empty.')
    args = ['commit', '-m', message]
    if allow_empty:
        args.append('--allow-empty')
    return _run_git(args, workspace)


def create_operator_commit(
    workspace: Path,
    run_id: str,
    iteration: int,
    summary: str,
    score: int | None = None,
) -> GitResult:
    """Create a standardised operator commit."""
    score_text = f' (score={score})' if score is not None else ''
    message = f'operator/{run_id[:8]}: iteration {iteration}{score_text}\n\n{summary}'
    stage_result = stage_all(workspace)
    if not stage_result.success:
        return stage_result
    status = get_git_status(workspace)
    if status.is_clean:
        return GitResult(success=True, command='git commit', stdout='Nothing to commit, working tree clean.')
    return commit(workspace, message)


# ---------------------------------------------------------------------------
# Diff inspection
# ---------------------------------------------------------------------------

def get_diff_summary(workspace: Path, staged: bool = False) -> str:
    """Get a compact summary of changes."""
    args = ['diff', '--stat']
    if staged:
        args.append('--staged')
    result = _run_git(args, workspace)
    return result.stdout if result.success else ''


def get_diff_files(workspace: Path, staged: bool = False) -> list[str]:
    """Get list of changed file paths."""
    args = ['diff', '--name-only']
    if staged:
        args.append('--staged')
    result = _run_git(args, workspace)
    if result.success:
        return [f.strip() for f in result.stdout.splitlines() if f.strip()]
    return []


# ---------------------------------------------------------------------------
# Safety checks
# ---------------------------------------------------------------------------

def pre_run_safety_check(workspace: Path) -> dict[str, Any]:
    """Run safety checks before an operator run starts.

    Returns a dict with 'safe' (bool) and 'warnings' (list of strings).
    """
    warnings: list[str] = []

    if not is_git_repo(workspace):
        return {'safe': True, 'warnings': ['Workspace is not a git repository.']}

    status = get_git_status(workspace)

    if status.unstaged_files:
        warnings.append(
            f'{len(status.unstaged_files)} unstaged files. '
            'Consider committing or stashing before the operator run.'
        )

    if status.behind > 0:
        warnings.append(
            f'Branch {status.branch} is {status.behind} commits behind remote. '
            'Consider pulling first.'
        )

    return {
        'safe': True,  # warnings are advisory, not blocking
        'warnings': warnings,
        'branch': status.branch,
        'isClean': status.is_clean,
    }


def render_git_status(status: GitStatus) -> str:
    """Human-readable git status for operator display."""
    lines = [f'Branch: {status.branch or "(detached)"}']
    if status.is_clean:
        lines.append('Working tree: clean')
    else:
        if status.staged_files:
            lines.append(f'Staged: {len(status.staged_files)} files')
        if status.unstaged_files:
            lines.append(f'Unstaged: {len(status.unstaged_files)} files')
        if status.untracked_files:
            lines.append(f'Untracked: {len(status.untracked_files)} files')
    if status.has_remote:
        if status.ahead or status.behind:
            lines.append(f'Remote: ahead={status.ahead}, behind={status.behind}')
        else:
            lines.append('Remote: up to date')
    return '\n'.join(lines)
