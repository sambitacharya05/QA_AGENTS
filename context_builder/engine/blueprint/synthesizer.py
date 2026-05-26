"""Blueprint framework detection, directory scaffolding, and template inference.

Extracted verbatim from ``ContextExtractor._synthesize_blueprint()``
in ``engine/extractor.py`` (Spec 003 — Wave 2).

Key difference from the original: ``BlueprintStore`` is *injected* rather than
constructed inside this method — satisfying Acceptance Criterion 3 (single
construction per ``ContextExtractor``).
"""

from __future__ import annotations

import logging
import os
from collections import Counter
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from db.graph_store import GraphStore
    from db.blueprint_store import BlueprintStore

log = logging.getLogger(__name__)


class BlueprintSynthesizer:
    """Runs heuristics over the active :class:`~db.graph_store.GraphStore` nodes
    and ingested folder structure to dynamically detect the framework, scaffolding
    directories, and page object templates.

    The ``BlueprintStore`` is injected so it is constructed exactly once per
    ``ContextExtractor`` instance.
    """

    def __init__(
        self,
        store: "GraphStore",
        blueprint: "BlueprintStore",
        workspace_path: str,
    ):
        self.store = store
        self.blueprint = blueprint  # injected — NOT constructed inside
        self.workspace_path = workspace_path

    def synthesize(self) -> None:
        """Detect framework, mine dominant folders, infer templates and locator
        priorities, then persist into ``self.blueprint``."""

        # Check if the blueprint is marked as locked by the developer
        standards_global = (
            self.blueprint.blueprint_data.get("coding_standards", {}).get("global", {})
        )
        if standards_global.get("is_locked", False):
            log.info(
                "Blueprint configuration is locked. Skipping dynamic blueprint synthesis."
            )
            return

        # 1. Framework Detection Heuristic (Recursive Manifest Discovery Sweep)
        detected_frameworks: list[str] = []
        package_jsons: list[str] = []
        pom_xmls: list[str] = []
        requirements_txts: list[str] = []

        for root, _, files in os.walk(self.workspace_path):
            if any(p in root for p in [".context_builder", "node_modules", ".git", "target", "bin"]):
                continue
            for f in files:
                f_path = os.path.join(root, f)
                if f == "package.json":
                    package_jsons.append(f_path)
                elif f == "pom.xml":
                    pom_xmls.append(f_path)
                elif f == "requirements.txt":
                    requirements_txts.append(f_path)

        for pkg in package_jsons:
            try:
                with open(pkg, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                    if "playwright-bdd" in content:
                        detected_frameworks.append("Playwright TypeScript (Strict BDD Mode)")
                    elif "@playwright/test" in content:
                        detected_frameworks.append("Playwright TypeScript (Standard Mode)")
            except Exception:
                pass

        for pom in pom_xmls:
            try:
                with open(pom, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                    if "com.qmetry" in content or "qaf" in content:
                        detected_frameworks.append("QAF Java Framework")
                    else:
                        detected_frameworks.append("Cucumber Java Selenium Framework")
            except Exception:
                pass

        for _req in requirements_txts:
            detected_frameworks.append("Pytest Python Framework")

        unique_frameworks: list[str] = []
        for f in detected_frameworks:
            if f not in unique_frameworks:
                unique_frameworks.append(f)

        if not unique_frameworks:
            framework = "Playwright TypeScript (Standard Mode)"
        elif len(unique_frameworks) == 1:
            framework = unique_frameworks[0]
        else:
            short_name_map = {
                "Playwright TypeScript (Strict BDD Mode)": "Playwright BDD",
                "Playwright TypeScript (Standard Mode)": "Playwright Standard",
                "QAF Java Framework": "QAF Java",
                "Cucumber Java Selenium Framework": "Cucumber Java",
                "Pytest Python Framework": "Pytest Python",
            }
            short_names = [short_name_map.get(f, f) for f in unique_frameworks]
            framework = f"Mixed Test Automation Framework ({' & '.join(short_names)})"

        self.blueprint.blueprint_data["generation_blueprints"]["target_meta_framework"] = framework

        # 2. Dynamic Directory Scaffolding Miner (Enforcing Document Formats Exclusion)
        scenarios = self.store.query_nodes(node_type="test_scenario")
        pages = self.store.query_nodes(node_type="ui_page_object")
        steps = self.store.query_nodes(node_type="test_step_definition")

        scaffolding: dict[str, str] = {}

        def extract_dominant_relative_dir(
            nodes_list: list[dict],
            relative_to: str,
            exclude_docs: bool = False,
        ) -> Optional[str]:
            paths: list[str] = []
            for n in nodes_list:
                f_path = n.get("metadata", {}).get("source_file")
                if f_path:
                    if os.path.isabs(f_path):
                        abs_path = f_path
                    else:
                        abs_path = os.path.normpath(os.path.join(relative_to, f_path))

                    if os.path.exists(abs_path):
                        if exclude_docs:
                            ext = os.path.splitext(abs_path.lower())[1]
                            if ext in [".md", ".docx", ".xlsx", ".pdf"]:
                                continue
                        rel = os.path.relpath(
                            os.path.dirname(abs_path), relative_to
                        ).replace('\\', '/')
                        if not rel.startswith('..'):
                            paths.append(rel)
            if not paths:
                return None
            most_common = Counter(paths).most_common(1)[0][0]
            return most_common + "/" if not most_common.endswith('/') else most_common

        pages_dir = extract_dominant_relative_dir(pages, self.workspace_path)
        steps_dir = extract_dominant_relative_dir(steps, self.workspace_path)
        features_dir = extract_dominant_relative_dir(
            scenarios, self.workspace_path, exclude_docs=True
        )

        if pages_dir:
            scaffolding[pages_dir] = "Isolated Page Object Class models wrapping elements and actions"
        if steps_dir:
            scaffolding[steps_dir] = "BDD Cucumber step definition implementation files"
        if features_dir:
            scaffolding[features_dir] = "Pure Gherkin business scenario requirements blueprints"

        if scaffolding:
            self.blueprint.blueprint_data["generation_blueprints"]["directory_scaffolding"] = scaffolding

        # 3. Dynamic Template Inference (Learning from Code)
        if pages:
            sample_page = None
            for p in pages:
                f_path = p.get("metadata", {}).get("source_file")
                if f_path:
                    if os.path.isabs(f_path):
                        abs_path = f_path
                    else:
                        abs_path = os.path.normpath(os.path.join(self.workspace_path, f_path))
                    if os.path.exists(abs_path):
                        sample_page = abs_path
                        break

            if sample_page:
                try:
                    with open(sample_page, 'r', encoding='utf-8', errors='ignore') as f:
                        content_str = f.read()

                    class_name = pages[0].get("metadata", {}).get("class_name", "")

                    if class_name:
                        templated = content_str.replace(class_name, "${className}")
                        if sample_page.endswith(('.ts', '.js')):
                            self.blueprint.blueprint_data["generation_blueprints"][
                                "scaffolding_templates"
                            ]["page_object_ts"] = templated
                        elif sample_page.endswith('.java'):
                            self.blueprint.blueprint_data["generation_blueprints"][
                                "scaffolding_templates"
                            ]["page_object_java"] = templated
                except Exception:
                    pass

        # 4. Dynamic Locator & Casing Strategy Inference (Scanning Values Instead of Keys)
        all_locators_values: list[str] = []
        for p in pages:
            locs = p.get("metadata", {}).get("locators", {})
            if isinstance(locs, dict):
                all_locators_values.extend(locs.values())

        semantic_count = sum(
            1 for v in all_locators_values
            if isinstance(v, str) and ("getBy" in v or v.startswith("getBy"))
        )
        if semantic_count > 0:
            self.blueprint.blueprint_data["generation_blueprints"]["mcp_exploration_rules"][
                "locator_priority_strategy"
            ] = ["getByRole", "getByLabel", "getByPlaceholder", "getByText", "getByTestId", "css", "xpath"]
        else:
            self.blueprint.blueprint_data["generation_blueprints"]["mcp_exploration_rules"][
                "locator_priority_strategy"
            ] = ["id", "name", "css", "xpath", "link"]

        self.blueprint.save_blueprint()
