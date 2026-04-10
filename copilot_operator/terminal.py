"""Terminal output helpers — ANSI colour with Windows/non-TTY fallback.

Respects the NO_COLOR (https://no-color.org/) and FORCE_COLOR environment
variables.  On Windows legacy consoles, enables VT100 virtual-terminal
processing automatically.

Zero external dependencies — stdlib only.
"""

from __future__ import annotations

__all__ = [
    'green',
    'yellow',
    'red',
    'cyan',
    'blue',
    'magenta',
    'bold',
    'dim',
    'ok',
    'warn',
    'err',
    'info',
    'score_color',
    'status_badge',
    'progress_bar',
    'header_line',
]

import os
import sys

# ---------------------------------------------------------------------------
# Colour detection
# ---------------------------------------------------------------------------

def _init_colors() -> bool:
    """Return True if ANSI colour should be emitted on stderr."""
    if os.environ.get('NO_COLOR'):
        return False
    if os.environ.get('FORCE_COLOR'):
        return True
    stream = sys.stderr
    if not hasattr(stream, 'isatty') or not stream.isatty():
        return False
    if os.environ.get('TERM') in ('dumb', ''):
        return False
    # Enable ANSI VT processing on Windows legacy console
    if sys.platform == 'win32':
        try:
            import ctypes
            k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            k32.SetConsoleMode(k32.GetStdHandle(-12), 7)
        except Exception:
            pass
    return True


_COLORS: bool = _init_colors()

# ANSI escape sequences — empty strings when colour is disabled
_R    = '\033[0m'  if _COLORS else ''
_BOLD = '\033[1m'  if _COLORS else ''
_DIM  = '\033[2m'  if _COLORS else ''
_RED  = '\033[31m' if _COLORS else ''
_GRN  = '\033[32m' if _COLORS else ''
_YLW  = '\033[33m' if _COLORS else ''
_BLU  = '\033[34m' if _COLORS else ''
_MGT  = '\033[35m' if _COLORS else ''
_CYN  = '\033[36m' if _COLORS else ''
_BRED = '\033[91m' if _COLORS else ''
_BGRN = '\033[92m' if _COLORS else ''
_BYLW = '\033[93m' if _COLORS else ''
_BBLU = '\033[94m' if _COLORS else ''
_BCYN = '\033[96m' if _COLORS else ''


# ---------------------------------------------------------------------------
# Basic colour helpers
# ---------------------------------------------------------------------------

def green(s: str) -> str:   return f'{_BGRN}{s}{_R}'
def yellow(s: str) -> str:  return f'{_BYLW}{s}{_R}'
def red(s: str) -> str:     return f'{_BRED}{s}{_R}'
def cyan(s: str) -> str:    return f'{_BCYN}{s}{_R}'
def blue(s: str) -> str:    return f'{_BBLU}{s}{_R}'
def magenta(s: str) -> str: return f'{_MGT}{s}{_R}'
def bold(s: str) -> str:    return f'{_BOLD}{s}{_R}'
def dim(s: str) -> str:     return f'{_DIM}{s}{_R}'


# ---------------------------------------------------------------------------
# Semantic status helpers
# ---------------------------------------------------------------------------

def ok(msg: str) -> str:   return f'{_BGRN}[ok]{_R} {msg}'
def warn(msg: str) -> str: return f'{_BYLW}[warn]{_R} {msg}'
def err(msg: str) -> str:  return f'{_BRED}[error]{_R} {msg}'
def info(msg: str) -> str: return f'{_BCYN}[info]{_R} {msg}'


def score_color(score: int | None) -> str:
    """Return *score* as a colour-coded string (green ≥ 85, yellow ≥ 60, red < 60)."""
    if score is None:
        return dim('??')
    s = str(score)
    if score >= 85:
        return green(s)
    if score >= 60:
        return yellow(s)
    return red(s)


def status_badge(status: str) -> str:
    """Return a colour-coded status badge string."""
    _map: dict[str, object] = {
        'complete': green,
        'done':     green,
        'in_progress':    cyan,
        'needs_continue': yellow,
        'blocked':     red,
        'error':       red,
        'dry_run':     dim,
        'interrupted': yellow,
    }
    fn = _map.get(status, bold)
    return fn(status)  # type: ignore[operator]


# ---------------------------------------------------------------------------
# Progress bar
# ---------------------------------------------------------------------------

def progress_bar(current: int, total: int, width: int = 20) -> str:
    """Return a compact inline ASCII/Unicode progress bar.

    Example output: ``[████████░░░░░░░░░░░░] 4/10 (40%)``
    """
    if total <= 0:
        return f'[{"─" * width}] {current}/?'
    pct = current / total
    filled = round(width * pct)
    bar_inner = '█' * filled + '░' * (width - filled)
    bar_str = cyan(bar_inner) if _COLORS else bar_inner
    return f'[{bar_str}] {current}/{total} ({int(pct * 100)}%)'


# ---------------------------------------------------------------------------
# Header / section divider
# ---------------------------------------------------------------------------

def header_line(title: str, width: int = 60) -> str:
    """Return a bold section header line suitable for terminal output."""
    pad = max(0, width - len(title) - 4)
    line = '─' * (pad // 2)
    return bold(f'{line} {title} {line}')
