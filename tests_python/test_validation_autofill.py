"""Tests for Phase 1: Validation Auto-Fill on Init."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from copilot_operator.bootstrap import (
    _hydrate_config_validation,
    detect_and_hydrate,
    initialize_workspace,
)
from copilot_operator.repo_inspector import WorkspaceInsight


class TestHydrateConfigValidation(unittest.TestCase):
    """T1.1–T1.4: hydration of empty validation commands in copilot-operator.yml."""

    def _write_config(self, tmp: Path, tests_cmd: str = '', lint_cmd: str = '', build_cmd: str = '') -> Path:
        config = tmp / 'copilot-operator.yml'
        config.write_text(
            f"""workspace: .
validation:
  - name: tests
    command: {tests_cmd}
    required: false
  - name: lint
    command: {lint_cmd}
    required: false
  - name: build
    command: {build_cmd}
    required: false
""",
            encoding='utf-8',
        )
        return config

    def test_node_project_hydrates_npm_commands(self):
        """T1.1: init on Node project fills npm test / npm run lint."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = self._write_config(tmp_path)
            insight = WorkspaceInsight(
                ecosystem='node',
                package_manager='npm',
                package_manager_command='npm',
                validation_hints={'tests': 'npm test', 'test': 'npm test', 'lint': 'npm run lint'},
                evidence=['package.json'],
            )
            applied = _hydrate_config_validation(config_path, insight)
            self.assertEqual(applied.get('tests'), 'npm test')
            self.assertEqual(applied.get('lint'), 'npm run lint')
            # Verify file was written back
            text = config_path.read_text(encoding='utf-8')
            self.assertIn('command: npm test', text)
            self.assertIn('command: npm run lint', text)

    def test_python_project_hydrates_pytest(self):
        """T1.2: init on Python project fills pytest."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = self._write_config(tmp_path)
            insight = WorkspaceInsight(
                ecosystem='python',
                package_manager='python',
                package_manager_command='python',
                validation_hints={'tests': 'pytest', 'test': 'pytest', 'lint': 'ruff check .'},
                evidence=['pyproject.toml'],
            )
            applied = _hydrate_config_validation(config_path, insight)
            self.assertEqual(applied.get('tests'), 'pytest')
            self.assertEqual(applied.get('lint'), 'ruff check .')
            text = config_path.read_text(encoding='utf-8')
            self.assertIn('command: pytest', text)
            self.assertIn('command: ruff check .', text)

    def test_empty_dir_no_hydration(self):
        """T1.3: empty dir → commands stay empty, no crash."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = self._write_config(tmp_path)
            insight = WorkspaceInsight()  # unknown ecosystem, no hints
            applied = _hydrate_config_validation(config_path, insight)
            self.assertEqual(applied, {})
            text = config_path.read_text(encoding='utf-8')
            # commands should still be empty
            self.assertNotIn('command: npm', text)
            self.assertNotIn('command: pytest', text)

    def test_existing_commands_not_overwritten(self):
        """T1.4: existing commands are NOT overwritten by hints."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            # Pre-fill tests with a custom command
            config_path = self._write_config(tmp_path, tests_cmd='custom-test-runner')
            insight = WorkspaceInsight(
                ecosystem='node',
                package_manager='npm',
                package_manager_command='npm',
                validation_hints={'tests': 'npm test', 'test': 'npm test', 'lint': 'npm run lint'},
                evidence=['package.json'],
            )
            applied = _hydrate_config_validation(config_path, insight)
            # tests should NOT be overwritten — it already had a value
            self.assertNotIn('tests', applied)
            # lint should be hydrated since it was empty
            self.assertEqual(applied.get('lint'), 'npm run lint')
            text = config_path.read_text(encoding='utf-8')
            self.assertIn('command: custom-test-runner', text)
            self.assertIn('command: npm run lint', text)


class TestInitializeWorkspaceWithHints(unittest.TestCase):
    """Test that initialize_workspace() integrates hint detection."""

    def test_init_node_fills_validation(self):
        """Init on a directory with package.json fills validation commands."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            # Create a minimal Node project
            (tmp_path / 'package.json').write_text(
                json.dumps({'scripts': {'test': 'jest', 'lint': 'eslint .'}}),
                encoding='utf-8',
            )
            result = initialize_workspace(tmp_path, force=True)
            self.assertEqual(result['ecosystem'], 'node')
            self.assertIn('tests', result['hydratedValidation'])

    def test_init_python_fills_validation(self):
        """Init on a directory with pyproject.toml + ruff fills validation."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / 'pyproject.toml').write_text(
                '[tool.pytest.ini_options]\n', encoding='utf-8',
            )
            (tmp_path / 'ruff.toml').write_text('', encoding='utf-8')
            (tmp_path / 'tests').mkdir()
            result = initialize_workspace(tmp_path, force=True)
            self.assertEqual(result['ecosystem'], 'python')
            self.assertIn('tests', result['hydratedValidation'])
            self.assertIn('lint', result['hydratedValidation'])

    def test_init_no_detect_hints_flag(self):
        """Init with detect_hints=False skips hydration."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / 'package.json').write_text(
                json.dumps({'scripts': {'test': 'jest'}}),
                encoding='utf-8',
            )
            result = initialize_workspace(tmp_path, force=True, detect_hints=False)
            self.assertEqual(result['ecosystem'], 'unknown')
            self.assertEqual(result['hydratedValidation'], {})


class TestDetectAndHydrate(unittest.TestCase):
    """T1.5: detect-hints subcommand works standalone."""

    def test_detect_and_hydrate_node(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / 'package.json').write_text(
                json.dumps({'scripts': {'test': 'vitest', 'lint': 'eslint .'}}),
                encoding='utf-8',
            )
            # First init to create the config file
            initialize_workspace(tmp_path, force=True, detect_hints=False)
            # Then run detect-and-hydrate standalone
            result = detect_and_hydrate(tmp_path)
            self.assertEqual(result['ecosystem'], 'node')
            self.assertIn('tests', result['applied'])

    def test_detect_and_hydrate_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            initialize_workspace(tmp_path, force=True, detect_hints=False)
            result = detect_and_hydrate(tmp_path)
            self.assertEqual(result['ecosystem'], 'unknown')
            self.assertEqual(result['applied'], {})


if __name__ == '__main__':
    unittest.main()
