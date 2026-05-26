# Context Builder Ingest Evaluation Report

## Scope

This report evaluates the current Context Builder ingestion pipeline and the checked-in ingest artifacts against:

- the runtime implementation in `main.py`, `engine/extractor.py`, `db/graph_store.py`, and `parsers/test_framework_parsers.py`
- the shipped agent contracts in `.github/agents/*.agent.md`
- the completed specifications in `specs/completed/*`
- the current sample corpus under `../ingest/*`
- the checked-in persisted artifacts under `../ingest/.context_builder/*`

This is an evaluator report only. No production code was modified.

## Method

The evaluation used four evidence sources:

1. Static review of the ingestion control path, parser logic, graph governance, and blueprint synthesis.
2. Direct inspection of the checked-in persisted artifacts in `../ingest/.context_builder`.
3. Focused regression execution against the ingest, parser, governance, and blueprint test suites.
4. Fresh isolated re-ingestion of a temporary copy of `../ingest` to distinguish stale persisted output from live pipeline behavior.

## Tool Overview

At a high level, Context Builder is an MCP server that ingests heterogeneous documentation and automation assets, builds a semantic graph, and exposes query and traceability tools.

- `main.py` exposes the public MCP surface, including workspace ingestion, semantic query, graph summary, traceability, sync checking, and blueprint lookup.
- `engine/extractor.py` is the main orchestration layer. It owns workspace scanning, parser dispatch, graph updates, heuristic relationship mapping, endpoint deduplication, utility capability harvesting, and blueprint synthesis.
- `db/graph_store.py` persists graph state and document caches, and includes the path-alignment and SSoT merge behavior introduced by the governance specifications.
- `parsers/test_framework_parsers.py` owns the framework-specific extraction that matters most for this corpus: Playwright-BDD step definitions and POMs, Java/QAF step classes, Selenium page objects, and test-side REST client extraction.

The shipped subagent contracts remain aligned with that architecture:

- `rule_extractor.agent.md` assumes stable rule identifiers and merged governance semantics.
- `tech_mapper.agent.md` assumes extraction of endpoints, models, step definitions, utilities, and page objects.
- `relationship_linker.agent.md` now documents `IMPLEMENTS`, `TESTS`, `VALIDATES`, `MAPS_TO`, `REFERENCES`, `CALLS`, and `USES` edges.

## Executive Summary

The current implementation is materially healthier than the checked-in ingest artifacts.

The code path now appears to contain the major fixes that the previous bug report called for:

- fresh ingestion eliminates stale `/fitness tracker/` path contamination
- fresh ingestion deduplicates the `/admin/test-data/reset` endpoint into a single node
- Playwright inline locators and Selenium-style Java page objects are being extracted as intended
- the focused parser, governance, drift, blueprint, and utility tests pass

However, the checked-in persisted artifacts under `../ingest/.context_builder` are still stale and do not reflect that improved behavior. They continue to fail the output regression suite and still carry pre-fix duplication and provenance contamination.

In addition, a fresh isolated ingest still shows several quality gaps relative to the authored specifications:

- cross-framework UI `REFERENCES` edges are still too loose
- this corpus does not currently produce `ClaimRequest` data-model nodes or corresponding `USES` links
- blueprint synthesis still undershoots the spec on framework naming and locator-priority inference

## Test Results

### Full Python Suite

`pytest`

- 57 passed
- 2 failed

The only failures were in `tests/test_ingest_output_regressions.py`:

1. `test_documents_cache_uses_current_workspace_paths_only`
2. `test_graph_normalizes_admin_reset_endpoint_to_single_identifier`

### Focused Regression Outcome

Focused execution of:

- `tests/test_ingest_output_regressions.py`
- `tests/test_framework_parsers.py`
- `tests/test_upsert_governance.py`
- `tests/test_blueprint_store.py`
- `tests/test_utility_compilation.py`

produced the same conclusion: parser and governance behavior is largely correct, but the checked-in persisted artifacts still fail the two ingest-output regressions.

### Fresh Isolated Ingest Outcome

A fresh isolated ingest run on a temporary copy of `../ingest` produced:

- `parsed_files`: 37
- `total_nodes`: 91
- `edge_count`: 216
- `legacy_document_path_count`: 0
- `legacy_source_file_count`: 0
- `admin_reset_count`: 1
- `target_meta_framework`: `Mixed Test Automation Framework (Playwright BDD & Cucumber Java)`

This demonstrates that the live pipeline behavior is better than the checked-in sample artifacts.

## Findings

### 1. Checked-in persisted artifacts are stale and still fail the repository’s own ingest regressions

Severity: High

The checked-in artifacts under `../ingest/.context_builder` do not represent the current code behavior.

Evidence:

