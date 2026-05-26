import os
import re
from typing import Dict, Type, Any
from parsers.base import BaseParser
from parsers.word_parser import WordParser
from parsers.excel_parser import ExcelParser
from parsers.pdf_parser import PDFParser
from parsers.code_parser import CodeParser
from parsers.feature_parser import FeatureParser
from parsers.schema_parser import SchemaParser
from parsers.markdown_parser import MarkdownParser
from parsers.properties_parser import PropertiesParser
from parsers.test_framework_parsers import PlaywrightBddParser, QafAutomationParser

# Map extensions to their parser classes
PARSER_MAP: Dict[str, Type[BaseParser]] = {
    ".docx": WordParser,
    ".pdf": PDFParser,
    ".xlsx": ExcelParser,
    ".csv": ExcelParser,
    ".feature": FeatureParser,
    ".json": SchemaParser,
    ".yaml": SchemaParser,
    ".yml": SchemaParser,
    ".md": MarkdownParser,
    ".bdd": QafAutomationParser,
    ".loc": QafAutomationParser,
    # SPEC-3 Wave 2: per-key rule_constant emission via PropertiesParser.
    # get_parser_for_file factory below still overrides this for locator-
    # style .properties files (routes to QafAutomationParser instead).
    ".properties": PropertiesParser,
    ".java": CodeParser,
    ".ts": CodeParser,
    ".tsx": CodeParser,
    ".js": CodeParser,
    ".jsx": CodeParser,
    ".py": CodeParser,
    ".go": CodeParser,
}

