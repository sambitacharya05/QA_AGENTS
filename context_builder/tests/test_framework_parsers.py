import os
import sys
import json
import pytest
from pathlib import Path

# Ensure parent directory is in python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from parsers.test_framework_parsers import PlaywrightBddParser, QafAutomationParser
from parsers import get_parser_for_file
from engine.extractor import ContextExtractor
from db.graph_store import GraphStore

def test_playwright_bdd_parser(tmp_path):
    parser = PlaywrightBddParser()
    
    content = """
    import { Given, When, Then } from '@cucumber/cucumber';
    import { Step } from '@playwright-bdd/decorators';
    
    Given("I am on the login page", async () => {
        await page.goto('/login');
    });
    
    When('I enter valid credentials', async ({ page }) => {
        await page.fill('input[type="text"]', 'user');
    });
    
    @Step('Verify login success')
    async verifySuccess() {
        expect(this.successMessage).toBeVisible();
    }
    
    class LoginPage {
        constructor() {
            this.usernameInput = this.page.locator('input#username');
            this.passwordInput = page.locator("input#password");
        }
    }
    """
    
    file_path = tmp_path / "login.steps.ts"
    file_path.write_text(content)
    
    result = parser.parse(str(file_path))
    entities = result["entities"]
    
    # Assert Step Definitions
    steps = [e for e in entities if e["type"] == "test_step_definition"]
    assert len(steps) == 3
    
    step_given = [s for s in steps if "Given" in s["name"]][0]
    assert step_given["metadata"]["pattern"] == "I am on the login page"
    assert step_given["metadata"]["step_type"] == "Given"
    
    step_dec = [s for s in steps if "Decorator" in s["name"]][0]
    assert step_dec["metadata"]["pattern"] == "Verify login success"
    
    # Assert Consolidated Page Object Class Node (Option B)
    locators = [e for e in entities if e["type"] == "ui_page_object"]
    assert len(locators) == 1
    login_page_node = locators[0]
    assert login_page_node["id"] == "pw_pom_loginpage"
    assert "usernameInput" in login_page_node["metadata"]["locators"]
    assert login_page_node["metadata"]["locators"]["usernameInput"] == "input#username"
    assert login_page_node["metadata"]["locators"]["passwordInput"] == "input#password"


def test_qaf_automation_parser_bdd(tmp_path):
    parser = QafAutomationParser()
    
    bdd_content = """
    SCENARIO: Premium Quote Rejected Under Age
    META-DATA: {"description": "Verify driver rejection under age limit", "groups": ["SMOKE", "REGRESSION"]}
    Given applicant age is 17
    When requesting premium quote
    Then request is rejected
    """
    
    file_path = tmp_path / "quote.bdd"
    file_path.write_text(bdd_content)
    
    result = parser.parse(str(file_path))
    entities = result["entities"]
    
    scenarios = [e for e in entities if e["type"] == "test_scenario"]
    assert len(scenarios) == 1
    assert scenarios[0]["name"] == "QAF BDD: Premium Quote Rejected Under Age"
    assert "Verify driver rejection" in scenarios[0]["metadata"]["qaf_meta"]["description"]


def test_qaf_automation_parser_properties(tmp_path):
    parser = QafAutomationParser()
    
    prop_content = """
    # Object Repository Locators
    login.username.input=xpath=//input[@id='username']
    login.submit.btn=css=button.submit-btn
    some.standard.config=true
    """
    
    file_path = tmp_path / "login.properties"
    file_path.write_text(prop_content)
    
    result = parser.parse(str(file_path))
    entities = result["entities"]
    
    locators = [e for e in entities if e["type"] == "ui_page_object"]
    assert len(locators) == 1
    repo_node = locators[0]
    assert repo_node["id"] == "qaf_repo_login_properties"
    assert repo_node["metadata"]["locators"]["login.username.input"] == "xpath=//input[@id='username']"
    assert repo_node["metadata"]["locators"]["login.submit.btn"] == "css=button.submit-btn"


