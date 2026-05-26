import os
import json
import logging
import fnmatch
from typing import Dict, List, Any, Optional

from db.local_store import LocalJsonStore

log = logging.getLogger(__name__)


class BlueprintStore:
    """Manages the persistence and retrieval of repository coding standards,
    scaffolding templates, and reuse footprints.

    Backed by :class:`db.local_store.LocalJsonStore` in **raw mode**
    (``use_envelope=False``), so the on-disk file remains plain JSON and is
    fully compatible with external tooling that reads it directly.  Atomic
    writes and .bak backup/rescue are still in effect.
    """

    def __init__(self, workspace_path: str):
        self.workspace_path = workspace_path
        self.storage_dir = os.path.join(workspace_path, ".context_builder")
        self.blueprint_path = os.path.join(self.storage_dir, "blueprint.json")
        os.makedirs(self.storage_dir, exist_ok=True)

        # Raw mode: no schema-version envelope; file stays plain JSON.
        self._store = LocalJsonStore(
            self.blueprint_path,
            schema_version=0,
            migrators={},
            use_envelope=False,
        )

        self.blueprint_data = self._load_blueprint()

        # Stateful Path Alignment Sweep — convert legacy absolute paths to relative
        changes_detected = False
        if "reusable_capabilities" in self.blueprint_data:
            for cap_id, info in self.blueprint_data["reusable_capabilities"].items():
                source_file = info.get("source_file")
                if source_file and isinstance(source_file, str):
                    idx = -1
                    if "ingest/" in source_file:
                        idx = source_file.find("ingest/")
                    elif "ingest\\" in source_file:
                        idx = source_file.find("ingest\\")
                    if idx != -1:
                        new_source_file = source_file[idx:].replace('\\', '/')
                        if source_file != new_source_file:
                            info["source_file"] = new_source_file
                            changes_detected = True
                    elif os.path.isabs(source_file):
                        new_source_file = os.path.relpath(
                            source_file, self.workspace_path
                        ).replace('\\', '/')
                        if source_file != new_source_file:
                            info["source_file"] = new_source_file
                            changes_detected = True

        if changes_detected:
            self.save_blueprint()

    # ------------------------------------------------------------------
    # Load
    # ------------------------------------------------------------------

    def _load_blueprint(self) -> Dict[str, Any]:
        """Load blueprint config from disk, or initialise from a preset."""
        with self._store.lock():
            try:
                data = self._store.safe_read()
            except Exception as exc:
                log.warning(
                    "Failed to load blueprint.json: %s — reinitialising clean state.", exc
                )
                data = None

        if data is not None:
            # Back-compat: add is_locked if absent
            if "coding_standards" in data and "global" in data["coding_standards"]:
                if "is_locked" not in data["coding_standards"]["global"]:
                    data["coding_standards"]["global"]["is_locked"] = False
            return data

        # Determine which preset to load
        config_path = os.path.join(self.storage_dir, "config.json")
        preferred_preset = "playwright_ts"
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    preferred_preset = config.get(
                        "preferred_framework", "playwright_ts"
                    ).lower()
            except Exception:
                pass

        presets = {
            "playwright_ts": {
                "coding_standards": {
                    "global": {
                        "naming_convention": "camelCase",
                        "indentation": "2_spaces",
                        "is_locked": False
                    },
                    "by_directory": {
                        "**/pages/**": {
                            "pattern": "Page Object Model (POM)",
                            "rules": [
                                "Locators must be instantiated exclusively inside constructors",
                                "Methods must wrap composite actions and avoid direct return values where possible"
                            ]
                        },
                        "**/steps/**": {
                            "pattern": "Step Definitions",
                            "rules": [
                                "Step text patterns must match Gherkin regular expressions",
                                "Do not assert values directly inside step definitions; delegate to assertions library"
                            ]
                        }
                    }
                },
                "reusable_capabilities": {
                    "util_playwright_common": {
                        "class_name": "PlaywrightCommon",
                        "source_file": "src/utils/PlaywrightCommon.ts",
                        "methods": [
                            {
                                "method_name": "waitForElementVisible",
                                "signature": "public async waitForElementVisible(selector: string, timeoutMs: number): Promise<void>",
                                "parameters": {"selector": "string", "timeoutMs": "number"},
                                "return_type": "Promise<void>",
                                "description": "Waits for a dynamic element selector to be fully visible in the DOM."
                            }
                        ]
                    }
                },
                "generation_blueprints": {
                    "target_meta_framework": "Playwright TypeScript (Strict BDD Mode)",
                    "directory_scaffolding": {
                        "src/config/": "Global runner configurations, env blocks, and viewport specs",
                        "src/pages/": "Isolated Page Object Class models wrapping elements and actions",
                        "src/steps/": "Playwright-BDD Cucumber implementation files matching feature keys",
                        "features/": "Pure Gherkin business scenario requirements blueprints"
                    },
                    "scaffolding_templates": {
                        "base_page_ts": "export class BasePage {\n  readonly page: Page;\n  constructor(page: Page) {\n    this.page = page;\n  }\n}",
                        "page_object_ts": "import { Page, Locator } from '@playwright/test';\nimport { BasePage } from './BasePage';\n\nexport class ${className} extends BasePage {\n  ${locatorsBlock}\n\n  constructor(page: Page) {\n    super(page);\n    ${locatorInitializersBlock}\n  }\n\n  ${methodsBlock}\n}"
                    },
                    "mcp_exploration_rules": {
                        "locator_priority_strategy": [
                            "getByRole", "getByLabel", "getByPlaceholder",
                            "getByText", "getByTestId", "css", "xpath"
                        ],
                        "page_object_extraction_threshold": (
                            "Generate a discrete unique ui_page_object node whenever "
                            "the browser URL path prefix changes (e.g., /billing vs /quotes)."
                        ),
                        "action_method_naming_taxonomy": "verb + TargetElement (e.g., clickSubmitButton, fillPremiumForm)"
                    }
                }
            },
            "selenium_java": {
                "coding_standards": {
                    "global": {
                        "naming_convention": "camelCase",
                        "indentation": "4_spaces",
                        "is_locked": False
                    },
                    "by_directory": {
                        "**/pages/**": {
                            "pattern": "Page Object Model (POM)",
                            "rules": [
                                "Use @FindBy annotation for all locator initializers",
                                "Expose fluent action methods that return this page object or target page object"
                            ]
                        },
                        "**/steps/**": {
                            "pattern": "Step Definitions",
                            "rules": [
                                "Step definitions must use Cucumber JVM annotations like @Given, @When, @Then",
                                "Use AssertJ or TestNG Assert assertions directly inside step definitions"
                            ]
                        }
                    }
                },
                "reusable_capabilities": {
                    "util_qaf_dateutils": {
                        "class_name": "DateUtils",
                        "source_file": "src/test/java/com/project/utils/DateUtils.java",
                        "methods": [
                            {
                                "method_name": "calculatePolicyOffset",
                                "signature": "public static String calculatePolicyOffset(int days)",
                                "parameters": {"days": "int"},
                                "return_type": "String",
                                "description": "Calculates an offset expiration date string from current time using format yyyy-MM-dd."
                            }
                        ]
                    }
                },
                "generation_blueprints": {
                    "target_meta_framework": "Selenium Java JUnit/TestNG",
                    "directory_scaffolding": {
                        "src/main/java/com/project/pages/": "Java Page Object class definitions",
                        "src/test/java/com/project/steps/": "Java BDD step definitions and hooks",
                        "src/test/resources/features/": "Cucumber Gherkin feature files"
                    },
                    "scaffolding_templates": {
                        "base_page_java": "package com.project.pages;\nimport org.openqa.selenium.WebDriver;\nimport org.openqa.selenium.support.PageFactory;\n\npublic class BasePage {\n    protected WebDriver driver;\n    public BasePage(WebDriver driver) {\n        this.driver = driver;\n        PageFactory.initElements(driver, this);\n    }\n}",
                        "page_object_java": "package com.project.pages;\nimport org.openqa.selenium.WebDriver;\nimport org.openqa.selenium.WebElement;\nimport org.openqa.selenium.support.FindBy;\n\npublic class ${className} extends BasePage {\n    ${locatorsBlock}\n    public ${className}(WebDriver driver) {\n        super(driver);\n    }\n    ${methodsBlock}\n}"
                    },
                    "mcp_exploration_rules": {
                        "locator_priority_strategy": ["id", "name", "css", "xpath", "link"],
                        "page_object_extraction_threshold": (
                            "Generate a discrete unique ui_page_object node whenever "
                            "the browser URL path prefix changes (e.g., /billing vs /quotes)."
                        ),
                        "action_method_naming_taxonomy": "verb + TargetElement (e.g., clickSubmitButton, fillPremiumForm)"
                    }
                }
            },
            "pytest_python": {
                "coding_standards": {
                    "global": {
                        "naming_convention": "snake_case",
                        "indentation": "4_spaces",
                        "is_locked": False
                    },
                    "by_directory": {
                        "**/pages/**": {
                            "pattern": "Page Object Model (POM)",
                            "rules": [
                                "Store selenium locators as tuples at the class level",
                                "Wrap interactions in standard Selenium WebDriverWait utility methods"
                            ]
                        },
                        "**/tests/**": {
                            "pattern": "Pytest Scenarios",
                            "rules": [
                                "Use pytest-bdd or basic assert statements for verification",
                                "Fixtures must handle all browser setup and teardown lifecycles"
                            ]
                        }
                    }
                },
                "reusable_capabilities": {
                    "util_python_common": {
                        "class_name": "pytest_helpers",
                        "source_file": "tests/utils/helpers.py",
                        "methods": [
                            {
                                "method_name": "wait_and_click",
                                "signature": "def wait_and_click(driver, locator, timeout_sec=10) -> None",
                                "parameters": {"driver": "Any", "locator": "tuple", "timeout_sec": "int"},
                                "return_type": "None",
                                "description": "Explicitly waits for an element to become clickable and clicks it."
                            }
                        ]
                    }
                },
                "generation_blueprints": {
                    "target_meta_framework": "Pytest Python Selenium",
                    "directory_scaffolding": {
                        "pages/": "Python Selenium Page Object modules",
                        "tests/": "Pytest test cases and fixture setups",
                        "conftest.py": "Pytest global setup, teardown, and driver fixtures"
                    },
                    "scaffolding_templates": {
                        "base_page_py": "from selenium.webdriver.support.ui import WebDriverWait\n\nclass BasePage:\n    def __init__(self, driver):\n        self.driver = driver\n        self.wait = WebDriverWait(driver, 10)",
                        "page_object_py": "from selenium.webdriver.common.by import By\nfrom .base_page import BasePage\n\nclass ${className}(BasePage):\n    ${locatorsBlock}\n    \n    ${methodsBlock}"
                    },
                    "mcp_exploration_rules": {
                        "locator_priority_strategy": ["id", "name", "css", "xpath", "link"],
                        "page_object_extraction_threshold": (
                            "Generate a discrete unique ui_page_object node whenever "
                            "the browser URL path prefix changes (e.g., /billing vs /quotes)."
                        ),
                        "action_method_naming_taxonomy": "verb + target_element (e.g., click_submit_button, fill_premium_form)"
                    }
                }
            }
        }

        default_data = presets.get(preferred_preset, presets["playwright_ts"])

        # Write default data atomically
        with self._store.lock():
            self._store.atomic_write(default_data)

        return default_data

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def save_blueprint(self) -> None:
        """Atomically serialise active blueprint data to disk."""
        with self._store.lock():
            self._store.atomic_write(self.blueprint_data)

    # ------------------------------------------------------------------
    # Mutation helpers
    # ------------------------------------------------------------------

    def register_directory_standard(
        self,
        directory_pattern: str,
        architectural_pattern: str,
        rules: List[str],
    ) -> None:
        """Bind coding standards to a target folder path pattern."""
        self.blueprint_data["coding_standards"]["by_directory"][directory_pattern] = {
            "pattern": architectural_pattern,
            "rules": rules,
        }
        self.save_blueprint()

    def register_utility_signature(
        self,
        utility_id: str,
        class_name: str,
        source_file: str,
        methods: List[Dict[str, Any]],
    ) -> None:
        """Register or update a utility class signature in the reusable assets registry."""
        if source_file and os.path.isabs(source_file):
            source_file = os.path.relpath(
                source_file, self.workspace_path
            ).replace('\\', '/')
        self.blueprint_data["reusable_capabilities"][utility_id] = {
            "class_name": class_name,
            "source_file": source_file,
            "methods": methods,
        }
        self.save_blueprint()

    def register_generation_blueprint(
        self,
        meta_framework: str,
        scaffolding: Dict[str, str],
        templates: Dict[str, str],
    ) -> None:
        """Configure foundational parameters and directory maps for green-field init."""
        if "generation_blueprints" not in self.blueprint_data:
            self.blueprint_data["generation_blueprints"] = {}
        self.blueprint_data["generation_blueprints"].update({
            "target_meta_framework": meta_framework,
            "directory_scaffolding": scaffolding,
            "scaffolding_templates": templates,
        })
        self.save_blueprint()

    def register_mcp_exploration_rules(
        self,
        priority_strategies: List[str],
        extraction_threshold: str,
        naming_taxonomy: str,
    ) -> None:
        """Define rules for translating Playwright MCP discoveries into boilerplate."""
        if "generation_blueprints" not in self.blueprint_data:
            self.blueprint_data["generation_blueprints"] = {}
        self.blueprint_data["generation_blueprints"]["mcp_exploration_rules"] = {
            "locator_priority_strategy": priority_strategies,
            "page_object_extraction_threshold": extraction_threshold,
            "action_method_naming_taxonomy": naming_taxonomy,
        }
        self.save_blueprint()

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def get_standards_for_path(self, file_path: str) -> Dict[str, Any]:
        """Match a file path against stored directory patterns and return rules."""
        normalized_path = os.path.normpath(file_path).replace('\\', '/')
        for pattern, config in self.blueprint_data["coding_standards"]["by_directory"].items():
            normalized_pattern = os.path.normpath(pattern).replace('\\', '/')
            if (
                fnmatch.fnmatch(normalized_path, normalized_pattern)
                or normalized_pattern in normalized_path
            ):
                return config
        return {"pattern": "Standard Code Class", "rules": []}

    def get_all_reusable_methods(self) -> List[Dict[str, Any]]:
        """Return all registered utility method signatures flattened."""
        flat_methods = []
        for util_id, info in self.blueprint_data["reusable_capabilities"].items():
            for method in info.get("methods", []):
                method_context = dict(method)
                method_context["utility_id"] = util_id
                method_context["class_name"] = info["class_name"]
                method_context["source_file"] = info["source_file"]
                flat_methods.append(method_context)
        return flat_methods

    def get_generation_blueprint(self) -> Dict[str, Any]:
        """Expose the full init and exploration rules context to code-generation agents."""
        return self.blueprint_data.get("generation_blueprints", {})
