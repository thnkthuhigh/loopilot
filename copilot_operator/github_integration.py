"""GitHub integration — pull issues, create branches, open PRs.

Provides the bridge between the operator and GitHub's REST API so the
operator can autonomously pick work, ship code, and open PRs.

Requires a GITHUB_TOKEN environment variable for authentication.
"""

from __future__ import annotations

__all__ = [
    'GitHubIssue',
    'PullRequest',
    'GitHubConfig',
    'load_github_config',
    'list_issues',
    'get_issue',
    'close_issue',
    'create_pull_request',
]

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from .logging_config import get_logger

logger = get_logger('github')

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class GitHubIssue:
    """Simplified GitHub issue representation."""
    number: int = 0
    title: str = ''
    body: str = ''
    labels: list[str] = field(default_factory=list)
    state: str = 'open'
    url: str = ''
    assignee: str = ''


@dataclass(slots=True)
class PullRequest:
    """Simplified PR representation."""
    number: int = 0
    title: str = ''
    body: str = ''
    head: str = ''
    base: str = ''
    url: str = ''
    state: str = 'open'
    draft: bool = True


@dataclass(slots=True)
class GitHubConfig:
    """GitHub connection settings."""
    token: str = ''
    owner: str = ''
    repo: str = ''
    api_base: str = 'https://api.github.com'

    @property
    def is_configured(self) -> bool:
        return bool(self.token and self.owner and self.repo)


# ---------------------------------------------------------------------------
# HTTP helpers (stdlib only — no requests dependency)
# ---------------------------------------------------------------------------

def _api_request(
    config: GitHubConfig,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    timeout: int = 30,
    max_retries: int = 2,
) -> dict[str, Any] | list[Any]:
    """Make an authenticated GitHub API request with retry on rate limit."""
    if not config.is_configured:
        raise RuntimeError('GitHub integration not configured (need token + owner + repo).')

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
                return json.loads(resp.read().decode('utf-8'))
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < max_retries:
                wait = min(2 ** (attempt + 1), 30)
                logger.warning('GitHub rate limited (429) — retrying in %ds', wait)
                time.sleep(wait)
                continue
            error_body = exc.read().decode('utf-8', errors='replace') if exc.fp else ''
            raise RuntimeError(f'GitHub API {method} {path} returned {exc.code}: {error_body}') from exc
    raise RuntimeError(f'GitHub API {method} {path} exhausted retries')


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def load_github_config(workspace_config: dict[str, Any] | None = None) -> GitHubConfig:
    """Load GitHub config from environment + optional workspace config override."""
    token = os.environ.get('GITHUB_TOKEN', '')
    owner = ''
    repo = ''

    if workspace_config:
        owner = str(workspace_config.get('owner', '')).strip()
        repo = str(workspace_config.get('repo', '')).strip()

    # Try to infer owner/repo from git remote
    if not owner or not repo:
        inferred = _infer_from_git_remote()
        if inferred:
            owner = owner or inferred[0]
            repo = repo or inferred[1]

    return GitHubConfig(token=token, owner=owner, repo=repo)


def _infer_from_git_remote() -> tuple[str, str] | None:
    """Try to extract owner/repo from 'git remote get-url origin'."""
    import subprocess
    try:
        result = subprocess.run(
            ['git', 'remote', 'get-url', 'origin'],
            capture_output=True, text=True, timeout=10, check=False,
        )
        if result.returncode != 0:
            return None
        url = result.stdout.strip()
        # https://github.com/owner/repo.git or git@github.com:owner/repo.git
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
# Issues
# ---------------------------------------------------------------------------

def list_issues(
    config: GitHubConfig,
    labels: list[str] | None = None,
    state: str = 'open',
    limit: int = 10,
) -> list[GitHubIssue]:
    """List issues from the repository."""
    params = f'?state={urllib.parse.quote(state)}&per_page={limit}'
    if labels:
        params += f'&labels={urllib.parse.quote(",".join(labels))}'

    data = _api_request(config, 'GET', f'/issues{params}')
    issues = []
    for item in data if isinstance(data, list) else []:
        if item.get('pull_request'):
            continue  # Skip PRs returned in issue list
        issues.append(GitHubIssue(
            number=item.get('number', 0),
            title=item.get('title', ''),
            body=item.get('body', '') or '',
            labels=[lbl.get('name', '') for lbl in item.get('labels', [])],
            state=item.get('state', 'open'),
            url=item.get('html_url', ''),
            assignee=item.get('assignee', {}).get('login', '') if item.get('assignee') else '',
        ))
    return issues


