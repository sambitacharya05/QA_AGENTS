"""
WorkspaceRegistry — per-workspace GraphStore cache.

Replaces the module-global ``active_store`` pattern in the old ``main.py``.
Every MCP tool call provides (or defaults to) a ``workspace_path``; the registry
hands back the correct ``GraphStore`` instance without any silent rebinding.

Thread safety
-------------
The registry uses a ``threading.Lock`` so concurrent synchronous tool calls
(FastMCP schedules them in a thread pool) cannot race on the internal dict.

Spec 009 (Wave 4)
-----------------
Added ``acquire_async`` / ``asyncio.Lock`` companion for async FastMCP handlers
used by the parallel shard tools.  The synchronous ``acquire`` / ``get``
surface is unchanged.
"""

import asyncio
import logging
import os
import threading
from typing import Dict, List, Optional

from db.graph_store import GraphStore

log = logging.getLogger(__name__)


class WorkspaceRegistry:
    """Thread-safe cache of per-workspace ``GraphStore`` instances.

    Usage::

        registry = WorkspaceRegistry()

        # On first ingest — creates and caches the store
        store = registry.acquire("/abs/path/to/workspace")

        # On any subsequent tool call — retrieves the cached store
        store = registry.get("/abs/path/to/workspace")

        # Fallback to the most-recently-ingested workspace
        store = registry.get()
    """

    def __init__(self) -> None:
        self._stores: Dict[str, GraphStore] = {}
        self._lock = threading.Lock()
        self._default_workspace: Optional[str] = None
        # Asyncio companion lock for Spec 009 async shard-tool handlers
        # (created lazily because asyncio.Lock() must be created inside a loop)
        self._async_lock: Optional[asyncio.Lock] = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize(path: str) -> str:
        """Resolve symlinks and normalise the path so two equivalent strings
        always map to the same registry key."""
        return os.path.realpath(os.path.abspath(path))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def acquire(self, workspace_path: str, *, make_default: bool = True) -> GraphStore:
        """Return the ``GraphStore`` for *workspace_path*, creating it if needed.

        Parameters
        ----------
        workspace_path:
            Absolute (or resolvable) path to the workspace root.
        make_default:
            When ``True`` (default), mark this workspace as the fallback
            returned by :meth:`get` when no path is supplied.

        Raises
        ------
        FileNotFoundError
            If *workspace_path* does not exist or is not a directory.
        """
        norm = self._normalize(workspace_path)
        if not os.path.isdir(norm):
            raise FileNotFoundError(
                f"Workspace path is not a directory: {workspace_path!r}"
            )
        with self._lock:
            store = self._stores.get(norm)
            if store is None:
                log.info("WorkspaceRegistry: initialising store for %s", norm)
                store = GraphStore(norm)
                self._stores[norm] = store
            if make_default:
                self._default_workspace = norm
        return store

    def get(self, workspace_path: Optional[str] = None) -> GraphStore:
        """Return the ``GraphStore`` for *workspace_path*.

        Parameters
        ----------
        workspace_path:
            Absolute path to a previously-acquired workspace.  When ``None``
            the most-recently acquired (default) workspace is returned.

        Raises
        ------
        LookupError
            If *workspace_path* was never acquired, or if no default has been
            set yet (i.e. ``ingest_workspace`` was never called).
        """
        with self._lock:
            if workspace_path:
                norm = self._normalize(workspace_path)
                store = self._stores.get(norm)
                if store is None:
                    raise LookupError(
                        f"Workspace {workspace_path!r} has not been ingested. "
                        f"Call ingest_workspace(workspace_path) first."
                    )
                return store

            if self._default_workspace is None:
                raise LookupError(
                    "No active workspace.  Call ingest_workspace(workspace_path) first."
                )
            return self._stores[self._default_workspace]

    async def acquire_async(
        self, workspace_path: str, *, make_default: bool = True
    ) -> GraphStore:
        """Async-safe version of :meth:`acquire` for use in async FastMCP handlers.

        Uses an ``asyncio.Lock`` instead of ``threading.Lock`` so that
        concurrent ``ingest_workspace_shard`` tool calls (dispatched as coroutines
        by FastMCP) do not block the event loop while waiting.

        The underlying ``acquire()`` call is still synchronous (GraphStore init
        involves disk I/O) but the lock is async-compatible.
        """
        if self._async_lock is None:
            self._async_lock = asyncio.Lock()
        async with self._async_lock:
            return self.acquire(workspace_path, make_default=make_default)

    def list_workspaces(self) -> List[str]:
        """Return the normalised paths of all registered workspaces."""
        with self._lock:
            return list(self._stores.keys())

    def default_workspace(self) -> Optional[str]:
        """Return the path of the current default workspace, or ``None``."""
        with self._lock:
            return self._default_workspace
