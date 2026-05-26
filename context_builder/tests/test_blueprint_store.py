import os
import sys
import json
import pytest

# Ensure parent directory is in python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.blueprint_store import BlueprintStore

@pytest.fixture
def store(tmp_path):
    """Provides a fresh BlueprintStore initialized in a temp directory."""
    return BlueprintStore(str(tmp_path))

class TestBlueprintStore:
    def test_initialization_creates_default_blueprint(self, store):
        """Verify that a brand new BlueprintStore initializes the folder and pre-populates default schemas."""
        assert os.path.exists(store.blueprint_path)
        
        # Load the newly created file directly to verify contents
        with open(store.blueprint_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        assert "coding_standards" in data
        assert "global" in data["coding_standards"]
        assert data["coding_standards"]["global"]["naming_convention"] == "camelCase"
        assert "**/pages/**" in data["coding_standards"]["by_directory"]
        assert "reusable_capabilities" in data
        assert "generation_blueprints" in data
        assert "target_meta_framework" in data["generation_blueprints"]

    def test_load_existing_blueprint_does_not_overwrite(self, tmp_path):
        """Verify that BlueprintStore loads an existing blueprint without overwriting manual modifications."""
        storage_dir = os.path.join(tmp_path, ".context_builder")
        os.makedirs(storage_dir, exist_ok=True)
        blueprint_path = os.path.join(storage_dir, "blueprint.json")
        
        custom_data = {
            "coding_standards": {
                "global": {"naming_convention": "PascalCase", "indentation": "4_spaces"},
                "by_directory": {}
            },
            "reusable_capabilities": {},
            "generation_blueprints": {"target_meta_framework": "Custom-Framework"}
        }
        
        with open(blueprint_path, 'w', encoding='utf-8') as f:
            json.dump(custom_data, f, indent=2)
            
        # Initialize store
        store = BlueprintStore(str(tmp_path))
        assert store.blueprint_data["coding_standards"]["global"]["naming_convention"] == "PascalCase"
        assert store.blueprint_data["generation_blueprints"]["target_meta_framework"] == "Custom-Framework"

    def test_register_and_get_standards_for_path(self, store):
        """Verify directory-specific standards can be registered and retrieved with exact/wildcard matches."""
        store.register_directory_standard("**/controllers/**", "REST Controllers", ["No state allowed"])
        
        # Test exact match
        standards = store.get_standards_for_path("src/controllers/BillingController.java")
        assert standards["pattern"] == "REST Controllers"
        assert "No state allowed" in standards["rules"]
        
        # Test fallback to standard
        fallback = store.get_standards_for_path("src/unmatched/File.java")
        assert fallback["pattern"] == "Standard Code Class"
        assert not fallback["rules"]

    def test_cross_platform_path_normalization(self, store):
        """Verify that path matcher normalizes cross-platform paths correctly (Windows backslashes vs Unix slashes)."""
        # Register a standard with unix slashes
        store.register_directory_standard("**/pages/**", "Page Object Model (POM)", ["Rule 1"])
        
        # Verify both unix and windows style paths match
        unix_match = store.get_standards_for_path("src/pages/BillingPage.ts")
        windows_match = store.get_standards_for_path("src\\pages\\BillingPage.ts")
        
        assert unix_match["pattern"] == "Page Object Model (POM)"
        assert windows_match["pattern"] == "Page Object Model (POM)"

    def test_utility_signature_registry(self, store):
        """Verify that utility signatures can be registered, updated, and flattened."""
        methods = [
            {
                "method_name": "formatAmount",
                "signature": "public String formatAmount(double val)",
                "parameters": {"val": "double"},
                "return_type": "String",
                "description": "Formats decimal values into standard dollar string."
            }
        ]
        store.register_utility_signature("util_formatter", "Formatter", "src/utils/Formatter.java", methods)
        
        # Flatten all reusable methods and verify
        all_methods = store.get_all_reusable_methods()
        assert len(all_methods) >= 2 # 1 default + 1 custom registered
        
        custom_methods = [m for m in all_methods if m["utility_id"] == "util_formatter"]
        assert len(custom_methods) == 1
        assert custom_methods[0]["class_name"] == "Formatter"
        assert custom_methods[0]["method_name"] == "formatAmount"

    def test_generation_blueprints_and_exploration_rules(self, store):
        """Verify generation templates and exploration rules are set and queryable."""
        scaffolds = {"src/": "Root src code"}
        templates = {"base": "export class Base {}"}
        store.register_generation_blueprint("Test-Framework", scaffolds, templates)
        
        rules = ["getByRole", "css"]
        store.register_mcp_exploration_rules(rules, "Strict prefix", "verb + Noun")
        
        gen_blueprint = store.get_generation_blueprint()
        assert gen_blueprint["target_meta_framework"] == "Test-Framework"
        assert gen_blueprint["directory_scaffolding"] == scaffolds
        assert gen_blueprint["scaffolding_templates"] == templates
        assert gen_blueprint["mcp_exploration_rules"]["locator_priority_strategy"] == rules

    def test_developer_lock_safety(self, tmp_path):
        """Verify that when is_locked is true in coding_standards.global,
        global coding standards are NOT overwritten but utility method
        signatures are still harvested.
        """
        # Set up a temporary workspace directory
        workspace_dir = str(tmp_path)
        
        # Initialize a mock .context_builder/blueprint.json with "is_locked": true
        # and pre-set styles PascalCase and tabs
        storage_dir = os.path.join(workspace_dir, ".context_builder")
        os.makedirs(storage_dir, exist_ok=True)
        blueprint_path = os.path.join(storage_dir, "blueprint.json")
        
        initial_blueprint = {
            "coding_standards": {
                "global": {
                    "naming_convention": "PascalCase",
                    "indentation": "tabs",
                    "is_locked": True
                },
                "by_directory": {}
            },
            "reusable_capabilities": {},
            "generation_blueprints": {
                "target_meta_framework": "Playwright TypeScript (Standard Mode)",
                "directory_scaffolding": {},
                "scaffolding_templates": {},
                "mcp_exploration_rules": {
                    "locator_priority_strategy": []
                }
            }
        }
        with open(blueprint_path, 'w', encoding='utf-8') as f:
            json.dump(initial_blueprint, f, indent=2)
            
        # Create a python source file in the workspace with space-indented snake_case code
        src_dir = os.path.join(workspace_dir, "src")
        os.makedirs(src_dir, exist_ok=True)
        
        # This will be detected by StandardsScanner as space-indented snake_case
        python_code = """
def calculate_premium_discount(age, history):
    # 4 spaces indentation
    if age > 65:
        return 0.15
    return 0.0
"""
        py_source_file = os.path.join(src_dir, "utils.py")
        with open(py_source_file, "w", encoding="utf-8") as f:
            f.write(python_code)
            
        # Write a helper utility module in the workspace with a helper function signature
        helper_code = """
def format_custom_message(prefix: str, message: str) -> str:
    \"\"\"
    Helper utility to format messages.
    \"\"\"
    return f"{prefix}: {message}"
"""
        helper_file = os.path.join(src_dir, "helpers.py")
        with open(helper_file, "w", encoding="utf-8") as f:
            f.write(helper_code)
            
        # Import ContextExtractor
        from engine.extractor import ContextExtractor
        
        # Instantiate extractor
        extractor = ContextExtractor(workspace_dir)
        
        # Register the helper as a test_utility node in the graph store so that it is processed
        extractor.store.upsert_node(
            "util_helpers",
            "test_utility",
            "Test Utility: helpers",
            "Python test_utility defined in automation suite",
            {
                "source_file": helper_file,
                "class_name": "helpers",
                "language": "python"
            }
        )
        extractor.store.save_graph()
        
        # Run ContextExtractor.ingest_workspace()
        extractor.ingest_workspace()
        
        # Load the updated blueprint
        with open(blueprint_path, 'r', encoding='utf-8') as f:
            updated_data = json.load(f)
            
        # Verify that the global coding standards (PascalCase and tabs) were NOT overwritten because of the developer lock
        global_standards = updated_data["coding_standards"]["global"]
        assert global_standards["naming_convention"] == "PascalCase"
        assert global_standards["indentation"] == "tabs"
        assert global_standards["is_locked"] is True
        
        # Verify that the helper utility's method signatures were still successfully harvested into reusable_capabilities
        assert "util_helpers" in updated_data["reusable_capabilities"]
        methods = updated_data["reusable_capabilities"]["util_helpers"]["methods"]
        assert len(methods) == 1
        method = methods[0]
        assert method["method_name"] == "format_custom_message"
        assert method["return_type"] == "str"
        assert "Helper utility to format messages" in method["description"]
        assert method["parameters"] == {"prefix": "str", "message": "str"}

    def test_meta_framework_and_locator_priority(self, tmp_path):
        """Verify blueprint framework identification, semantic locator priority,
        and scaffolding directory computation excluding documentation format.
        """
        from engine.extractor import ContextExtractor
        workspace = str(tmp_path)
        extractor = ContextExtractor(workspace)
        
        # 1. Mock a Maven pom.xml containing 'qaf' dependency
        pom_content = """
        <project>
            <dependencies>
                <dependency>
                    <groupId>io.github.qafframework</groupId>
                    <artifactId>qaf</artifactId>
                    <version>3.0.0</version>
                </dependency>
            </dependencies>
        </project>
        """
        with open(os.path.join(workspace, "pom.xml"), "w") as f:
            f.write(pom_content)
            
        # 2. Mock a package.json containing playwright and playwright-bdd
        package_content = """
        {
            "dependencies": {
                "@playwright/test": "^1.40.0",
                "playwright-bdd": "^6.0.0"
            }
        }
        """
        with open(os.path.join(workspace, "package.json"), "w") as f:
            f.write(package_content)
            
        # 3. Create a POM file containing locator values with getByRole to trigger semantic locator priority
        src_dir = os.path.join(workspace, "src", "pages")
        os.makedirs(src_dir, exist_ok=True)
        pom_code = """
        import { page } from '@playwright/test';
        export class LoginPage {
            readonly submitButton = page.getByRole('button', { name: 'Submit' });
        }
        """
        pom_file = os.path.join(src_dir, "LoginPage.ts")
        with open(pom_file, "w") as f:
            f.write(pom_code)
            
        # Register the POM node
        extractor.store.upsert_node(
            "page_loginpage",
            "ui_page_object",
            "LoginPage",
            "Login POM",
            {
                "source_file": pom_file,
                "class_name": "LoginPage",
                "framework": "playwright",
                "locators": [
                    {"name": "submitButton", "strategy": "getByRole", "value": "getByRole('button', { name: 'Submit' })"}
                ]
            }
        )
        extractor.store.save_graph()
        
        # 4. Create some documentation files and a BDD file to test scaffolding dir calculation
        doc_dir = os.path.join(workspace, "docs")
        os.makedirs(doc_dir, exist_ok=True)
        with open(os.path.join(doc_dir, "UserGuide.md"), "w") as f:
            f.write("Documentation")
            
        bdd_dir = os.path.join(workspace, "src", "features")
        os.makedirs(bdd_dir, exist_ok=True)
        with open(os.path.join(bdd_dir, "login.feature"), "w") as f:
            f.write("Feature: Login\nScenario: User Login\nGiven a valid user")
            
        # Run ingest and synthesize blueprint
        extractor.ingest_workspace()
        
        # Read the generated blueprint.json
        blueprint_path = os.path.join(workspace, ".context_builder", "blueprint.json")
        assert os.path.exists(blueprint_path)
        
        with open(blueprint_path, 'r', encoding='utf-8') as f:
            blueprint_data = json.load(f)
            
        # Assert mixed meta framework name is calculated correctly
        meta_framework = blueprint_data["generation_blueprints"]["target_meta_framework"]
        assert meta_framework == "Mixed Test Automation Framework (Playwright BDD & QAF Java)"
        
        # Assert semantic locator priority is set because of getByRole value
        locator_strategy = blueprint_data["generation_blueprints"]["mcp_exploration_rules"]["locator_priority_strategy"]
        assert locator_strategy[0] == "getByRole"
        
        # Assert feature directories in scaffolding are derived from actual BDD files, excluding docs
        scaffolding = blueprint_data["generation_blueprints"]["directory_scaffolding"]
        assert any("features" in key for key in scaffolding)
        assert not any("docs" in key for key in scaffolding)

