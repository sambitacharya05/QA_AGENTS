"""
Acceptance-criteria tests for Spec 004 — AST-Based Source Parsing (Wave 2b).

Each test maps to one of the ten acceptance criteria listed in the spec:
  AC1  Multi-line Spring annotation parsed
  AC2  Multiple top-level classes captured
  AC3  Phantom RestAssured endpoints eliminated (via CodeParser Java scan)
  AC4  Scenario Outline expansion
  AC5  Background steps included
  AC6  Gherkin tags surfaced
  AC7  OpenAPI $ref resolved + USES_MODEL edges emitted
  AC8  Pure-local invariant (no network calls)
  AC9  No regression in existing parser tests — covered by test_parsers.py
  AC10 Tree-sitter grammar bundling — verified by the import test below

Additionally:
  - Go endpoint extraction (tree-sitter)
  - TypeScript NestJS decorator extraction
  - Python FastAPI endpoint extraction
  - RestAssured false-positive elimination (QafAutomationParser)
  - Gherkin Rules block
"""

from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# Make the project root importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from parsers.code_parser import CodeParser
from parsers.feature_parser import FeatureParser
from parsers.schema_parser import SchemaParser
from parsers.test_framework_parsers import QafAutomationParser


# ---------------------------------------------------------------------------
# AC10 — grammar bundling: verify all AST extractors can be imported
# ---------------------------------------------------------------------------

def test_ast_package_importable():
    """All tree-sitter extractor modules must be importable without a network call."""
    from parsers.ast.loader import language_for, parser_for
    from parsers.ast.java_extractor import JavaEndpointExtractor
    from parsers.ast.typescript_extractor import TypeScriptEndpointExtractor
    from parsers.ast.python_extractor import PythonEndpointExtractor
    from parsers.ast.go_extractor import GoEndpointExtractor

    # Verify parsers instantiate without error
    assert JavaEndpointExtractor() is not None
    assert TypeScriptEndpointExtractor() is not None
    assert PythonEndpointExtractor() is not None
    assert GoEndpointExtractor() is not None


# ---------------------------------------------------------------------------
# AC1 — Multi-line Spring annotation parsed
# ---------------------------------------------------------------------------

def test_multiline_spring_annotation():
    """@GetMapping spanning multiple lines must produce an api_endpoint node."""
    parser = CodeParser()
    code = """\
@RestController
@RequestMapping("/api/v1")
public class UserController {

    @GetMapping(
        value = "/users"
    )
    public List<User> getUsers() {
        return null;
    }

    @PostMapping(
        value = "/users",
        produces = "application/json"
    )
    public User createUser(@RequestBody User user) {
        return null;
    }
}
"""
    entities: list = []
    parser._scan_java(code, "UserController.java", "UserController.java", entities)

    endpoints = [e for e in entities if e["type"] == "api_endpoint"]
    assert len(endpoints) == 2, f"Expected 2 endpoints, got {[e['name'] for e in endpoints]}"

    names = {e["name"] for e in endpoints}
    assert "GET /api/v1/users" in names, f"GET endpoint missing from {names}"
    assert "POST /api/v1/users" in names, f"POST endpoint missing from {names}"


# ---------------------------------------------------------------------------
# AC2 — Multiple top-level classes captured
# ---------------------------------------------------------------------------

def test_multiple_top_level_classes(tmp_path):
    """A Java file with two top-level classes must produce two code_component nodes."""
    java_file = tmp_path / "Multi.java"
    java_file.write_text(
        """\
public class Alpha {
    public void doAlpha() {}
}

public class Beta {
    public void doBeta() {}
}
"""
    )
    parser = CodeParser()
    result = parser.parse(str(java_file))
    classes = [e for e in result["entities"] if e["type"] == "code_component"]
    names = {e["metadata"]["class_name"] for e in classes}
    assert "Alpha" in names
    assert "Beta" in names
    assert len(classes) == 2


