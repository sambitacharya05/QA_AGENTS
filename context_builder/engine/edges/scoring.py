"""TF-IDF cosine similarity scoring for edge candidate pairs.

Spec 006 (Wave 3): replaces raw token-count overlap (``len(A & B) >= 2``) with
an IDF-weighted cosine so rare-but-discriminating terms get amplified and
ubiquitous domain terms (already stripped by ``tokenize()``) get further
suppressed.

Design notes
------------
- Pure Python, zero external dependencies — consistent with the "local-only"
  project constraint.
- Binary TF (0/1 per document) × IDF² weighting gives a cosine on a {0,1}
  term-presence vector.  Equivalent to Jaccard but IDF-rescaled.
- The model is rebuilt once per ingest run over the full node corpus, then
  used for O(candidates²) scoring.  For graphs up to a few thousand nodes
  this is fast enough; Spec 009 can cache it across shards if needed.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Iterable

from engine.edges.tokenize import tokenize


class CorpusTfIdf:
    """Lightweight in-memory TF-IDF model over a node corpus.

    Parameters
    ----------
    docs:
        Iterable of ``(doc_id, text)`` pairs covering all nodes whose text
        should inform the IDF weighting.  Each pair is consumed once; the
        iterable need not be re-iterable.
    extra_stopwords:
        Forwarded verbatim to ``tokenize()``; kept as a frozenset so it is
        hashable and safe for caching inside ``tokenize``.
    """

    def __init__(
        self,
        docs: Iterable[tuple[str, str]],
        extra_stopwords: frozenset[str] = frozenset(),
    ) -> None:
        self.idf: dict[str, float] = {}
        self.doc_tokens: dict[str, frozenset] = {}
        self._extra_stopwords = extra_stopwords

        token_doc_count: Counter = Counter()
        n_docs = 0

        for doc_id, text in docs:
            toks = tokenize(text, extra_stopwords)
            self.doc_tokens[doc_id] = toks
            for t in toks:
                token_doc_count[t] += 1
            n_docs += 1

        # Smoothed IDF:  log((N+1) / (df+1)) + 1
        # The +1 smoothing prevents zero division and handles unseen tokens.
        for t, df in token_doc_count.items():
            self.idf[t] = math.log((n_docs + 1) / (df + 1)) + 1.0

        self.n_docs = n_docs

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def cosine(self, a_id: str, b_id: str) -> float:
        """IDF-weighted cosine similarity between doc *a_id* and *b_id*.

        Both docs must have been supplied to the constructor.  Returns ``0.0``
        if either is unknown or if there is no shared vocabulary after stopword
        filtering.
        """
        a = self.doc_tokens.get(a_id, frozenset())
        b = self.doc_tokens.get(b_id, frozenset())
        common = a & b
        if not common:
            return 0.0

        # Binary TF × IDF²: numerator is the dot product of binary-TF·IDF vectors
        num = sum(self.idf[t] ** 2 for t in common)
        denom = math.sqrt(
            sum(self.idf[t] ** 2 for t in a)
            * sum(self.idf[t] ** 2 for t in b)
        )
        return (num / denom) if denom else 0.0

    def discriminating_overlap(self, a_id: str, b_id: str) -> list[str]:
        """Shared tokens between *a_id* and *b_id*, sorted by IDF descending.

        Most discriminating (rarest corpus-wide) token appears first.  Used to
        populate ``metadata.evidence.discriminating_tokens`` on emitted edges.
        """
        common = (
            self.doc_tokens.get(a_id, frozenset())
            & self.doc_tokens.get(b_id, frozenset())
        )
        return sorted(common, key=lambda t: -self.idf.get(t, 0.0))

    def add_doc(self, doc_id: str, text: str) -> None:
        """Register a new document without rebuilding the full corpus.

        IDF values are NOT updated (the model was already fitted).  This method
        is intended for late-arriving nodes (e.g. nodes added by manual
        ``add_node`` calls after ingest) so they at least participate in cosine
        comparisons with the tokens they share with the existing corpus.
        """
        toks = tokenize(text, self._extra_stopwords)
        self.doc_tokens[doc_id] = toks
