# Context Builder MCP Server

An agentic, multi-format document parser and knowledge representation engine built in Python. Designed to run as a **Model Context Protocol (MCP) Server** to perform the heavy lifting of workspace scanning, parsing heterogeneous documents (Word, Excel, CSV, PDF, Java, TypeScript, OpenAPI specifications, and Gherkin BDD Feature files), and compiling them into a **Semantic Context Graph** persisted as **JSON using NetworkX**.

Downstream AI agents in VS Code (like GitHub Copilot Chat, Cursor, Claude Desktop, or Roo-Code/Cline) can natively connect to this server to query context, trace business rules to technical implementations, and generate robust test suites automatically.

---

## Key Features

1. **Heterogeneous Ingestion Pipeline**: Dedicated parsing modules for:
   * **Word (`.docx`) & PDF**: Extracts headings, text, and converts complex tables directly into Markdown grids.
   * **Excel (`.xlsx`) & CSV**: Converts spreadsheets into semantic data matrices (ideal for insurance premium calculation grids).
   * **Java & TypeScript Source Files**: Regex annotation scanner designed to find Spring Boot controllers (`@RestController`, mapping paths) and TypeScript routes (NestJS controller annotations, Express routing blocks).
   * **BDD Gherkin (`.feature`)**: Parses features, scenarios, and Given/When/Then steps to model existing test coverage.
   * **OpenAPI specifications (`.json`/`.yaml`)**: Extracts paths, methods, response types, and definitions.
2. **JSON-backed Semantic Graph Store (NetworkX)**: Keeps track of logical nodes (`business_rule`, `product_feature`, `api_endpoint`, `data_model`, `code_component`, `test_scenario`) and directed edges (`IMPLEMENTS`, `VALIDATES`, `TESTS`, `MAPS_TO`).
3. **Hybrid Edge-Generator**: Combines deterministic heuristics (TF-IDF scoring) with host-LLM review to build semantic links between rules, endpoints, and tests.  The server packages ambiguous node pairs into a structured MCP prompt (`propose_edge_candidates`) that the host LLM — e.g. GitHub Copilot Chat — executes inside its own context window using its own credentials.  The server then validates and applies the resulting edges via `apply_edge_proposals`.  **No external API keys required.** No external network calls from the server.  The host LLM owns the model call; the server owns the graph.
4. **Lifecycle Hooks Registry**: Exposes lifecycle callbacks (`on_parse_start`, `on_document_parsed`, `on_entity_discovered`, `on_extraction_complete`) enabling pluggable custom logic or custom prompt overrides.

---

## Architecture Overview

```
context_builder/
├── db/
│   ├── graph_store.py      # Graph store, serialization, and helper functions
│   └── blueprint_store.py  # Decoupled blueprint rules, schemas, and templates
├── parsers/
│   ├── base.py             # Abstract base class parser contract
│   ├── word_parser.py      # Extracts paragraphs, tables, lists from Word documents
│   ├── excel_parser.py      # Parses sheet grids and CSV rows into Markdown tables
│   ├── pdf_parser.py        # Extracts raw pages and splits into semantic sections
│   ├── code_parser.py      # Spring Boot Java and Express/NestJS TS endpoint scanner
│   ├── feature_parser.py   # Parses Gherkin BDD scenario lists and steps
│   ├── schema_parser.py    # Reads OpenAPI specs, components, and data models
│   └── __init__.py         # Parser factory class mapped by file extension
├── engine/
│   ├── hooks.py            # Lifecycle hooks register (on_document_parsed, etc.)
│   ├── prompts/
│   │   └── edge_proposal.py  # MCP prompt template for Copilot-native edge review
│   └── extractor.py        # Pipeline coordinator and heuristic edge builder
├── tests/
│   └── run_test.py         # Programmatic integration and verification test
├── main.py                 # MCP Server entry point (using fastmcp)
├── requirements.txt        # PIP dependencies
└── README.md               # Quickstart and integration guide
```

---

## Installation & Setup

### 1. Install Dependencies
Ensure you have Python 3.10+ installed. Clone this repository or open the folder, and run:
```bash
pip install -r requirements.txt
```

### 2. Copilot Chat Integration
This server connects to VS Code Copilot Chat (or any MCP-compatible LLM host) via the standard MCP stdio transport.  No additional authentication is required beyond your existing Copilot subscription.

The **edge-augmentation workflow** is a two-step round-trip:
1. Call the `propose_edge_candidates` MCP prompt — the server returns a structured prompt containing ambiguous node pairs.
2. Copilot Chat executes that prompt with its own model and returns a JSON array.
3. Pass the JSON to `apply_edge_proposals` — the server validates, confidence-gates, and upserts the accepted edges with full provenance metadata.

The server makes zero outbound network calls.  All LLM inference is performed by the host (Copilot Chat).

---

## Run Integration Verification Test

To verify that the database schema, all specialized document parsers, extraction engine, and heuristic mapping systems are working perfectly together, execute the verification script:
```bash
python tests/run_test.py
```
This script programmatically generates mock Word, Excel, Java, TS, Feature, and OpenAPI files, runs the full ingestion pipeline, prints out the compiled graph structure from the graph store, and successfully completes without errors.