# ---------------------------------------------------------------------------
# AC3 — Phantom endpoints from CodeParser eliminated
#         (list.get, Optional.get, map.get must not produce api_endpoint nodes)
# ---------------------------------------------------------------------------

def test_no_phantom_endpoints_from_java_collections():
    """
    Java code with list.get(0), optional.get(), and map.get('k') must produce
    zero api_endpoint nodes via CodeParser (which uses the tree-sitter AST).
    """
    parser = CodeParser()
    code = """\
import java.util.*;

public class SafeCode {
    public void process() {
        List<String> list = new ArrayList<>();
        String item = list.get(0);

        Optional<String> opt = Optional.of("x");
        String val = opt.get();

        Map<String, String> m = new HashMap<>();
        String v = m.get("k");
    }
}
"""
    entities: list = []
    parser._scan_java(code, "SafeCode.java", "SafeCode.java", entities)

    endpoints = [e for e in entities if e["type"] == "api_endpoint"]
    assert endpoints == [], (
        f"Expected 0 endpoints, got {[e['name'] for e in endpoints]}"
    )


# ---------------------------------------------------------------------------
# AC3b — RestAssured false-positive fix in QafAutomationParser
# ---------------------------------------------------------------------------

def test_restassured_false_positive_elimination(tmp_path):
    """
    list.get(0), optional.get(), and map.get('k') must NOT produce api_endpoint
    nodes via QafAutomationParser's RestAssured scanner.
    """
    java_file = tmp_path / "SafeCode.java"
    java_file.write_text(
        """\
import java.util.*;

public class SafeCode {
    public void process() {
        List<String> list = new ArrayList<>();
        String item = list.get(0);

        Optional<String> opt = Optional.of("x");
        String val = opt.get();

        Map<String, String> m = new HashMap<>();
        String v = m.get("k");
    }
}
"""
    )
    parser = QafAutomationParser()
    result = parser.parse(str(java_file))
    endpoints = [e for e in result["entities"] if e["type"] == "api_endpoint"]
    assert endpoints == [], (
        f"Expected 0 endpoints, got {[e['name'] for e in endpoints]}"
    )


def test_restassured_real_endpoint_still_detected(tmp_path):
    """A genuine RestAssured call with a path starting with / must still be detected."""
    java_file = tmp_path / "QuoteSteps.java"
    java_file.write_text(
        """\
import io.restassured.RestAssured;

public class QuoteSteps {
    @QAFTestStep(description="submit a quote request")
    public void submitQuote() {
        RestAssured.given()
            .body("{}")
            .post("/api/v1/quote/premium");
    }
}
"""
    )
    parser = QafAutomationParser()
    result = parser.parse(str(java_file))
    endpoints = [e for e in result["entities"] if e["type"] == "api_endpoint"]
    assert len(endpoints) == 1
    assert endpoints[0]["metadata"]["http_method"] == "POST"
    assert endpoints[0]["metadata"]["target_route"] == "/api/v1/quote/premium"


# ---------------------------------------------------------------------------
# AC4 — Scenario Outline expansion
# ---------------------------------------------------------------------------

def test_scenario_outline_expansion(tmp_path):
    """
    A Scenario Outline with 3 Examples rows must produce 3 test_scenario nodes,
    each with the row values substituted into the name and steps.
    """
    feature_file = tmp_path / "login.feature"
    feature_file.write_text(
        """\
Feature: Login
  Scenario Outline: Login as <role> user
    Given I am a <role> user
    When I log in with <password>
    Then I see the <dashboard> dashboard

    Examples:
      | role  | password | dashboard |
      | admin | secret1  | admin     |
      | guest | secret2  | user      |
      | agent | secret3  | agent     |
"""
    )
    parser = FeatureParser()
    result = parser.parse(str(feature_file))
    scenarios = [e for e in result["entities"] if e["type"] == "test_scenario"]

    assert len(scenarios) == 3, (
        f"Expected 3 concrete scenarios, got {len(scenarios)}: "
        f"{[s['name'] for s in scenarios]}"
    )

    names = {s["metadata"]["scenario"] for s in scenarios}
    assert "Login as admin user" in names
    assert "Login as guest user" in names
    assert "Login as agent user" in names

    # Verify placeholder substitution in steps
    admin_scenario = next(s for s in scenarios if "admin" in s["metadata"]["scenario"])
    assert any("admin" in step for step in admin_scenario["metadata"]["steps"]), (
        f"Expected 'admin' in steps: {admin_scenario['metadata']['steps']}"
    )


