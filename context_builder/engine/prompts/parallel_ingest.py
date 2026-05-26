"""Parallel ingestion planning prompt — Spec 009 (Wave 4).

The ``build_parallel_ingest_prompt`` function renders the planning instructions
that the main Copilot agent receives after ``plan_parallel_ingestion`` computes
the shard breakdown.  The resulting text asks the agent to dispatch one subagent
per shard IN A SINGLE RESPONSE so VS Code fans them out in parallel.

The phrase "in a SINGLE response so VS Code fans them out in parallel" is
verified verbatim by the acceptance-criteria test
``tests/test_parallel_ingest_prompt.py``.
"""

from __future__ import annotations

from typing import List, TYPE_CHECKING

if TYPE_CHECKING:
    from engine.ingestion.sharder import ShardPlan


def _shards_table(plans: "List[ShardPlan]") -> str:
    """Render shard plan as a Markdown table for embedding in the prompt."""
    header = (
        "| Shard ID | Files | Size | Include globs | Description |\n"
        "|---|---|---|---|---|\n"
    )
    rows = []
    for p in plans:
        globs_str = ", ".join(f"`{g}`" for g in p.include_globs[:3])
        if len(p.include_globs) > 3:
            globs_str += f" (+{len(p.include_globs) - 3} more)"
        from engine.ingestion.sharder import _fmt_bytes
        rows.append(
            f"| `{p.shard_id}` | {p.estimated_file_count} "
            f"| {_fmt_bytes(p.estimated_byte_size)} "
            f"| {globs_str} | {p.description} |"
        )
    return header + "\n".join(rows)


PARALLEL_INGEST_PROMPT_TEMPLATE = """\
The Context Builder MCP server has computed a parallel ingestion plan for
`{workspace_path}` using strategy `{strategy}`.

## Plan ({n_shards} shards, est. {total_files} files, {total_mb} MB total)

{shards_table}

## Your task

Dispatch one subagent per shard, IN PARALLEL. Each subagent must call:

```
ingest_workspace_shard(
    workspace_path="{workspace_path}",
    shard_id="<shard_id from plan>",
    include_globs=<copy from plan>,
    exclude_globs=<copy from plan>,
)
```

When all subagents have reported back, YOU (the main agent) call:

```
merge_workspace_shards(
    workspace_path="{workspace_path}",
    shard_ids=[<list of completed shard_ids>],
)
```

Use the bundled custom agent profile `.context_builder/agents/shard_worker.md`
for each subagent — it exposes only the shard ingestion tool, keeping its
context window lean.

## Critical

- Emit ALL `runSubagent` calls in a SINGLE response so VS Code fans them out in parallel (sequential calls in separate turns will NOT parallelize).
- Do not merge until every shard has returned. Partial merges are not supported.
- If a shard fails, report its shard_id but proceed to merge the rest.
- If the plan has only 1 shard, skip subagents entirely and call
  `ingest_workspace(workspace_path="{workspace_path}")` directly instead.
"""


def build_parallel_ingest_prompt(
    workspace_path: str,
    strategy: str,
    plans: "List[ShardPlan]",
) -> str:
    """Render the parallel ingestion planning prompt.

    Parameters
    ----------
    workspace_path:
        Absolute path to the workspace root.
    strategy:
        The strategy used to compute the plan (``by_directory``, ``by_format``,
        or ``by_size``).
    plans:
        List of :class:`~engine.ingestion.sharder.ShardPlan` descriptors.

    Returns
    -------
    str
        Markdown-formatted prompt ready to be returned as an ``@mcp.prompt``
        response.
    """
    total_files = sum(p.estimated_file_count for p in plans)
    total_bytes = sum(p.estimated_byte_size for p in plans)
    total_mb = round(total_bytes / 1024 / 1024, 1)

    return PARALLEL_INGEST_PROMPT_TEMPLATE.format(
        workspace_path=workspace_path,
        strategy=strategy,
        n_shards=len(plans),
        total_files=total_files,
        total_mb=total_mb,
        shards_table=_shards_table(plans),
    )
