"""Tests for logging_config and vscode_chat modules."""

from __future__ import annotations

import logging
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from copilot_operator.logging_config import (
    LOG_DATE_FORMAT,
    LOG_FORMAT,
    get_logger,
    setup_logging,
)
from copilot_operator.vscode_chat import (
    VSCodeChatError,
    VSCodeChatRetryableError,
    _code_binary,
    run_code_command,
    send_chat_prompt,
    snapshot_chat_sessions,
    wait_for_session_file,
)

# ── logging_config ──────────────────────────────────────────────────

class TestGetLogger(unittest.TestCase):
    def test_returns_child_logger(self):
        logger = get_logger("test_child")
        self.assertIsInstance(logger, logging.Logger)
        self.assertEqual(logger.name, "copilot_operator.test_child")

    def test_different_names_different_loggers(self):
        a = get_logger("aaa")
        b = get_logger("bbb")
        self.assertNotEqual(a.name, b.name)


class TestSetupLogging(unittest.TestCase):
    def setUp(self):
        # Reset the _CONFIGURED flag before each test
        import copilot_operator.logging_config as lc
        self._orig = lc._CONFIGURED
        lc._CONFIGURED = False
        # Remove existing handlers
        root = logging.getLogger("copilot_operator")
        root.handlers.clear()

    def tearDown(self):
        import copilot_operator.logging_config as lc
        lc._CONFIGURED = self._orig
        root = logging.getLogger("copilot_operator")
        root.handlers.clear()

    def test_returns_logger(self):
        logger = setup_logging(level="DEBUG")
        self.assertIsInstance(logger, logging.Logger)
        self.assertEqual(logger.name, "copilot_operator")

    def test_sets_level(self):
        logger = setup_logging(level="WARNING")
        self.assertEqual(logger.level, logging.WARNING)

    def test_quiet_mode_no_console_handler(self):
        logger = setup_logging(quiet=True)
        stream_handlers = [h for h in logger.handlers if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)]
        self.assertEqual(len(stream_handlers), 0)

    def test_with_log_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "sub" / "test.log"
            logger = setup_logging(log_file=log_path)
            file_handlers = [h for h in logger.handlers if isinstance(h, logging.FileHandler)]
            self.assertTrue(len(file_handlers) >= 1)
            self.assertTrue(log_path.parent.exists())
            # Clean up handlers
            for h in file_handlers:
                h.close()

    def test_idempotent_after_configured(self):
        setup_logging(level="INFO")
        import copilot_operator.logging_config as lc
        self.assertTrue(lc._CONFIGURED)
        # Second call should return same logger without adding handlers
        root = logging.getLogger("copilot_operator")
        count_before = len(root.handlers)
        setup_logging(level="DEBUG")
        self.assertEqual(len(root.handlers), count_before)

    def test_format_constants(self):
        self.assertIn("%(levelname)s", LOG_FORMAT)
        self.assertIn("%(name)s", LOG_FORMAT)
        self.assertIn("%Y", LOG_DATE_FORMAT)


# ── vscode_chat ─────────────────────────────────────────────────────

class TestVSCodeChatErrors(unittest.TestCase):
    def test_error_hierarchy(self):
        self.assertTrue(issubclass(VSCodeChatError, RuntimeError))
        self.assertTrue(issubclass(VSCodeChatRetryableError, VSCodeChatError))

    def test_retryable_is_vscode_chat_error(self):
        err = VSCodeChatRetryableError("timeout")
        self.assertIsInstance(err, VSCodeChatError)
        self.assertEqual(str(err), "timeout")


class TestCodeBinary(unittest.TestCase):
    @patch("shutil.which", return_value=None)
    def test_raises_when_not_found(self, mock_which):
        with self.assertRaises(VSCodeChatError) as ctx:
            _code_binary()
        self.assertIn("code.cmd", str(ctx.exception))

    @patch("shutil.which", side_effect=lambda c: "/usr/bin/code" if c == "code" else None)
    def test_finds_code(self, mock_which):
        result = _code_binary()
        self.assertEqual(result, "/usr/bin/code")


class TestRunCodeCommand(unittest.TestCase):
    @patch("copilot_operator.vscode_chat._code_binary", return_value="code")
    @patch("subprocess.run")
    def test_success(self, mock_run, mock_bin):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr="")
        result = run_code_command(["--version"], cwd=Path("."))
        self.assertEqual(result.returncode, 0)

    @patch("copilot_operator.vscode_chat._code_binary", return_value="code")
    @patch("subprocess.run")
    def test_failure_raises_retryable(self, mock_run, mock_bin):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="fail")
        with self.assertRaises(VSCodeChatRetryableError):
            run_code_command(["bad"], cwd=Path("."))

    @patch("copilot_operator.vscode_chat._code_binary", return_value="code")
    @patch("subprocess.run")
    def test_retry_on_failure(self, mock_run, mock_bin):
        fail_result = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="err")
        ok_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr="")
        mock_run.side_effect = [fail_result, ok_result]
        result = run_code_command(["x"], cwd=Path("."), retries=1, retry_delay_seconds=0)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(mock_run.call_count, 2)


class TestSnapshotChatSessions(unittest.TestCase):
    def test_empty_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = snapshot_chat_sessions(Path(tmp))
            self.assertEqual(result, {})

    def test_nonexistent_dir(self):
        result = snapshot_chat_sessions(Path("/nonexistent/path"))
        self.assertEqual(result, {})

    def test_finds_json_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            chat_dir = Path(tmp)
            (chat_dir / "session1.json").write_text("{}")
            (chat_dir / "session2.jsonl").write_text("{}")
            (chat_dir / "readme.txt").write_text("nope")
            result = snapshot_chat_sessions(chat_dir)
            self.assertEqual(len(result), 2)


class TestWaitForSessionFile(unittest.TestCase):
    def test_timeout_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            chat_dir = Path(tmp)
            with self.assertRaises(VSCodeChatRetryableError):
                wait_for_session_file(chat_dir, {}, timeout_seconds=0.1, poll_interval=0.05)


class TestSendChatPrompt(unittest.TestCase):
    @patch("copilot_operator.vscode_chat.run_code_command")
    @patch("copilot_operator.vscode_chat.focus_workspace")
    def test_writes_prompt_file(self, mock_focus, mock_run):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            config = MagicMock()
            config.workspace = ws
            config.mode = "agent"
            config.code_command_retries = 0
            config.code_command_retry_delay_seconds = 0

            send_chat_prompt(config, "Hello world", add_files=[Path("extra.py")])

            prompt_file = ws / ".copilot-operator" / "current-prompt.md"
            self.assertTrue(prompt_file.exists())
            self.assertEqual(prompt_file.read_text(encoding="utf-8"), "Hello world")
            mock_run.assert_called_once()
            call_args = mock_run.call_args[0][0]
            self.assertIn("--add-file", call_args)


if __name__ == "__main__":
    unittest.main()
