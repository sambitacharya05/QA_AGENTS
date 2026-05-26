---
name: document-parsing
description: Routing map that tells the @build_context agent which document type is handled by which parser/agent layer, and what graph nodes each produces. Updated for the 6-agent architecture (SPEC-1/2/3).
---
# Agent Skill: Document Parsing — Routing & Node Ownership

This skill teaches the `@build_context` agent the **routing logic** for the two-layer parsing pipeline:
- **Layer 1 (Python structural parsers)** — low-level, deterministic, no LLM. Runs during `ingest_workspace` (Stage 1 of `/ingest`).
- **Layer 2 (LLM extractor agents)** — semantic, typed, high-precision. Runs in parallel during Stage 3 of `/ingest`.

Use this skill to understand which file types go where, what node types each route produces, and what is **explicitly out of scope**.

---

## 🗺️ Master Routing Table

| File type / pattern | Python parser (Layer 1) | LLM agent (Layer 2) | Nodes produced |
|---|---|---|---|
| `.docx`, `.pdf`, `.md` (narrative prose / BRDs) | `WordParser` / `PDFParser` / `MarkdownParser` — extracts raw text + headings | `rule-extractor` | `business_rule`, `validation_rule`, `eligibility_rule`, `ui_business_rule`, `security_rule`, `product_feature` |
| `.xlsx`, `.csv` — **field-spec layout** (≥3 of: Field ID, Field Name, Type, Validation, Error Message, Mandatory …) | `ExcelParser` — per-row decomposition into `field_specification` nodes | `field-spec-parser` | `field_specification` |
| `.xlsx`, `.csv` — **narrative / data sheet** (no field-spec header) | `ExcelParser` — one legacy `business_rule` blob per sheet | `rule-extractor` (reads via raw text) | `business_rule` (from narrative sheets); data sheets are metadata-only on scenario nodes |
| `.json` / `.yaml` / `.yml` with `openapi:` / `swagger:` at top | `SchemaParser` — base node | `api-contract-mapper` | `api_endpoint` (per path+method), `data_model` (per schema) |
| `.json` with `$schema` or `"type": "object"` at top (standalone JSON schema) | `SchemaParser` | `api-contract-mapper` | `data_model` |
| `.java` / `.ts` — **API test code** (RestAssured chains, Playwright `request.post()`, Lombok `@Data`) | `CodeParser` — base scan | `api-contract-mapper` | `api_endpoint` (inferred from test calls), `data_model` (from DTO classes / TS interfaces), `code_component` (API client wrappers only) |
| `.feature` (Gherkin BDD) | `FeatureParser` — base node | `test-infra-mapper` | `test_scenario` |
| `.java` — **Page Objects** (`@FindBy`, `extends WebDriverTestPage`) | `CodeParser` | `test-infra-mapper` | `ui_page_object`, `ui_element` |
| `.ts` — **Page Objects** (`page.locator()`, `page.getByRole()`) | `CodeParser` | `test-infra-mapper` | `ui_page_object`, `ui_element` |
| `.java` — **Step definitions** (`@QAFTestStep`, `@Given`/`@When`/`@Then`) | `CodeParser` | `test-infra-mapper` | `code_component` |
| `.ts` — **BDD step files** (`defineStep`, Playwright-BDD hooks) | `CodeParser` | `test-infra-mapper` | `code_component` |
| `.properties` / `.loc` — **config / locator files** | `PropertiesParser` (Wave 2) — per-key decomposition | `test-infra-mapper` (wires `MAPS_TO` edges only; does NOT create nodes) | `rule_constant` (from Python parser); `ui_element` (from locator `.loc` files) |
| `.java` / `.ts` — **under `/utils/`, `/utilities/`, `/helpers/`, `/common/`, `/shared/`** | `CodeParser` | `coding-standards-extractor` | `test_utility` (enriched with method signatures + `coding_standards_observation` metadata) |

---

## ❌ Out of Scope — Do Not Route These

The following file types were handled by the **old single-agent** skill. They are **out of scope** for the QA-team use case and must NOT produce nodes:

