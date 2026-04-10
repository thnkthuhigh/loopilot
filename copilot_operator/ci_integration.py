"""CI integration — trigger CI pipelines and read results.

Provides a bridge between the operator and CI systems (GitHub Actions)
so the operator can trigger builds, read results, and feed failures
back into the fix loop.

Requires GITHUB_TOKEN for GitHub Actions API access.
"""

from __future__ import annotations

__all__ = [
    'CIConfig',
    'WorkflowRun',
    'WorkflowJob',
    'CIResult',
    'load_ci_config',
    'list_workflows',
    'trigger_workflow',
    'get_latest_run',
    'get_run_jobs',
    'wait_for_run',
    'analyse_run',
]

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from .logging_config import get_logger

logger = get_logger('ci')


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class CIConfig:
    """CI connection settings."""
    token: str = ''
    owner: str = ''
    repo: str = ''
    api_base: str = 'https://api.github.com'
    default_branch: str = 'main'

    @property
    def is_configured(self) -> bool:
        return bool(self.token and self.owner and self.repo)


@dataclass(slots=True)
class WorkflowRun:
    """Simplified GitHub Actions workflow run."""
    run_id: int = 0
    name: str = ''
    status: str = ''          # queued, in_progress, completed
    conclusion: str = ''      # success, failure, cancelled, skipped, timed_out
    head_branch: str = ''
    head_sha: str = ''
    url: str = ''
    created_at: str = ''
    updated_at: str = ''


@dataclass(slots=True)
class WorkflowJob:
    """A single job within a workflow run."""
    job_id: int = 0
    name: str = ''
    status: str = ''
    conclusion: str = ''
    steps: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class CIResult:
    """Aggregated CI result for operator consumption."""
    success: bool = False
    run_id: int = 0
    conclusion: str = ''
    failed_jobs: list[str] = field(default_factory=list)
    failed_steps: list[str] = field(default_factory=list)
    log_excerpt: str = ''
    url: str = ''


# ---------------------------------------------------------------------------
# HTTP helper (mirrors github_integration pattern)
# ---------------------------------------------------------------------------

def _ci_api_request(
    config: CIConfig,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    timeout: int = 30,
    max_retries: int = 2,
) -> dict[str, Any] | list[Any]:
    """Make an authenticated GitHub Actions API request."""
    if not config.is_configured:
        raise RuntimeError('CI integration not configured (need token + owner + repo).')

    url = f'{config.api_base}/repos/{config.owner}/{config.repo}{path}'
    headers = {
        'Authorization': f'token {config.token}',
        'Accept': 'application/vnd.github+json',
        'User-Agent': 'copilot-operator/1.0',
        'X-GitHub-Api-Version': '2022-11-28',
    }
    data = json.dumps(body).encode('utf-8') if body else None
    if data:
        headers['Content-Type'] = 'application/json'

    for attempt in range(max_retries + 1):
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode('utf-8')
                return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < max_retries:
                wait = min(2 ** (attempt + 1), 30)
                logger.warning('GitHub rate limited (429) — retrying in %ds', wait)
                time.sleep(wait)
                continue
            error_body = exc.read().decode('utf-8', errors='replace') if exc.fp else ''
            raise RuntimeError(f'CI API {method} {path} returned {exc.code}: {error_body}') from exc
    raise RuntimeError(f'CI API {method} {path} exhausted retries')


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def load_ci_config(github_token: str = '', owner: str = '', repo: str = '') -> CIConfig:
    """Load CI config, attempting to infer owner/repo from git remote."""
    import os
    token = github_token or os.environ.get('GITHUB_TOKEN', '')
    if not owner or not repo:
        inferred = _infer_owner_repo()
        owner = owner or (inferred[0] if inferred else '')
        repo = repo or (inferred[1] if inferred else '')
    return CIConfig(token=token, owner=owner, repo=repo)


def _infer_owner_repo() -> tuple[str, str] | None:
    """Extract owner/repo from git remote."""
    import subprocess
    try:
        result = subprocess.run(
            ['git', 'remote', 'get-url', 'origin'],
            capture_output=True, text=True, timeout=10, check=False,
        )
        if result.returncode != 0:
            return None
        url = result.stdout.strip()
        if 'github.com' not in url:
            return None
        if url.startswith('git@'):
            path = url.split(':')[-1]
        else:
            path = url.split('github.com/')[-1]
        path = path.removesuffix('.git')
        parts = path.split('/')
        if len(parts) >= 2:
            return parts[0], parts[1]
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Workflow operations
# ---------------------------------------------------------------------------

