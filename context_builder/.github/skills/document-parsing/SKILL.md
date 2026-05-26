---
name: document-parsing
description: Skill to parse structured/unstructured specifications (PDF, Word, Excel, Java/TS code) into Semantic Context Graph nodes.
---
# Agent Skill: Document Parsing & Attribute Normalization

This skill teaches the `@build_context` agent how to analyze and parse structured and unstructured specifications in the `./ingest/` folder.

---

## 📋 Word & PDF Parsing Rules

1. **Paragraph & Heading Splitting**:
   * Split Word/PDF text into logical segments using headings (e.g. `Heading 1` or `Heading 2`).
   * Group contiguous text paragraphs under the nearest preceding heading.
2. **Tabular Data Extractor**:
   * Do not skip tables; they often contain insurance rating lists or eligibility thresholds.
   * Parse tables into markdown format (`| Head 1 | Head 2 |`).
   * Generate a standalone `business_rule` node for every extracted table grid, saving the raw table markdown and headers in its JSON metadata.

---

## 📊 Excel & CSV Parsing Rules

1. **Sheet Isolation**:
   * Treat each spreadsheet sheet or tab as a separate, distinct table.
   * Do not mix sheets. Expose each sheet as a unique `business_rule` node.
2. **Matrix Conversion**:
   * Translate every sheet grid into clean Markdown table strings.
   * Extract header rows dynamically and store cell coordinates alongside values inside the node's `metadata` block to ensure absolute traceability.

---

## 💻 Java & TypeScript Scanner Rules

1. **Controller & Route Matching**:
   * Scans TS/JS NestJS controllers and Express route files for HTTP endpoints (`@Get`, `@Post`, `app.post`).
   * Scans Java source files for `@RestController` annotations and mapping signatures (`@GetMapping`, `@PostMapping`).
   * Normalize base mapping paths (e.g. `/api/v1/calculator`) and concatenate them with method paths.
2. **Node Creation**:
   * Create an `api_endpoint` node for every REST mapping.
   * Create a `code_component` node for the containing Class/Module, linking all its endpoints to it.

---

## 🥒 Gherkin BDD Feature Rules

1. **Structure Extraction**:
   * Extract `Feature:` titles as structural headers.
   * Extract each `Scenario:` or `Scenario Outline:` block.
2. **Steps Mapping**:
   * Bundle all `Given`, `When`, `Then`, `And`, and `But` steps together as a clean string.
   * Expose each scenario as a `test_scenario` node, keeping the raw step list in its metadata.