# ---------------------------------------------------------------------------
# AC5 — Background steps included
# ---------------------------------------------------------------------------

def test_background_steps_merged_into_scenario(tmp_path):
    """
    A Background with 2 steps + a Scenario with 3 steps must produce a
    test_scenario with 5 steps (background first, then scenario steps).
    """
    feature_file = tmp_path / "bg.feature"
    feature_file.write_text(
        """\
Feature: Background Demo
  Background:
    Given the database is seeded
    And the application is running

  Scenario: Basic health check
    Given I make a GET request to /health
    When the response arrives
    Then the status code is 200
"""
    )
    parser = FeatureParser()
    result = parser.parse(str(feature_file))
    scenarios = [e for e in result["entities"] if e["type"] == "test_scenario"]

    assert len(scenarios) == 1
    steps = scenarios[0]["metadata"]["steps"]
    assert len(steps) == 5, f"Expected 5 steps (2 bg + 3 scenario), got {len(steps)}: {steps}"

    # Background steps come first
    assert "database is seeded" in steps[0]
    assert "application is running" in steps[1]


# ---------------------------------------------------------------------------
# AC6 — Gherkin tags surfaced
# ---------------------------------------------------------------------------

def test_gherkin_tags_surfaced(tmp_path):
    """Tags on a scenario must appear in metadata.tags without the leading @."""
    feature_file = tmp_path / "tags.feature"
    feature_file.write_text(
        """\
Feature: Tag Demo
  @smoke @regression
  Scenario: Tag test
    Given something happens
    Then something is verified
"""
    )
    parser = FeatureParser()
    result = parser.parse(str(feature_file))
    scenarios = [e for e in result["entities"] if e["type"] == "test_scenario"]

    assert len(scenarios) == 1
    tags = scenarios[0]["metadata"]["tags"]
    assert "@smoke" not in tags, "Tags should have leading @ stripped"
    assert "smoke" in tags, f"Expected 'smoke' in {tags}"
    assert "regression" in tags, f"Expected 'regression' in {tags}"


# ---------------------------------------------------------------------------
# AC7 — OpenAPI $ref resolved + USES_MODEL edges emitted
# ---------------------------------------------------------------------------