---

## Integrating with VS Code IDEs

As an MCP Server, this server connects natively to IDE tools that support the stdio transport protocol.

### 1. Cursor IDE Setup
1. Open Cursor Settings (**Settings** -> **Features** -> **MCP**).
2. Click **+ Add New MCP Server**.
3. Fill out the fields:
   * **Name**: `ContextBuilder`
   * **Type**: `stdio`
   * **Command**: `python /absolute/path/to/context_builder/main.py`
4. Click **Save**. The server status should display a green dot showing it's successfully connected.

### 2. Roo-Code / Cline (VS Code Extension)
Add the server configuration to your global `cline_mcp_settings.json` (typically stored in `~/Library/Application Support/Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json`):
```json
{
  "mcpServers": {
    "context-builder": {
      "command": "python",
      "args": ["/absolute/path/to/context_builder/main.py"],
      "env": {},
      "disabled": false
    }
  }
}
```

---

## Protocol Interface Reference

Once connected, downstream agents can interact with the server via the following protocol endpoints:

### MCP Tools (Callable Actions)

* **`ingest_workspace(workspace_path: str)`**: Scans the workspace directory, runs parsers, and builds the Semantic Context Graph.
* **`query_semantic_graph(query: str, node_type: str = None)`**: Performs a keyword search on the database. Optional node types: `business_rule`, `product_feature`, `api_endpoint`, `data_model`, `code_component`, `test_scenario`.
* **`get_rule_traceability(rule_id: str)`**: Walk the graph starting from an insurance rule and return all connected tech endpoints and scenarios.
* **`query_framework_blueprint(target_file_path: str)`**: Queries the framework blueprint to extract coding standards, architectural rules, and a global index of reusable utility methods applicable to the target file path.
* **`get_framework_generation_blueprint()`**: Retrieves the green-field scaffolding layout rules, class code templates, and Playwright MCP exploration guidelines required to build out a new framework.

### MCP Resources (Exposed Data Sources)

* **`context://graph/summary`**: Returns node counts, edge counts, and schema summaries.
* **`context://rules/{rule_id}`**: Exposes complete structured properties for a target business rule.

### MCP Prompts (AI Instruction Templates)

* **`generate_bdd_tests(rule_id: str)`**: Packages the selected business rule along with all its traced endpoints, schemas, and fields into a tailored context-rich prompt template, telling Copilot Chat exactly how to write a premium Cucumber Gherkin feature file.
* **`generate_api_tests(endpoint_id: str)`**: Guides the test generator agent in writing integration tests in Java (JUnit/RestAssured) or TS.

---

## How to Use Framework Blueprints & Standards

The **Decoupled Framework Blueprint & Coding Standards Engine** provides programmatic and tool-based rules to ensure that generated frameworks and Page Object Models match exact repository guidelines.

### 1. Modifying Blueprint Configurations (For Engineers)
On first run, the Context Builder automatically creates `.context_builder/blueprint.json` in your workspace. You can open and edit this file at any time to:
* Customize code directories or directory-specific rules (e.g. adding rules for `**/controllers/**`).
* Change base-page or page-object code boilerplate templates.
* Update locator priority strategies (e.g., placing `getByTestId` first) and naming conventions.

The system **loads your manual modifications directly on start and never overwrites them**.

### 2. Programmatic Usage (Python API)
You can instantiate and query the rules engine directly in Python scripts:

```python
from db.blueprint_store import BlueprintStore

# 1. Initialize the store in your project workspace
blueprint = BlueprintStore("/path/to/workspace")

# 2. Register directory standards and naming patterns
blueprint.register_directory_standard(
    "**/pages/**",
    "Page Object Model (POM)",
    ["Methods must wrap composite actions and avoid direct return values"]
)

# 3. Retrieve coding standards applicable to a target file path
standards = blueprint.get_standards_for_path("src/pages/BillingPage.ts")
print(standards["pattern"])  # "Page Object Model (POM)"
print(standards["rules"])    # ["Methods must wrap composite actions..."]

# 4. Register reusable utility signatures
methods = [{
    "method_name": "calculatePolicyOffset",
    "signature": "public static String calculatePolicyOffset(int days)",
    "parameters": {"days": "int"},
    "return_type": "String",
    "description": "Calculates offset dates."
}]
blueprint.register_utility_signature("util_date", "DateUtils", "src/utils/DateUtils.java", methods)

# 5. Fetch all registered utilities across the repository
all_utils = blueprint.get_all_reusable_methods()
```

### 3. IDE Agent Integration (Stdio MCP Client)
External agent extensions (e.g., Cursor, Roo-Code, Claude Desktop) automatically query these standards when writing or generating code:
* **`query_framework_blueprint`**: The agent passes the file path it is about to modify (e.g. `src/pages/AuthPage.ts`) to extract applicable coding styles, indentation rules, and available reusable utilities in the codebase.
* **`get_framework_generation_blueprint`**: Exploration agents call this when bootstrapping a zero-state directory to pull directory structures, class boilerplate templates, and prioritized locator priorities (e.g. `getByRole`, `getByPlaceholder`).
