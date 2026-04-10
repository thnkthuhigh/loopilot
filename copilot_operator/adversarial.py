"""Adversarial review — runs a Critic session against the Coder's output.

The Critic's job is to find flaws, edge cases, security holes, and logic
errors in the Coder's work. The operator merges findings into the next
baton so the Coder fixes them.
"""

from __future__ import annotations

__all__ = [
    'CriticFinding',
    'CriticReport',
    'build_critic_prompt',
    'parse_critic_report',
    'build_fix_baton_from_critic',
    'should_run_critic',
]

import json
import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class CriticFinding:
    """A single issue found by the Critic."""
    severity: str = 'medium'   # low, medium, high, critical
    category: str = ''         # logic_error, edge_case, security, test_gap, style, performance
    file_path: str = ''
    line_hint: str = ''
    description: str = ''
    suggestion: str = ''


@dataclass(slots=True)
class CriticReport:
    """Aggregated Critic output."""
    findings: list[CriticFinding] = field(default_factory=list)
    summary: str = ''
    overall_quality: str = 'unknown'  # good, acceptable, needs_work, poor
    confidence: float = 0.0


# ---------------------------------------------------------------------------
# Critic prompt building
# ---------------------------------------------------------------------------

def build_critic_prompt(
    goal: str,
    coder_summary: str,
    coder_score: int | None,
    diff_summary: str = '',
    iteration: int = 0,
) -> str:
    """Build the adversarial review prompt for the Critic session."""
    return f"""You are a CODE CRITIC reviewing work done by another Copilot session (the Coder).
Your job is to find REAL problems — not to praise. Be specific and actionable.

Original goal:
{goal}

What the Coder claims to have done (iteration {iteration}):
{coder_summary}

Coder's self-assessed score: {coder_score or 'unknown'}

{f'Diff summary:{chr(10)}{diff_summary}' if diff_summary else ''}

Your review instructions:
1. Read the actual workspace files — do NOT trust the Coder's summary.
2. Look for:
   - Logic errors or incorrect implementations
   - Missing edge cases or error handling
   - Security vulnerabilities (see SAST checklist below)
   - Gaps in test coverage for the changes
   - Breaking changes to existing functionality
   - Style/lint issues the Coder missed
3. **SAST Security Checklist** — explicitly check for each of these:
   - [ ] **Injection**: SQL injection, command injection (os.system, subprocess with user input, child_process), template injection
   - [ ] **Hardcoded Secrets**: API keys, passwords, tokens, private keys embedded in source code
   - [ ] **Path Traversal**: User-controlled paths used in file operations without sanitization (../../)
   - [ ] **Arbitrary Code Execution**: eval(), exec(), pickle.load(), yaml.unsafe_load(), Function() constructor
   - [ ] **Insecure Deserialization**: Deserializing untrusted data (pickle, yaml.load, JSON.parse of user input into eval)
   - [ ] **Data Exfiltration**: Unexpected HTTP requests, socket connections, or file writes to external paths
   - [ ] **Destructive Operations**: rm -rf, shutil.rmtree, fs.rmSync on user-controlled paths
   - [ ] **Insecure Crypto**: Hardcoded IVs, weak algorithms (MD5/SHA1 for security), Math.random() for secrets
   - [ ] **Missing Auth/Authz**: New endpoints or functions that skip authentication or authorization checks
   - [ ] **Sensitive Data Exposure**: Logging passwords/tokens, returning secrets in API responses
4. For each finding, report severity, category, file, and a concrete suggestion.
5. Be honest about overall quality. If the work is good, say so.
6. Do NOT make any code changes yourself — report only.

At the end of your reply, emit:
<CRITIC_REPORT>
{{
  "overall_quality": "good|acceptable|needs_work|poor",
  "confidence": 0.8,
  "summary": "one-paragraph assessment",
  "findings": [
    {{
      "severity": "low|medium|high|critical",
      "category": "logic_error|edge_case|security|test_gap|style|performance",
      "file_path": "path/to/file.py",
      "line_hint": "near line 42",
      "description": "what's wrong",
      "suggestion": "how to fix it"
    }}
  ]
}}
</CRITIC_REPORT>"""