def test_qaf_automation_parser_java(tmp_path):
    parser = QafAutomationParser()
    
    java_content = """
    package com.insurance.test.steps;
    
    import io.restassured.RestAssured;
    import lombok.Data;
    import lombok.Builder;
    
    @Data
    @Builder
    public class QuoteRequest {
        private String applicantName;
        private int age;
    }
    
    public class QuoteSteps {
        @QAFTestStep(description="submit a request for user {0}")
        public void submitQuoteRequest(String name) {
            QuoteRequest req = QuoteRequest.builder()
                .applicantName(name)
                .age(25)
                .build();
                
            RestAssured.given()
                .body(req)
                .post("/api/v1/quote/premium");
        }
    }
    """
    
    file_path = tmp_path / "QuoteSteps.java"
    file_path.write_text(java_content)
    
    result = parser.parse(str(file_path))
    entities = result["entities"]
    
    # Assert Lombok model
    models = [e for e in entities if e["type"] == "data_model"]
    assert len(models) == 1
    assert models[0]["name"] == "Lombok Model: QuoteRequest"
    assert "applicantName" in models[0]["description"]
    
    # Assert QAF Step
    steps = [e for e in entities if e["type"] == "test_step_definition"]
    assert len(steps) == 1
    assert steps[0]["name"] == "QAF Java Step: submit a request for user {0}"
    
    # Assert RestAssured Endpoint
    endpoints = [e for e in entities if e["type"] == "api_endpoint"]
    assert len(endpoints) == 1
    assert endpoints[0]["metadata"]["http_method"] == "POST"
    assert endpoints[0]["metadata"]["target_route"] == "/api/v1/quote/premium"


def test_dispatcher_factory(tmp_path):
    # Verify that file paths are correctly dispatched to parser instances
    
    # Fast path - needs file to exist for get_parser_for_file
    p1_path = tmp_path / "test.bdd"
    p1_path.touch()
    p1 = get_parser_for_file(str(p1_path))
    assert isinstance(p1, QafAutomationParser)
    
    # Properties locator path
    os.makedirs(tmp_path / "pages", exist_ok=True)
    p2_path = tmp_path / "pages" / "ui.properties"
    p2_path.touch()
    p2 = get_parser_for_file(str(p2_path))
    assert isinstance(p2, QafAutomationParser)
    
    # Playwright TS steps
    os.makedirs(tmp_path / "e2e", exist_ok=True)
    p3_path = tmp_path / "e2e" / "login.steps.ts"
    p3_path.touch()
    p3 = get_parser_for_file(str(p3_path))
    assert isinstance(p3, PlaywrightBddParser)


