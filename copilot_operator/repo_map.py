"""Repo Map — lightweight AST-based codebase index for context injection.

Builds a compact symbol map of the workspace (classes, functions, exports)
that can be injected into Copilot prompts so the LLM understands the codebase
structure without reading every file.

Inspired by Aider's repo map, but implemented with stdlib only (ast + regex),
no tree-sitter dependency required.
"""

from __future__ import annotations

__all__ = [
    'FileSymbols',
    'RepoMap',
    'build_repo_map',
    'render_repo_map_for_prompt',
]

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .logging_config import get_logger

logger = get_logger('repo_map')

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Source file extensions to include in the map
_SOURCE_EXTENSIONS: dict[str, str] = {
    '.py': 'python',
    '.js': 'javascript',
    '.ts': 'typescript',
    '.jsx': 'javascript',
    '.tsx': 'typescript',
    '.go': 'go',
    '.rs': 'rust',
    '.rb': 'ruby',
    '.java': 'java',
    '.cs': 'csharp',
    '.cpp': 'cpp',
    '.c': 'c',
    '.h': 'c',
    '.hpp': 'cpp',
    '.swift': 'swift',
    '.kt': 'kotlin',
    '.php': 'php',
    '.vue': 'vue',
    '.svelte': 'svelte',
}

_IGNORE_DIRS = frozenset({
    '__pycache__', '.git', 'node_modules', '.venv', 'venv', 'env',
    'dist', 'build', 'out', '.next', '.nuxt', 'target', 'vendor',
    'coverage', '.pytest_cache', '.mypy_cache', '.ruff_cache',
    '.copilot-operator', '.github',
})

_IGNORE_FILES = frozenset({'package-lock.json', 'yarn.lock', 'poetry.lock', 'uv.lock'})

# Max chars of map content to emit (prevents context bloat)
_DEFAULT_MAX_CHARS = 6000
_DEFAULT_MAX_FILES = 60


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class FileSymbols:
    path: str          # workspace-relative path
    language: str
    classes: list[str] = field(default_factory=list)
    functions: list[str] = field(default_factory=list)
    exports: list[str] = field(default_factory=list)
    line_count: int = 0

    def is_empty(self) -> bool:
        return not self.classes and not self.functions and not self.exports


@dataclass
class RepoMap:
    files: list[FileSymbols] = field(default_factory=list)
    total_files_scanned: int = 0
    skipped_files: int = 0
    workspace: str = ''

    @property
    def symbol_count(self) -> int:
        return sum(
            len(f.classes) + len(f.functions) + len(f.exports)
            for f in self.files
        )


# ---------------------------------------------------------------------------
# Language parsers
# ---------------------------------------------------------------------------

