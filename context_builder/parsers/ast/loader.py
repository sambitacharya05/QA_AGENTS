"""
Tree-sitter language loader.

Uses individual per-language wheels (tree-sitter-java, tree-sitter-typescript,
tree-sitter-python, tree-sitter-go) because tree-sitter-languages does not yet
ship wheels for Python 3.12+. All grammars are compiled into those wheels —
no network access at runtime.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from tree_sitter import Language, Parser

log = logging.getLogger(__name__)

SUPPORTED_LANGS = {"java", "typescript", "tsx", "javascript", "python", "go"}


@lru_cache(maxsize=None)
def language_for(language: str) -> Language:
    """Return (and cache) the tree-sitter Language for *language*."""
    if language not in SUPPORTED_LANGS:
        raise ValueError(f"Unsupported language: {language!r}. Supported: {SUPPORTED_LANGS}")

    try:
        if language == "java":
            import tree_sitter_java as _lib
            return Language(_lib.language())
        elif language in ("typescript", "tsx"):
            import tree_sitter_typescript as _lib
            return Language(_lib.language_typescript())
        elif language in ("javascript",):
            import tree_sitter_typescript as _lib
            # tsx grammar is a superset of JavaScript — adequate for routing extraction
            return Language(_lib.language_tsx())
        elif language == "python":
            import tree_sitter_python as _lib
            return Language(_lib.language())
        elif language == "go":
            import tree_sitter_go as _lib
            return Language(_lib.language())
    except ImportError as exc:
        raise ImportError(
            f"tree-sitter grammar for '{language}' is not installed. "
            f"Run: pip install tree-sitter-{language}"
        ) from exc


@lru_cache(maxsize=None)
def parser_for(language: str) -> Parser:
    """Return (and cache) a tree-sitter Parser configured for *language*."""
    lang = language_for(language)
    return Parser(lang)