def test_test_relationship_mapping(tmp_path):
    # Setup mock workspace files in tmp_path inside test directory to trigger is_test_path
    workspace_dir = tmp_path / "workspace"
    test_dir = workspace_dir / "test"
    os.makedirs(test_dir, exist_ok=True)
    
    # 1. Lombok Data Model in test paths with DTO suffix
    java_model = """
    package com.insurance.models;
    import lombok.Builder;
    import lombok.Data;
    
    @Data
    @Builder
    public class DriverInfoDTO {
        private String name;
        private int age;
    }
    """
    with open(os.path.join(test_dir, "DriverInfoDTO.java"), "w") as f:
        f.write(java_model)
        
    # 2. REST Utility using RestAssured client
    rest_util = """
    package com.insurance.utils;
    import io.restassured.RestAssured;
    
    public class RestClient {
        public void postDriver(DriverInfoDTO info) {
            RestAssured.given()
                .body(info)
                .post("/api/v1/drivers");
        }
    }
    """
    with open(os.path.join(test_dir, "RestClient.java"), "w") as f:
        f.write(rest_util)
        
    # 3. QAF Step definition referencing imports, calling utility class, and instantiating Lombok builder
    java_steps = """
    package com.insurance.steps;
    import com.insurance.models.DriverInfoDTO;
    import com.insurance.utils.RestClient;
    
    public class DriverSteps {
        @QAFTestStep(description="create driver {0} aged {1}")
        public void createDriver(String name, int age) {
            DriverInfoDTO info = DriverInfoDTO.builder()
                .name(name)
                .age(age)
                .build();
            new RestClient().postDriver(info);
        }
    }
    """
    with open(os.path.join(test_dir, "DriverSteps.java"), "w") as f:
        f.write(java_steps)
        
    # 4. QAF BDD Scenario referencing the builder class
    bdd_scenario = """
    SCENARIO: Validate Driver Age Limit
    Given create driver "Alex" aged 17
    Then request fails due to DriverInfoDTO validation
    """
    with open(os.path.join(test_dir, "validate_driver.bdd"), "w") as f:
        f.write(bdd_scenario)

    # 5. Playwright Scattered locator constants config
    const_config = """
    export const SELECTORS = {
        usernameInput: '#username-field',
        passwordInput: 'css=input[type="password"]'
    }
    """
    with open(os.path.join(test_dir, "selectors.ts"), "w") as f:
        f.write(const_config)

    # 6. Playwright POM referencing constants and accepting Lombok model inside action method
    pw_pom = """
    import { SELECTORS } from './selectors';
    import { DriverInfoDTO } from '../models/DriverInfoDTO';
    
    export class DriverPage {
        readonly username = page.locator(SELECTORS.usernameInput);
        readonly password = page.locator(SELECTORS.passwordInput);
        
        async fillDetails(info: DriverInfoDTO) {
            await this.username.fill(info.name);
        }
    }
    """
    with open(os.path.join(test_dir, "DriverPage.page.ts"), "w") as f:
        f.write(pw_pom)

    # 7. SDD spec detailing a Business Rule
    spec_md = """
    # Feature: Driver Limits
    
    ## Rule: Driver Validation Rule
    Verify Playwright POM details for a driver.
    """
    with open(os.path.join(test_dir, "spec.md"), "w") as f:
        f.write(spec_md)

    # Ingest the workspace using ContextExtractor
    extractor = ContextExtractor(str(workspace_dir))
    extractor.ingest_workspace()
    
    # Query graph edges to verify relations
    store = GraphStore(str(workspace_dir))
    nodes = store.query_nodes()
    print("\n--- DEBUG NODES ---")
    for n in nodes:
        print(f"Node: {n['id']} | Type: {n['type']} | Name: {n['name']} | File: {n.get('metadata', {}).get('source_file')}")
    
    edges = store.get_edges()
    print("\n--- DEBUG EDGES ---")
    for e in edges:
        print(f"Edge: {e['source_id']} -- [{e['relationship']}] --> {e['target_id']}")

    # Verify CALLS edge from Step Definition to RestClient
    calls_edges = [e for e in edges if e["relationship"] == "CALLS"]
    assert len(calls_edges) > 0
    assert any("create_driver" in e["source_id"] and "restclient" in e["target_id"] for e in calls_edges)
    
    # Verify USES edge from Step Definition to DriverInfoDTO model
    uses_edges = [e for e in edges if e["relationship"] == "USES"]
    assert len(uses_edges) > 0
    assert any("create_driver" in e["source_id"] and "driverinfodto" in e["target_id"] for e in uses_edges)
    assert any("validate_driver" in e["source_id"] and "driverinfodto" in e["target_id"] for e in uses_edges)
    
    # Verify USES edge from Playwright POM to DriverInfoDTO data_model (Lombok DTO mapping inside Page Objects)
    assert any("pw_pom_driverpage" in e["source_id"] and "driverinfodto" in e["target_id"] for e in uses_edges)

    # Verify VALIDATES edge from POM class to business rule (UI POM overlap validation mapping)
    validates_edges = [e for e in edges if e["relationship"] == "VALIDATES"]
    assert len(validates_edges) > 0
    assert any("pw_pom_driverpage" in e["source_id"] and "driver_validation_rule" in e["target_id"] for e in validates_edges)

    # Verify TESTS edge from Step Definition to RestAssured api_endpoint
    tests_edges = [e for e in edges if e["relationship"] == "TESTS"]
    assert len(tests_edges) > 0
    assert any("create_driver" in e["source_id"] and "endpoint_post_api_v1_drivers" in e["target_id"] for e in tests_edges)


def test_playwright_class_fields_and_constants(tmp_path):
    # Test class fields and unquoted constant lookup in Playwright Bdd Parser
    shared_ctx = {"locator_cache": {"USERNAME_SELECTOR": "xpath=//input[@id='user']"}}
    parser = PlaywrightBddParser(shared_ctx)

    content = """
    import { USERNAME_SELECTOR } from './selectors';
    
    export class ProfilePage {
        readonly usernameInput = page.locator(USERNAME_SELECTOR);
        public submitButton = page.locator('.submit-btn');
        protected email = page.getByPlaceholder('Enter email');
        
        async saveProfile() {
            await this.submitButton.click();
        }
    }
    """

    file_path = tmp_path / "ProfilePage.page.ts"
    file_path.write_text(content)

    result = parser.parse(str(file_path))
    entities = result["entities"]

    locators = [e for e in entities if e["type"] == "ui_page_object"]
    assert len(locators) == 1
    page_node = locators[0]
    
    assert page_node["metadata"]["class_name"] == "ProfilePage"
    # Constant resolved
    assert page_node["metadata"]["locators"]["usernameInput"] == "xpath=//input[@id='user']"
    # Modifier resolved
    assert page_node["metadata"]["locators"]["submitButton"] == ".submit-btn"
    # Semantic selector resolved
    assert page_node["metadata"]["locators"]["email"] == "getByPlaceholder('Enter email')"
    # Method action resolved
    assert "saveProfile" in page_node["metadata"]["exposed_actions"]


