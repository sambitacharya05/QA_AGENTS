"""
Path containment helpers for the Context Builder MCP server.

The MCP server accepts caller-supplied path strings (workspace paths, source
file metadata, etc.).  Without validation, a malformed tool call carrying
``source_file: "../../../etc/passwd"`` would cause the server to open that
file and write its contents into the graph.

``ensure_within_workspace`` resolves the candidate path and asserts it stays
inside the workspace root.  Every code path that ``open()``s a caller-supplied
path must route through this function first.
"""

import os
from typing import Optional


class PathTraversalError(ValueError):
    """Raised when a caller-supplied path resolves outside the workspace root."""


def ensure_within_workspace(candidate: str, workspace_path: str) -> str:
    """Resolve *candidate* and verify it stays inside *workspace_path*.

    Parameters
    ----------
    candidate:
        A path string supplied by the MCP tool caller.  May be absolute or
        relative (relative paths are interpreted as relative to *workspace_path*).
    workspace_path:
        The absolute path of the active workspace root.

    Returns
    -------
    str
        The normalised absolute path of *candidate*.

    Raises
    ------
    PathTraversalError
        If *candidate* resolves to a location outside *workspace_path*.
    """
    workspace_abs = os.path.realpath(os.path.abspath(workspace_path))

    if os.path.isabs(candidate):
        resolved = os.path.realpath(candidate)
    else:
        resolved = os.path.realpath(os.path.join(workspace_abs, candidate))

    try:
        common = os.path.commonpath([resolved, workspace_abs])
    except ValueError:
        # Different drive letters on Windows
        raise PathTraversalError(
            f"Path {candidate!r} is on a different drive than workspace {workspace_abs!r}"
        )

    if common != workspace_abs:
        raise PathTraversalError(
            f"Path {candidate!r} resolves to {resolved!r}, which is outside "
            f"the workspace root {workspace_abs!r}"
        )

    return resolved


def to_workspace_rel(path: str, workspace_path: str) -> str:
    """Return *path* as a POSIX-style path relative to *workspace_path*.

    Calls :func:`ensure_within_workspace` first, so this also validates the
    path before relativising it.
    """
    abs_path = ensure_within_workspace(path, workspace_path)
    workspace_abs = os.path.realpath(os.path.abspath(workspace_path))
    return os.path.relpath(abs_path, workspace_abs).replace("\\", "/")


def is_safe_workspace_path(candidate: str, workspace_path: str) -> bool:
    """Return ``True`` if *candidate* is safely contained within *workspace_path*.

    A non-raising convenience wrapper around :func:`ensure_within_workspace`.
    """
    try:
        ensure_within_workspace(candidate, workspace_path)
        return True
    except (PathTraversalError, ValueError):
        return False
