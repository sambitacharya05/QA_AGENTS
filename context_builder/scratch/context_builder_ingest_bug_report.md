# Context Builder Ingest Output Bug Report

## Scope

This report compares the expected Context Builder behavior defined by the shipped specs and MCP surfaces against the currently persisted output under `ingest/.context_builder`.

The evaluation covers:

- the ingestion architecture in `context_builder/main.py`, `context_builder/engine/extractor.py`, and `context_builder/parsers/*`
- the shipped subagent contracts in `.github/agents/*.agent.md`
- the completed specifications in `context_builder/specs/completed/*`
- the actual sample corpus under `ingest/*`
- the current generated artifacts in `ingest/.context_builder/*`

This is a bug report, not a fix proposal. Production code was not modified.

## What The Tool Is Supposed To Do

At a high level, the tool ingests heterogeneous documentation and automation code from `./ingest`, extracts semantic entities, links them into a graph, and synthesizes a reusable blueprint for downstream generation agents.

Expected end-to-end behavior from the code and specs:

- `main.py` exposes MCP tools for ingestion, semantic querying, rule traceability, drift reporting, and blueprint lookup.
- `engine/extractor.py` orchestrates workspace scan order, parser dispatch, relationship mapping, utility harvesting, and blueprint synthesis.
- `db/graph_store.py` is supposed to preserve single-source-of-truth semantics via upsert and governance metadata.
- `db/blueprint_store.py` is supposed to persist coding standards, reusable capabilities, and framework generation blueprints.
- `parsers/test_framework_parsers.py` is supposed to ingest Playwright-BDD and QAF/Selenium assets as first-class technical components.

## Shipped Subagents

The repo ships three agent profiles that define the intended extraction model:

- `rule_extractor.agent.md`: extracts business rules and product features, and is explicitly instructed to reuse exact rule IDs for merged governance.
- `tech_mapper.agent.md`: extracts application/test APIs, data models, step definitions, page objects, and utilities.
- `relationship_linker.agent.md`: creates semantic edges such as `IMPLEMENTS`, `TESTS`, `VALIDATES`, and `MAPS_TO`.

Those contracts matter because the persisted graph should remain compatible with the ontology and merge rules they assume.

## Expected Output According To Specs

From the completed specs, the current corpus should produce the following qualities in the generated output.

### Parser And Ontology Expectations

- Playwright-BDD and QAF assets should produce `test_step_definition`, `ui_page_object`, `api_endpoint`, `data_model`, and `test_utility` nodes where appropriate.
- UI automation should maintain a direct step-to-page-object relationship. The POM spec explicitly calls for UI test steps to bind to `ui_page_object` components via `REFERENCES` edges.
- Page objects should surface locator metadata so downstream explorers can infer locator strategies and synthesize compatible templates.
- Java and TypeScript automation clients should map back to shared API endpoints without creating duplicate logical endpoint IDs.

### Governance Expectations

- node IDs should converge toward a single-source-of-truth model under the SUEI/upsert governance specification
- source provenance should remain coherent and point at the current corpus being ingested
- merged nodes should not accumulate contradictory path formats or fragment across duplicate IDs

### Blueprint Expectations

- framework detection should inspect manifests and infer the actual target meta-framework
- directory scaffolding should reflect real POM and step-definition directories, not be dominated by generic documentation directories
- reusable capabilities should point to real source files in the current workspace
- locator priority should adapt when semantic Playwright selectors such as `getByTestId` dominate the extracted POMs

## Actual Output Summary

The current run does ingest a broad set of assets from the sample corpus. The graph contains nodes for the BRD, scenario matrix, defects catalog, OpenAPI endpoints, schemas, Playwright steps, Java steps, and some automation components.

However, the persisted output is not trustworthy as a clean semantic index. The artifacts currently show:

