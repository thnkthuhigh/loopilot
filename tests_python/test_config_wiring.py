"""Tests for new config fields, validation sanitization, and config→module wiring."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from copilot_operator.config import OperatorConfig, load_config
from copilot_operator.stop_controller import build_stop_controller_config
from copilot_operator.validation import _sanitize_command

# ── validation sanitization ─────────────────────────────────────────

class TestSanitizeCommand(unittest.TestCase):
    def test_normal_command(self):
        self.assertEqual(_sanitize_command("python -m pytest"), "python -m pytest")

    def test_strips_whitespace(self):
        self.assertEqual(_sanitize_command("  npm test  "), "npm test")

    def test_empty_returns_empty(self):
        self.assertEqual(_sanitize_command(""), "")
        self.assertEqual(_sanitize_command("   "), "")

    def test_null_bytes_raise(self):
        with self.assertRaises(ValueError) as ctx:
            _sanitize_command("echo\x00bad")
        self.assertIn("null bytes", str(ctx.exception))

    def test_too_long_raises(self):
        with self.assertRaises(ValueError) as ctx:
            _sanitize_command("x" * 5000)
        self.assertIn("4096", str(ctx.exception))

    def test_max_length_ok(self):
        cmd = "x" * 4096
        self.assertEqual(_sanitize_command(cmd), cmd)


# ── new OperatorConfig fields ───────────────────────────────────────

class TestNewConfigFields(unittest.TestCase):
    def _make_config(self, **kwargs):
        return OperatorConfig(workspace=Path("."), **kwargs)

    def test_stop_controller_defaults(self):
        cfg = self._make_config()
        self.assertEqual(cfg.stop_no_progress_iterations, 2)
        self.assertEqual(cfg.stop_score_floor, 20)
        self.assertEqual(cfg.stop_score_floor_iterations, 3)

    def test_repo_map_defaults(self):
        cfg = self._make_config()
        self.assertEqual(cfg.repo_map_max_chars, 6000)
        self.assertEqual(cfg.repo_map_max_files, 60)

    def test_snapshot_defaults(self):
        cfg = self._make_config()
        self.assertEqual(cfg.max_snapshots, 10)

    def test_promotion_defaults(self):
        cfg = self._make_config()
        self.assertEqual(cfg.promotion_trap_repeat, 2)
        self.assertEqual(cfg.promotion_validation_confirm, 2)
        self.assertEqual(cfg.promotion_strategy_success, 3)

    def test_custom_values(self):
        cfg = self._make_config(
            stop_no_progress_iterations=5,
            stop_score_floor=30,
            repo_map_max_chars=3000,
            max_snapshots=5,
            promotion_trap_repeat=4,
        )
        self.assertEqual(cfg.stop_no_progress_iterations, 5)
        self.assertEqual(cfg.stop_score_floor, 30)
        self.assertEqual(cfg.repo_map_max_chars, 3000)
        self.assertEqual(cfg.max_snapshots, 5)
        self.assertEqual(cfg.promotion_trap_repeat, 4)


# ── config → stop controller wiring ─────────────────────────────────

class TestBuildStopControllerConfig(unittest.TestCase):
    def test_wires_from_operator_config(self):
        cfg = OperatorConfig(
            workspace=Path("."),
            stop_no_progress_iterations=4,
            stop_score_floor=30,
            stop_score_floor_iterations=5,
            max_task_seconds=600,
            escalation_policy="hard",
        )
        sc = build_stop_controller_config(cfg)
        self.assertEqual(sc.no_progress_max_iterations, 4)
        self.assertEqual(sc.score_floor, 30)
        self.assertEqual(sc.score_floor_max_iterations, 5)
        self.assertEqual(sc.task_timeout_seconds, 600)
        self.assertEqual(sc.default_escalation, "hard")

    def test_defaults_when_attrs_missing(self):
        # Simulates legacy config without new fields
        mock_cfg = MagicMock(spec=[])
        sc = build_stop_controller_config(mock_cfg)
        self.assertEqual(sc.no_progress_max_iterations, 2)
        self.assertEqual(sc.score_floor, 20)
        self.assertEqual(sc.score_floor_max_iterations, 3)


# ── YAML config loading with new fields ──────────────────────────────

class TestLoadConfigNewFields(unittest.TestCase):
    def test_yaml_with_new_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            cfg_file = ws / "copilot-operator.yml"
            cfg_file.write_text(
                "workspace: .\n"
                "stopNoProgressIterations: 5\n"
                "stopScoreFloor: 25\n"
                "repoMapMaxChars: 4000\n"
                "repoMapMaxFiles: 30\n"
                "maxSnapshots: 7\n"
                "promotionTrapRepeat: 3\n",
                encoding="utf-8",
            )
            cfg = load_config(cfg_file)
            self.assertEqual(cfg.stop_no_progress_iterations, 5)
            self.assertEqual(cfg.stop_score_floor, 25)
            self.assertEqual(cfg.repo_map_max_chars, 4000)
            self.assertEqual(cfg.repo_map_max_files, 30)
            self.assertEqual(cfg.max_snapshots, 7)
            self.assertEqual(cfg.promotion_trap_repeat, 3)

    def test_yaml_snake_case_aliases(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            cfg_file = ws / "copilot-operator.yml"
            cfg_file.write_text(
                "workspace: .\n"
                "stop_no_progress_iterations: 4\n"
                "repo_map_max_chars: 5000\n"
                "max_snapshots: 8\n",
                encoding="utf-8",
            )
            cfg = load_config(cfg_file)
            self.assertEqual(cfg.stop_no_progress_iterations, 4)
            self.assertEqual(cfg.repo_map_max_chars, 5000)
            self.assertEqual(cfg.max_snapshots, 8)


if __name__ == "__main__":
    unittest.main()