| File pattern | Why excluded |
|---|---|
| Spring `@RestController` / `@Service` / `@Repository` classes | Application backend code, not QA-team framework. Routing to `api-contract-mapper` is incorrect. |
| NestJS `@Controller`, Express `app.get()` / `app.post()` route registrations | Same — application backend, not test framework. |
| Vendor/dependency directories (`node_modules/`, `target/`, `build/`, `.gradle/`, `venv/`) | Auto-generated or third-party; not ingest targets. The walk prunes these. |
| Diagrams, glossary pages, stakeholder lists (from Word/PDF) | Not normative statements — do not produce rule nodes. |

---

## 📋 Word & PDF Parsing — Layer Details

### Python parser (Layer 1)
- Splits text into logical segments using headings (`Heading 1`, `Heading 2`).
- Groups contiguous paragraphs under the nearest preceding heading.
- Parses tables into Markdown format `| Head 1 | Head 2 |` and stores them in raw text.
- Outputs: raw text to be passed to `rule-extractor` via `get_raw_documents`.

### `rule-extractor` agent (Layer 2)
- Scans for normative language: *shall*, *must*, *should not*, *is required to*, *cannot*, *only when*, *between X and Y*, *no more than N*, *must not exceed*.
- Each normative clause → one atomic rule node (not one node per FR-XX section).
- Node types emitted: `business_rule`, `validation_rule`, `eligibility_rule`, `ui_business_rule`, `security_rule`, `product_feature`.
- NFR metrics (Performance, Accessibility, Compatibility) → `business_rule` with `metadata.rule_origin = "nfr"`.
- **Does NOT** call `add_edge`. Edge creation belongs to `relationship-linker`.

---

## 📊 Excel & CSV Parsing — Layer Details

### Sheet classification (Python `excel_parser.py` — Layer 1)

The parser classifies each sheet before emitting nodes:

1. **Field-spec sheet** — header row contains ≥3 of: `field_id`, `field_name`, `type`, `length`, `mandatory`, `validation`, `error_message`, `default`, `description`, `placeholder`, `element_type`, `selector`.
   - Emits one `field_specification` node per non-empty data row.
   - ID pattern: `field_spec_<lowercase_field_id>` (e.g., `field_spec_fld_dob`).

2. **Dataresource sheet** — header row contains a column literally named `reference`.
   - Kept as legacy one-node-per-sheet (raw text only). Test data linkage is recorded as metadata on `test_scenario` nodes, NOT as new node types.

3. **Narrative/data sheet** — everything else.
   - Legacy one-node-per-sheet `business_rule` blob. `rule-extractor` agent reads it via raw text.

### `field-spec-parser` agent (Layer 2)
- Receives field-spec sheets only. Applies the same header detection logic as the Python parser.
- For each row, enriches the parser-created `field_specification` node with semantic MAPS_TO edges to matching rule nodes.
- **Does NOT** decompose multi-clause validations into separate rule nodes (that's `rule-extractor`'s job from the BRD prose).

---

## 💻 Java & TypeScript — Layer Details

> ⚠️ **Critical scope boundary**: the agent layer does **NOT** parse Spring `@RestController`, `@Service`, NestJS `@Controller`, or Express route registrations. That is application backend code. Only test-framework artefacts are in scope.

### `api-contract-mapper` agent

Handles (in priority order):
1. **OpenAPI / Swagger specs** (`.json`/`.yaml` with `openapi:`/`swagger:` at top):
   - One `api_endpoint` per `paths.<path>.<method>` tuple.
   - One `data_model` per `components.schemas.<name>`.
   - Emits `USES_MODEL` edge when `$ref` is explicit.
2. **Standalone JSON schemas** (`.json` with `$schema` or `"type": "object"`):
   - One `data_model` per schema.
3. **Java RestAssured test code** (`RestAssured.given()...post|get(...)` chains):
   - Matches path against existing `api_endpoint` nodes. If match exists → skip. If not → new `api_endpoint` with `metadata.inferred_from = "test_code"`.
4. **TypeScript Playwright API tests** (`request.post(...)`, `apiContext.get(...)`):
   - Same match-or-create logic as RestAssured.