def list_workflows(config: CIConfig) -> list[dict[str, Any]]:
    """List available workflows in the repository."""
    data = _ci_api_request(config, 'GET', '/actions/workflows')
    workflows = data.get('workflows', []) if isinstance(data, dict) else []
    return [
        {'id': w.get('id'), 'name': w.get('name', ''), 'path': w.get('path', ''), 'state': w.get('state', '')}
        for w in workflows
    ]


def trigger_workflow(
    config: CIConfig,
    workflow_id: str | int,
    ref: str = '',
    inputs: dict[str, str] | None = None,
) -> bool:
    """Trigger a workflow dispatch event. Returns True on success."""
    ref = ref or config.default_branch
    body: dict[str, Any] = {'ref': ref}
    if inputs:
        body['inputs'] = inputs
    try:
        _ci_api_request(config, 'POST', f'/actions/workflows/{workflow_id}/dispatches', body=body)
        logger.info('Triggered workflow %s on ref %s', workflow_id, ref)
        return True
    except RuntimeError as exc:
        logger.error('Failed to trigger workflow: %s', exc)
        return False


def get_latest_run(config: CIConfig, branch: str = '', workflow_id: str | int = '') -> WorkflowRun | None:
    """Get the most recent workflow run, optionally filtered by branch/workflow."""
    params = '?per_page=1'
    if branch:
        params += f'&branch={urllib.parse.quote(branch)}'

    path = f'/actions/workflows/{workflow_id}/runs{params}' if workflow_id else f'/actions/runs{params}'
    data = _ci_api_request(config, 'GET', path)
    runs = data.get('workflow_runs', []) if isinstance(data, dict) else []
    if not runs:
        return None

    run = runs[0]
    return WorkflowRun(
        run_id=run.get('id', 0),
        name=run.get('name', ''),
        status=run.get('status', ''),
        conclusion=run.get('conclusion', ''),
        head_branch=run.get('head_branch', ''),
        head_sha=run.get('head_sha', ''),
        url=run.get('html_url', ''),
        created_at=run.get('created_at', ''),
        updated_at=run.get('updated_at', ''),
    )


def get_run_jobs(config: CIConfig, run_id: int) -> list[WorkflowJob]:
    """Get jobs for a specific workflow run."""
    data = _ci_api_request(config, 'GET', f'/actions/runs/{run_id}/jobs')
    jobs_data = data.get('jobs', []) if isinstance(data, dict) else []
    jobs: list[WorkflowJob] = []
    for j in jobs_data:
        steps = [
            {'name': s.get('name', ''), 'status': s.get('status', ''), 'conclusion': s.get('conclusion', '')}
            for s in j.get('steps', [])
        ]
        jobs.append(WorkflowJob(
            job_id=j.get('id', 0),
            name=j.get('name', ''),
            status=j.get('status', ''),
            conclusion=j.get('conclusion', ''),
            steps=steps,
        ))
    return jobs


def wait_for_run(config: CIConfig, run_id: int, timeout_seconds: int = 600, poll_seconds: int = 15) -> WorkflowRun | None:
    """Poll until a workflow run completes or timeout."""
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        data = _ci_api_request(config, 'GET', f'/actions/runs/{run_id}')
        if not isinstance(data, dict):
            time.sleep(poll_seconds)
            continue
        status = data.get('status', '')
        if status == 'completed':
            return WorkflowRun(
                run_id=data.get('id', 0),
                name=data.get('name', ''),
                status=status,
                conclusion=data.get('conclusion', ''),
                head_branch=data.get('head_branch', ''),
                head_sha=data.get('head_sha', ''),
                url=data.get('html_url', ''),
                created_at=data.get('created_at', ''),
                updated_at=data.get('updated_at', ''),
            )
        time.sleep(poll_seconds)
    logger.warning('Timed out waiting for run %d after %ds', run_id, timeout_seconds)
    return None