def _parse_python(path: Path) -> FileSymbols:
    """Extract classes and functions from a Python file using ast."""
    lang = 'python'
    classes: list[str] = []
    functions: list[str] = []

    try:
        source = path.read_text(encoding='utf-8', errors='replace')
        tree = ast.parse(source, filename=str(path))
    except (OSError, SyntaxError):
        return FileSymbols(path='', language=lang)

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            classes.append(node.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # Only top-level and class methods (depth 1 or 2)
            functions.append(node.name)

    lines = source.count('\n') + 1
    return FileSymbols(path='', language=lang, classes=classes, functions=functions, line_count=lines)


# Regex patterns keyed by language
_PATTERNS: dict[str, dict[str, str]] = {
    'javascript': {
        'class': r'(?:^|\n)\s*(?:export\s+)?class\s+(\w+)',
        'function': r'(?:^|\n)\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)',
        'arrow': r'(?:^|\n)\s*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?\(',
    },
    'typescript': {
        'class': r'(?:^|\n)\s*(?:export\s+)?(?:abstract\s+)?class\s+(\w+)',
        'function': r'(?:^|\n)\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)',
        'interface': r'(?:^|\n)\s*(?:export\s+)?interface\s+(\w+)',
        'type': r'(?:^|\n)\s*(?:export\s+)?type\s+(\w+)\s*=',
        'arrow': r'(?:^|\n)\s*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?\(',
    },
    'go': {
        'function': r'(?:^|\n)func\s+(?:\(\w+\s+\*?\w+\)\s+)?(\w+)\s*\(',
        'struct': r'(?:^|\n)type\s+(\w+)\s+struct',
        'interface': r'(?:^|\n)type\s+(\w+)\s+interface',
    },
    'rust': {
        'function': r'(?:^|\n)\s*(?:pub\s+)?(?:async\s+)?fn\s+(\w+)',
        'struct': r'(?:^|\n)\s*(?:pub\s+)?struct\s+(\w+)',
        'trait': r'(?:^|\n)\s*(?:pub\s+)?trait\s+(\w+)',
        'impl': r'(?:^|\n)\s*impl(?:<[^>]+>)?\s+(\w+)',
    },
    'ruby': {
        'class': r'(?:^|\n)\s*class\s+(\w+)',
        'function': r'(?:^|\n)\s*def\s+(\w+)',
        'module': r'(?:^|\n)\s*module\s+(\w+)',
    },
    'java': {
        'class': r'(?:^|\n)\s*(?:public\s+|private\s+|protected\s+)?(?:abstract\s+)?class\s+(\w+)',
        'interface': r'(?:^|\n)\s*(?:public\s+)?interface\s+(\w+)',
        'method': r'(?:^|\n)\s*(?:public|private|protected|static)[\w\s]+\s+(\w+)\s*\(',
    },
    'csharp': {
        'class': r'(?:^|\n)\s*(?:public\s+|private\s+|protected\s+|internal\s+)?(?:abstract\s+|sealed\s+)?class\s+(\w+)',
        'interface': r'(?:^|\n)\s*(?:public\s+)?interface\s+(\w+)',
        'method': r'(?:^|\n)\s*(?:public|private|protected|internal|static|override|virtual)[\w\s<>]+\s+(\w+)\s*\(',
    },
}

# Map multiple language keys → same pattern set
_ALIAS: dict[str, str] = {
    'jsx': 'javascript',
    'tsx': 'typescript',
    'vue': 'typescript',
    'svelte': 'javascript',
    'kotlin': 'java',
    'swift': 'java',
    'php': 'javascript',
    'cpp': 'rust',
    'c': 'rust',
    'h': 'rust',
    'hpp': 'rust',
}


def _parse_with_regex(path: Path, language: str) -> FileSymbols:
    """Extract symbols using regex for non-Python languages."""
    lang = _ALIAS.get(language, language)
    patterns = _PATTERNS.get(lang, {})

    try:
        source = path.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return FileSymbols(path='', language=language)

    classes: list[str] = []
    functions: list[str] = []
    exports: list[str] = []

    for kind, pattern in patterns.items():
        matches = re.findall(pattern, source, re.MULTILINE)
        if kind in ('class', 'struct', 'interface', 'type', 'trait', 'module', 'impl'):
            classes.extend(m for m in matches if m and not m.startswith('_'))
        elif kind in ('function', 'method', 'arrow'):
            functions.extend(m for m in matches if m and not m.startswith('_'))
        else:
            exports.extend(m for m in matches if m)

    lines = source.count('\n') + 1
    return FileSymbols(
        path='',
        language=language,
        classes=list(dict.fromkeys(classes))[:20],   # dedup, cap per-type
        functions=list(dict.fromkeys(functions))[:30],
        exports=list(dict.fromkeys(exports))[:10],
        line_count=lines,
    )


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------

def _iter_source_files(workspace: Path, max_files: int) -> list[tuple[Path, str]]:
    """Walk workspace, return (path, language) tuples for source files."""
    results: list[tuple[Path, str]] = []
    try:
        for entry in sorted(workspace.rglob('*')):
            if len(results) >= max_files * 3:
                break
            # Skip ignored directory trees
            if any(part in _IGNORE_DIRS for part in entry.parts):
                continue
            if not entry.is_file():
                continue
            if entry.name in _IGNORE_FILES:
                continue
            ext = entry.suffix.lower()
            lang = _SOURCE_EXTENSIONS.get(ext)
            if lang:
                results.append((entry, lang))
    except (PermissionError, OSError):
        pass
    return results[:max_files * 3]


def _score_file(sym: FileSymbols, goal_keywords: frozenset[str]) -> int:
    """Score a file's relevance to the goal (higher = more relevant)."""
    score = 0
    # More symbols → higher base score
    score += len(sym.classes) * 3 + len(sym.functions) * 2 + len(sym.exports)
    # Keyword match in path or symbol names
    path_lower = sym.path.lower()
    all_names = ' '.join(sym.classes + sym.functions + sym.exports).lower()
    for kw in goal_keywords:
        if kw in path_lower:
            score += 5
        if kw in all_names:
            score += 2
    return score


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_repo_map(
    workspace: Path,
    goal: str = '',
    max_files: int = _DEFAULT_MAX_FILES,
    max_chars: int = _DEFAULT_MAX_CHARS,
) -> RepoMap:
    """Scan workspace and build a compact symbol map.

    Args:
        workspace: Root directory of the project.
        goal: Goal text used to score file relevance (more relevant files first).
        max_files: Maximum number of files to include in the map.
        max_chars: Maximum characters of rendered map (prevents context bloat).

    Returns:
        A RepoMap with ranked FileSymbols.
    """
    repo_map = RepoMap(workspace=str(workspace))
    raw_files = _iter_source_files(workspace, max_files * 2)
    repo_map.total_files_scanned = len(raw_files)

    # Extract symbols
    all_symbols: list[FileSymbols] = []
    for path, lang in raw_files:
        try:
            rel = str(path.relative_to(workspace)).replace('\\', '/')
        except ValueError:
            rel = path.name

        if lang == 'python':
            sym = _parse_python(path)
        else:
            sym = _parse_with_regex(path, lang)

        sym.path = rel
        if not sym.is_empty():
            all_symbols.append(sym)
        else:
            repo_map.skipped_files += 1

    # Rank by goal relevance
    goal_keywords = frozenset(
        w.lower() for w in re.split(r'\W+', goal) if len(w) > 3
    )
    all_symbols.sort(key=lambda s: _score_file(s, goal_keywords), reverse=True)

    # Fit within char budget
    total_chars = 0
    for sym in all_symbols[:max_files]:
        rendered = _render_file_symbols(sym)
        if total_chars + len(rendered) > max_chars:
            repo_map.skipped_files += 1
            continue
        repo_map.files.append(sym)
        total_chars += len(rendered)

    logger.debug(
        'Repo map: %d files, %d symbols, %d skipped, ~%d chars',
        len(repo_map.files), repo_map.symbol_count, repo_map.skipped_files, total_chars,
    )
    return repo_map


def _render_file_symbols(sym: FileSymbols) -> str:
    """Render a single file's symbols as a compact string."""
    parts: list[str] = []
    if sym.classes:
        parts.append('classes: ' + ', '.join(sym.classes[:8]))
    if sym.functions:
        parts.append('fns: ' + ', '.join(sym.functions[:12]))
    if sym.exports:
        parts.append('exports: ' + ', '.join(sym.exports[:6]))
    body = ' | '.join(parts)
    lines_info = f' ({sym.line_count}L)' if sym.line_count else ''
    return f'{sym.path}{lines_info}: {body}\n'


def render_repo_map_for_prompt(repo_map: RepoMap, max_chars: int = _DEFAULT_MAX_CHARS) -> str:
    """Render a RepoMap as a compact prompt section."""
    if not repo_map.files:
        return ''
    lines: list[str] = [
        f'Codebase map ({len(repo_map.files)} files, {repo_map.symbol_count} symbols):',
    ]
    total = 0
    for sym in repo_map.files:
        rendered = _render_file_symbols(sym)
        if total + len(rendered) > max_chars:
            lines.append(f'... and {repo_map.skipped_files} more files')
            break
        lines.append(rendered.rstrip())
        total += len(rendered)
    return '\n'.join(lines)


def get_repo_map_stats(repo_map: RepoMap) -> dict[str, Any]:
    """Return summary statistics for the repo map (useful for doctor/status)."""
    lang_counts: dict[str, int] = {}
    for sym in repo_map.files:
        lang_counts[sym.language] = lang_counts.get(sym.language, 0) + 1
    return {
        'files_in_map': len(repo_map.files),
        'total_scanned': repo_map.total_files_scanned,
        'skipped': repo_map.skipped_files,
        'total_symbols': repo_map.symbol_count,
        'languages': lang_counts,
    }
