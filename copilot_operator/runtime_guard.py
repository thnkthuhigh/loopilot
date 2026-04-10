"""Runtime guard — session isolation, conflict detection, and state checkpoints.

Addresses the #1 weakness: platform coupling at the session boundary.

Features:
  - Lock file to prevent concurrent operator runs on the same workspace
  - Same-window conflict detection (warn/abort if VS Code window is busy)
  - Per-iteration state checkpoints for smooth resume after errors
  - Context continuity bridge when CLI creates a new chat session
"""

from __future__ import annotations

__all__ = [
    'LockInfo',
    'IterationCheckpoint',
    'acquire_lock',
    'release_lock',
    'read_lock',
    'refresh_lock',
    'detect_window_conflict',
    'save_checkpoint',
    'clear_checkpoint',
]

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .logging_config import get_logger
from .session_store import find_workspace_storage

logger = get_logger('runtime_guard')

# ---------------------------------------------------------------------------
# Lock file — prevent concurrent operator runs on the same workspace
# ---------------------------------------------------------------------------

_LOCK_FILENAME = '.copilot-operator/operator.lock'
_LOCK_STALE_SECONDS = 600  # 10 minutes


@dataclass(slots=True)
class LockInfo:
    pid: int = 0
    run_id: str = ''
    started_at: float = 0.0
    workspace: str = ''


def _lock_path(workspace: Path) -> Path:
    return workspace / _LOCK_FILENAME


def acquire_lock(workspace: Path, run_id: str) -> LockInfo | None:
    """Try to acquire the workspace lock.

    Returns LockInfo on success, or None if the workspace is already locked
    by another live process.
    """
    lock_file = _lock_path(workspace)
    lock_file.parent.mkdir(parents=True, exist_ok=True)

    # Check existing lock
    existing = read_lock(workspace)
    if existing is not None:
        # Same process can re-acquire with a different run_id
        if existing.pid == os.getpid():
            logger.info('Re-acquiring lock from same process (old run: %s)', existing.run_id)
        elif _is_process_alive(existing.pid) and not _is_lock_stale(existing):
            logger.warning(
                'Workspace locked by PID %d (run %s, started %.0fs ago)',
                existing.pid, existing.run_id,
                time.time() - existing.started_at,
            )
            return None
        else:
            # Stale or dead — safe to take over
            logger.info('Taking over stale lock from PID %d', existing.pid)

    info = LockInfo(
        pid=os.getpid(),
        run_id=run_id,
        started_at=time.time(),
        workspace=str(workspace),
    )
    try:
        lock_file.write_text(json.dumps({
            'pid': info.pid,
            'runId': info.run_id,
            'startedAt': info.started_at,
            'workspace': info.workspace,
        }), encoding='utf-8')
    except OSError as exc:
        logger.error('Failed to write lock file: %s', exc)
        return None
    return info


def release_lock(workspace: Path, run_id: str) -> bool:
    """Release the workspace lock if we own it. Returns True on success."""
    lock_file = _lock_path(workspace)
    existing = read_lock(workspace)
    if existing is None:
        return True  # No lock to release
    if existing.run_id != run_id and existing.pid != os.getpid():
        logger.warning('Cannot release lock owned by PID %d (run %s)', existing.pid, existing.run_id)
        return False
    try:
        lock_file.unlink(missing_ok=True)
        return True
    except OSError as exc:
        logger.error('Failed to remove lock file: %s', exc)
        return False


def read_lock(workspace: Path) -> LockInfo | None:
    """Read the current lock info. Returns None if no lock exists."""
    lock_file = _lock_path(workspace)
    if not lock_file.exists():
        return None
    try:
        data = json.loads(lock_file.read_text(encoding='utf-8'))
        return LockInfo(
            pid=int(data.get('pid', 0)),
            run_id=str(data.get('runId', '')),
            started_at=float(data.get('startedAt', 0)),
            workspace=str(data.get('workspace', '')),
        )
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def refresh_lock(workspace: Path, run_id: str) -> None:
    """Update the lock timestamp to prevent stale detection."""
    lock_file = _lock_path(workspace)
    existing = read_lock(workspace)
    if existing is not None and (existing.run_id == run_id or existing.pid == os.getpid()):
        existing.started_at = time.time()
        try:
            lock_file.write_text(json.dumps({
                'pid': existing.pid,
                'runId': existing.run_id,
                'startedAt': existing.started_at,
                'workspace': existing.workspace,
            }), encoding='utf-8')
        except OSError:
            pass