def get_issue(config: GitHubConfig, number: int) -> GitHubIssue:
    """Get a single issue by number."""
    item = _api_request(config, 'GET', f'/issues/{number}')
    if not isinstance(item, dict):
        raise RuntimeError(f'Unexpected response for issue #{number}')
    return GitHubIssue(
        number=item.get('number', 0),
        title=item.get('title', ''),
        body=item.get('body', '') or '',
        labels=[lbl.get('name', '') for lbl in item.get('labels', [])],
        state=item.get('state', 'open'),
        url=item.get('html_url', ''),
        assignee=item.get('assignee', {}).get('login', '') if item.get('assignee') else '',
    )


def add_issue_comment(config: GitHubConfig, number: int, body: str) -> dict[str, Any]:
    """Add a comment to an issue."""
    result = _api_request(config, 'POST', f'/issues/{number}/comments', {'body': body})
    return result if isinstance(result, dict) else {}


def issue_to_goal(issue: GitHubIssue) -> str:
    """Convert a GitHub issue to an operator goal string."""
    lines = [f'Fix issue #{issue.number}: {issue.title}']
    if issue.body:
        # Truncate very long bodies
        body = issue.body[:1000].strip()
        lines.append(f'\nIssue description:\n{body}')
    if issue.labels:
        lines.append(f'\nLabels: {", ".join(issue.labels)}')
    lines.extend([
        '',
        'Constraints:',
        '- Keep the fix minimal and localized.',
        '- Add or update tests near the changed behavior.',
        '- Do not make unrelated changes.',
    ])
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Pull Requests
# ---------------------------------------------------------------------------

def create_pull_request(
    config: GitHubConfig,
    title: str,
    body: str,
    head: str,
    base: str = 'main',
    draft: bool = True,
) -> PullRequest:
    """Create a pull request."""
    data = _api_request(config, 'POST', '/pulls', {
        'title': title,
        'body': body,
        'head': head,
        'base': base,
        'draft': draft,
    })
    if not isinstance(data, dict):
        raise RuntimeError('Unexpected response creating PR')
    return PullRequest(
        number=data.get('number', 0),
        title=data.get('title', ''),
        body=data.get('body', ''),
        head=head,
        base=base,
        url=data.get('html_url', ''),
        state=data.get('state', 'open'),
        draft=data.get('draft', True),
    )


def build_pr_body(
    goal: str,
    run_id: str,
    iterations: int,
    score: int | None,
    reason_code: str,
    plan_summary: str = '',
) -> str:
    """Build a standardised PR description from operator run results."""
    lines = [
        '## Copilot Operator Run',
        '',
        f'**Goal:** {goal}',
        f'**Run ID:** `{run_id}`',
        f'**Iterations:** {iterations}',
        f'**Final Score:** {score}',
        f'**Reason Code:** `{reason_code}`',
    ]
    if plan_summary:
        lines.extend(['', f'**Plan:** {plan_summary}'])
    lines.extend([
        '',
        '---',
        '*This PR was created automatically by the Copilot Operator.*',
    ])
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# End-to-end: issue → goal → run → PR (orchestration helper)
# ---------------------------------------------------------------------------

def pick_next_issue(
    config: GitHubConfig,
    preferred_labels: list[str] | None = None,
) -> GitHubIssue | None:
    """Pick the most suitable unassigned issue for the operator to work on."""
    labels = preferred_labels or ['good first issue', 'bug', 'enhancement']
    for label in labels:
        issues = list_issues(config, labels=[label], limit=5)
        unassigned = [i for i in issues if not i.assignee]
        if unassigned:
            return unassigned[0]
    # Fallback: any open issue
    all_issues = list_issues(config, limit=5)
    unassigned_all = [i for i in all_issues if not i.assignee]
    return unassigned_all[0] if unassigned_all else None


# ---------------------------------------------------------------------------
# Issue lifecycle — status updates and sync
# ---------------------------------------------------------------------------