- stale provenance from a different workspace
- duplicate logical identifiers for at least one endpoint
- parser classification failures for UI automation components
- blueprint contamination from phantom or stale utilities
- framework synthesis that ignores the nested Playwright-BDD and QAF manifests in the sample repo
- relationship noise that creates false traceability links
- ontology drift between documented node types and actual stored types

## Confirmed Findings

### Critical: Persisted Provenance Is Contaminated With A Different Workspace Root

Expected:

- `documents.json`, `graph.json`, and `blueprint.json` should describe the currently ingested `agents/ingest` corpus.
- provenance should be internally consistent and suitable for sync, drift, and traceability consumers.

Actual:

- `ingest/.context_builder/documents.json` stores keys under `/Users/sambitacharya/Documents/projects/fitness tracker/...`
- `ingest/.context_builder/graph.json` includes many `source_file` and `source_origins` values under the same stale `fitness tracker` root
- `ingest/.context_builder/blueprint.json` also carries stale absolute paths

Impact:

- sync and drift analysis are operating on corrupted provenance
- downstream consumers cannot safely determine what workspace the artifacts actually represent
- any future incremental re-ingestion risks merging current data with stale data

### Critical: Single-Source-Of-Truth Governance Is Violated By Duplicate Endpoint IDs

Expected:

- one logical endpoint should resolve to one normalized identifier after ingestion and upsert.

Actual:

- the same admin reset route appears as both `endpoint_post_admin_test_data_reset` and `endpoint_post_admin_testdata_reset` in `ingest/.context_builder/graph.json`

Impact:

- traceability splits across duplicate nodes
- relationship counts and endpoint coverage become unreliable
- the graph contradicts the SUEI and merge-governance specification

### High: Playwright POM Locator Extraction Misses Inline Semantic Locators

Expected:

- the Playwright sample page object should expose semantic selectors such as `getByTestId("dashboard-header")` and `getByTestId("plan-name")` in its metadata so blueprint synthesis can infer semantic locator priority.

Actual:

- `ingest/playwright/src/pages/memberDashboardPage.ts` uses inline `getByTestId(...)` calls inside methods
- the generated `pw_pom_memberdashboardpage` node in `ingest/.context_builder/graph.json` reports zero harvested locator definitions
- `ingest/.context_builder/blueprint.json` falls back to `id`, `name`, `css`, `xpath`, `link` rather than semantic Playwright strategies

Impact:

- the page object layer loses the most important UI traceability signal from the Playwright sample
- downstream explorers and generators will produce weaker selector strategies than the source corpus actually demonstrates

### High: The Java Member Dashboard Page Is Misclassified As A Utility Instead Of A UI Page Object

Expected:

- `ingest/java-qaf/src/main/java/com/mockhealth/pages/MemberDashboardPage.java` should ingest as a `ui_page_object` because it encapsulates UI locators and actions.

Actual:

- the persisted output models it as `util_memberdashboardpage`
- `ingest/.context_builder/blueprint.json` then treats it as a reusable utility capability rather than a page-object artifact

Impact:

- UI automation structure is misrepresented
- blueprint synthesis learns the wrong architectural pattern from the Java sample
- traceability from UI steps to UI components is degraded

### High: Blueprint Reusable Capabilities Reference Phantom Or Stale Files

Expected:

- every reusable capability in `blueprint.json` should point to a real file in the current workspace and should be derived from actual harvested utilities.

Actual:

- `util_playwright_common` points to `src/utils/PlaywrightCommon.ts`, which does not exist in the sample corpus
- `util_memberdashboardpage` points to a stale absolute path under the `fitness tracker` workspace

Impact:

- downstream code generation may rely on capabilities that do not exist
- the blueprint no longer functions as a trustworthy source of reusable building blocks

### High: Framework Synthesis Ignores Nested Manifests And Falls Back To Standard Playwright Mode

Expected:

- the synthesizer should inspect manifests and detect the actual framework mix present in the sample corpus
- the sample corpus contains a nested Playwright package with `playwright-bdd` and a nested Maven project with QAF dependencies

Actual:

