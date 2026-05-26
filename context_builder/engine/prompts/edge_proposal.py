"""Edge-proposal prompt template for Copilot-native edge augmentation.

Spec 005 (Wave 3b).

The MCP server does NOT call an LLM directly.  Instead it packages graph
context + candidate pairs into a structured prompt string that the *host* LLM
(e.g. GitHub Copilot Chat) executes inside its own context window using its
own credentials.  The server then validates and applies the resulting JSON via
``apply_edge_proposals``.

No external network calls.  No API keys.  No SDK dependencies.
"""

from __future__ import annotations

import dataclasses
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from engine.edges.heuristic import EdgeCandidate

# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------

EDGE_PROPOSAL_PROMPT = """You are a code-knowledge-graph reviewer.
The Context Builder MCP server has surfaced {n_candidates} ambiguous pairs of nodes
that *might* share a semantic relationship but did not pass the heuristic
auto-emit threshold. Your job is to confirm or reject each one based on the
descriptions and excerpts provided.

## Allowed relationship types (return EXACTLY one of these or null):
- IMPLEMENTS  — a code component implements a business rule or product feature
- VALIDATES   — a business rule validates a data model
- TESTS       — a test scenario tests a business rule, feature, or endpoint
- USES_MODEL  — an endpoint uses a data model as request/response body
- EXCLUDES    — two rules are mutually exclusive
- MAPS_TO     — two nodes describe the same concept across sources

## Candidates:
{candidates_json}

## Instructions
For each candidate, decide:
1. Is the proposed relationship correct? If not, is there a *different* relationship
   from the allowed list that does fit? If neither, return null.
2. Score your confidence on a 0.0-1.0 scale. Be conservative — score < 0.7 means
   "plausible but not certain". Edges below 0.6 will be discarded.
3. Provide a one-sentence rationale grounded in the source/target excerpts.

## Output format (STRICT)
Return ONLY a JSON array. No prose, no markdown fences. Schema:
[
  {{
    "source_id": "<copied from candidate>",
    "target_id": "<copied from candidate>",
    "relationship": "IMPLEMENTS" | "VALIDATES" | "TESTS" | "USES_MODEL" | "EXCLUDES" | "MAPS_TO" | null,
    "confidence": 0.0,
    "rationale": "<one sentence>"
  }},
  ...
]

If a candidate cannot be evaluated, omit it from the array (do not return null entries).
"""


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------

def render_edge_proposal_prompt(
    candidates: list[EdgeCandidate],
    max_chars: int = 60_000,
) -> str:
    """Render the prompt string for the given *candidates*.

    Candidates should be pre-sorted descending by score so that if truncation
    is required the lowest-confidence pairs are dropped first.

    The rendered string is guaranteed to be ≤ *max_chars* characters (default
    60 000) — a safe budget for Copilot Chat's typical 8 k–32 k token window.
    """
    payload = [dataclasses.asdict(c) for c in candidates]
    while True:
        rendered = EDGE_PROPOSAL_PROMPT.format(
            n_candidates=len(payload),
            candidates_json=json.dumps(payload, indent=2),
        )
        if len(rendered) <= max_chars or not payload:
            return rendered
        payload.pop()  # drop the last (lowest-scored) candidate and retry
