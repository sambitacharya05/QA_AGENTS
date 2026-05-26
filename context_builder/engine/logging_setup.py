"""
Logging configuration for the Context Builder MCP server.

IMPORTANT: stdout is reserved exclusively for MCP JSON-RPC framing.  Any
unstructured text written to stdout corrupts the protocol stream and will
cause VS Code's MCP client to disconnect.

Call ``configure_logging()`` as the very first line of ``__main__`` before
any other imports emit output.

Environment variables
---------------------
CONTEXT_BUILDER_LOG_LEVEL   : one of DEBUG / INFO / WARNING / ERROR (default INFO)
CONTEXT_BUILDER_STDIO_GUARD : set to "0" to disable the stdout→stderr redirect
                               shim (useful when running the server interactively
                               outside of VS Code)
"""

import logging
import os
import sys


def configure_logging() -> None:
    """Configure the root logger to write ONLY to stderr.

    All handlers are cleared first so that third-party libraries that
    install stdout handlers do not corrupt the MCP stdio stream.

    A defensive shim replaces ``sys.stdout`` so that any stray ``print``
    or direct ``sys.stdout.write`` call is transparently redirected to
    stderr rather than breaking the protocol.
    """
    level_name = os.environ.get("CONTEXT_BUILDER_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)

    # Remove every existing handler — some libraries (e.g. uvicorn, httpx)
    # install stdout handlers by default.
    for handler in list(root.handlers):
        root.removeHandler(handler)

    stderr_handler = logging.StreamHandler(stream=sys.stderr)
    stderr_handler.setFormatter(
        logging.Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s")
    )
    root.addHandler(stderr_handler)

    # Defensive stdout → stderr redirect so stray print() calls don't corrupt
    # the JSON-RPC stream.  Disable with CONTEXT_BUILDER_STDIO_GUARD=0.
    #
    # IMPORTANT: pass the *real* sys.stdout into the shim BEFORE replacing it,
    # so that _StderrShim.buffer can return the original binary buffer.
    # The MCP SDK's stdio_server() does:
    #   anyio.wrap_file(TextIOWrapper(sys.stdout.buffer, encoding="utf-8"))
    # and needs the real buffer — not the shim — to set up its transport.
    if os.environ.get("CONTEXT_BUILDER_STDIO_GUARD", "1") == "1":
        sys.stdout = _StderrShim(sys.stdout)


class _StderrShim:
    """Redirect any write to sys.stdout onto sys.stderr instead.

    Captures the real sys.stdout at construction time and exposes its
    ``.buffer`` attribute so the MCP SDK can still reach the underlying
    binary stream when setting up the stdio transport.  All higher-level
    text writes (print / sys.stdout.write) are sent to stderr instead.
    """

    def __init__(self, real_stdout) -> None:
        # Preserve the original stdout so .buffer remains accessible.
        self._real_stdout = real_stdout

    @property
    def buffer(self):
        """Binary buffer of the real stdout, required by mcp.server.stdio."""
        return self._real_stdout.buffer

    def write(self, s: str) -> int:  # pragma: no cover
        return sys.stderr.write(s)

    def flush(self) -> None:  # pragma: no cover
        sys.stderr.flush()

    def fileno(self) -> int:  # pragma: no cover
        return sys.stderr.fileno()

    @property
    def encoding(self) -> str:  # pragma: no cover
        return sys.stderr.encoding or "utf-8"

    @property
    def errors(self) -> str:  # pragma: no cover
        return sys.stderr.errors or "replace"

    def isatty(self) -> bool:  # pragma: no cover
        return False
