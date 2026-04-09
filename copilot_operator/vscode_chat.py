from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Iterable

from .config import OperatorConfig
from .session_store import find_workspace_storage, get_latest_request, load_chat_session, request_completed


class VSCodeChatError(RuntimeError):
    pass


class VSCodeChatRetryableError(VSCodeChatError):
    pass


def _code_binary() -> str:
    for candidate in ('code.cmd', 'code'):
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    raise VSCodeChatError('Unable to find code.cmd on PATH.')


def run_code_command(
    args: list[str],
    cwd: Path,
    timeout_seconds: int = 120,
    retries: int = 0,
    retry_delay_seconds: float = 2.0,
) -> subprocess.CompletedProcess[str]:
    command = [_code_binary(), *args]
    attempts = retries + 1
    last_error: str | None = None
    for attempt in range(1, attempts + 1):
        result = subprocess.run(
            command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        if result.returncode == 0:
            return result
        last_error = result.stderr.strip() or result.stdout.strip() or 'VS Code command failed.'
        if attempt < attempts:
            time.sleep(retry_delay_seconds)
    raise VSCodeChatRetryableError(last_error or 'VS Code command failed.')


def focus_workspace(workspace: Path, config: OperatorConfig | None = None, *, new_window: bool = False) -> None:
    retries = config.code_command_retries if config else 0
    delay = config.code_command_retry_delay_seconds if config else 2.0
    flag = '--new-window' if new_window else '--reuse-window'
    run_code_command([flag, str(workspace)], cwd=workspace, timeout_seconds=60, retries=retries, retry_delay_seconds=delay)


# Cache so we only open VS Code once per process
_workspace_storage_cache: dict[str, Path] = {}


def ensure_workspace_storage(config: OperatorConfig) -> Path:
    ws_key = str(config.workspace)
    cached = _workspace_storage_cache.get(ws_key)
    if cached and cached.exists():
        return cached

    # Check if workspaceStorage already exists (VS Code already open)
    storage = find_workspace_storage(config.workspace)
    if not storage:
        # Open in a NEW window so we don't hijack the user's active editor.
        focus_workspace(config.workspace, config, new_window=True)
        deadline = time.time() + 20
        while time.time() < deadline:
            storage = find_workspace_storage(config.workspace)
            if storage:
                break
            time.sleep(1)

    if not storage:
        raise VSCodeChatError(f'Could not resolve workspaceStorage for {config.workspace}.')

    _workspace_storage_cache[ws_key] = storage
    return storage


def snapshot_chat_sessions(chat_dir: Path) -> dict[Path, float]:
    if not chat_dir.exists():
        return {}
    return {path: path.stat().st_mtime for path in chat_dir.glob('*.json*') if path.is_file()}


def send_chat_prompt(
    config: OperatorConfig,
    prompt: str,
    add_files: Iterable[Path] | None = None,
    maximize: bool = False,
) -> None:
    # NOTE: We do NOT call focus_workspace() here.
    # When multiple VS Code windows are open, `--reuse-window` targets whichever
    # window is currently focused — not necessarily the correct workspace.
    # Fix: focus the target workspace first, then send the chat command.
    focus_workspace(config.workspace, config)

    # Windows command-line escaping can inflate prompt length 3-4x.
    # Write prompt to a file in the workspace and use --add-file to pass it.
    prompt_file = config.workspace / '.copilot-operator' / 'current-prompt.md'
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text(prompt, encoding='utf-8')

    args = ['chat', '--reuse-window', '--mode', config.mode]
    args.extend(['--add-file', str(prompt_file)])
    for add_file in add_files or []:
        args.extend(['--add-file', str(add_file)])
    if maximize:
        args.append('--maximize')
    # Inline text triggers the session; the real instructions are in the attached file.
    # We repeat the OPERATOR_STATE requirement inline so it appears even if the file
    # is treated as context rather than the primary prompt.
    args.append(
        'Follow the instructions in the attached .copilot-operator/current-prompt.md file exactly. '
        'Your reply MUST end with <OPERATOR_STATE>{...}</OPERATOR_STATE> and <OPERATOR_PLAN>{...}</OPERATOR_PLAN> blocks as specified.'
    )
    run_code_command(
        args,
        cwd=config.workspace,
        timeout_seconds=120,
        retries=config.code_command_retries,
        retry_delay_seconds=config.code_command_retry_delay_seconds,
    )


def wait_for_session_file(chat_dir: Path, baseline: dict[Path, float], timeout_seconds: int, poll_interval: float) -> Path:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        current = snapshot_chat_sessions(chat_dir)
        changed = [path for path, mtime in current.items() if mtime > baseline.get(path, 0.0)]
        if changed:
            return max(changed, key=lambda item: item.stat().st_mtime)
        time.sleep(poll_interval)
    raise VSCodeChatRetryableError('Timed out waiting for a new or updated chat session file.')


def wait_for_completed_session(session_path: Path, config: OperatorConfig) -> dict:
    deadline = time.time() + config.session_timeout_seconds
    quiet_rounds = 0
    last_mtime = 0.0
    latest_session: dict | None = None
    while time.time() < deadline:
        if not session_path.exists():
            time.sleep(config.poll_interval_seconds)
            continue
        current_mtime = session_path.stat().st_mtime
        if current_mtime != last_mtime:
            last_mtime = current_mtime
            quiet_rounds = 0
        else:
            quiet_rounds += 1

        try:
            session = load_chat_session(session_path)
        except (ValueError, json.JSONDecodeError, OSError):
            # File may be in the middle of being written; skip and retry
            time.sleep(config.poll_interval_seconds)
            continue

        latest_session = session
        latest_request = get_latest_request(latest_session)
        if latest_request and request_completed(latest_session, latest_request) and quiet_rounds >= config.quiet_period_polls:
            return latest_session
        time.sleep(config.poll_interval_seconds)

    if latest_session is not None:
        return latest_session
    raise VSCodeChatError('Timed out waiting for the chat session to complete.')