# ---------------------------------------------------------------------------
# Result analysis
# ---------------------------------------------------------------------------

def analyse_run(config: CIConfig, run_id: int) -> CIResult:
    """Analyse a completed workflow run and extract failure details."""
    jobs = get_run_jobs(config, run_id)
    failed_jobs: list[str] = []
    failed_steps: list[str] = []

    for job in jobs:
        if job.conclusion in ('failure', 'timed_out'):
            failed_jobs.append(job.name)
            for step in job.steps:
                if step.get('conclusion') in ('failure', 'timed_out'):
                    failed_steps.append(f'{job.name} → {step["name"]}')

    # Try to get log excerpt for failed jobs
    log_excerpt = ''
    if failed_jobs:
        try:
            log_excerpt = _get_failed_log_excerpt(config, run_id, jobs)
        except Exception as exc:
            logger.debug('Could not fetch failure logs: %s', exc)

    overall_success = not failed_jobs
    run_data = _ci_api_request(config, 'GET', f'/actions/runs/{run_id}')
    conclusion = run_data.get('conclusion', '') if isinstance(run_data, dict) else ''
    url = run_data.get('html_url', '') if isinstance(run_data, dict) else ''

    return CIResult(
        success=overall_success,
        run_id=run_id,
        conclusion=conclusion,
        failed_jobs=failed_jobs,
        failed_steps=failed_steps,
        log_excerpt=log_excerpt[:2000],  # Cap at 2KB
        url=url,
    )


def _get_failed_log_excerpt(config: CIConfig, run_id: int, jobs: list[WorkflowJob]) -> str:
    """Try to extract log lines for failed steps."""
    excerpts: list[str] = []
    for job in jobs:
        if job.conclusion not in ('failure', 'timed_out'):
            continue
        try:
            # Download job logs (returns plain text, not JSON)
            url = f'{config.api_base}/repos/{config.owner}/{config.repo}/actions/jobs/{job.job_id}/logs'
            headers = {
                'Authorization': f'token {config.token}',
                'Accept': 'application/vnd.github+json',
                'User-Agent': 'copilot-operator/1.0',
            }
            req = urllib.request.Request(url, headers=headers, method='GET')
            with urllib.request.urlopen(req, timeout=30) as resp:
                log_text = resp.read().decode('utf-8', errors='replace')
                # Take last 50 lines as the most relevant
                lines = log_text.strip().splitlines()
                excerpt = '\n'.join(lines[-50:])
                excerpts.append(f'--- {job.name} ---\n{excerpt}')
        except Exception:
            continue
    return '\n\n'.join(excerpts)


# ---------------------------------------------------------------------------
# Operator integration helpers
# ---------------------------------------------------------------------------

def build_ci_fix_prompt(ci_result: CIResult) -> str:
    """Build a prompt for Copilot to fix CI failures."""
    lines = [
        'The CI pipeline has failed. Fix the following issues:',
        '',
        f'**Failed jobs**: {", ".join(ci_result.failed_jobs) or "none"}',
        f'**Failed steps**: {", ".join(ci_result.failed_steps) or "none"}',
        f'**Conclusion**: {ci_result.conclusion}',
    ]
    if ci_result.url:
        lines.append(f'**CI URL**: {ci_result.url}')
    if ci_result.log_excerpt:
        lines.extend(['', '**Failure log excerpt**:', '```', ci_result.log_excerpt, '```'])
    lines.extend([
        '',
        'Analyze the failure logs, identify the root cause, and fix the code.',
        'After fixing, ensure all tests pass locally before reporting done.',
    ])
    return '\n'.join(lines)


def render_ci_summary(result: CIResult) -> str:
    """Human-readable CI result summary."""
    status = '✅ PASS' if result.success else '❌ FAIL'
    lines = [
        f'CI Result: {status} ({result.conclusion})',
        f'Run ID: {result.run_id}',
    ]
    if result.failed_jobs:
        lines.append(f'Failed jobs: {", ".join(result.failed_jobs)}')
    if result.failed_steps:
        lines.append(f'Failed steps: {", ".join(result.failed_steps)}')
    if result.url:
        lines.append(f'URL: {result.url}')
    return '\n'.join(lines)