def get_parser_for_file(file_path: str, shared_context: Dict[str, Any] = None) -> BaseParser:
    """
    Factory function that dynamically routes a file to the appropriate parser instance.
    Uses multi-dimensional extension, path, and content heuristics to distinguish between 
    PlaywrightBddParser, QafAutomationParser, and CodeParser/SchemaParser, resolving 
    framework path overlaps (e.g. '/steps/', '/pages/', '/tests/') cleanly.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Target file does not exist: {file_path}")
        
    _, ext = os.path.splitext(file_path.lower())
    normalized_path = os.path.normpath(file_path).replace('\\', '/')
    file_name = os.path.basename(file_path)

    # 1. Absolute/Fast-Path Routing for Exclusively QAF BDD Files
    if ext in [".bdd", ".loc"]:
        return QafAutomationParser(shared_context)

    # 2. Properties File Heuristics (Locator Repositories vs Standard Configs)
    if ext == ".properties":
        if file_name.lower() in ["application.properties", "bootstrap.properties", "pom.properties"]:
            if file_name.lower() == "application.properties" and shared_context is not None:
                try:
                    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                        for line in f:
                            if "step.provider.pkg" in line:
                                kv = line.split('=')
                                if len(kv) > 1:
                                    pkgs = [p.strip() for p in kv[1].replace(';', ',').split(',') if p.strip()]
                                    shared_context["step_provider_packages"] = pkgs
                except Exception:
                    pass
            # SPEC-3 Wave 2: route to PropertiesParser (per-key rule_constants),
            # not SchemaParser (which emitted zero entities for .properties).
            return PropertiesParser()

        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
        except Exception:
            content = ""

        # UI Locator patterns match "element.name=xpath=//..." or "id=", "css="
        has_locator_strategy = any(
            strategy in content for strategy in ["id=", "name=", "xpath=", "css=", "link=", "class="]
        )
        is_locator_path = any(
            pat in normalized_path.lower()
            for pat in ["/locators/", "/objectrepo/", "/pages/", "/qaf/", "/ui/"]
        )

        if has_locator_strategy or is_locator_path:
            return QafAutomationParser(shared_context)
        # SPEC-3 Wave 2: fallback for unknown .properties files.
        return PropertiesParser()

    # 3. Java File Heuristics (QAF Steps / Page Objects / RestAssured / Lombok DTOs vs Spring Code)
    if ext == ".java":
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
        except Exception:
            content = ""

        # Fast-Path: Resolve Step Definitions via registered step.provider.pkg property configuration
        pkg_match = re.search(r'package\s+([a-zA-Z0-9_\.]+);', content)
        if pkg_match and shared_context and "step_provider_packages" in shared_context:
            file_package = pkg_match.group(1)
            if any(file_package.startswith(sp_pkg) for sp_pkg in shared_context["step_provider_packages"]):
                return QafAutomationParser(shared_context)

        # Core Signature Scanning
        has_qaf_selenium = any(
            keyword in content 
            for keyword in ["com.qmetry.qaf", "@QAFTestStep", "org.openqa.selenium", "WebDriverBaseTestPage", "extends MobileBasePage", "@FindBy(locator"]
        )
        has_rest_assured = "io.restassured" in content or "RestAssured" in content
        has_lombok_model = "@Data" in content or "@Builder" in content or "@Getter" in content
        is_java_record = "record " in content or re.search(r'\brecord\s+\w+', content) is not None
        
        is_test_path = any(
            pat in normalized_path.lower() 
            for pat in ["/src/test/", "/test/", "/steps/", "/pages/", "/locators/", "/automation/", "/java-qaf/", "/model/"]
        )

        # File Suffix Conventions
        is_page_suffix = file_name.lower().endswith(("page.java", "pom.java", "pageobject.java"))
        is_step_suffix = file_name.lower().endswith(("steps.java", "step.java", "stepdef.java", "stepdefinitions.java"))
        is_model_suffix = file_name.lower().endswith(("dto.java", "model.java", "request.java", "response.java"))

        if has_qaf_selenium:
            return QafAutomationParser(shared_context)
            
        if is_test_path:
            if has_rest_assured or is_step_suffix or is_page_suffix:
                return QafAutomationParser(shared_context)
            # Route lombok DTOs or Java records in test path to QAF Parser to map them as 'data_model' nodes
            if (has_lombok_model or is_java_record) and is_model_suffix:
                return QafAutomationParser(shared_context)

        return CodeParser()

    # 4. TypeScript & JavaScript Heuristics (Playwright Steps / POMs / Specs vs NestJS/React Code)
    if ext in [".ts", ".js", ".tsx", ".jsx"]:
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
        except Exception:
            content = ""

        # Playwright Specific Signatures
        has_pw_bdd_steps = any(
            keyword in content 
            for keyword in ["Given(", "When(", "Then(", "createBdd(", "@Step("]
        )
        has_pw_test = "import" in content and "@playwright/test" in content
        has_pw_locators = any(
            keyword in content 
            for keyword in ["page.locator(", "page.getByRole(", "page.getByLabel(", "page.getByPlaceholder(", "page.getByText(", "page.getByAltText(", "page.getByTitle(", "page.getByTestId("]
        )

        is_test_path = any(
            pat in normalized_path.lower() 
            for pat in ["/tests/", "/steps/", "/pages/", "/specs/", "/playwright/", "/e2e/", "/api/"]
        )
        
        is_step_suffix = file_name.lower().endswith((".steps.ts", ".step.ts", ".steps.js", ".step.js"))
        is_page_suffix = file_name.lower().endswith((".page.ts", ".pom.ts", ".page.js", ".pom.js"))
        is_spec_suffix = file_name.lower().endswith((".spec.ts", ".test.ts", ".spec.js", ".test.js"))
        
        has_ts_interface = "interface " in content or "type " in content
        is_api_client = "api" in file_name.lower() or "client" in file_name.lower()

        if has_pw_bdd_steps:
            return PlaywrightBddParser(shared_context)
            
        if is_step_suffix or is_page_suffix:
            return PlaywrightBddParser(shared_context)
            
        if is_spec_suffix and (has_pw_test or has_pw_locators):
            return PlaywrightBddParser(shared_context)
            
        if is_test_path and (has_pw_locators or "expect(" in content or (has_ts_interface and is_api_client)):
            return PlaywrightBddParser(shared_context)

        return CodeParser()

    # 5. Fallback Static Mapping
    parser_class = PARSER_MAP.get(ext)
    if not parser_class:
        raise ValueError(f"No parser registered for file extension: {ext}")
        
    if parser_class == QafAutomationParser:
        return QafAutomationParser(shared_context)
        
    return parser_class()
