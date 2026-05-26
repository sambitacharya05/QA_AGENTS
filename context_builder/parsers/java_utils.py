"""Shared Java source-parsing utilities.

SPEC-003 (Wave 5): extracted so both ``QafAutomationParser`` and
``JavaEndpointExtractor`` can use the same block-comment stripper and
anchored class-declaration regex without duplicating logic.
"""

import re

__all__ = ["strip_block_comments", "JAVA_CLASS_DECL_RE"]


def strip_block_comments(source: str) -> str:
    """Remove ``/* … */`` block comments (including Javadoc) from Java source.

    This is a pre-pass applied before any regex that searches for class/interface
    declarations, preventing false matches on Javadoc sentences like
    ``"Utility class providing composite actions."``.

    Two-pass approach:
    * Pass 1 removes well-formed ``/* … */`` pairs (non-greedy, correct for
      valid Java because nested block comments are not permitted by the spec).
    * Pass 2 strips any bare ``*/`` left behind when malformed or non-Java input
      contains a ``*/`` sequence inside a comment body, causing pass 1 to
      terminate early.  This second pass is a no-op for all valid Java files.
    """
    stripped = re.sub(r'/\*.*?\*/', '', source, flags=re.DOTALL)
    return re.sub(r'\*/', '', stripped)


# Anchored class-declaration regex (SPEC-003, Wave 5).
#
# Differences from the previous pattern:
#   • ``^[ \\t]*`` — anchors to line start; prevents mid-sentence matches.
#   • Full modifier list — real Java declarations carry access/modifier keywords;
#     Javadoc sentences do not.
#   • ``[A-Z][a-zA-Z0-9_]*`` — Java class names are PascalCase; rejects
#     lowercase words like ``providing`` that appear after ``class`` in prose.
JAVA_CLASS_DECL_RE = re.compile(
    r'^[ \t]*'
    r'(?:(?:public|protected|private|abstract|final|static|strictfp|sealed|non-sealed)\s+)*'
    r'(?:class|interface|enum|record)\s+'
    r'([A-Z][a-zA-Z0-9_]*)',
    re.MULTILINE,
)
