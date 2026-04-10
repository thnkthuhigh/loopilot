from __future__ import annotations

__all__ = [
    'WorkspaceInsight',
    'detect_workspace_insight',
    'as_dict',
]

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class WorkspaceInsight:
    ecosystem: str = 'unknown'
    package_manager: str = ''
    package_manager_command: str = ''
    validation_hints: dict[str, str] = field(default_factory=dict)
    evidence: list[str] = field(default_factory=list)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding='utf-8')
    except OSError:
        return ''


def _add_validation_hint(target: dict[str, str], name: str, command: str) -> None:
    normalized = name.lower().strip()
    target[normalized] = command
    if normalized == 'test':
        target['tests'] = command
    if normalized == 'tests':
        target['test'] = command


def _node_run_command(package_manager: str, script: str) -> str:
    manager = package_manager or 'npm'
    if manager == 'npm':
        return 'npm test' if script == 'test' else f'npm run {script}'
    if manager == 'pnpm':
        return 'pnpm test' if script == 'test' else f'pnpm run {script}'
    if manager == 'yarn':
        return f'yarn {script}'
    if manager == 'bun':
        return 'bun test' if script == 'test' else f'bun run {script}'
    return f'{manager} run {script}'


def _placeholder_test_script(script: str) -> bool:
    normalized = ' '.join(script.lower().split())
    return 'no test specified' in normalized or normalized in {'exit 1', 'echo todo'}


def _detect_node_insight(workspace: Path) -> WorkspaceInsight | None:
    package_json_path = workspace / 'package.json'
    if not package_json_path.exists():
        return None

    package_manager = 'npm'
    if (workspace / 'pnpm-lock.yaml').exists():
        package_manager = 'pnpm'
    elif (workspace / 'yarn.lock').exists():
        package_manager = 'yarn'
    elif (workspace / 'bun.lockb').exists() or (workspace / 'bun.lock').exists():
        package_manager = 'bun'
    elif (workspace / 'package-lock.json').exists():
        package_manager = 'npm'

    validation_hints: dict[str, str] = {}
    evidence = ['package.json']
    if (workspace / 'pnpm-lock.yaml').exists():
        evidence.append('pnpm-lock.yaml')
    if (workspace / 'yarn.lock').exists():
        evidence.append('yarn.lock')
    if (workspace / 'bun.lockb').exists() or (workspace / 'bun.lock').exists():
        evidence.append('bun.lock*')
    if (workspace / 'package-lock.json').exists():
        evidence.append('package-lock.json')

    try:
        package_data = json.loads(_read_text(package_json_path) or '{}')
    except json.JSONDecodeError:
        package_data = {}
    scripts = package_data.get('scripts', {}) if isinstance(package_data.get('scripts', {}), dict) else {}

    for script_name in ('test', 'lint', 'build'):
        script_body = str(scripts.get(script_name, '')).strip()
        if not script_body:
            continue
        if script_name == 'test' and _placeholder_test_script(script_body):
            continue
        _add_validation_hint(validation_hints, script_name, _node_run_command(package_manager, script_name))

    return WorkspaceInsight(
        ecosystem='node',
        package_manager=package_manager,
        package_manager_command=package_manager,
        validation_hints=validation_hints,
        evidence=evidence,
    )


def _python_run_prefix(workspace: Path) -> tuple[str, str, list[str]]:
    evidence: list[str] = []
    if (workspace / 'uv.lock').exists():
        evidence.append('uv.lock')
        return 'uv', 'uv run ', evidence
    if (workspace / 'poetry.lock').exists():
        evidence.append('poetry.lock')
        return 'poetry', 'poetry run ', evidence
    if (workspace / 'requirements.txt').exists() or list(workspace.glob('requirements*.txt')):
        evidence.append('requirements*.txt')
    return 'python', '', evidence


def _detect_python_insight(workspace: Path) -> WorkspaceInsight | None:
    markers = [workspace / 'pyproject.toml', workspace / 'requirements.txt', workspace / 'setup.py']
    if not any(marker.exists() for marker in markers) and not list(workspace.glob('requirements*.txt')):
        return None

    package_manager, run_prefix, evidence = _python_run_prefix(workspace)
    validation_hints: dict[str, str] = {}
    combined_text = '\n'.join(_read_text(path) for path in markers if path.exists()).lower()

    tests_dir_exists = (workspace / 'tests').exists() or (workspace / 'test').exists()
    if 'pytest' in combined_text or tests_dir_exists:
        _add_validation_hint(validation_hints, 'tests', f'{run_prefix}pytest'.strip())

    if 'ruff' in combined_text or (workspace / '.ruff.toml').exists() or (workspace / 'ruff.toml').exists():
        _add_validation_hint(validation_hints, 'lint', f'{run_prefix}ruff check .'.strip())
    elif 'flake8' in combined_text or (workspace / '.flake8').exists():
        _add_validation_hint(validation_hints, 'lint', f'{run_prefix}flake8 .'.strip())

    if package_manager == 'poetry':
        _add_validation_hint(validation_hints, 'build', 'poetry build')
    elif (workspace / 'pyproject.toml').exists():
        _add_validation_hint(validation_hints, 'build', f'{run_prefix}python -m build'.strip())

    pyproject_path = workspace / 'pyproject.toml'
    if pyproject_path.exists():
        evidence.append('pyproject.toml')
    if (workspace / 'setup.py').exists():
        evidence.append('setup.py')
    if tests_dir_exists:
        evidence.append('tests/')

    return WorkspaceInsight(
        ecosystem='python',
        package_manager=package_manager,
        package_manager_command=run_prefix.strip() or 'python',
        validation_hints=validation_hints,
        evidence=evidence,
    )


def _merge_insights(node: WorkspaceInsight | None, python: WorkspaceInsight | None) -> WorkspaceInsight:
    if node and python:
        merged_hints = dict(python.validation_hints)
        merged_hints.update(node.validation_hints)
        return WorkspaceInsight(
            ecosystem='mixed',
            package_manager=node.package_manager or python.package_manager,
            package_manager_command=node.package_manager_command or python.package_manager_command,
            validation_hints=merged_hints,
            evidence=node.evidence + [item for item in python.evidence if item not in node.evidence],
        )
    if node:
        return node
    if python:
        return python
    return WorkspaceInsight(
        ecosystem='unknown',
        package_manager='',
        package_manager_command='',
        validation_hints={},
        evidence=[],
    )


def detect_workspace_insight(workspace: str | Path) -> WorkspaceInsight:
    workspace_path = Path(workspace).resolve()
    return _merge_insights(_detect_node_insight(workspace_path), _detect_python_insight(workspace_path))


def as_dict(insight: WorkspaceInsight) -> dict[str, Any]:
    return {
        'ecosystem': insight.ecosystem,
        'packageManager': insight.package_manager,
        'packageManagerCommand': insight.package_manager_command,
        'validationHints': insight.validation_hints,
        'evidence': insight.evidence,
    }
