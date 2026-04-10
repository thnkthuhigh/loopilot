"""Pre-validation diff security scanner.

Scans AI-generated diffs for dangerous patterns BEFORE running test/lint/build
commands. This mitigates the risk of arbitrary code execution: if the AI writes
malicious code (e.g. ``os.system("rm -rf /")``) into a test file, the scanner
catches it before pytest/jest imports and executes it.

The scanner uses regex-based pattern matching — not a full AST parser — because
it needs to work across all languages (Python, JS, TS, Go, Rust, etc.) and the
goal is to catch *obvious* dangerous patterns, not replace a real SAST tool.
"""

from __future__ import annotations

__all__ = [
    'DiffFinding',
    'ScanResult',
    'scan_diff_for_threats',
    'scan_workspace_diff',
]

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .logging_config import get_logger

logger = get_logger('diff_scan')


@dataclass(slots=True)
class DiffFinding:
    """A single dangerous pattern detected in a diff."""
    severity: str = 'medium'     # low, medium, high, critical
    category: str = ''           # ace, exfiltration, destructive, secrets, suspicious
    pattern_name: str = ''       # human-readable name of the matched pattern
    file_path: str = ''          # file where the pattern was found
    line_content: str = ''       # the actual line content
    description: str = ''        # explanation of the risk


@dataclass(slots=True)
class ScanResult:
    """Aggregated result of scanning a diff."""
    findings: list[DiffFinding] = field(default_factory=list)
    files_scanned: int = 0
    blocked: bool = False        # True if critical findings → should NOT run validation

    @property
    def has_critical(self) -> bool:
        return any(f.severity == 'critical' for f in self.findings)

    @property
    def has_high(self) -> bool:
        return any(f.severity in ('critical', 'high') for f in self.findings)

    def render(self) -> str:
        if not self.findings:
            return 'Diff security scan: clean.'
        lines = [f'Diff security scan: {len(self.findings)} finding(s)']
        for f in self.findings:
            lines.append(f'  [{f.severity.upper()}] {f.pattern_name}: {f.description}')
            if f.file_path:
                lines.append(f'    in {f.file_path}')
            if f.line_content:
                lines.append(f'    > {f.line_content[:200]}')
        if self.blocked:
            lines.append('  ⛔ BLOCKED — validation skipped due to critical findings.')
        return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Pattern definitions
# ---------------------------------------------------------------------------

# Each pattern: (name, category, severity, regex, description)
# Patterns match against ADDED lines in the diff (lines starting with +)
_THREAT_PATTERNS: list[tuple[str, str, str, re.Pattern[str], str]] = [
    # ── Arbitrary Code Execution ─────────────────────────────────
    ('shell_exec_python', 'ace', 'critical',
     re.compile(r'\b(?:os\.system|os\.popen|subprocess\.call|subprocess\.run|subprocess\.Popen)\s*\(', re.IGNORECASE),
     'Direct shell execution — could run arbitrary commands'),
    ('exec_eval_python', 'ace', 'high',
     re.compile(r'\b(?:exec|eval|compile)\s*\(', re.IGNORECASE),
     'Dynamic code execution via exec/eval/compile'),
    ('child_process_js', 'ace', 'critical',
     re.compile(r'(?:child_process|execSync|spawnSync|exec)\s*[\(\.]', re.IGNORECASE),
     'Node.js child process execution'),
    ('shell_command_generic', 'ace', 'critical',
     re.compile(r'(?:rm\s+-rf\s+/|rm\s+-rf\s+~|rmdir\s+/s|del\s+/f\s+/s|format\s+c:)', re.IGNORECASE),
     'Destructive shell command in code'),

    # ── Data Exfiltration ────────────────────────────────────────
    ('curl_wget', 'exfiltration', 'high',
     re.compile(r'\b(?:curl|wget|Invoke-WebRequest|Invoke-RestMethod)\s+', re.IGNORECASE),
     'External HTTP request — potential data exfiltration'),
    ('http_request_python', 'exfiltration', 'medium',
     re.compile(r'\b(?:urllib\.request\.urlopen|requests\.(?:get|post|put|delete)|httpx\.(?:get|post))\s*\(', re.IGNORECASE),
     'HTTP request in code — verify target URL'),
    ('fetch_api', 'exfiltration', 'medium',
     re.compile(r'\bfetch\s*\(\s*[\'"`]https?://', re.IGNORECASE),
     'Fetch API call to external URL'),
    ('socket_bind', 'exfiltration', 'high',
     re.compile(r'\.(?:bind|listen)\s*\(\s*[\(\[\'"]?\s*(?:0\.0\.0\.0|[:]{2})', re.IGNORECASE),
     'Network socket binding — could open backdoor port'),

    # ── Filesystem Destruction ───────────────────────────────────
    ('rmtree_python', 'destructive', 'critical',
     re.compile(r'\bshutil\.rmtree\s*\(', re.IGNORECASE),
     'Recursive directory deletion'),
    ('unlink_glob', 'destructive', 'high',
     re.compile(r'(?:Path|os)\.(?:unlink|remove|rmdir)', re.IGNORECASE),
     'File/directory deletion'),
    ('fs_unlink_js', 'destructive', 'high',
     re.compile(r'fs\.(?:rmSync|unlinkSync|rmdirSync|rm)\s*\(', re.IGNORECASE),
     'Node.js filesystem deletion'),

    # ── Hardcoded Secrets ────────────────────────────────────────
    ('hardcoded_key', 'secrets', 'high',
     re.compile(r'(?:api[_-]?key|secret[_-]?key|password|token|auth)\s*[=:]\s*[\'"][A-Za-z0-9+/=_-]{16,}[\'"]', re.IGNORECASE),
     'Possible hardcoded secret/API key'),
    ('private_key', 'secrets', 'critical',
     re.compile(r'-----BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY-----'),
     'Private key embedded in code'),
    ('aws_key', 'secrets', 'critical',
     re.compile(r'AKIA[0-9A-Z]{16}'),
     'AWS access key ID detected'),

    # ── Suspicious Patterns ──────────────────────────────────────
    ('base64_decode', 'suspicious', 'medium',
     re.compile(r'\b(?:base64\.b64decode|atob|Buffer\.from)\s*\(', re.IGNORECASE),
     'Base64 decode — often used to hide payloads'),
    ('pickle_load', 'suspicious', 'high',
     re.compile(r'\bpickle\.(?:load|loads)\s*\(', re.IGNORECASE),
     'Pickle deserialization — arbitrary code execution risk'),
    ('yaml_unsafe', 'suspicious', 'high',
     re.compile(r'\byaml\.(?:load|unsafe_load)\s*\(', re.IGNORECASE),
     'Unsafe YAML loading — arbitrary code execution risk'),
    ('ctypes_import', 'suspicious', 'medium',
     re.compile(r'\bimport\s+ctypes\b|from\s+ctypes\s+import\b', re.IGNORECASE),
     'ctypes import — FFI access to native code'),
    ('env_override', 'suspicious', 'medium',
     re.compile(r'\bos\.environ\s*\[.*\]\s*=', re.IGNORECASE),
     'Environment variable mutation'),
]

