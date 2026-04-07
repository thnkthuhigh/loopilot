from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse


def _uri_to_path(raw: str) -> Path | None:
    if not raw:
        return None
    parsed = urlparse(raw)
    if parsed.scheme != 'file':
        return None
    path = unquote(parsed.path)
    if path.startswith('/') and len(path) > 2 and path[2] == ':':
        path = path[1:]
    return Path(path).resolve()


def find_workspace_storage(workspace: str | Path, storage_root: str | Path | None = None) -> Path | None:
    workspace_path = Path(workspace).resolve()
    root = Path(storage_root or Path.home() / 'AppData' / 'Roaming' / 'Code' / 'User' / 'workspaceStorage')
    if not root.exists():
        return None

    for candidate in root.glob('*/workspace.json'):
        try:
            data = json.loads(candidate.read_text(encoding='utf-8'))
        except json.JSONDecodeError:
            continue
        folder = _uri_to_path(str(data.get('folder', '')))
        if folder and folder == workspace_path:
            return candidate.parent
        workspace_uri = data.get('workspace')
        if isinstance(workspace_uri, dict):
            config_path = _uri_to_path(str(workspace_uri.get('configPath', '')))
            if config_path and config_path.resolve() == workspace_path:
                return candidate.parent
    return None


def _ensure_parent(container: Any, key: Any, next_key: Any) -> Any:
    if isinstance(key, int):
        if not isinstance(container, list):
            raise TypeError('Expected a list while replaying chat session events.')
        while len(container) <= key:
            container.append([] if isinstance(next_key, int) else {})
        return container[key]
    if key not in container or container[key] is None:
        container[key] = [] if isinstance(next_key, int) else {}
    return container[key]


def _set_at_path(state: Any, path: list[Any], value: Any) -> None:
    if not path:
        raise ValueError('Empty event path.')
    target = state
    for index, key in enumerate(path[:-1]):
        target = _ensure_parent(target, key, path[index + 1])
    last = path[-1]
    if isinstance(last, int):
        if not isinstance(target, list):
            raise TypeError('Expected a list at the final event path.')
        while len(target) <= last:
            target.append(None)
        target[last] = value
    else:
        target[last] = value


def _get_at_path(state: Any, path: list[Any]) -> Any:
    target = state
    for key in path:
        target = target[key]
    return target


def load_chat_session(path: str | Path) -> dict[str, Any]:
    session_path = Path(path)
    if session_path.suffix == '.json':
        return json.loads(session_path.read_text(encoding='utf-8'))

    state: dict[str, Any] | None = None
    for raw_line in session_path.read_text(encoding='utf-8').splitlines():
        if not raw_line.strip():
            continue
        event = json.loads(raw_line)
        kind = int(event['kind'])
        if kind == 0:
            state = copy.deepcopy(event['v'])
            continue
        if state is None:
            raise ValueError(f'Session file {session_path} is missing the initial snapshot event.')
        path_tokens = event['k']
        value = copy.deepcopy(event['v'])
        if kind == 1:
            _set_at_path(state, path_tokens, value)
            continue
        if kind == 2:
            try:
                target = _get_at_path(state, path_tokens)
            except (KeyError, IndexError, TypeError):
                _set_at_path(state, path_tokens, value)
                continue
            if isinstance(target, list) and isinstance(value, list):
                target.extend(value)
            elif isinstance(target, dict) and isinstance(value, dict):
                target.update(value)
            else:
                _set_at_path(state, path_tokens, value)
            continue
        raise ValueError(f'Unsupported chat session event kind: {kind}')

    if state is None:
        raise ValueError(f'No chat session state could be loaded from {session_path}.')
    return state


def get_latest_request(session: dict[str, Any]) -> dict[str, Any] | None:
    requests = session.get('requests') or []
    if not requests:
        return None
    return requests[-1]


def extract_response_text(request: dict[str, Any] | None) -> str:
    if not request:
        return ''
    parts: list[str] = []
    for item in request.get('response') or []:
        value = item.get('value') if isinstance(item, dict) else None
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    return '\n\n'.join(parts).strip()


def request_needs_continue(request: dict[str, Any] | None) -> bool:
    if not request:
        return False
    confirmation = str(request.get('confirmation', '')).strip().lower()
    if confirmation == 'continue':
        return True
    result = request.get('result') or {}
    metadata = result.get('metadata') or {}
    if metadata.get('maxToolCallsExceeded') is True:
        return True
    return False


def request_completed(session: dict[str, Any], request: dict[str, Any] | None) -> bool:
    if not request:
        return False
    pending = session.get('pendingRequests') or []
    if pending:
        return False
    model_state = request.get('modelState') or {}
    result = request.get('result')
    return bool(result) and bool(model_state.get('completedAt'))
