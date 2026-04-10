"""Structured logging for Copilot Operator.

Provides a unified logger with consistent formatting across all modules.
Supports both console and file output.
"""

from __future__ import annotations

__all__ = [
    'LOG_FORMAT',
    'LOG_DATE_FORMAT',
    'setup_logging',
    'get_logger',
]

import logging
import sys
from pathlib import Path

_CONFIGURED = False

LOG_FORMAT = '%(asctime)s [%(levelname)s] %(name)s: %(message)s'
LOG_DATE_FORMAT = '%Y-%m-%dT%H:%M:%S'


def setup_logging(
    level: str = 'INFO',
    log_file: Path | None = None,
    quiet: bool = False,
) -> logging.Logger:
    """Configure the root copilot_operator logger.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR).
        log_file: Optional file path for log output.
        quiet: If True, suppress console output (file-only).

    Returns:
        The root ``copilot_operator`` logger.
    """
    global _CONFIGURED
    root = logging.getLogger('copilot_operator')

    if _CONFIGURED:
        return root

    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)

    if not quiet:
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(formatter)
        root.addHandler(console)

    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(str(log_file), encoding='utf-8')
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    _CONFIGURED = True
    return root


def get_logger(name: str) -> logging.Logger:
    """Get a child logger under the copilot_operator namespace."""
    return logging.getLogger(f'copilot_operator.{name}')
