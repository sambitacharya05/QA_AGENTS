"""Workspace sharder — Spec 009 (Wave 4).

Computes how to split a workspace into independent shards that can be ingested
in parallel by Copilot subagents.  Three strategies are supported:

- ``by_directory`` — one shard per top-level subdirectory that contains at
  least one parseable file.
- ``by_format`` — one shard per file-extension family present in the workspace.
- ``by_size`` — greedy bin-packing into N approximately equal-byte shards.

The ``Sharder`` is a pure-Python helper invoked by the ``plan_parallel_ingestion``
MCP prompt.  It does no parsing and writes nothing to disk — it only inspects
the filesystem to build ``ShardPlan`` descriptors that the prompt then asks the
host LLM to dispatch.

All three strategies are deterministic (given the same filesystem snapshot) so
the planner prompt output is stable across identical workspaces.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from typing import Dict, List, Literal, Set

# Parseable extension families used by both "by_format" and existence checks.
FORMAT_FAMILIES: Dict[str, List[str]] = {
    "docs":         [".md", ".docx", ".pdf", ".txt", ".rst"],
    "spreadsheets": [".xlsx", ".csv", ".xls"],
    "java":         [".java"],
    "typescript":   [".ts", ".tsx", ".js", ".jsx"],
    "python":       [".py"],
    "go":           [".go"],
    "features":     [".feature"],
    "schemas":      [".yaml", ".yml", ".json"],
}

# Flat set of all parseable extensions (for quick membership tests)
ALL_PARSEABLE: Set[str] = {
    ext for exts in FORMAT_FAMILIES.values() for ext in exts
}

# Directories to always skip when walking the workspace
_SKIP_DIRS: Set[str] = {
    ".context_builder", ".git", "__pycache__", "node_modules",
    ".venv", "venv", ".env", "dist", "build", "target",
}

ShardStrategy = Literal["by_directory", "by_format", "by_size"]


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------


@dataclass
class ShardPlan:
    """Descriptor for a single shard of work.

    Attributes
    ----------
    shard_id:
        Stable, filesystem-safe identifier (used as the staging directory name).
    include_globs:
        Glob patterns (relative to workspace root) that the shard worker should
        ingest.  Passed verbatim to ``ingest_workspace_shard``.
    exclude_globs:
        Patterns to exclude (e.g. other shards' directories, test fixtures).
    estimated_file_count:
        Approximate number of parseable files in this shard.
    estimated_byte_size:
        Approximate total byte size of parseable files in this shard.
    description:
        Human-readable summary, shown in the planning prompt table.
    """

    shard_id: str
    include_globs: List[str] = field(default_factory=list)
    exclude_globs: List[str] = field(default_factory=list)
    estimated_file_count: int = 0
    estimated_byte_size: int = 0
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "shard_id": self.shard_id,
            "include_globs": self.include_globs,
            "exclude_globs": self.exclude_globs,
            "estimated_file_count": self.estimated_file_count,
            "estimated_byte_size": self.estimated_byte_size,
            "description": self.description,
        }


# ---------------------------------------------------------------------------
# Sharder
# ---------------------------------------------------------------------------


class Sharder:
    """Computes shard plans for a workspace.

    Parameters
    ----------
    workspace_path:
        Absolute path to the workspace root.
    max_shards:
        Upper bound on the number of shards emitted by any strategy.
        Strategies that would produce more shards merge the smallest ones.
    """

    def __init__(self, workspace_path: str, max_shards: int = 8) -> None:
        self.workspace_path = os.path.realpath(workspace_path)
        self.max_shards = max_shards

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def plan(self, strategy: ShardStrategy) -> List[ShardPlan]:
        """Compute and return shard plans for the given strategy.

        Returns an empty list if the workspace has no parseable files, or a
        single-element list if the workspace is too small to benefit from
        sharding (file count < 2).
        """
        if strategy == "by_directory":
            return self._plan_by_directory()
        elif strategy == "by_format":
            return self._plan_by_format()
        elif strategy == "by_size":
            return self._plan_by_size()
        raise ValueError(
            f"Unknown sharding strategy {strategy!r}. "
            "Choose one of: by_directory, by_format, by_size"
        )

    # ------------------------------------------------------------------
    # Internal walk helpers
    # ------------------------------------------------------------------

    def _walk(self):
        """Yield (rel_path, abs_path, size_bytes) for every parseable file."""
        for dirpath, dirnames, filenames in os.walk(self.workspace_path):
            # Prune skip-dirs in-place so os.walk doesn't descend into them
            dirnames[:] = [
                d for d in dirnames
                if d not in _SKIP_DIRS and not d.startswith(".")
            ]
            for fname in filenames:
                ext = os.path.splitext(fname)[1].lower()
                if ext not in ALL_PARSEABLE:
                    continue
                abs_path = os.path.join(dirpath, fname)
                rel_path = os.path.relpath(abs_path, self.workspace_path)
                try:
                    size = os.path.getsize(abs_path)
                except OSError:
                    size = 0
                yield rel_path, abs_path, size

    # ------------------------------------------------------------------
    # Strategy: by_directory
    # ------------------------------------------------------------------

    def _plan_by_directory(self) -> List[ShardPlan]:
        """One shard per top-level subdirectory containing parseable files."""
        # Group files by their top-level directory component
        dir_files: Dict[str, List[tuple]] = {}
        for rel_path, _abs, size in self._walk():
            parts = rel_path.replace("\\", "/").split("/")
            top = parts[0] if len(parts) > 1 else "__root__"
            dir_files.setdefault(top, []).append((rel_path, size))

        if not dir_files:
            return []

        plans: List[ShardPlan] = []
        for top_dir, files in sorted(dir_files.items()):
            shard_id = _safe_id(top_dir)
            total_bytes = sum(s for _, s in files)
            if top_dir == "__root__":
                globs = ["*"]
                excl = [f"{d}/**" for d in dir_files if d != "__root__"]
            else:
                globs = [f"{top_dir}/**"]
                excl = []
            plans.append(ShardPlan(
                shard_id=shard_id,
                include_globs=globs,
                exclude_globs=excl,
                estimated_file_count=len(files),
                estimated_byte_size=total_bytes,
                description=f"Directory: {top_dir!r} ({len(files)} files, {_fmt_bytes(total_bytes)})",
            ))

        return self._merge_small(plans)

    # ------------------------------------------------------------------
    # Strategy: by_format
    # ------------------------------------------------------------------

    def _plan_by_format(self) -> List[ShardPlan]:
        """One shard per extension-family present in the workspace."""
        # Map family name → files in that family
        family_files: Dict[str, List[tuple]] = {}
        for rel_path, _abs, size in self._walk():
            ext = os.path.splitext(rel_path)[1].lower()
            for family, exts in FORMAT_FAMILIES.items():
                if ext in exts:
                    family_files.setdefault(family, []).append((rel_path, size))
                    break

        if not family_files:
            return []

        plans: List[ShardPlan] = []
        for family, files in sorted(family_files.items()):
            exts = FORMAT_FAMILIES[family]
            total_bytes = sum(s for _, s in files)
            # Build globs that match all extensions in this family
            globs = [f"**/*{ext}" for ext in exts]
            plans.append(ShardPlan(
                shard_id=f"fmt_{family}",
                include_globs=globs,
                exclude_globs=[],
                estimated_file_count=len(files),
                estimated_byte_size=total_bytes,
                description=(
                    f"Format family: {family!r} "
                    f"({', '.join(exts)}) — {len(files)} files, {_fmt_bytes(total_bytes)}"
                ),
            ))

        return self._merge_small(plans)

    # ------------------------------------------------------------------
    # Strategy: by_size
    # ------------------------------------------------------------------

    def _plan_by_size(self) -> List[ShardPlan]:
        """Greedy bin-packing into N ≈ equal-byte shards."""
        all_files = list(self._walk())
        if not all_files:
            return []

        n_shards = min(self.max_shards, len(all_files))
        if n_shards <= 1:
            total = sum(s for _, _, s in all_files)
            return [ShardPlan(
                shard_id="shard_000",
                include_globs=["**/*"],
                exclude_globs=[],
                estimated_file_count=len(all_files),
                estimated_byte_size=total,
                description=f"All files ({len(all_files)} files, {_fmt_bytes(total)})",
            )]

        # Sort by size descending (largest first → tighter packing)
        all_files.sort(key=lambda t: t[2], reverse=True)

        # Greedy bin-packing: always assign to the lightest bin
        bins: List[List[str]] = [[] for _ in range(n_shards)]
        bin_sizes: List[int] = [0] * n_shards

        for rel_path, _abs, size in all_files:
            lightest = min(range(n_shards), key=lambda i: bin_sizes[i])
            bins[lightest].append(rel_path)
            bin_sizes[lightest] += size

        plans: List[ShardPlan] = []
        for i, (bin_files, total_bytes) in enumerate(zip(bins, bin_sizes)):
            if not bin_files:
                continue
            shard_id = f"shard_{i:03d}"
            plans.append(ShardPlan(
                shard_id=shard_id,
                # Use explicit file list encoded as globs
                include_globs=[f.replace("\\", "/") for f in bin_files],
                exclude_globs=[],
                estimated_file_count=len(bin_files),
                estimated_byte_size=total_bytes,
                description=(
                    f"Size bucket {i+1}/{n_shards}: "
                    f"{len(bin_files)} files, {_fmt_bytes(total_bytes)}"
                ),
            ))

        return plans

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _merge_small(self, plans: List[ShardPlan]) -> List[ShardPlan]:
        """Merge the smallest shards until total count ≤ max_shards."""
        while len(plans) > self.max_shards:
            # Sort by file count ascending; merge the two smallest
            plans.sort(key=lambda p: p.estimated_file_count)
            a, b = plans[0], plans[1]
            merged = ShardPlan(
                shard_id=f"{a.shard_id}_and_{b.shard_id}",
                include_globs=a.include_globs + b.include_globs,
                exclude_globs=list(set(a.exclude_globs + b.exclude_globs)),
                estimated_file_count=a.estimated_file_count + b.estimated_file_count,
                estimated_byte_size=a.estimated_byte_size + b.estimated_byte_size,
                description=f"Merged: {a.shard_id} + {b.shard_id}",
            )
            plans = [merged] + plans[2:]
        return plans


# ---------------------------------------------------------------------------
# Module-level utilities
# ---------------------------------------------------------------------------


def _safe_id(name: str) -> str:
    """Convert a directory or format name to a filesystem-safe shard ID."""
    import re
    safe = re.sub(r"[^a-zA-Z0-9_\-]", "_", name)
    return safe[:48] or "root"


def _fmt_bytes(n: int) -> str:
    """Format byte count as human-readable string."""
    if n < 1024:
        return f"{n} B"
    elif n < 1024 ** 2:
        return f"{n / 1024:.1f} KB"
    elif n < 1024 ** 3:
        return f"{n / 1024**2:.1f} MB"
    return f"{n / 1024**3:.1f} GB"