# Files to skip scanning (test fixtures, configs, docs)
_SKIP_EXTENSIONS = frozenset({
    '.md', '.txt', '.rst', '.json', '.yml', '.yaml', '.toml',
    '.csv', '.svg', '.png', '.jpg', '.gif', '.ico', '.lock',
})


def _should_skip_file(path: str) -> bool:
    """Skip scanning non-code files."""
    lower = path.lower()
    return any(lower.endswith(ext) for ext in _SKIP_EXTENSIONS)


def _extract_added_lines(diff_text: str) -> list[tuple[str, str]]:
    """Extract (file_path, line_content) for added lines from a unified diff."""
    results: list[tuple[str, str]] = []
    current_file = ''
    for line in diff_text.splitlines():
        if line.startswith('+++ b/'):
            current_file = line[6:]
        elif line.startswith('+') and not line.startswith('+++'):
            content = line[1:]  # strip the leading +
            if content.strip():
                results.append((current_file, content))
    return results


def scan_diff_for_threats(diff_text: str) -> ScanResult:
    """Scan a unified diff string for dangerous patterns.

    Only examines *added* lines (lines starting with ``+``).
    Returns a ScanResult with findings and a blocked flag if criticals found.
    """
    if not diff_text or not diff_text.strip():
        return ScanResult()

    added_lines = _extract_added_lines(diff_text)
    files_seen: set[str] = set()
    findings: list[DiffFinding] = []
    seen_patterns: set[tuple[str, str]] = set()  # (pattern_name, file) dedup

    for file_path, line_content in added_lines:
        if _should_skip_file(file_path):
            continue
        files_seen.add(file_path)

        for name, category, severity, pattern, description in _THREAT_PATTERNS:
            if pattern.search(line_content):
                dedup_key = (name, file_path)
                if dedup_key in seen_patterns:
                    continue
                seen_patterns.add(dedup_key)
                findings.append(DiffFinding(
                    severity=severity,
                    category=category,
                    pattern_name=name,
                    file_path=file_path,
                    line_content=line_content.strip(),
                    description=description,
                ))

    result = ScanResult(
        findings=findings,
        files_scanned=len(files_seen),
        blocked=any(f.severity == 'critical' for f in findings),
    )
    return result


def scan_workspace_diff(workspace: Path) -> ScanResult:
    """Run ``git diff HEAD`` in the workspace and scan the result.

    Falls back to ``git diff`` (unstaged) if HEAD fails.
    Returns an empty ScanResult if git is not available or workspace is clean.
    """
    for diff_cmd in (['git', 'diff', 'HEAD'], ['git', 'diff']):
        try:
            proc = subprocess.run(
                diff_cmd,
                cwd=str(workspace),
                capture_output=True,
                timeout=30,
                check=False,
            )
            diff_text = (proc.stdout or b'').decode('utf-8', errors='replace')
            if diff_text.strip():
                return scan_diff_for_threats(diff_text)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            continue

    return ScanResult()