def _is_process_alive(pid: int) -> bool:
    """Check if a process with given PID exists."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError, SystemError, PermissionError):
        return False


def _is_lock_stale(info: LockInfo) -> bool:
    """Check if a lock is older than the stale threshold."""
    return (time.time() - info.started_at) > _LOCK_STALE_SECONDS


# ---------------------------------------------------------------------------
# Same-window conflict detection
# ---------------------------------------------------------------------------

def detect_window_conflict(workspace: Path) -> str | None:
    """Check if VS Code has an active Copilot chat session in the workspace.

    Returns a warning message if a conflict is detected, None otherwise.
    Scans for recently-modified session files (< 60s old) that suggest
    active Copilot interaction.
    """
    from .operator import _CHAT_DIR_CANDIDATES

    storage = find_workspace_storage(workspace)
    if not storage:
        return None

    now = time.time()
    for candidate in _CHAT_DIR_CANDIDATES:
        chat_dir = storage / candidate
        if not chat_dir.exists():
            continue
        for session_file in chat_dir.glob('*.json*'):
            try:
                age = now - session_file.stat().st_mtime
                if age < 60:  # Active session within last 60 seconds
                    return (
                        f'Active Copilot session detected ({session_file.name}, '
                        f'{age:.0f}s old). Another operator or manual chat may be '
                        f'using this workspace. Risk of session collision.'
                    )
            except OSError:
                continue
    return None


# ---------------------------------------------------------------------------
# Per-iteration state checkpoint
# ---------------------------------------------------------------------------

_CHECKPOINT_FILENAME = '.copilot-operator/checkpoint.json'


@dataclass(slots=True)
class IterationCheckpoint:
    """Minimal state needed to resume mid-run after crash."""
    run_id: str = ''
    iteration: int = 0
    goal: str = ''
    goal_profile: str = 'default'
    status: str = 'running'
    last_decision_action: str = ''
    last_decision_reason_code: str = ''
    last_decision_next_prompt: str = ''
    score: int | None = None
    timestamp: float = 0.0
    all_changed_files: list[str] = field(default_factory=list)


def save_checkpoint(workspace: Path, checkpoint: IterationCheckpoint) -> None:
    """Save an iteration checkpoint for crash recovery."""
    path = workspace / _CHECKPOINT_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(json.dumps({
            'runId': checkpoint.run_id,
            'iteration': checkpoint.iteration,
            'goal': checkpoint.goal,
            'goalProfile': checkpoint.goal_profile,
            'status': checkpoint.status,
            'lastDecision': {
                'action': checkpoint.last_decision_action,
                'reasonCode': checkpoint.last_decision_reason_code,
                'nextPrompt': checkpoint.last_decision_next_prompt,
            },
            'score': checkpoint.score,
            'timestamp': checkpoint.timestamp,
            'allChangedFiles': checkpoint.all_changed_files,
        }), encoding='utf-8')
    except OSError as exc:
        logger.debug('Failed to write checkpoint: %s', exc)


def load_checkpoint(workspace: Path) -> IterationCheckpoint | None:
    """Load the most recent checkpoint. Returns None if not found."""
    path = workspace / _CHECKPOINT_FILENAME
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        decision = data.get('lastDecision', {})
        return IterationCheckpoint(
            run_id=str(data.get('runId', '')),
            iteration=int(data.get('iteration', 0)),
            goal=str(data.get('goal', '')),
            goal_profile=str(data.get('goalProfile', 'default')),
            status=str(data.get('status', 'running')),
            last_decision_action=str(decision.get('action', '')),
            last_decision_reason_code=str(decision.get('reasonCode', '')),
            last_decision_next_prompt=str(decision.get('nextPrompt', '')),
            score=data.get('score'),
            timestamp=float(data.get('timestamp', 0)),
            all_changed_files=list(data.get('allChangedFiles', [])),
        )
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def clear_checkpoint(workspace: Path) -> None:
    """Remove the checkpoint file after a clean finish."""
    path = workspace / _CHECKPOINT_FILENAME
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Context continuity bridge
# ---------------------------------------------------------------------------

def build_continuity_context(
    previous_iterations: list[dict[str, Any]],
    all_changed_files: list[str],
    current_score: int | None,
    worker_summary: str,
) -> str:
    """Build a context summary that bridges across chat sessions.

    When VS Code CLI creates a new chat session per invocation, this
    function generates a compact context block that gets injected into
    the new session to maintain continuity.
    """
    if not previous_iterations:
        return ''

    lines = [
        '## Session Continuity Context',
        '',
        f'This is a continuation. {len(previous_iterations)} prior iterations completed.',
    ]

    if current_score is not None:
        lines.append(f'Current score: {current_score}')

    # Score trajectory
    scores = [
        h.get('score') for h in previous_iterations
        if h.get('score') is not None
    ]
    if scores:
        trajectory = ' → '.join(str(s) for s in scores[-5:])
        lines.append(f'Score trajectory: {trajectory}')

    # Recent summaries (last 3)
    for entry in previous_iterations[-3:]:
        iter_num = entry.get('iteration', '?')
        summary = str(entry.get('summary', ''))[:150]
        decision = entry.get('decisionCode', '')
        lines.append(f'- Iter {iter_num} ({decision}): {summary}')

    # Changed files
    if all_changed_files:
        lines.append('')
        lines.append(f'Files changed so far ({len(all_changed_files)}):')
        for f in all_changed_files[:15]:
            lines.append(f'  - {f}')
        if len(all_changed_files) > 15:
            lines.append(f'  ... and {len(all_changed_files) - 15} more')

    # Worker summary
    if worker_summary:
        lines.append('')
        lines.append(worker_summary)

    return '\n'.join(lines)