def test_qaf_java_findby_generics(tmp_path):
    shared_ctx = {"locator_cache": {"login.username": "xpath=//input", "login.buttons": "css=button"}}
    parser = QafAutomationParser(shared_ctx)

    content = """
    package com.insurance.pages;
    import com.qmetry.qaf.automation.ui.annotations.FindBy;
    import com.qmetry.qaf.automation.ui.webdriver.QAFWebElement;
    
    public class LoginPage extends WebDriverBaseTestPage {
        @FindBy(locator = "login.username")
        private QAFWebElement username;
        
        @FindBy(locator = "login.buttons")
        protected List<QAFWebElement> buttons;
    }
    """

    file_path = tmp_path / "LoginPage.java"
    file_path.write_text(content)

    result = parser.parse(str(file_path))
    entities = result["entities"]

    locators = [e for e in entities if e["type"] == "ui_page_object"]
    assert len(locators) == 1
    page_node = locators[0]

    assert page_node["metadata"]["class_name"] == "LoginPage"
    assert page_node["metadata"]["bindings"]["username"]["key_reference"] == "login.username"
    assert page_node["metadata"]["bindings"]["username"]["literal_selector"] == "xpath=//input"
    assert page_node["metadata"]["bindings"]["buttons"]["key_reference"] == "login.buttons"
    assert page_node["metadata"]["bindings"]["buttons"]["literal_selector"] == "css=button"


def test_playwright_sample_pom_harvests_inline_semantic_locators():
    parser = PlaywrightBddParser()
    sample_file = (
        Path(__file__).resolve().parents[2]
        / "ingest"
        / "playwright"
        / "src"
        / "pages"
        / "memberDashboardPage.ts"
    )

    result = parser.parse(str(sample_file))
    entities = result["entities"]

    locators = [e for e in entities if e["type"] == "ui_page_object"]
    assert len(locators) == 1

    page_node = locators[0]
    locator_values = list(page_node["metadata"]["locators"].values())

    assert any("dashboard-header" in value for value in locator_values)
    assert any("plan-name" in value for value in locator_values)


def test_qaf_sample_java_page_object_is_ingested_as_ui_page_object():
    parser = QafAutomationParser()
    sample_file = (
        Path(__file__).resolve().parents[2]
        / "ingest"
        / "java-qaf"
        / "src"
        / "main"
        / "java"
        / "com"
        / "mockhealth"
        / "pages"
        / "MemberDashboardPage.java"
    )

    result = parser.parse(str(sample_file))
    entities = result["entities"]

    page_objects = [e for e in entities if e["type"] == "ui_page_object"]
    assert len(page_objects) == 1

    page_node = page_objects[0]
    assert page_node["metadata"]["class_name"] == "MemberDashboardPage"

    locator_text = json.dumps(page_node["metadata"])
    assert "dashboard-header" in locator_text
    assert "plan-name" in locator_text
    assert "billing-status" in locator_text


def test_java_record_and_ts_interface_parsing(tmp_path):
    # Test Java Record Ingestion
    java_parser = QafAutomationParser()
    java_record = """
    package com.mockhealth.model;
    
    public record ClaimRequest(
        String memberId,
        String providerId,
        double amountBilled
    ) {}
    """
    java_file = tmp_path / "ClaimRequest.java"
    java_file.write_text(java_record)
    
    java_result = java_parser.parse(str(java_file))
    java_entities = java_result["entities"]
    
    java_models = [e for e in java_entities if e["type"] == "data_model"]
    assert len(java_models) == 1
    assert java_models[0]["id"] == "schema_claimrequest_java"
    assert java_models[0]["name"] == "Java Record: ClaimRequest"
    assert java_models[0]["metadata"]["is_java_record"] is True
    assert "memberId" in java_models[0]["metadata"]["fields"]
    assert java_models[0]["metadata"]["fields"]["amountBilled"] == "double"

    # Test TypeScript Interface Ingestion
    ts_parser = PlaywrightBddParser()
    ts_interface = """
    export interface ClaimRequest {
      memberId: string;
      providerId: string;
      amountBilled: number;
    }
    """
    ts_file = tmp_path / "claimsApi.ts"
    ts_file.write_text(ts_interface)
    
    ts_result = ts_parser.parse(str(ts_file))
    ts_entities = ts_result["entities"]
    
    ts_models = [e for e in ts_entities if e["type"] == "data_model"]
    assert len(ts_models) == 1
    assert ts_models[0]["id"] == "schema_claimrequest_ts"
    assert ts_models[0]["name"] == "TS Interface: ClaimRequest"
    assert ts_models[0]["metadata"]["is_ts_interface"] is True
    assert "memberId" in ts_models[0]["metadata"]["fields"]
    assert ts_models[0]["metadata"]["fields"]["amountBilled"] == "number"