- `ingest/.context_builder/blueprint.json` reports `Playwright TypeScript (Standard Mode)`
- the output does not reflect a mixed Playwright-BDD plus QAF/Selenium corpus

Impact:

- downstream generation templates are aligned to the wrong framework posture
- framework-specific conventions from the existing sample code are missed

### Medium: UI Step-To-Page Traceability Uses The Wrong Edge Semantics

Expected:

- the POM ingestion specification calls for UI step definitions to bind to page objects through `REFERENCES` edges.

Actual:

- the current graph connects Playwright step definitions to the page object with `MAPS_TO`

Impact:

- the graph weakens the distinction between UI references and equivalence mapping
- downstream consumers cannot rely on the documented relationship semantics

### Medium: Relationship Heuristics Produce False-Positive Traceability

Expected:

- heuristic edges should be conservative enough that scenario-to-rule and component-to-rule links remain meaningfully precise.

Actual:

- generic documentation headings from the BRD are linked to unrelated endpoints and rules
- Playwright step definitions generate `CALLS` edges to unrelated Java test components because the static import matching is too loose
- scenarios accumulate links to unrelated endpoints such as the admin reset route

Impact:

- graph search and traceability become noisy
- agent consumers may overstate coverage or infer incorrect implementation status

### Medium: The Stored Ontology No Longer Matches The Public MCP Contract

Expected:

- the node types returned by ingestion should remain compatible with the types documented in `README.md` and consumed by `main.py` MCP resources and prompts.

Actual:

- the graph contains richer custom rule-like types such as `claims`, `billing`, `access_control`, `enrollment`, and `non_functional`
- MCP resources such as `get_rule_detail` are still centered on `business_rule`

Impact:

- parts of the graph may be invisible or weakly addressed by the MCP-facing tools
- the persisted artifact and the published contract are drifting apart

## What Is Working Well Enough To Count As Partial Success

Not everything is broken. The current run still demonstrates some valid extraction behavior:

- Playwright and Java step definitions are discovered as `test_step_definition` nodes
- Java and TypeScript test API clients for `POST /claims` are extracted and mapped to the shared endpoint
- scenario-to-endpoint and scenario-to-rule links sometimes land on correct targets before heuristic noise expands them
- the graph does contain a broad sample of BRD, schema, OpenAPI, feature, and automation inputs

## Findings That Should Not Be Reported As Parser Bugs

The scenario matrix lists a much larger set of expected journeys than the current sample framework code actually implements. That matters.

The following should be treated as sample-corpus limitations unless additional source files are added:

- missing Playwright or Java implementations for all `S01` through `S12` matrix entries
- absent framework-specific files for some negative or seeded-defect scenarios
- incomplete step coverage for matrix rows that have no corresponding source artifact in `ingest/playwright` or `ingest/java-qaf`

Those are not parser defects by themselves. They only become parser defects if the source asset exists and the ingest output still fails to represent it.

## Test-Only Implementation Added In This Session

To make the findings durable without changing production code, the following regression coverage was added:

- `tests/test_framework_parsers.py`
  - strict `xfail` for Playwright inline `getByTestId` locator harvesting from the sample page object
  - strict `xfail` for Java Selenium-style page-object classification on the sample `MemberDashboardPage.java`
- `tests/test_ingest_output_regressions.py`
  - strict `xfail` for stale workspace paths in `documents.json`
  - strict `xfail` for missing or phantom `blueprint.json` capability sources
  - strict `xfail` for duplicated admin reset endpoint IDs in `graph.json`
  - strict `xfail` for fallback `Playwright TypeScript (Standard Mode)` framework synthesis

These tests document current defects while keeping the suite executable and readable.

## Current Verification Status

Executed during this session:

- `pytest tests/test_framework_parsers.py -q` -> `8 passed, 2 xfailed`
- `pytest tests/test_ingest_output_regressions.py -q` -> `4 xfailed`

That means the added regression coverage is wired correctly and is currently describing real, still-open defects rather than speculative ones.
