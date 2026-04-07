"""Snapshot + Rollback — git-based safety net for operator iterations.

Before each iteration the operator can take a lightweight snapshot of the
working tree.  If a subsequent iteration makes things worse (score regression,
tests break) the operator can rollback to the last known-good state.

Implementation uses ``git stash`` for zero-overhead snapshots that don't
pollute the commit history.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .logging_config import get_logger

logger = get_logger('snapshot')

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class Snapshot:
    """Record of a single snapshot taken between operator iterations."""
    snapshot_id: str = ''
    iteration: int = 0
    score: int | None = None
    timestamp: str = ''
    stash_ref: str = ''
    reason: str = ''


@dataclass(slots=True)
class SnapshotManager:
    """Manages a stack of snapshots for the current run."""
    snapshots: list[Snapshot] = field(default_factory=list)
    max_snapshots: int = 10
    last_good_score: int | None = None
    last_good_iteration: int = 0


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def _run_git(*args: str, timeout: int = 30) -> tuple[int, str]:
    """Run a git command and return (returncode, stdout)."""
    try:
        result = subprocess.run(
            ['git', *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return result.returncode, result.stdout.strip()
    except subprocess.TimeoutExpired:
        return 1, 'git command timed out'
    except FileNotFoundError:
        return 1, 'git not found'


# ---------------------------------------------------------------------------
# Snapshot operations
# ---------------------------------------------------------------------------

def take_snapshot(
    manager: SnapshotManager,
    iteration: int,
    score: int | None = None,
    reason: str = '',
) -> Snapshot | None:
    """Take a snapshot of the current working tree via ``git stash``.

    Returns the Snapshot or None if no changes to stash.
    """
    msg = f'operator-snapshot-iter-{iteration}'

    # Include untracked files in the stash
    rc, output = _run_git('stash', 'push', '--include-untracked', '-m', msg)

    if rc != 0 or 'No local changes' in output:
        return None

    # Get the stash ref (always stash@{0} right after push)
    stash_ref = 'stash@{0}'

    snap = Snapshot(
        snapshot_id=f'snap-{iteration}',
        iteration=iteration,
        score=score,
        timestamp=datetime.now(timezone.utc).isoformat(),
        stash_ref=stash_ref,
        reason=reason or f'Pre-iteration {iteration} snapshot',
    )
    manager.snapshots.append(snap)

    # Immediately re-apply the stash so the working tree is restored
    # (We want to *keep* the stash AND keep the working tree intact)
    rc, output = _run_git('stash', 'apply', stash_ref)
    if rc != 0:
        logger.warning('git stash apply failed after snapshot: %s', output)

    # Track the best score
    if score is not None and (manager.last_good_score is None or score >= manager.last_good_score):
        manager.last_good_score = score
        manager.last_good_iteration = iteration

    # Prune old snapshots if over limit
    _prune_old_snapshots(manager)

    return snap


def _prune_old_snapshots(manager: SnapshotManager) -> None:
    """Drop the oldest snapshots beyond the configured limit."""
    while len(manager.snapshots) > manager.max_snapshots:
        oldest = manager.snapshots.pop(0)
        # Drop the corresponding git stash entry
        rc, output = _run_git('stash', 'drop', oldest.stash_ref)
        if rc != 0:
            logger.warning('Failed to drop stash %s: %s', oldest.stash_ref, output)


def rollback_to_snapshot(manager: SnapshotManager, snapshot: Snapshot) -> bool:
    """Rollback the working tree to a specific snapshot.

    This does ``git checkout .`` to discard current changes, then applies
    the stash.  Returns True on success.
    """
    # First discard current working-tree changes
    rc, _ = _run_git('checkout', '.')
    if rc != 0:
        return False

    # Also clean untracked files that might conflict
    rc, output = _run_git('clean', '-fd')
    if rc != 0:
        logger.warning('git clean failed during rollback: %s', output)

    # Apply the stash (but keep it in the stash list for potential re-use)
    rc, output = _run_git('stash', 'apply', snapshot.stash_ref)
    if rc != 0:
        logger.error('git stash apply failed during rollback: %s', output)
    return rc == 0


def rollback_to_last_good(manager: SnapshotManager) -> Snapshot | None:
    """Find and rollback to the snapshot with the best recorded score.

    Returns the snapshot on success, None if no good snapshot exists.
    """
    good = find_best_snapshot(manager)
    if good is None:
        return None

    if rollback_to_snapshot(manager, good):
        return good
    return None


def find_best_snapshot(manager: SnapshotManager) -> Snapshot | None:
    """Find the snapshot with the highest score."""
    scored = [s for s in manager.snapshots if s.score is not None]
    if not scored:
        return None
    return max(scored, key=lambda s: s.score or 0)


# ---------------------------------------------------------------------------
# Decision logic — integrate with operator loop
# ---------------------------------------------------------------------------

def should_rollback(
    current_score: int | None,
    previous_score: int | None,
    regression_threshold: int = 10,
) -> bool:
    """Decide whether a rollback is warranted based on score regression."""
    if current_score is None or previous_score is None:
        return False
    return previous_score - current_score >= regression_threshold


def should_snapshot(iteration: int, interval: int = 1) -> bool:
    """Decide whether to take a snapshot before this iteration."""
    return iteration % interval == 0


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def snapshot_summary(manager: SnapshotManager) -> str:
    """Render a human-readable summary of all snapshots."""
    if not manager.snapshots:
        return 'No snapshots taken.'

    lines = [f'Snapshots ({len(manager.snapshots)}):']
    for snap in manager.snapshots:
        score_str = f'score={snap.score}' if snap.score is not None else 'no score'
        lines.append(f'  [{snap.snapshot_id}] iter {snap.iteration} — {score_str} — {snap.reason}')

    if manager.last_good_score is not None:
        lines.append(f'\nBest: iteration {manager.last_good_iteration} (score {manager.last_good_score})')

    return '\n'.join(lines)
