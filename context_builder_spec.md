# Context Builder - Technical Specification

## Overview
**Context Builder** is an agentic, multi-format document parser and knowledge representation engine built in Python. Designed to run as a **Model Context Protocol (MCP) Server**, it performs the heavy lifting of scanning workspaces, parsing heterogeneous documents, and compiling them into a **Semantic Context Graph** persisted as JSON using NetworkX.

The system acts as a foundational backend that downstream AI agents in VS Code (like GitHub Copilot Chat, Cursor, Claude Desktop, or Roo-Code/Cline) can connect to natively. It enables them to query context, trace business rules to technical implementations, and automatically generate robust test suites.

## Key Features
1. **Heterogeneous Ingestion Pipeline**: Dedicated parsing modules tailored for diverse formats:
   - **Word (`.docx`) & PDF**: Extracts text, headings, and converts complex tables directly into Markdown grids.
   - **Excel (`.xlsx`) & CSV**: Converts spreadsheets into semantic data matrices (e.g., insurance premium calculation grids).
   - **Java & TypeScript Source Files**: Uses a regex annotation scanner to identify Spring Boot controllers (`@RestController`, mappings) and TypeScript routes (NestJS, Express routing).
   - **BDD Gherkin (`.feature`)**: Parses features, scenarios, and Given/When/Then steps to model existing test coverage.
   - **OpenAPI Specifications (`.json`/`.yaml`)**: Extracts paths, methods, response types, and definitions.
2. **JSON-backed Semantic Graph Store**: Utilizes NetworkX to maintain logical nodes (`business_rule`, `product_feature`, `api_endpoint`, `data_model`, `code_component`, `test_scenario`) and directed edges (`IMPLEMENTS`, `VALIDATES`, `TESTS`, `MAPS_TO`).
3. **Hybrid Edge-Generator**: Combines deterministic TF-IDF scoring heuristics with host-LLM reviews (via Copilot/Cursor) to establish semantic links. Ambiguous pairs are packaged into a structured prompt (`propose_edge_candidates`) executed by the host LLM, with no external API keys or outbound network calls from the server itself.
4. **Lifecycle Hooks Registry**: Exposes callbacks (`on_parse_start`, `on_document_parsed`, `on_entity_discovered`) enabling pluggable custom logic and prompt overrides.
5. **Decoupled Framework Blueprint**: Provides programmatic and tool-based rules to enforce coding standards, repository guidelines, and code scaffolding.

## Architecture & Technology Stack
- **Language**: Python 3.10+
- **Protocol**: MCP (Model Context Protocol) via `FastMCP` (stdio transport).
- **Database/Storage**: NetworkX (for in-memory graph representation and BFS traversals), serialized to JSON.
- **Core Components**:
  - `db/graph_store.py`: Graph store, serialization, node/edge upserts, and BFS traceability.
  - `db/blueprint_store.py`: Manages decoupled coding rules, templates, and directory standards.
  - `parsers/`: Factory and specific parser implementations (`word_parser`, `excel_parser`, `pdf_parser`, `code_parser`, `feature_parser`, `schema_parser`).
  - `engine/extractor.py`: Pipeline coordinator and heuristic edge builder.

## MCP Protocol Interface
### Tools
- `ingest_workspace(workspace_path)`: Scans the directory, runs parsers, and builds the graph.
- `query_semantic_graph(query, node_type)`: Performs BM25-ranked keyword searches.
- `get_rule_traceability(rule_id, depth)`: Walks the graph via BFS to return connected technical endpoints and scenarios.
- `check_workspace_sync(workspace_path)`: Compares files against the indexed document store (new, modified, removed).
- `apply_edge_proposals(proposals_json)`: Upserts edge proposals validated by a host LLM.
- `query_framework_blueprint(target_file_path)`: Extracts coding standards and reusable utilities.
- `get_behavioral_drift_report()`: Scans the graph for requirement/implementation conflicts.

### Resources
- `context://graph/summary`: Returns node/edge counts and schema summaries.
- `context://rules/{rule_id}`: Exposes complete structured properties for a target business rule.

### Prompts
- `generate_bdd_tests(rule_id)`: Provides a system prompt and context to generate Gherkin BDD tests.
- `generate_api_tests(endpoint_id)`: Guides agents to write integration tests (JUnit/TS).
- `propose_edge_candidates(limit)`: Packages ambiguous edge candidates for LLM review.

## Autonomous Agents (.agent.md)
The Context Builder utilizes specialized subagents to autonomously build and interlink the context graph:
1. **Rule Extractor (`rule_extractor.agent.md`)**: An Insurance Business Systems Analyst subagent that extracts core insurance policy rules, eligibility constraints, premium limits, and rating tables from parsed document content, registering them via the `add_business_rule_node` tool.
2. **Tech Mapper (`tech_mapper.agent.md`)**: A Technical Component Mapper subagent that scans application controllers, test automation scripts, OpenAPI schemas, and Lombok POJOs to extract and register API endpoints, data models, and UI page objects using the `add_tech_component_node` tool.
3. **Relationship Linker (`relationship_linker.agent.md`)**: A Semantic Relationship and Traceability Linker subagent that identifies and registers directed edges (such as `IMPLEMENTS`, `TESTS`, `VALIDATES`, `MAPS_TO`) between business rules, API endpoints, data models, and test scenarios using the `add_semantic_edge` tool.