5. **Lombok `@Data`/`@Builder` classes** and **TS `interface`/`type`**:
   - One `data_model` per class/interface. Fields stored in `metadata.fields` array — no per-field child nodes.

### `test-infra-mapper` agent

Handles test-execution-layer Java/TS artefacts:
1. **Java `@FindBy` page objects**: one `ui_page_object` per class; one `ui_element` per `@FindBy` field.
2. **TS `page.locator()` / `page.getByRole()` / `page.getByLabel()` page objects**: same pattern.
3. **Java `@QAFTestStep` / `@Given`/`@When`/`@Then` step definitions**: one `code_component` per class.
4. **Playwright-BDD TS step files** (`defineStep`, `Given`/`When`/`Then` registrations): one `code_component` per file.
5. **Fallback for `.loc` / locator `.properties` files**: only if page-class scan produced zero `ui_element` nodes; each `KEY=selector` line → one `ui_element`.

### `coding-standards-extractor` agent

Handles utility/helper code:
- Scans folders matching `/(utils|utilities|helpers|common|shared)/i` first; then `lib/`, `base/`, `framework/` if needed.
- One `test_utility` node per class (not per method). Methods captured in `metadata.public_methods`.
- Records `coding_standards_observation` metadata blocks on representative `ui_page_object`, `code_component`, and `data_model` nodes to feed `blueprint.json`.

---

## 🥒 Gherkin BDD — Layer Details

### Python `FeatureParser` (Layer 1)
- Extracts a base node per feature file. Passes raw text to agent.

### `test-infra-mapper` agent (Layer 2)

Critical rules (deviating from old skill):

1. **One `test_scenario` per Scenario block, ONE per Scenario Outline** — NOT one per Examples row. The "63 nodes for 25 outlines" bug from the review is fixed here.
2. **Background steps merged** — Feature Background steps are prepended to every scenario's step list, tagged `"source": "background"`.
3. **Scenario Outline Examples** — captured in `metadata.examples` as an array of row dicts. Not expanded.
4. **Tags captured**: union of feature-level + scenario-level `@tags` stored in `metadata.tags`.
5. **BRD reference comments**: `# BRD Reference: FR-XX, FR-YY` header lines → `metadata.brd_references = ["FR-02"]`. The `relationship-linker` uses these for high-confidence `TESTS` edges.
6. **External data linkage** (QAF metadata/dataresource pattern): when a scenario's `@tag` matches a row ID in a metadata Excel sheet, the path is recorded in `scenario.metadata.external_data_source`. No new node type is created for data files.

---

## ⚙️ Properties & Config Files — Layer Details

### Python `PropertiesParser` (Layer 1 — Wave 2)
- Handles `application.properties`, `bootstrap.properties`, `pom.properties`, and any `.properties` file without locator-strategy markers.
- Per-key emission: one `rule_constant` node per `KEY=value` line.
- ID pattern: `rule_constant_<key_with_dots_as_underscores>` (e.g., `dob.min.age=18` → `rule_constant_dob_min_age`).
- Infers value type (`integer`, `float`, `boolean`, `list`, `string`).
- Nodes carry `sync_governance.origin = "documentation"` — they are **governance-locked**. Agents may not overwrite them.

### `test-infra-mapper` agent (Layer 2)
- Does NOT create `rule_constant` nodes (Python handles creation).
- Detects parser-created `rule_constant` nodes via `query_semantic_graph("", "rule_constant")`.
- Wires `MAPS_TO` edges to matching rule nodes (confidence ≥ 0.6).

---

## 🔗 Edge Ownership Summary

Edges are **never** created by extractor agents except:
- `field-spec-parser`: `MAPS_TO` (field_specification → rule) with confidence ≥ 0.6.
- `api-contract-mapper`: `USES_MODEL` (api_endpoint → data_model) when source carries explicit `$ref`.
- `test-infra-mapper`: `REFERENCES` (ui_page_object → ui_element) and `MAPS_TO` (rule_constant → rule).

All `TESTS`, `IMPLEMENTS`, `VALIDATES`, `CALLS`, `USES_DATA`, and `PART_OF` edges are emitted exclusively by the **`relationship-linker`** in Stage 4 of `/ingest`.
