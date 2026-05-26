"""Parallel parser worker pool — Spec 009 (Wave 4).

Provides ``ParallelParser``, which fans out file-level parsing across a
``ProcessPoolExecutor`` worker pool.  Each worker is a clean Python subprocess
that imports its own parser instances and returns serialisable
entity/relationship dicts to the parent; no shared mutable state crosses the
process boundary.

Design invariants
-----------------
- Workers **never write to disk**.  They return entity/relationship dicts; the
  parent process owns all writes through Spec 001's transaction + flock.
- Parsers must be **picklable** (all current parsers are functions/classes with
  no un-picklable state).
- The pool is created **once per ``parse_many`` call** and torn down
  immediately after, so startup overhead is amortised across the shard.
- When ``max_workers ≤ 1`` or the task list has fewer than
  ``min_files_for_pool`` entries, parsing falls back to a simple serial loop
  (no subprocess overhead, easier debugging).
- Worker count is capped at ``min(cpu_count, 8)`` by default; a higher cap
  rarely helps and increases IPC overhead and memory pressure.

Configuration
-------------
Worker count can be overridden at construction time or via
``.context_builder/parallelism.json``::

    {
      "max_parser_workers": 4,
      "min_files_for_parallel_parse": 3
    }
"""

from __future__ import annotations

import logging
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger(__name__)

# Default tuning knobs
_DEFAULT_MAX_WORKERS = min(os.cpu_count() or 4, 8)
_DEFAULT_MIN_FILES = 3  # below this, serial is cheaper than pool startup


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ParseTask:
    """Descriptor for a single file-parse job.

    ``shared_context_snapshot`` is an immutable copy of the locator-cache state
    at shard-start time.  Workers receive it but MUST NOT mutate it (they
    return newly discovered locators as part of the result instead).
    """

    abs_path: str
    rel_path: str
    extension: str
    shared_context_snapshot: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ParseResult:
    """Outcome of a single file-parse worker invocation."""

    rel_path: str
    entities: List[Dict[str, Any]] = field(default_factory=list)
    relationships: List[Dict[str, Any]] = field(default_factory=list)
    raw_text: str = ""
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Worker function (runs in child process — must be a module-level function
# so multiprocessing can pickle it on all platforms including Windows)
# ---------------------------------------------------------------------------


def _parse_worker(task: ParseTask) -> dict:
    """Runs in a child process.  Re-imports parsers fresh — no shared state."""
    try:
        # Late import inside worker so the parent's already-loaded modules
        # don't get serialised unnecessarily.
        from parsers import get_parser_for_extension  # type: ignore[import]
        parser = get_parser_for_extension(
            task.extension,
            shared_context=dict(task.shared_context_snapshot),
        )
        result = parser.parse(task.abs_path)
        entities = result.get("entities", [])
        relationships = result.get("relationships", [])
        raw_text = result.get("raw_text", "")
        return {
            "rel_path": task.rel_path,
            "entities": entities,
            "relationships": relationships,
            "raw_text": raw_text,
            "error": None,
        }
    except Exception as exc:
        return {
            "rel_path": task.rel_path,
            "entities": [],
            "relationships": [],
            "raw_text": "",
            "error": repr(exc),
        }


# ---------------------------------------------------------------------------
# ParallelParser
# ---------------------------------------------------------------------------


class ParallelParser:
    """Fan-out file-level parsing across a ``ProcessPoolExecutor`` pool.

    Parameters
    ----------
    max_workers:
        Maximum number of worker processes.  ``None`` → ``min(cpu_count, 8)``.
    min_files_for_pool:
        Minimum task count to justify creating a pool.  Below this threshold
        the serial path is used.
    """

    def __init__(
        self,
        max_workers: Optional[int] = None,
        min_files_for_pool: int = _DEFAULT_MIN_FILES,
    ) -> None:
        cpu = os.cpu_count() or 4
        self.max_workers: int = max_workers if max_workers is not None else min(cpu, 8)
        self.min_files_for_pool = min_files_for_pool

    def parse_many(
        self,
        tasks: List[ParseTask],
        on_complete: Optional[Callable[[dict], None]] = None,
    ) -> List[dict]:
        """Parse all *tasks*, returning a list of result dicts.

        Parameters
        ----------
        tasks:
            List of :class:`ParseTask` descriptors.
        on_complete:
            Optional callback invoked in the parent process for each completed
            task (useful for progress reporting).

        Returns
        -------
        list[dict]
            Each element has keys: ``rel_path``, ``entities``,
            ``relationships``, ``raw_text``, ``error``.
        """
        if not tasks:
            return []

        # Serial fallback when pool startup isn't worth it
        if len(tasks) < self.min_files_for_pool or self.max_workers <= 1:
            log.debug("ParallelParser: serial path (%d tasks)", len(tasks))
            results = []
            for t in tasks:
                res = _parse_worker(t)
                results.append(res)
                if on_complete:
                    on_complete(res)
            return results

        log.info(
            "ParallelParser: pool path — %d tasks, %d workers",
            len(tasks), self.max_workers,
        )
        results: List[dict] = []
        try:
            with ProcessPoolExecutor(max_workers=self.max_workers) as pool:
                futs = {pool.submit(_parse_worker, t): t for t in tasks}
                for fut in as_completed(futs):
                    try:
                        res = fut.result()
                    except Exception as exc:
                        task = futs[fut]
                        log.warning(
                            "Worker crashed for %s: %s", task.rel_path, exc
                        )
                        res = {
                            "rel_path": futs[fut].rel_path,
                            "entities": [],
                            "relationships": [],
                            "raw_text": "",
                            "error": repr(exc),
                        }
                    results.append(res)
                    if on_complete:
                        on_complete(res)
        except Exception as exc:
            log.error("ProcessPoolExecutor failed: %s — falling back to serial", exc)
            # Graceful degradation: re-run serially
            results = []
            for t in tasks:
                res = _parse_worker(t)
                results.append(res)
                if on_complete:
                    on_complete(res)

        return results