- `tests/test_ingest_output_regressions.py` asserts that artifact paths must not contain `fitness tracker` and that `/admin/test-data/reset` must normalize to a single endpoint.
- `../ingest/.context_builder/documents.json` still contains the old absolute workspace root on every document key.
- `../ingest/.context_builder/graph.json` still contains two separate admin reset endpoint nodes:
  - `endpoint_post_admin_test_data_reset`
  - `endpoint_post_admin_testdata_reset`

Impact:

- the shipped sample output cannot be treated as a trustworthy golden artifact
- downstream evaluation of the tool will understate the current implementation quality
- repository-level `pytest` remains red until the checked-in artifacts are regenerated or replaced

Expected according to spec:

- `Ingestion Pipeline Pathology and SSoT Spec.md` requires path-alignment migration and endpoint deduplication
- `tests/test_ingest_output_regressions.py` encodes those requirements directly

Actual checked-in output:

- stale absolute path contamination remains
- endpoint duplication remains

Actual fresh output:

- no path contamination observed
- exactly one admin reset endpoint observed

Conclusion:

The implementation appears fixed for these two regressions, but the repository artifact snapshot is not current.

### 2. Broader SSoT convergence is still incomplete in the checked-in graph snapshot

Severity: High

The stale checked-in graph contains additional duplicate logical endpoints beyond the admin reset route.

Observed duplicate groups in the checked-in artifact:

- `POST /claims`
  - `endpoint_post_claims`
  - `endpoint_java_client_post_claims`
  - `endpoint_ts_client_post_claims`
- `GET /claims/{claimId}`
  - `endpoint_get_claims_claimid`
  - `endpoint_java_client_get_claims_claimid`
- `POST /admin/test-data/reset`
  - `endpoint_post_admin_test_data_reset`
  - `endpoint_post_admin_testdata_reset`

Impact:

- rule-to-endpoint and scenario-to-endpoint traceability is inflated
- entity counts in the persisted graph do not reflect the intended SSoT model
- downstream consumers can misread test clients as separate logical APIs rather than linked implementations

Expected according to spec:

- `Ingestion Pipeline Pathology and SSoT Spec.md` requires unified endpoint normalization and a post-ingest deduplication sweep based on verb and normalized path

Actual checked-in output:

- duplicate endpoint groups are still present

Actual fresh output:

- duplicate endpoint groups were not present in the isolated ingest result

Conclusion:

This is the same underlying pattern as the admin reset failure: the shipped artifact snapshot predates the current deduplication behavior.

### 3. Fresh ingestion still creates cross-framework `REFERENCES` edges that are too loose

Severity: Medium

The import-based reference logic is language-isolated, but the separate name-based page-object heuristic is not.

Observed fresh behavior:

- each Playwright step definition references the Playwright `MemberDashboardPage`, which is expected
- the same Playwright step definitions also reference the Java `MemberDashboardPage`, which is not expected for this corpus

Root cause in implementation:

- `engine/extractor.py` first applies language isolation in the import-based branch
- then the `Inline Page Object Mention Heuristics` branch adds `REFERENCES` edges whenever a class name string appears in the step file content, regardless of source language

Why this matters:

- the `Test Framework Parsers and Semantic Linking Spec.md` explicitly called for language isolation to prevent cross-framework contamination
- the current fresh output still overstates UI traceability by linking TS steps to Java POMs sharing the same class name

Conclusion:

This is no longer a broad cross-language `CALLS` contamination problem, but there is still a narrower cross-language `REFERENCES` contamination issue.

### 4. This corpus still misses payload-model extraction and `USES` edges for `ClaimRequest`

Severity: Medium

The shipped sample corpus contains both:

- a Java `ClaimRequest` record
- a TypeScript `ClaimRequest` interface

But a fresh isolated ingest produced:

- no `ClaimRequest` nodes
- no `ClaimRequest`-related edges
- no `USES` edges at all in the fresh graph for payload models

Why this matters:

- the framework specs call for `data_model` extraction and payload traceability from test steps and helpers
- the synthetic parser tests cover Lombok-style DTOs, but this real sample uses a Java `record` and a TS `interface`, which fall outside that narrower path

Expected according to spec:

- technical mapping should capture test-side payload models where they are part of execution traces
- step definitions and utility layers should link to the underlying request models they instantiate or pass through

Actual fresh output:

- the corpus has endpoint and utility coverage, but not the corresponding `ClaimRequest` model coverage

Conclusion:

This is a corpus-realistic residual blind spot. It is not exposed by the current repository tests, but it materially lowers output quality for downstream generation and traceability.

### 5. Blueprint synthesis is functional but still below the authored target quality

Severity: Medium