# ---------------------------------------------------------------------------
# Critic response parsing
# ---------------------------------------------------------------------------

_CRITIC_REPORT_RE = re.compile(r"<CRITIC_REPORT>\s*(.*?)\s*</CRITIC_REPORT>", re.IGNORECASE | re.DOTALL)


def parse_critic_report(response_text: str) -> CriticReport | None:
    """Extract a CriticReport from the Critic's response."""
    matches = _CRITIC_REPORT_RE.findall(response_text or '')
    if not matches:
        return None
    payload = matches[-1].strip()
    if payload.startswith('```'):
        lines = payload.splitlines()
        if len(lines) >= 3:
            payload = '\n'.join(lines[1:-1]).strip()
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, ValueError):
        return None

    # Validate required fields exist
    if not isinstance(data, dict):
        return None
    if 'findings' not in data and 'overall_quality' not in data:
        return None

    findings = []
    for item in data.get('findings', []):
        findings.append(CriticFinding(
            severity=str(item.get('severity', 'medium')).lower(),
            category=str(item.get('category', '')),
            file_path=str(item.get('file_path', '')),
            line_hint=str(item.get('line_hint', '')),
            description=str(item.get('description', '')),
            suggestion=str(item.get('suggestion', '')),
        ))

    return CriticReport(
        findings=findings,
        summary=str(data.get('summary', '')),
        overall_quality=str(data.get('overall_quality', 'unknown')).lower(),
        confidence=float(data.get('confidence', 0.5)),
    )


# ---------------------------------------------------------------------------
# Merge critic findings into coder baton
# ---------------------------------------------------------------------------

def build_fix_baton_from_critic(report: CriticReport, original_goal: str) -> str:
    """Turn Critic findings into a concrete fix-it baton for the Coder."""
    if not report.findings:
        return ''

    lines = [
        'A code review found the following issues. Fix them before claiming done:',
        '',
    ]

    high_findings = [f for f in report.findings if f.severity in ('critical', 'high')]
    other_findings = [f for f in report.findings if f.severity not in ('critical', 'high')]

    for f in high_findings:
        file_hint = f' in {f.file_path}' if f.file_path else ''
        line_hint = f' ({f.line_hint})' if f.line_hint else ''
        lines.append(f'[{f.severity.upper()}] {f.description}{file_hint}{line_hint}')
        if f.suggestion:
            lines.append(f'  -> Fix: {f.suggestion}')

    if other_findings:
        lines.append('')
        lines.append(f'Plus {len(other_findings)} lower-severity issues:')
        for f in other_findings[:5]:
            file_hint = f' in {f.file_path}' if f.file_path else ''
            lines.append(f'  [{f.severity}] {f.description}{file_hint}')
            if f.suggestion:
                lines.append(f'    -> {f.suggestion}')

    lines.extend([
        '',
        'After fixing, run all tests and lint, then emit OPERATOR_STATE with an honest score.',
        f'Original goal: {original_goal}',
    ])

    return '\n'.join(lines)


def should_run_critic(
    iteration: int,
    score: int | None,
    target_score: int,
    run_critic_after: int = 1,
) -> bool:
    """Decide whether to invoke the Critic after this iteration."""
    if iteration < run_critic_after:
        return False
    # Run critic if score is above a threshold (coder claims near-done)
    if score is not None and score >= target_score - 15:
        return True
    # Run critic every 2 iterations as a check
    return iteration % 2 == 0


def render_critic_summary(report: CriticReport) -> str:
    """Human-readable summary of the critic report."""
    if not report:
        return 'No critic report available.'
    lines = [
        f'Critic assessment: {report.overall_quality} (confidence={report.confidence:.0%})',
        f'Summary: {report.summary}',
        f'Findings: {len(report.findings)} total',
    ]
    severity_counts: dict[str, int] = {}
    for f in report.findings:
        severity_counts[f.severity] = severity_counts.get(f.severity, 0) + 1
    if severity_counts:
        lines.append('  ' + ', '.join(f'{k}={v}' for k, v in sorted(severity_counts.items())))
    return '\n'.join(lines)
