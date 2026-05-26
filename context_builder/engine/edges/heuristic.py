"""TF-IDF–scored heuristic edge mapper.

Spec 006 (Wave 3): replaces the binary ≥2-token-overlap rule with:

1. Domain-stopword filtering (``engine.edges.tokenize``) so ubiquitous
   insurance vocabulary (policy, coverage, claim, …) no longer creates noise
   edges.
2. In-memory TF-IDF scoring (``engine.edges.scoring``) so only discriminating
   terms drive edge confidence.
3. Per-relationship applicable-type gating loaded from
   ``<workspace>/.context_builder/edge_policy.json`` (auto-created on first
   use).
4. Skip-if-deterministic check: if the store already has a parser-emitted
   edge (``confidence = 1.0``) for the same (source, target, rel) triple, the
   heuristic mapper does not re-emit a lower-confidence duplicate.
5. Structured ``metadata.evidence`` block on every emitted edge, replacing
   the plain ``metadata.reason`` string.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from db.graph_store import GraphStore

from engine.edges.tokenize import DEFAULT_DOMAIN_STOPWORDS
from engine.edges.scoring import CorpusTfIdf


# ---------------------------------------------------------------------------
# Spec 005: EdgeCandidate — sub-threshold pair surfaced for Copilot review
# ---------------------------------------------------------------------------

@dataclass
class EdgeCandidate:
    """A node-pair that scored between candidate_threshold and auto_emit_threshold.

    These are not confident enough for automatic emission but are strong enough
    to warrant a host-LLM review (e.g. Copilot Chat via the
    ``propose_edge_candidates`` MCP prompt).
    """
    source_id: str
    target_id: str
    proposed_relationship: str
    score: float           # 0.0–1.0
    rationale: str         # e.g. "3 shared tokens: ['policy', 'age', 'eligibility']"
    source_excerpt: str    # ≤500 chars from source node description
    target_excerpt: str    # ≤500 chars from target node description

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default edge policy (written to disk on first run if absent)
# ---------------------------------------------------------------------------

_DEFAULT_POLICY: dict = {
    "schema_version": 1,
    "auto_emit_threshold": 0.75,
    "candidate_threshold": 0.35,
    "traceability_default_depth": 2,
    "traceability_max_nodes": 80,
    "domain_stopwords": [],  # merged with DEFAULT_DOMAIN_STOPWORDS at runtime
    "relationship_rules": {
        "TESTS": {
            "min_score": 0.65,
            "applicable_types": [
                ["test_scenario", "business_rule"],
                ["test_scenario", "api_endpoint"],
            ],
        },
        "IMPLEMENTS": {
            "min_score": 0.70,
            "applicable_types": [
                ["code_component", "business_rule"],
                ["api_endpoint", "business_rule"],
                ["api_endpoint", "product_feature"],
                ["api_endpoint", "code_component"],
            ],
        },
        "VALIDATES": {
            "min_score": 0.70,
            "applicable_types": [
                ["business_rule", "data_model"],
                ["ui_page_object", "business_rule"],
                ["data_model", "business_rule"],   # SPEC-006 (Wave 5): config nodes → rules
            ],
        },
        "USES_MODEL": {
            "min_score": 0.95,
            "applicable_types": [
                ["api_endpoint", "data_model"],
            ],
        },
    },
}


def _load_policy(storage_dir: str) -> dict:
    """Load ``edge_policy.json`` from *storage_dir*, creating it if absent.

    The file is NEVER overwritten once created — users can edit thresholds and
    domain_stopwords and expect changes to persist across re-ingests.
    """
    policy_path = os.path.join(storage_dir, "edge_policy.json")
    if not os.path.exists(policy_path):
        try:
            os.makedirs(storage_dir, exist_ok=True)
            with open(policy_path, "w", encoding="utf-8") as f:
                json.dump(_DEFAULT_POLICY, f, indent=2)
            log.info("Created default edge_policy.json at %s", policy_path)
        except OSError as exc:
            log.warning("Could not create edge_policy.json: %s — using defaults", exc)
            return _DEFAULT_POLICY

    try:
        with open(policy_path, "r", encoding="utf-8") as f:
            policy = json.load(f)
        # Back-fill any missing top-level keys from defaults (forward-compat)
        for key, val in _DEFAULT_POLICY.items():
            policy.setdefault(key, val)
        return policy
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Could not read edge_policy.json (%s) — using defaults", exc)
        return _DEFAULT_POLICY


def _applicable_types(policy: dict, relationship: str) -> list[list[str]]:
    """Return the applicable ``[source_type, target_type]`` pairs for *relationship*."""
    rules = policy.get("relationship_rules", {})
    return rules.get(relationship, {}).get("applicable_types", [])


def _rel_min_score(policy: dict, relationship: str) -> float:
    """Return the per-relationship minimum score threshold."""
    rules = policy.get("relationship_rules", {})
    return rules.get(relationship, {}).get("min_score", policy.get("auto_emit_threshold", 0.75))


class HeuristicEdgeMapper:
    """TF-IDF–scored heuristic edge mapper.

    Implements the :class:`~engine.edges.base.EdgeMapper` protocol.
    Queries the store directly for node lists so it can be called without a
    pre-collected node list.

    After each ``map()`` call the sub-threshold pairs are stored in
    ``last_candidates`` so the Copilot review prompt (Spec 005) can retrieve
    them without re-running the expensive scoring pass.
    """

    name = "heuristic"

    def __init__(self) -> None:
        self.last_candidates: list[EdgeCandidate] = []

    # ------------------------------------------------------------------ #
    # EdgeMapper protocol                                                   #
    # ------------------------------------------------------------------ #

    def map(self, store: "GraphStore") -> list[dict]:
        """Run TF-IDF heuristic scoring and return proposed edges.

        Edges are only proposed if:
        1. The (source_type, target_type) pair is in applicable_types for the
           relationship (type gating).
        2. The TF-IDF cosine score exceeds the per-relationship min_score threshold.
        3. No deterministic parser-emitted edge already exists for the same triple.
        """
        log.info("Running TF-IDF heuristic edge mapping …")

        # Reset candidates from the previous run (Spec 005)
        self.last_candidates = []

        nodes = store.query_nodes()
        if not nodes:
            return []

        # --- Load edge policy ---
        policy = _load_policy(store.storage_dir)
        auto_emit_threshold: float = policy.get("auto_emit_threshold", 0.75)
        candidate_threshold: float = policy.get("candidate_threshold", 0.35)
        extra_stopwords: frozenset[str] = frozenset(
            policy.get("domain_stopwords", [])
        ) | DEFAULT_DOMAIN_STOPWORDS  # merge sets

        # --- Build TF-IDF corpus from all node text ---
        # Materialise to list so we can look up node text when building excerpts
        nodes_list = list(nodes)
        node_text_map = {
            n["id"]: (n.get("name", "") + " " + n.get("description", "")).strip()
            for n in nodes_list
        }
        corpus_docs = (
            (nid, txt) for nid, txt in node_text_map.items()
        )
        idf_model = CorpusTfIdf(corpus_docs, extra_stopwords=extra_stopwords)

        # --- Bucket nodes by type ---
        business_rules = [n for n in nodes_list if n["type"] == "business_rule"]
        features = [n for n in nodes_list if n["type"] == "product_feature"]
        endpoints = [n for n in nodes_list if n["type"] == "api_endpoint"]
        code_components = [n for n in nodes_list if n["type"] == "code_component"]
        scenarios = [n for n in nodes_list if n["type"] == "test_scenario"]
        ui_pages = [n for n in nodes_list if n["type"] == "ui_page_object"]

        proposed: list[dict] = []

        # --- Helper factories ---
        def _type_allowed(rel: str, src_type: str, tgt_type: str) -> bool:
            pairs = _applicable_types(policy, rel)
            if not pairs:
                return True  # no gate configured → allow all
            return [src_type, tgt_type] in pairs

        def _score_edge(
            src_id: str,
            tgt_id: str,
            rel: str,
            src_type: str,
            tgt_type: str,
        ) -> None:
            if not _type_allowed(rel, src_type, tgt_type):
                return
            threshold = max(_rel_min_score(policy, rel), auto_emit_threshold)
            score = idf_model.cosine(src_id, tgt_id)

            # Spec 005: collect sub-threshold pairs as Copilot review candidates
            if candidate_threshold <= score < threshold:
                if not store.has_edge(src_id, tgt_id, rel):
                    tokens = idf_model.discriminating_overlap(src_id, tgt_id)[:5]
                    self.last_candidates.append(EdgeCandidate(
                        source_id=src_id,
                        target_id=tgt_id,
                        proposed_relationship=rel,
                        score=round(score, 4),
                        rationale=(
                            f"{len(tokens)} shared token(s): {tokens}"
                            if tokens else f"tfidf score={round(score, 4)}"
                        ),
                        source_excerpt=node_text_map.get(src_id, "")[:500],
                        target_excerpt=node_text_map.get(tgt_id, "")[:500],
                    ))

            if score < threshold:
                return
            # Skip if a deterministic parser-emitted edge already exists
            if store.has_edge(src_id, tgt_id, rel):
                return
            tokens = idf_model.discriminating_overlap(src_id, tgt_id)[:5]
            proposed.append({
                "source_id": src_id,
                "target_id": tgt_id,
                "relationship": rel,
                "metadata": {
                    "source": "heuristic",
                    "mapper": self.name,
                    "confidence": round(score, 4),
                    "evidence": {
                        "method": "tfidf_overlap",
                        "score": round(score, 4),
                        "discriminating_tokens": tokens,
                    },
                    # Legacy key kept for backward-compat with any tooling that
                    # reads metadata.reason (will be removed in Spec 008).
                    "reason": f"tfidf:{rel.lower()}:{round(score, 3)}",
                },
            })

        # 1. Scenarios → Business Rules (TESTS)
        # SPEC-005 (Wave 5): exclude NFR-tagged nodes from TESTS scoring.
        # NFR tables/sections document system-wide quality attributes (Security,
        # Performance, Accessibility) — not testable scenario-level behaviours.
        # Nodes tagged rule_origin="nfr" by word_parser are filtered out here;
        # missing key defaults to "functional" for backward compatibility.
        _functional_rules = [
            rule for rule in business_rules
            if rule.get("metadata", {}).get("rule_origin", "functional") != "nfr"
        ]
        for sc in scenarios:
            for rule in _functional_rules:
                _score_edge(sc["id"], rule["id"], "TESTS",
                            sc["type"], rule["type"])

        # 2. Scenarios → Endpoints (TESTS)
        #    Preserved path-based admin-endpoint gate from original implementation.
        _EXCLUDED_EP_TOKENS = {
            "get", "post", "put", "delete", "patch",
            "api", "v1", "v2", "admin", "test", "data", "json",
        }
        for sc in scenarios:
            sc_name_lower = (sc.get("name", "") + " " + sc.get("description", "")).lower()
            for ep in endpoints:
                ep_path = (
                    ep.get("metadata", {}).get("path")
                    or ep.get("metadata", {}).get("target_route")
                    or ""
                )
                is_admin = "admin" in ep_path.lower() or "reset" in ep_path.lower()
                if is_admin:
                    path_clean = ep_path.lower()
                    if not (
                        path_clean in sc_name_lower
                        or "test_data_reset" in sc_name_lower
                        or "testdata_reset" in sc_name_lower
                        or "reset" in sc_name_lower
                    ):
                        continue

                # Type-gate-compatible path-token match (kept from original)
                if not _type_allowed("TESTS", sc["type"], ep["type"]):
                    continue

                ep_raw_tokens = set(
                    re.findall(r"\b[a-zA-Z]{3,}\b", ep.get("name", "").lower())
                )
                ep_filtered = ep_raw_tokens - _EXCLUDED_EP_TOKENS
                sc_raw_tokens = set(
                    re.findall(r"\b[a-zA-Z]{3,}\b", sc_name_lower)
                )
                if ep_filtered and ep_filtered.intersection(sc_raw_tokens):
                    if not store.has_edge(sc["id"], ep["id"], "TESTS"):
                        proposed.append({
                            "source_id": sc["id"],
                            "target_id": ep["id"],
                            "relationship": "TESTS",
                            "metadata": {
                                "source": "heuristic",
                                "mapper": self.name,
                                "confidence": 0.75,
                                "evidence": {
                                    "method": "path_match",
                                    "score": 0.75,
                                    "discriminating_tokens": sorted(
                                        ep_filtered.intersection(sc_raw_tokens)
                                    )[:5],
                                },
                                "reason": "heuristics:scenario_references_endpoint_path",
                            },
                        })

        # 3. Endpoints → Business Rules (IMPLEMENTS)
        for ep in endpoints:
            for rule in business_rules:
                _score_edge(ep["id"], rule["id"], "IMPLEMENTS",
                            ep["type"], rule["type"])

        # 4. Endpoints → Product Features (IMPLEMENTS)
        for ep in endpoints:
            for feat in features:
                _score_edge(ep["id"], feat["id"], "IMPLEMENTS",
                            ep["type"], feat["type"])

        # 5. Code Classes → Endpoints (IMPLEMENTS via class-name metadata)
        #    Deterministic structural match preserved from original — not TF-IDF.
        for comp in code_components:
            comp_name = comp.get("name", "").lower()
            for ep in endpoints:
                ep_class = ep.get("metadata", {}).get("class", "").lower()
                if ep_class and ep_class in comp_name:
                    if not _type_allowed("IMPLEMENTS", ep["type"], comp["type"]):
                        continue
                    if store.has_edge(ep["id"], comp["id"], "IMPLEMENTS"):
                        continue
                    proposed.append({
                        "source_id": ep["id"],
                        "target_id": comp["id"],
                        "relationship": "IMPLEMENTS",
                        "metadata": {
                            "source": "heuristic",
                            "mapper": self.name,
                            "confidence": 1.0,
                            "evidence": {
                                "method": "explicit_ref",
                                "score": 1.0,
                                "discriminating_tokens": [ep_class],
                            },
                            "reason": "structural:endpoint_in_code_class",
                        },
                    })

        # 6. UI Page Objects → Business Rules (VALIDATES)
        for page in ui_pages:
            for rule in business_rules:
                _score_edge(page["id"], rule["id"], "VALIDATES",
                            page["type"], rule["type"])

        log.info("Proposed %d heuristic edges.", len(proposed))
        return proposed

    # ------------------------------------------------------------------ #
    # Spec 005: ambiguous candidates for Copilot Chat review              #
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # Spec 008: incremental edge mapping                                   #
    # ------------------------------------------------------------------ #

    def map_incremental(
        self, store: "GraphStore", dirty_node_ids: set
    ) -> list[dict]:
        """Re-map only edges where at least one endpoint is in *dirty_node_ids*.

        This turns the O(N²) full pair-scan into O(|dirty| · N), which is the
        primary performance win for incremental ingests where only a few files
        changed.

        The implementation re-uses the same TF-IDF corpus and scoring logic as
        ``map()`` but skips pairs where NEITHER endpoint is in *dirty_node_ids*.

        If *dirty_node_ids* is empty the method returns immediately with an
        empty list (the orchestrator skips calling us in that case, but being
        defensive is cheap).
        """
        if not dirty_node_ids:
            return []

        log.info(
            "Running incremental TF-IDF edge mapping for %d dirty nodes …",
            len(dirty_node_ids),
        )

        # Reset candidates from the previous run (Spec 005)
        self.last_candidates = []

        nodes = store.query_nodes()
        if not nodes:
            return []

        policy = _load_policy(store.storage_dir)
        auto_emit_threshold: float = policy.get("auto_emit_threshold", 0.75)
        candidate_threshold: float = policy.get("candidate_threshold", 0.35)
        extra_stopwords: frozenset = frozenset(
            policy.get("domain_stopwords", [])
        ) | DEFAULT_DOMAIN_STOPWORDS

        nodes_list = list(nodes)
        node_text_map = {
            n["id"]: (n.get("name", "") + " " + n.get("description", "")).strip()
            for n in nodes_list
        }
        corpus_docs = ((nid, txt) for nid, txt in node_text_map.items())
        idf_model = CorpusTfIdf(corpus_docs, extra_stopwords=extra_stopwords)

        # Bucket nodes by type (same as map())
        business_rules = [n for n in nodes_list if n["type"] == "business_rule"]
        features = [n for n in nodes_list if n["type"] == "product_feature"]
        endpoints = [n for n in nodes_list if n["type"] == "api_endpoint"]
        code_components = [n for n in nodes_list if n["type"] == "code_component"]
        scenarios = [n for n in nodes_list if n["type"] == "test_scenario"]
        ui_pages = [n for n in nodes_list if n["type"] == "ui_page_object"]

        proposed: list[dict] = []

        def _type_allowed(rel: str, src_type: str, tgt_type: str) -> bool:
            pairs = _applicable_types(policy, rel)
            if not pairs:
                return True
            return [src_type, tgt_type] in pairs

        def _score_edge_inc(
            src_id: str, tgt_id: str, rel: str, src_type: str, tgt_type: str
        ) -> None:
            # KEY optimisation: skip if neither endpoint is dirty
            if src_id not in dirty_node_ids and tgt_id not in dirty_node_ids:
                return

            if not _type_allowed(rel, src_type, tgt_type):
                return
            threshold = max(_rel_min_score(policy, rel), auto_emit_threshold)
            score = idf_model.cosine(src_id, tgt_id)

            if candidate_threshold <= score < threshold:
                if not store.has_edge(src_id, tgt_id, rel):
                    tokens = idf_model.discriminating_overlap(src_id, tgt_id)[:5]
                    self.last_candidates.append(EdgeCandidate(
                        source_id=src_id,
                        target_id=tgt_id,
                        proposed_relationship=rel,
                        score=round(score, 4),
                        rationale=(
                            f"{len(tokens)} shared token(s): {tokens}"
                            if tokens else f"tfidf score={round(score, 4)}"
                        ),
                        source_excerpt=node_text_map.get(src_id, "")[:500],
                        target_excerpt=node_text_map.get(tgt_id, "")[:500],
                    ))

            if score < threshold:
                return
            if store.has_edge(src_id, tgt_id, rel):
                return
            tokens = idf_model.discriminating_overlap(src_id, tgt_id)[:5]
            proposed.append({
                "source_id": src_id,
                "target_id": tgt_id,
                "relationship": rel,
                "metadata": {
                    "source": "heuristic",
                    "mapper": self.name,
                    "confidence": round(score, 4),
                    "evidence": {
                        "method": "tfidf_overlap",
                        "score": round(score, 4),
                        "discriminating_tokens": tokens,
                    },
                    "reason": f"tfidf:{rel.lower()}:{round(score, 3)}",
                },
            })

        # Mirror the same pair-iteration structure as map(), but each call
        # goes through _score_edge_inc which gates on dirty_node_ids.
        for sc in scenarios:
            for rule in business_rules:
                _score_edge_inc(sc["id"], rule["id"], "TESTS",
                                sc["type"], rule["type"])

        _EXCLUDED_EP_TOKENS = {
            "get", "post", "put", "delete", "patch",
            "api", "v1", "v2", "admin", "test", "data", "json",
        }
        for sc in scenarios:
            sc_name_lower = (sc.get("name", "") + " " + sc.get("description", "")).lower()
            for ep in endpoints:
                if sc["id"] not in dirty_node_ids and ep["id"] not in dirty_node_ids:
                    continue
                ep_path = (
                    ep.get("metadata", {}).get("path")
                    or ep.get("metadata", {}).get("target_route")
                    or ""
                )
                is_admin = "admin" in ep_path.lower() or "reset" in ep_path.lower()
                if is_admin:
                    path_clean = ep_path.lower()
                    if not (
                        path_clean in sc_name_lower
                        or "test_data_reset" in sc_name_lower
                        or "testdata_reset" in sc_name_lower
                        or "reset" in sc_name_lower
                    ):
                        continue
                if not _type_allowed("TESTS", sc["type"], ep["type"]):
                    continue
                ep_raw_tokens = set(
                    re.findall(r"\b[a-zA-Z]{3,}\b", ep.get("name", "").lower())
                )
                ep_filtered = ep_raw_tokens - _EXCLUDED_EP_TOKENS
                sc_raw_tokens = set(
                    re.findall(r"\b[a-zA-Z]{3,}\b", sc_name_lower)
                )
                if ep_filtered and ep_filtered.intersection(sc_raw_tokens):
                    if not store.has_edge(sc["id"], ep["id"], "TESTS"):
                        proposed.append({
                            "source_id": sc["id"],
                            "target_id": ep["id"],
                            "relationship": "TESTS",
                            "metadata": {
                                "source": "heuristic",
                                "mapper": self.name,
                                "confidence": 0.75,
                                "evidence": {
                                    "method": "path_match",
                                    "score": 0.75,
                                    "discriminating_tokens": sorted(
                                        ep_filtered.intersection(sc_raw_tokens)
                                    )[:5],
                                },
                                "reason": "heuristics:scenario_references_endpoint_path",
                            },
                        })

        for ep in endpoints:
            for rule in business_rules:
                _score_edge_inc(ep["id"], rule["id"], "IMPLEMENTS",
                                ep["type"], rule["type"])

        for ep in endpoints:
            for feat in features:
                _score_edge_inc(ep["id"], feat["id"], "IMPLEMENTS",
                                ep["type"], feat["type"])

        for comp in code_components:
            comp_name = comp.get("name", "").lower()
            for ep in endpoints:
                if comp["id"] not in dirty_node_ids and ep["id"] not in dirty_node_ids:
                    continue
                ep_class = ep.get("metadata", {}).get("class", "").lower()
                if ep_class and ep_class in comp_name:
                    if not _type_allowed("IMPLEMENTS", ep["type"], comp["type"]):
                        continue
                    if store.has_edge(ep["id"], comp["id"], "IMPLEMENTS"):
                        continue
                    proposed.append({
                        "source_id": ep["id"],
                        "target_id": comp["id"],
                        "relationship": "IMPLEMENTS",
                        "metadata": {
                            "source": "heuristic",
                            "mapper": self.name,
                            "confidence": 1.0,
                            "evidence": {
                                "method": "explicit_ref",
                                "score": 1.0,
                                "discriminating_tokens": [ep_class],
                            },
                            "reason": "structural:endpoint_in_code_class",
                        },
                    })

        for page in ui_pages:
            for rule in business_rules:
                _score_edge_inc(page["id"], rule["id"], "VALIDATES",
                                page["type"], rule["type"])

        log.info("Proposed %d incremental heuristic edges.", len(proposed))
        return proposed

    # ------------------------------------------------------------------ #
    # Spec 005: ambiguous candidates for Copilot Chat review              #
    # ------------------------------------------------------------------ #

    def ambiguous_candidates(self, limit: int = 50) -> list[EdgeCandidate]:
        """Return sub-threshold edge candidates collected during the last ``map()`` call.

        Results are sorted descending by score (highest confidence first) so
        the prompt renderer can truncate to the most-promising pairs.

        Call ``map(store)`` first; returns an empty list if the mapper has not
        been run yet this session.
        """
        return sorted(self.last_candidates, key=lambda c: -c.score)[:limit]
