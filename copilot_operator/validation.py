from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Literal

from .config import ValidationCommand

ValidationPhase = Literal['before_prompt', 'after_response']


def _should_run(item: ValidationCommand, phase: ValidationPhase) -> bool:
    if phase == 'before_prompt':
        return item.run_before_prompt
    return item.run_after_response


def run_validations(validations: list[ValidationCommand], workspace: Path, phase: ValidationPhase = 'after_response') -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for item in validations:
        if not _should_run(item, phase):
            continue
        if not item.command:
            results.append(
                {
                    'name': item.name,
                    'command': '',
                    'status': 'skipped',
                    'required': item.required,
                    'phase': phase,
                    'source': item.source,
                    'summary': 'No command configured or inferred.',
                    'returncode': None,
                    'stdout': '',
                    'stderr': '',
                }
            )
            continue
        try:
            completed = subprocess.run(
                item.command,
                cwd=str(workspace),
                shell=True,
                capture_output=True,
                timeout=item.timeout_seconds,
                check=False,
            )
            stdout = (completed.stdout or b'').decode('utf-8', errors='replace')
            stderr = (completed.stderr or b'').decode('utf-8', errors='replace')
            completed_returncode = completed.returncode
            summary = stdout.strip() or stderr.strip() or 'No output captured.'
            results.append(
                {
                    'name': item.name,
                    'command': item.command,
                    'status': 'pass' if completed_returncode == 0 else 'fail',
                    'required': item.required,
                    'phase': phase,
                    'source': item.source,
                    'summary': summary.splitlines()[0][:300],
                    'returncode': completed_returncode,
                    'stdout': stdout,
                    'stderr': stderr,
                }
            )
        except subprocess.TimeoutExpired as exc:
            stdout = (exc.stdout or b'').decode('utf-8', errors='replace') if isinstance(exc.stdout, bytes) else (exc.stdout or '')
            stderr = (exc.stderr or b'').decode('utf-8', errors='replace') if isinstance(exc.stderr, bytes) else (exc.stderr or '')
            summary = stderr.strip() or stdout.strip() or 'Validation command timed out.'
            results.append(
                {
                    'name': item.name,
                    'command': item.command,
                    'status': 'timeout',
                    'required': item.required,
                    'phase': phase,
                    'source': item.source,
                    'summary': summary.splitlines()[0][:300],
                    'returncode': None,
                    'stdout': stdout,
                    'stderr': stderr,
                }
            )
    return results


def dump_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding='utf-8')
