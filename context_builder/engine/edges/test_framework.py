"""Specialized test-framework relationship mapper.

Extracted verbatim from ``ContextExtractor._map_test_relationships()``
in ``engine/extractor.py`` (Spec 003 — Wave 2).

No behavior change from the original.  The only structural difference is that
edges are *returned* as a list rather than written directly to the store.
To preserve the chained-validation-tracing behavior (step 3 reads CALLS edges
emitted in step 1), a local ``_proposed_calls`` dict shadows newly proposed
CALLS edges so step 3 can see them without a round-trip through the store.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from db.graph_store import GraphStore

log = logging.getLogger(__name__)


class TestEdgeMapper:
    """Import-graph, POM-reference, model-usage, and page-validation mapper.

    Implements the :class:`~engine.edges.base.EdgeMapper` protocol.
    Handles CALLS, REFERENCES, TESTS, USES, and VALIDATES edges for test
    automation workspaces.
    """

    name = "test_framework"

    def map(self, store: "GraphStore") -> list[dict]:
        """Run specialized test framework relationship mappings and return proposed edges."""
        log.info("Running specialized test framework relationship mappings...")

        nodes = store.query_nodes()

        step_definitions = [n for n in nodes if n["type"] == "test_step_definition"]
        scenarios = [n for n in nodes if n["type"] == "test_scenario"]
        data_models = [n for n in nodes if n["type"] == "data_model"]
        endpoints = [n for n in nodes if n["type"] == "api_endpoint"]
        code_components = [n for n in nodes if n["type"] == "code_component"]
        ui_page_objects = [n for n in nodes if n["type"] == "ui_page_object"]
        utilities = [n for n in nodes if n["type"] == "test_utility"]
        dest_components = code_components + utilities + ui_page_objects

        proposed: list[dict] = []

        # Local shadow of CALLS edges proposed in step 1, keyed by step_id.
        # Used by step 3 (chained validation tracing) so it can see edges that
        # haven't been written to the store yet — preserving the original
        # behavior where step 1 wrote directly before step 3 read.
        _proposed_calls: dict[str, list[str]] = {}

        def clean_tokens(text: str) -> set:
            if not text:
                return set()
            return set(re.findall(r'\b[a-zA-Z]{3,}\b', text.lower()))

        def edge(src: str, tgt: str, rel: str, reason: str) -> None:
            proposed.append({
                "source_id": src,
                "target_id": tgt,
                "relationship": rel,
                "metadata": {"reason": reason},
            })

        # 1. Import-Based Dependency Linking (CALLS and REFERENCES Edges)
        for step in step_definitions:
            file_path = step.get("metadata", {}).get("source_file")
            if not file_path:
                continue

            doc_info = store.documents.get(file_path, {})
            content = doc_info.get("content", "")
            if not content:
                continue

            is_java_step = file_path.endswith(".java")
            is_ts_step = file_path.endswith((".ts", ".js", ".tsx", ".jsx"))

            # Extract Java/TS imports
            imports = []
            if is_java_step:
                imports = re.findall(r'import\s+([a-zA-Z0-9_\.]+);', content)
            elif is_ts_step:
                matches = re.finditer(
                    r'import\s+.*?\s+from\s+[\'"]([^\'"]+)[\'"]', content
                )
                imports = [m.group(1) for m in matches]

            for imp in imports:
                last_segment = imp.split('.')[-1].split('/')[-1]

                for comp in dest_components:
                    comp_file = comp.get("metadata", {}).get("source_file", "")

                    # Language Compatibility Check
                    is_java_comp = (
                        comp_file.endswith(".java")
                        or comp.get("metadata", {}).get("language") == "java"
                    )
                    is_ts_comp = (
                        comp_file.endswith((".ts", ".js", ".tsx", ".jsx"))
                        or comp.get("metadata", {}).get("language") == "typescript"
                    )

                    if is_java_step and not is_java_comp:
                        continue
                    if is_ts_step and not is_ts_comp:
                        continue

                    comp_name = comp.get("metadata", {}).get(
                        "class_name", comp.get("name", "")
                    )

                    # Exact case-insensitive match
                    if last_segment.lower() == comp_name.lower():
                        relationship = (
                            "REFERENCES" if comp["type"] == "ui_page_object" else "CALLS"
                        )
                        edge(
                            step["id"],
                            comp["id"],
                            relationship,
                            f"Static Import analysis reference: {imp}",
                        )
                        if relationship == "CALLS":
                            _proposed_calls.setdefault(step["id"], []).append(comp["id"])

        # 2. Inline Page Object Mention Heuristics (REFERENCES Edge with Enforced Language Isolation)
        for page in ui_page_objects:
            class_name = page.get("metadata", {}).get("class_name", "")
            if not class_name or class_name.lower() in [
                "page", "basepage", "webdriverbasetestpage"
            ]:
                continue

            page_file = page.get("metadata", {}).get("source_file", "")
            is_java_page = page_file.endswith(".java") or page.get("metadata", {}).get(
                "framework"
            ) in ["qaf_selenium", "selenium"]
            is_ts_page = page_file.endswith((".ts", ".js", ".tsx", ".jsx")) or page.get(
                "metadata", {}
            ).get("framework") == "playwright"

            # e.g., LoginPage -> loginPage
            camel_name = class_name[0].lower() + class_name[1:] if len(class_name) > 0 else ""

            for step in step_definitions:
                file_path = step.get("metadata", {}).get("source_file")
                if not file_path:
                    continue

                is_java_step = file_path.endswith(".java")
                is_ts_step = file_path.endswith((".ts", ".js", ".tsx", ".jsx"))

                # Enforce language isolation to prevent cross-framework linking
                if is_java_step and not is_java_page:
                    continue
                if is_ts_step and not is_ts_page:
                    continue

                doc_info = store.documents.get(file_path, {})
                content = doc_info.get("content", "")
                if not content:
                    continue

                if class_name in content or (camel_name and camel_name in content):
                    edge(
                        step["id"],
                        page["id"],
                        "REFERENCES",
                        f"Step definition references POM class: {class_name}",
                    )

        # 3. Chained Validation Tracing (TESTS Edge)
        for step in step_definitions:
            # Combine CALLS edges already in the store with locally proposed ones
            store_calls = store.get_edges(source_id=step["id"])
            store_call_ids = [
                e["target_id"] for e in store_calls if e["relationship"] == "CALLS"
            ]
            local_call_ids = _proposed_calls.get(step["id"], [])
            called_comp_ids = store_call_ids + local_call_ids

            for comp_id in called_comp_ids:
                called_comp = store.get_node(comp_id)
                if not called_comp:
                    continue
                comp_file = called_comp.get("metadata", {}).get("source_file")
                if not comp_file:
                    continue

                for ep in endpoints:
                    ep_file = ep.get("metadata", {}).get("source_file")
                    if ep_file and ep_file == comp_file:
                        edge(
                            step["id"],
                            ep["id"],
                            "TESTS",
                            "Heuristics: Step definition validates API via utility trace",
                        )

        # 4. Lombok/Record/Interface Step-to-Model & POM-to-Model Ingestion
        #    (USES Edge with Language Isolation)
        for model in data_models:
            class_name = model.get("metadata", {}).get("class_name", "")
            if not class_name:
                continue

            is_java_model = (
                model.get("metadata", {}).get("language") == "java"
                or model["id"].endswith("_java")
            )
            is_ts_model = (
                model.get("metadata", {}).get("language") == "typescript"
                or model["id"].endswith("_ts")
            )

            for step in step_definitions:
                file_path = step.get("metadata", {}).get("source_file")
                if not file_path:
                    continue

                is_java_step = file_path.endswith(".java")
                is_ts_step = file_path.endswith((".ts", ".js", ".tsx", ".jsx"))

                doc_info = store.documents.get(file_path, {})
                content = doc_info.get("content", "")
                if not content:
                    continue

                # Enforce language isolation unless there is a cross-language reference
                if is_java_step and not is_java_model:
                    has_same_lang_model = any(
                        m.get("metadata", {}).get("class_name", "").lower()
                        == class_name.lower()
                        and (
                            m.get("metadata", {}).get("language") == "java"
                            or m["id"].endswith("_java")
                        )
                        for m in data_models
                    )
                    if has_same_lang_model or class_name not in content:
                        continue
                if is_ts_step and not is_ts_model:
                    has_same_lang_model = any(
                        m.get("metadata", {}).get("class_name", "").lower()
                        == class_name.lower()
                        and (
                            m.get("metadata", {}).get("language") == "typescript"
                            or m["id"].endswith("_ts")
                        )
                        for m in data_models
                    )
                    if has_same_lang_model or class_name not in content:
                        continue

                has_builder_use = f"{class_name}.builder()" in content
                has_new_instantiation = f"new {class_name}(" in content
                has_ts_type_use = is_ts_step and (
                    f": {class_name}" in content
                    or f":{class_name}" in content
                    or f"<{class_name}>" in content
                    or f"as {class_name}" in content
                )

                if has_builder_use or has_new_instantiation or has_ts_type_use:
                    edge(
                        step["id"],
                        model["id"],
                        "USES",
                        f"Model usage reference: {class_name}",
                    )

            for sc in scenarios:
                desc = sc.get("description", "")
                name = sc.get("name", "")
                if class_name.lower() in desc.lower() or class_name.lower() in name.lower():
                    edge(
                        sc["id"],
                        model["id"],
                        "USES",
                        f"BDD scenario references model: {class_name}",
                    )

            # POM method parameter DTO USES edge
            for page in ui_page_objects:
                file_path = page.get("metadata", {}).get("source_file")
                if not file_path:
                    continue

                is_java_page = file_path.endswith(".java")
                is_ts_page = file_path.endswith((".ts", ".js", ".tsx", ".jsx"))

                doc_info = store.documents.get(file_path, {})
                content = doc_info.get("content", "")
                if not content:
                    continue

                # Enforce language isolation unless there is a cross-language reference
                if is_java_page and not is_java_model:
                    has_same_lang_model = any(
                        m.get("metadata", {}).get("class_name", "").lower()
                        == class_name.lower()
                        and (
                            m.get("metadata", {}).get("language") == "java"
                            or m["id"].endswith("_java")
                        )
                        for m in data_models
                    )
                    if has_same_lang_model or class_name not in content:
                        continue
                if is_ts_page and not is_ts_model:
                    has_same_lang_model = any(
                        m.get("metadata", {}).get("class_name", "").lower()
                        == class_name.lower()
                        and (
                            m.get("metadata", {}).get("language") == "typescript"
                            or m["id"].endswith("_ts")
                        )
                        for m in data_models
                    )
                    if has_same_lang_model or class_name not in content:
                        continue

                if class_name in content:
                    edge(
                        page["id"],
                        model["id"],
                        "USES",
                        f"Page Object uses data model: {class_name}",
                    )

        # 5. UI Page Object to Business Rule Overlap (VALIDATES Edge)
        business_rules = [n for n in nodes if n["type"] == "business_rule"]
        for page in ui_page_objects:
            page_name = page.get("name", "") + " " + page.get("description", "")
            page_tokens = clean_tokens(page_name)
            if not page_tokens:
                continue
            for rule in business_rules:
                rule_name = rule.get("name", "") + " " + rule.get("description", "")
                rule_tokens = clean_tokens(rule_name)
                if len(page_tokens.intersection(rule_tokens)) >= 2:
                    edge(
                        page["id"],
                        rule["id"],
                        "VALIDATES",
                        "Heuristics: UI Page Object overlap with business rule",
                    )

        log.info("Mapped %d specialized test relationships.", len(proposed))
        return proposed