def update_issue_labels(
    config: GitHubConfig,
    number: int,
    add_labels: list[str] | None = None,
    remove_labels: list[str] | None = None,
) -> None:
    """Add and/or remove labels on an issue."""
    if add_labels:
        _api_request(config, 'POST', f'/issues/{number}/labels', {'labels': add_labels})
    for label in (remove_labels or []):
        try:
            _api_request(config, 'DELETE', f'/issues/{number}/labels/{urllib.parse.quote(label)}')
        except RuntimeError:
            pass  # Label may not exist


def close_issue(config: GitHubConfig, number: int, comment: str = '') -> None:
    """Close an issue, optionally adding a comment."""
    if comment:
        add_issue_comment(config, number, comment)
    _api_request(config, 'PATCH', f'/issues/{number}', {'state': 'closed'})


def post_run_summary_to_issue(
    config: GitHubConfig,
    issue_number: int,
    result: dict[str, Any],
) -> None:
    """Post a structured summary of an operator run as an issue comment."""
    status = result.get('status', 'unknown')
    score = result.get('score', 'N/A')
    iterations = result.get('iterations', 0)
    reason_code = result.get('reasonCode', '')

    status_emoji = {'complete': '✅', 'blocked': '⚠️', 'error': '❌'}.get(status, '❓')

    body = (
        f'## {status_emoji} Copilot Operator Run Report\n\n'
        f'| Metric | Value |\n|--------|-------|\n'
        f'| Status | **{status}** |\n'
        f'| Score | {score} |\n'
        f'| Iterations | {iterations} |\n'
        f'| Reason | `{reason_code}` |\n'
    )

    pr_info = result.get('pullRequest')
    if pr_info:
        body += f'\n**PR:** [{pr_info.get("title", "")}]({pr_info.get("url", "")})\n'

    body += '\n---\n*Automated by Copilot Operator*'
    add_issue_comment(config, issue_number, body)


def sync_issue_status(
    config: GitHubConfig,
    issue_number: int,
    result: dict[str, Any],
    auto_close: bool = False,
) -> None:
    """Sync operator result back to GitHub issue (labels + comment + optional close)."""
    status = result.get('status', 'unknown')

    # Label management
    if status == 'complete':
        update_issue_labels(config, issue_number,
                            add_labels=['operator:done'],
                            remove_labels=['operator:in-progress', 'operator:blocked'])
    elif status == 'blocked':
        update_issue_labels(config, issue_number,
                            add_labels=['operator:blocked'],
                            remove_labels=['operator:in-progress'])
    else:
        update_issue_labels(config, issue_number,
                            add_labels=['operator:in-progress'])

    # Post run summary
    post_run_summary_to_issue(config, issue_number, result)

    # Auto-close on success
    if auto_close and status == 'complete':
        score = result.get('score')
        if score is not None and score >= 85:
            close_issue(config, issue_number,
                        f'Closed by Copilot Operator (score={score}).')


def batch_process_issues(
    config: GitHubConfig,
    labels: list[str] | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """List issues suitable for batch processing by the operator.

    Returns a list of dicts with issue info + generated goal text.
    """
    issues = list_issues(config, labels=labels, limit=limit)
    return [
        {
            'number': issue.number,
            'title': issue.title,
            'labels': issue.labels,
            'goal': issue_to_goal(issue),
            'url': issue.url,
        }
        for issue in issues
        if not issue.assignee  # Only unassigned issues
    ]


def post_blocked_alert(
    config: GitHubConfig,
    issue_number: int,
    reason: str,
    run_id: str = '',
    elapsed_seconds: float = 0,
) -> None:
    """Post a blocked alert to an issue when the operator is stuck.

    Called by the operator when a run enters 'blocked' state and
    SLA enforcement is configured.
    """
    elapsed_text = f'{elapsed_seconds / 60:.0f} min' if elapsed_seconds else 'unknown'
    run_text = f' (run `{run_id[:8]}`)' if run_id else ''

    body = (
        f'## 🚨 Operator Blocked Alert{run_text}\n\n'
        f'The operator has been blocked for **{elapsed_text}**.\n\n'
        f'**Reason:** {reason}\n\n'
        f'Human intervention may be needed to unblock.\n\n'
        f'---\n*Automated by Copilot Operator*'
    )
    add_issue_comment(config, issue_number, body)
    update_issue_labels(
        config, issue_number,
        add_labels=['operator:blocked', 'needs-attention'],
        remove_labels=['operator:in-progress'],
    )