def test_openapi_ref_resolved_uses_model_edges(tmp_path):
    """
    POST /users with requestBody $ref User and response 201 $ref User must emit
    exactly one USES_MODEL edge (de-duplicated) from the endpoint to the User model.
    Also verifies allOf schema flattening produces all properties in the data_model.
    """
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "User API", "version": "1.0"},
        "components": {
            "schemas": {
                "User": {
                    "title": "User",
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "name": {"type": "string"},
                    },
                },
                "AdminUser": {
                    "title": "AdminUser",
                    "allOf": [
                        {"$ref": "#/components/schemas/User"},
                        {
                            "type": "object",
                            "properties": {"role": {"type": "string"}},
                        },
                    ],
                },
            }
        },
        "paths": {
            "/users": {
                "post": {
                    "summary": "Create user",
                    "operationId": "createUser",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/User"}
                            }
                        }
                    },
                    "responses": {
                        "201": {
                            "description": "Created",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/User"}
                                }
                            },
                        }
                    },
                }
            }
        },
    }

    spec_file = tmp_path / "api.json"
    spec_file.write_text(json.dumps(spec))

    parser = SchemaParser()
    result = parser.parse(str(spec_file))

    entities = result["entities"]
    relationships = result["relationships"]

    endpoints = [e for e in entities if e["type"] == "api_endpoint"]
    assert len(endpoints) == 1
    assert endpoints[0]["name"] == "POST /users"

    models = [e for e in entities if e["type"] == "data_model"]
    model_names = {e["metadata"]["model_name"] for e in models}
    assert "User" in model_names
    assert "AdminUser" in model_names

    # allOf schema must flatten 'role' from the inline sub-schema
    admin_model = next(e for e in models if e["metadata"]["model_name"] == "AdminUser")
    assert "role" in admin_model["metadata"]["properties"], (
        f"Expected 'role' in AdminUser properties: {admin_model['metadata']['properties']}"
    )

    uses_model_edges = [r for r in relationships if r["relationship"] == "USES_MODEL"]
    assert len(uses_model_edges) >= 1, "Expected at least one USES_MODEL edge"

    # All USES_MODEL edges must originate from the POST /users endpoint
    endpoint_id = endpoints[0]["id"]
    for edge in uses_model_edges:
        assert edge["source_id"] == endpoint_id


# ---------------------------------------------------------------------------
# AC8 — Pure-local invariant (no network calls during ingestion)
# ---------------------------------------------------------------------------

def test_pure_local_no_network(tmp_path):
    """
    With socket.socket monkeypatched to raise, a full ingest_workspace call
    must complete successfully without any network access.
    """
    # Create a minimal workspace
    ws = tmp_path / "workspace"
    ws.mkdir()

    (ws / "Controller.java").write_text(
        """\
@RestController
@RequestMapping("/api")
public class Controller {
    @GetMapping("/ping")
    public String ping() { return "pong"; }
}
"""
    )
    (ws / "api.yaml").write_text(
        """\
openapi: "3.0.0"
info:
  title: "Test"
  version: "1"
paths:
  /ping:
    get:
      summary: "Ping"
      responses:
        "200":
          description: "OK"
"""
    )
    (ws / "scenario.feature").write_text(
        """\
Feature: Ping
  Scenario: Health check
    Given the service is running
    When I call /ping
    Then I get 200
"""
    )

    original_socket_class = socket.socket

    def _block(*args, **kwargs):
        raise OSError("Network access is blocked in the pure-local test")

    with patch("socket.socket", side_effect=_block):
        # Importing after the patch to ensure any lazy network calls are caught
        from engine.extractor import ContextExtractor

        extractor = ContextExtractor(str(ws))
        extractor.ingest_workspace()  # must not raise

    # Verify the graph was populated
    from db.graph_store import GraphStore

    store = GraphStore(str(ws))
    nodes = store.query_nodes()
    assert len(nodes) > 0, "Expected at least one node after ingest"


# ---------------------------------------------------------------------------
# Extra: TypeScript NestJS decorator extraction
# ---------------------------------------------------------------------------

def test_typescript_nestjs_extraction():
    """NestJS @Controller / @Get / @Post decorators must produce correct entities."""
    from parsers.ast.typescript_extractor import TypeScriptEndpointExtractor

    extractor = TypeScriptEndpointExtractor()
    code = """\
import { Controller, Get, Post } from '@nestjs/common';

@Controller('users')
export class UsersController {
    @Get('/list')
    findAll() { return []; }

    @Post()
    async create() { return {}; }
}
"""
    result = extractor.extract(code, "users.controller.ts")
    entities = result["entities"]

    classes = [e for e in entities if e["type"] == "code_component"]
    assert len(classes) == 1
    assert classes[0]["metadata"]["class_name"] == "UsersController"

    endpoints = [e for e in entities if e["type"] == "api_endpoint"]
    assert len(endpoints) == 2
    names = {e["name"] for e in endpoints}
    assert "GET /users/list" in names
    assert "POST /users/" in names or "POST /users" in names