Fresh synthesis is no longer shallow root-only detection. It correctly detects a mixed framework and derives page and step directories from the corpus. That is an improvement over the old baseline.

However, three quality issues remain.

#### 5.1 Meta-framework naming is weaker than the spec target

Fresh output:

- `Mixed Test Automation Framework (Playwright BDD & Cucumber Java)`

Spec target:

- `Mixed Test Automation Framework (Playwright BDD & QAF Java)` or equivalent QAF-aware wording

Interpretation:

- the regression only checks that the output is not the old single-framework fallback, so it passes
- but the synthesized label still misses the stronger QAF identity expected by the spec

#### 5.2 Locator-priority inference does not reflect the extracted semantic locators

Fresh output:

- `locator_priority_strategy = ["id", "name", "css", "xpath", "link"]`

But the extracted Playwright POM clearly contains semantic locators:

- `inline_testid_dashboard_header`
- `inline_testid_plan_name`

Interpretation:

- the inference logic scans locator keys rather than locator values
- because the stored keys are synthetic labels, not raw `getBy...` strings, the semantic strategy branch is never triggered for this corpus

Impact:

- downstream browser exploration guidance is lower quality than the available evidence supports

#### 5.3 Directory scaffolding still uses documentation directories for feature sources

Fresh output scaffolding:

- `playwright/src/pages/`
- `playwright/features/step_definitions/`
- `shared/brd/`

Interpretation:

- page and step directories are appropriate
- feature/scenario scaffolding is still dominated by documentation-derived scenario nodes rather than concrete automation feature locations

Impact:

- the blueprint is usable, but not yet aligned with the stronger “reflect real POM and step-definition directories” requirement from the earlier evaluation criteria

Conclusion:

Blueprint synthesis is no longer failing at a regression level, but it still underfits the corpus compared with the authored design intent.

### 6. Parser and governance fixes that were previously missing are now present and working

Severity: Positive finding

The current implementation does satisfy several previously missing behaviors.

Confirmed strengths:

- Playwright inline semantic locators are extracted into the POM metadata.
- Standard Selenium-style Java page objects are ingested as `ui_page_object` nodes and retain their selector maps.
- The parser and governance test suites pass.
- The graph store includes path-alignment migration logic on initialization.
- Fresh isolated ingestion removes path contamination and collapses the admin reset endpoint as intended.

This is important context: the repository is not in the same state described by the old bug report. The remaining problems are concentrated in artifact freshness and a smaller set of residual quality gaps.

## Expected vs Actual Summary

### Expected According To Specs

- persisted artifacts should migrate to the active workspace root
- logical API endpoints should converge toward one node per endpoint
- Playwright and Java test framework assets should produce `ui_page_object`, `test_step_definition`, `api_endpoint`, `data_model`, and `test_utility` coverage where applicable
- step definitions should link to page objects and utilities without cross-language contamination
- blueprint synthesis should detect nested mixed frameworks, infer realistic scaffolding, and prefer semantic locator strategies when supported by the corpus

### Actual Checked-in Artifacts

- stale workspace paths still present
- duplicate endpoints still present
- graph still contains legacy noise from an older snapshot
- blueprint still reflects the weaker mixed-framework naming and non-semantic locator priority

### Actual Fresh Isolated Ingest

- path migration behavior is effectively fixed
- admin reset endpoint deduplication is effectively fixed
- Playwright inline locator extraction works
- Java Selenium POM classification works
- mixed-framework detection works at a regression-safe level
- residual gaps remain in cross-framework page references, payload-model extraction for this corpus, and blueprint synthesis quality

## Overall Assessment

The codebase appears to have incorporated the main fixes implied by the previous bug report, but the checked-in ingest artifacts have not been regenerated to match that improved behavior.

From an evaluator standpoint, the most important distinction is this:

- **artifact quality in the repository is still failing**
- **live ingestion quality is noticeably better, but not yet fully spec-complete**

If the goal is to judge the shipped repository state as a consumer would see it today, the answer is that the ingest output is still not publication-ready because the golden artifact snapshot is stale and still fails the repository’s own output regressions.

If the goal is to judge the implementation trajectory, the answer is more positive: the parser and governance fixes are largely in place, and the remaining issues are narrower and more quality-oriented than the original failures.

## Recommended Next Validation Focus

No fixes are proposed in this report, but these are the highest-value residual checks for future evaluation:

1. Assert that TypeScript step definitions cannot `REFERENCES`-link to Java page objects solely by shared class name.
2. Add corpus-level regression coverage for Java `record` and TypeScript `interface` request-model extraction.
3. Add blueprint regression checks for semantic locator priority and feature-directory scaffolding quality.
4. Regenerate the checked-in `../ingest/.context_builder` artifacts before treating them as authoritative sample output.
