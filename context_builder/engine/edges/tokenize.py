"""Token normalisation with domain and generic stopword filtering.

Spec 006 (Wave 3): replaces the bare-minimum ``re.findall(r'\\b[a-zA-Z]{3,}\\b', ...)``
call inside ``HeuristicEdgeMapper`` with a proper stopword-filtered, cached tokeniser.

The two stopword sets are intentionally separate so callers can reason about which
words are domain-specific vs. generic English function words.
"""

from __future__ import annotations

import re
from functools import lru_cache

# ---------------------------------------------------------------------------
# Stopword sets
# ---------------------------------------------------------------------------

# Insurance-domain terms that saturate *every* node's token set and carry
# near-zero discriminating signal.  Can be extended at runtime via the
# ``extra_stopwords`` argument (loaded from ``.context_builder/edge_policy.json``).
DEFAULT_DOMAIN_STOPWORDS: frozenset[str] = frozenset({
    "policy", "policies", "coverage", "coverages", "claim", "claims",
    "premium", "premiums", "rule", "rules", "test", "tests", "scenario",
    "scenarios", "endpoint", "endpoints", "request", "response",
    "feature", "features", "user", "users", "data", "model", "service",
    "controller", "handler", "client", "config", "context", "value",
    "field", "input", "output", "system", "process", "step", "steps",
})

GENERIC_STOPWORDS: frozenset[str] = frozenset({
    # English short stopwords beyond NLTK basics
    "the", "and", "for", "with", "that", "this", "from", "must",
    "should", "will", "have", "has", "had", "when", "then", "given",
    "but", "are", "was", "were", "been", "being",
})

# Minimum 3 chars, starts with a letter (a-z), may contain digits and underscores.
_TOKEN_RE = re.compile(r"[a-z][a-z0-9_]{2,}")


@lru_cache(maxsize=10_000)
def tokenize(text: str, extra_stopwords: frozenset = frozenset()) -> frozenset:
    """Return a frozenset of discriminating lowercase tokens from *text*.

    Filtering applied:
    - Generic English stopwords are removed.
    - Domain-specific insurance stopwords are removed.
    - Tokens shorter than 3 characters are excluded by the regex.
    - *extra_stopwords* (a ``frozenset`` — hashable for lru_cache) can supply
      workspace-specific overrides loaded from ``edge_policy.json``.

    The result is cached aggressively (10 k entries) because the same node text
    is scored against potentially thousands of candidate pairs per ingest run.
    """
    stop = DEFAULT_DOMAIN_STOPWORDS | GENERIC_STOPWORDS | extra_stopwords
    return frozenset(t for t in _TOKEN_RE.findall((text or "").lower()) if t not in stop)