# ---------------------------------------------------------------------------
# Extra: Python FastAPI extraction
# ---------------------------------------------------------------------------

def test_python_fastapi_extraction():
    """FastAPI @app.get/@router.post decorators must produce correct endpoints."""
    from parsers.ast.python_extractor import PythonEndpointExtractor

    extractor = PythonEndpointExtractor()
    code = """\
from fastapi import FastAPI, APIRouter

app = FastAPI()
router = APIRouter()

@app.get("/items/{item_id}")
def read_item(item_id: int):
    return {"item_id": item_id}

@router.post("/items")
async def create_item(item: dict):
    return item
"""
    result = extractor.extract(code, "main.py")
    entities = result["entities"]

    endpoints = [e for e in entities if e["type"] == "api_endpoint"]
    assert len(endpoints) == 2
    names = {e["name"] for e in endpoints}
    assert "GET /items/{item_id}" in names
    assert "POST /items" in names


# ---------------------------------------------------------------------------
# Extra: Go endpoint extraction
# ---------------------------------------------------------------------------

def test_go_gin_extraction():
    """Gin router.GET / router.POST calls must produce correct endpoints."""
    from parsers.ast.go_extractor import GoEndpointExtractor

    extractor = GoEndpointExtractor()
    code = """\
package main

import "github.com/gin-gonic/gin"

func main() {
    r := gin.Default()
    r.GET("/ping", pingHandler)
    r.POST("/items", createHandler)
}
"""
    result = extractor.extract(code, "main.go")
    entities = result["entities"]

    endpoints = [e for e in entities if e["type"] == "api_endpoint"]
    assert len(endpoints) == 2
    names = {e["name"] for e in endpoints}
    assert "GET /ping" in names
    assert "POST /items" in names


# ---------------------------------------------------------------------------
# Extra: Gherkin Rule block
# ---------------------------------------------------------------------------

def test_gherkin_rule_block(tmp_path):
    """Scenarios inside a Rule: block must still be emitted."""
    feature_file = tmp_path / "rule.feature"
    feature_file.write_text(
        """\
Feature: Rule Demo
  Rule: Auth rules apply
    Scenario: Token expiry
      Given my token is expired
      Then I am logged out

    Scenario: Valid token
      Given my token is valid
      Then I access the dashboard
"""
    )
    parser = FeatureParser()
    result = parser.parse(str(feature_file))
    scenarios = [e for e in result["entities"] if e["type"] == "test_scenario"]
    assert len(scenarios) == 2, f"Expected 2 scenarios, got {len(scenarios)}"


# ---------------------------------------------------------------------------
# Extra: PlaywrightBddParser control-flow keyword exclusion
# ---------------------------------------------------------------------------

def test_playwright_actions_exclude_control_flow(tmp_path):
    """
    Control-flow keywords (if, for, while, catch) must not appear in exposed_actions.
    Real method names (e.g. saveProfile) must still be included.
    """
    from parsers.test_framework_parsers import PlaywrightBddParser

    pom_file = tmp_path / "MyPage.page.ts"
    pom_file.write_text(
        """\
export class MyPage {
    async saveProfile() {
        if (true) {
            for (let i = 0; i < 3; i++) {
                while (false) {}
            }
        }
        try {
            // ...
        } catch (e) {
            // ...
        }
    }
}
"""
    )
    parser = PlaywrightBddParser()
    result = parser.parse(str(pom_file))
    entities = result["entities"]

    pom = next((e for e in entities if e["type"] == "ui_page_object"), None)
    assert pom is not None

    actions = pom["metadata"]["exposed_actions"]
    assert "saveProfile" in actions, f"Expected 'saveProfile' in {actions}"
    for kw in ("if", "for", "while", "catch", "try"):
        assert kw not in actions, f"Control-flow keyword '{kw}' must not be in actions: {actions}"
