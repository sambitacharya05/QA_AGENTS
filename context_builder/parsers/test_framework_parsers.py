import re
import os
import json
import logging
from typing import Dict, List, Any
from parsers.base import BaseParser

log = logging.getLogger(__name__)
from parsers.id_utils import generate_node_id as _generate_node_id
from parsers.java_utils import strip_block_comments as _strip_block_comments, JAVA_CLASS_DECL_RE as _JAVA_CLASS_DECL_RE

# SPEC-001 (Wave 5): QAF uses colon-format strategy prefixes (id:, css:, xpath:),
# not the Selenium 4 / Playwright equals-format (id=, css=, xpath=).
# Both formats must be accepted so locators.properties files resolve correctly.
_LOCATOR_STRATEGY_PREFIXES = (
    # QAF colon-format (e.g. id:login-app-no-input)
    "id:", "name:", "xpath:", "css:", "link:", "class:", "tag:",
    # Selenium 4 / Playwright equals-format
    "id=", "name=", "xpath=", "css=", "link=", "class=",
)

class PlaywrightBddParser(BaseParser):
    """Parses Playwright-BDD TypeScript step definitions, hooks, Page Object Models, and scattered locator utilities."""

    def __init__(self, shared_context: Dict[str, Any] = None):
        self.shared_context = shared_context if shared_context is not None else {}
        if "locator_cache" not in self.shared_context:
            self.shared_context["locator_cache"] = {}

    def parse(self, file_path: str) -> Dict[str, Any]:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()

        entities = []
        relationships = []
        file_name = os.path.basename(file_path)
        lower_content = content.lower()

        # Heuristic: Identify Page Object Class or Scattered Locator helper files
        is_pom_file = "page" in file_name.lower() or ("class" in lower_content and "page.locator" in lower_content)
        is_locator_ref_file = "locator" in file_name.lower() or "selector" in file_name.lower() or ("const" in content and any(kw in lower_content for kw in ["id", "xpath", "css", "role"]))

        # Check for utility class definitions in TS helper files
        normalized_path = file_path.replace('\\', '/').lower()
        is_util_file = (
            any(ind in normalized_path for ind in ["/utils/", "/helpers/", "/support/", "/lib/", "/common/"]) or
            any(ind in file_name.lower() for ind in ["util", "helper", "support"])
        )
        class_match = re.search(r'class\s+(\w+)', content)
        class_name = class_match.group(1) if class_match else None

        # 1. Harvest Scattered Locators in Utility/Configuration files
        if is_locator_ref_file and not is_pom_file:
            loc_matches = re.finditer(r'(?:const|let|var)\s+([a-zA-Z0-9_]+)\s*=\s*[\'"]([^\'"]+)[\'"]', content)
            locators_map = {}
            for match in loc_matches:
                key, val = match.group(1), match.group(2)
                if any(strategy in val or val.startswith(('#', '.', '//')) for strategy in ["id=", "xpath=", "css=", "role="]):
                    locators_map[key] = val
                    self.shared_context["locator_cache"][key] = val
            
            if locators_map:
                entities.append({
                    "id": f"pw_scattered_loc_{file_name.lower().replace('.', '_')}",
                    "type": "ui_page_object",
                    "name": f"Scattered Locators: {file_name}",
                    "description": f"Decoupled helper selector constants harvested from scattered utility config file.",
                    "metadata": {"source_file": file_path, "locators": locators_map}
                })

        elif is_util_file and not is_pom_file and not is_locator_ref_file and class_name:
            utility_id = f"util_{class_name.lower()}"
            entities.append({
                "id": utility_id,
                "type": "test_utility",
                "name": f"Test Utility: {class_name}",
                "description": f"TypeScript/JavaScript Test Utility defined in {file_name}",
                "metadata": {
                    "source_file": file_path,
                    "class_name": class_name,
                    "language": "typescript"
                }
            })

        # 2. Harvest Standard Page Object Model Class
        elif is_pom_file:
            class_match = re.search(r'class\s+(\w+)', content)
            class_name = class_match.group(1) if class_match else file_name.split('.')[0]
            page_id = f"pw_pom_{class_name.lower()}"

            locators_map = {}
            actions_list = []

            # A. Harvest inline class fields & constructor locators (supporting modifiers & optional quotes)
            # Match elements like: readonly usernameInput = page.locator('#user') or this.submit = page.locator(SUBMIT_BTN)
            locator_reg = re.compile(
                r'(?:(?:public|private|protected|readonly)\s+)?(?:this\.)?([a-zA-Z0-9_]+)\s*=\s*(?:this\.)?page\.locator\((?:[\'"]([^\'"]+)[\'"]|([a-zA-Z0-9_]+))\)'
            )
            for match in locator_reg.finditer(content):
                element_name = match.group(1)
                selector_string = match.group(2)
                const_identifier = match.group(3)

                if selector_string:
                    locators_map[element_name] = selector_string
                elif const_identifier:
                    # Resolve constants dynamically against cache
                    resolved_selector = self.shared_context["locator_cache"].get(const_identifier, f"Unresolved: {const_identifier}")
                    locators_map[element_name] = resolved_selector

            # B. Harvest semantic user-facing locators
            # e.g., readonly submitButton = page.getByRole('button', { name: 'Submit' })
            semantic_reg = re.compile(
                r'(?:(?:public|private|protected|readonly)\s+)?(?:this\.)?([a-zA-Z0-9_]+)\s*=\s*(?:this\.)?page\.getBy(Role|Label|Placeholder|Text|AltText|Title|TestId)\(([^)]+)\)'
            )
            for match in semantic_reg.finditer(content):
                element_name, strategy, query_params = match.group(1), match.group(2), match.group(3)
                locators_map[element_name] = f"getBy{strategy}({query_params.strip()})"

            # C. Harvest inline locators inside methods recursively
            inline_semantic_reg = re.compile(
                r'(?:this\.)?page\.getBy(Role|Label|Placeholder|Text|AltText|Title|TestId)\(\s*([\'"])(.*?)\2(?:,\s*\{[^}]*\})?\)'
            )
            inline_locator_reg = re.compile(
                r'(?:this\.)?page\.locator\(\s*([\'"])(.*?)\1\)'
            )
            
            for match in inline_semantic_reg.finditer(content):
                strategy = match.group(1)
                query = match.group(3)
                slug = re.sub(r'[^a-zA-Z0-9_]', '_', query.lower())
                slug = re.sub(r'_+', '_', slug).strip('_')
                key = f"inline_{strategy.lower()}_{slug}"
                val = f"getBy{strategy}(\"{query}\")"
                locators_map[key] = val

            for match in inline_locator_reg.finditer(content):
                query = match.group(2)
                slug = re.sub(r'[^a-zA-Z0-9_]', '_', query.lower())
                slug = re.sub(r'_+', '_', slug).strip('_')
                key = f"inline_locator_{slug}"
                val = f"locator(\"{query}\")"
                locators_map[key] = val

            # D. Harvest accessible Page actions/methods
            # Exclude control-flow keywords that the regex would otherwise match
            # (e.g. if(cond){, for(...){, while(...){, catch(e){)
            _CONTROL_FLOW = frozenset({
                "if", "for", "while", "switch", "catch", "try", "else", "do",
                "finally", "with",
            })
            action_matches = re.finditer(r'(?:async\s+)?([a-zA-Z0-9_]+)\s*\([^)]*\)\s*\{', content)
            for match in action_matches:
                method_name = match.group(1)
                if (
                    method_name not in ["constructor", "locator", "expect"]
                    and method_name not in _CONTROL_FLOW
                ):
                    actions_list.append(method_name)

            entities.append({
                "id": page_id,
                "type": "ui_page_object",
                "name": f"Playwright POM: {class_name}",
                "description": f"UI interaction gateway layer wrapping {len(locators_map)} structural DOM element definitions.",
                "metadata": {
                    "source_file": file_path,
                    "framework": "playwright",
                    "class_name": class_name,
                    "locators": locators_map,
                    "exposed_actions": actions_list
                }
            })

            # Spec 007: emit ui_locator nodes + BINDS_LOCATOR edges for every locator
            for loc_name, loc_value in locators_map.items():
                loc_id = _generate_node_id("ui_locator", f"{page_id}:{loc_name}")
                entities.append(self.make_entity(
                    loc_id, "ui_locator", loc_name,
                    description=f"Locator: {loc_value}",
                    metadata={"selector": loc_value, "source_file": file_path},
                ))
                relationships.append(self.make_relationship(
                    page_id, loc_id, "BINDS_LOCATOR",
                    parser_name="playwright_bdd",
                    notes=f"page_field:{loc_name}",
                ))

        # 3. Handle step definition parsing tracks (Given/When/Then steps can overlap in step files)
        # Also detect POM imports to emit USES_POM edges (Spec 007)
        imported_pom_ids: List[str] = []
        ts_import_re = re.compile(
            r'import\s+\{?\s*([A-Z][a-zA-Z0-9_]+)\s*\}?\s+from\s+[\'"]([^\'"]*(?:page|pom|Page|POM)[^\'"]*)[\'"]'
        )
        for imp_match in ts_import_re.finditer(content):
            imported_class = imp_match.group(1)
            pom_id = f"pw_pom_{imported_class.lower()}"
            imported_pom_ids.append(pom_id)

        step_matches = re.finditer(r'(Given|When|Then)\s*\(\s*[\'"]([^\'"]+)[\'"]\s*,\s*(?:async\s*)?\(', content)
        for match in step_matches:
            step_type, text_pattern = match.group(1), match.group(2)
            step_id = f"pw_step_{re.sub(r'[^a-zA-Z0-9_]', '_', text_pattern.lower())}"
            entities.append({
                "id": step_id,
                "type": "test_step_definition",
                "name": f"Step Def [{step_type}]: {text_pattern}",
                "description": f"Playwright-BDD step definition mapping test actions to local code interfaces.",
                "metadata": {"source_file": file_path, "step_type": step_type, "pattern": text_pattern}
            })
            # Emit USES_POM for each POM imported in this step file
            for pom_id in imported_pom_ids:
                relationships.append(self.make_relationship(
                    step_id, pom_id, "USES_POM",
                    parser_name="playwright_bdd",
                    notes=f"step_imports_pom:{pom_id}",
                ))

        # 4. Scan Class Method Step Decorators: @Step('([^']+)')
        decorator_matches = re.finditer(r'@Step\s*\(\s*[\'"]([^\'"]+)[\'"]\s*\)', content)
        for match in decorator_matches:
            text_pattern = match.group(1)
            step_id = f"pw_step_{re.sub(r'[^a-zA-Z0-9_]', '_', text_pattern.lower())}"
            entities.append({
                "id": step_id,
                "type": "test_step_definition",
                "name": f"Step Def [Decorator]: {text_pattern}",
                "description": f"Playwright-BDD step method decorator configuration.",
                "metadata": {"source_file": file_path, "step_type": "decorator", "pattern": text_pattern}
            })

        # 5. Scan TypeScript Interfaces as Data Models
        interface_matches = re.finditer(r'(?:export\s+)?interface\s+(\w+)\s*\{([\s\S]*?)\}', content)
        for match in interface_matches:
            interface_name = match.group(1)
            body = match.group(2)
            
            fields_list = []
            fields_meta = {}
            # Match fields like name: string; or age?: number; or payload: Type;
            field_matches = re.finditer(r'(\w+)(\s*\??\s*):\s*([\w<>\[\]\s|{}":;,\(\)=>]+?)(?:;|,|\n)', body)
            for f_match in field_matches:
                field_name = f_match.group(1)
                is_optional = "?" in f_match.group(2)
                field_type = f_match.group(3).strip()
                fields_list.append(f"- `{field_name}` ({field_type}{' [Optional]' if is_optional else ''})")
                fields_meta[field_name] = field_type
                
            model_id = f"schema_{interface_name.lower()}_ts"
            entities.append({
                "id": model_id,
                "type": "data_model",
                "name": f"TS Interface: {interface_name}",
                "description": f"TypeScript data structure interface containing properties:\n" + "\n".join(fields_list),
                "metadata": {
                    "source_file": file_path,
                    "class_name": interface_name,
                    "language": "typescript",
                    "is_ts_interface": True,
                    "fields": fields_meta
                }
            })

        return {"raw_text": content, "entities": entities, "relationships": relationships}


class QafAutomationParser(BaseParser):
    """Parses QAF configurations, BDD scenarios, object repositories, and Java Page Object Classes using shared context."""

    def __init__(self, shared_context: Dict[str, Any] = None):
        self.shared_context = shared_context if shared_context is not None else {}
        if "locator_cache" not in self.shared_context:
            self.shared_context["locator_cache"] = {}
        # Fix-B (post-wave-5): initialise config_cache unconditionally so callers
        # never receive a shared_context without the key, even when no config
        # entries are present in the parsed file.
        if "config_cache" not in self.shared_context:
            self.shared_context["config_cache"] = {}

    def parse(self, file_path: str) -> Dict[str, Any]:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()

        entities = []
        relationships = []
        # Fix-C (post-wave-5): collect structured warnings (duplicate keys, etc.)
        # so callers can detect misconfigured .properties files without inspecting
        # logs.  Initialised here (not inside the .properties branch) so all code
        # paths include it in the return dict.
        _parse_warnings: List[Dict[str, Any]] = []
        file_name = os.path.basename(file_path)

        # 1. QAF Scenario Processing (.bdd files)
        if file_path.endswith('.bdd'):
            scenarios = re.split(r'(?=SCENARIO:\s*)', content)
            for sc_block in scenarios:
                if not sc_block.strip():
                    continue
                name_match = re.search(r'SCENARIO:\s*(.+)', sc_block)
                meta_match = re.search(r'META-DATA:\s*(\{.*\})', sc_block)

                if name_match:
                    sc_name = name_match.group(1).strip()
                    meta_dict = {}
                    if meta_match:
                        try:
                            meta_dict = json.loads(meta_match.group(1))
                        except Exception:
                            pass

                    node_id = f"qaf_scenario_{re.sub(r'[^a-zA-Z0-9_]', '_', sc_name.lower())}"
                    entities.append({
                        "id": node_id,
                        "type": "test_scenario",
                        "name": f"QAF BDD: {sc_name}",
                        "description": f"QAF test validation block context:\n{sc_block.strip()}",
                        "metadata": {"source_file": file_path, "qaf_meta": meta_dict}
                    })

        # 2. UI Object Repository Processing (.properties / .loc files)
        elif file_path.endswith(('.properties', '.loc')):
            locators_map = {}
            config_map = {}   # SPEC-006 (Wave 5): collect non-locator config k/v pairs

            for line in content.split('\n'):
                line_stripped = line.strip()
                if not line_stripped or line_stripped.startswith(('#', '!')):
                    continue

                kv_match = re.match(r'^([^=]+)=\s*(.+)$', line_stripped)
                if kv_match:
                    key, val = kv_match.group(1).strip(), kv_match.group(2).strip()

                    # SPEC-001 (Wave 5): strip .locator suffix so Java @FindBy(locator="FLD_APP_NO")
                    # references resolve against bare key names (not "FLD_APP_NO.locator").
                    cache_key = key[:-len(".locator")] if key.endswith(".locator") else key

                    is_locator = (
                        any(val.startswith(p) or p in val for p in _LOCATOR_STRATEGY_PREFIXES)
                        or val.startswith(("//", "/", "#", "."))
                    )

                    if is_locator:
                        # Fix-C: detect duplicate locator keys before overwriting
                        if cache_key in locators_map:
                            log.warning(
                                "Duplicate locator key %r in %s — last value wins.",
                                cache_key, file_path,
                            )
                            _parse_warnings.append({
                                "reason": "duplicate_key",
                                "key": cache_key,
                                "type": "locator",
                                "file": file_path,
                            })
                        self.shared_context["locator_cache"][cache_key] = val
                        locators_map[cache_key] = val
                    else:
                        # SPEC-006: non-locator entries go to config_map
                        # Fix-C: detect duplicate config keys before overwriting
                        if key in config_map:
                            log.warning(
                                "Duplicate config key %r in %s — last value wins.",
                                key, file_path,
                            )
                            _parse_warnings.append({
                                "reason": "duplicate_key",
                                "key": key,
                                "type": "config",
                                "file": file_path,
                            })
                        config_map[key] = val

            if locators_map:
                entities.append({
                    "id": f"qaf_repo_{file_name.lower().replace('.', '_')}",
                    "type": "ui_page_object",
                    "name": f"Global Locator Repository: {file_name}",
                    "description": f"Decoupled QAF object repository file wrapping {len(locators_map)} selector bindings.",
                    "metadata": {"source_file": file_path, "locators": locators_map}
                })

            # SPEC-006 (Wave 5): emit a data_model node for configuration .properties files
            if config_map:
                config_id = f"config_{file_name.lower().replace('.', '_').replace('-', '_')}"
                desc_lines = [f"- `{k}` = `{v}`" for k, v in sorted(config_map.items())]
                entities.append({
                    "id": config_id,
                    "type": "data_model",
                    "name": f"Configuration: {file_name}",
                    "description": (
                        f"Application configuration file defining {len(config_map)} runtime parameters:\n"
                        + "\n".join(desc_lines[:30])
                        + ("\n..." if len(desc_lines) > 30 else "")
                    ),
                    "metadata": {
                        "source_file": file_path,
                        "subtype": "config",
                        "config": config_map,
                    },
                })
                # Make config values queryable by other parsers via shared_context
                self.shared_context.setdefault("config_cache", {}).update(config_map)

        # 3. Automation Java Source File Scanning (Steps, RestAssured, Lombok DTOs, Page Objects)
        elif file_path.endswith('.java'):
            # SPEC-003 (Wave 5): strip Javadoc/block comments before scanning so that prose
            # like "Utility class providing actions" doesn't match before the real declaration.
            # Anchored regex requires PascalCase name, preventing lowercase false-positives.
            _source_clean = _strip_block_comments(content)
            class_match = _JAVA_CLASS_DECL_RE.search(_source_clean)
            class_name = class_match.group(1) if class_match else ""
            is_lombok_model = "@Data" in content or "@Builder" in content or "@Getter" in content
            is_java_record = "record " in content or re.search(r'\brecord\s+\w+', content) is not None
            is_page_object = (
                any(kw in content for kw in ["BasePage", "WebDriverBaseTestPage", "extends MobileBasePage"])
                or ("Page" in class_name and ("WebDriver" in content or "org.openqa.selenium.By" in content))
            )

            # Sub-Branch A: Lombok Class / Record Definition Mining
            if is_lombok_model or is_java_record:
                if class_name:
                    fields_list = []
                    fields_meta = {}
                    if is_java_record:
                        # Extract record components from record ClaimRequest(String memberId, ...)
                        record_match = re.search(r'\brecord\s+' + re.escape(class_name) + r'\s*\((.*?)\)', content, re.DOTALL)
                        if record_match:
                            params_str = record_match.group(1)
                            for param in params_str.split(','):
                                param = param.strip()
                                if param:
                                    parts = param.split()
                                    if len(parts) >= 2:
                                        p_name = parts[-1]
                                        p_type = " ".join(parts[:-1])
                                        fields_list.append(f"- `{p_name}` ({p_type})")
                                        fields_meta[p_name] = p_type
                    else:
                        field_matches = re.finditer(r'private\s+([a-zA-Z0-9_<>\s?,]+)\s+([a-zA-Z0-9_]+);', content)
                        for f_match in field_matches:
                            fields_list.append(f"- `{f_match.group(2)}` ({f_match.group(1).strip()})")
                            fields_meta[f_match.group(2)] = f_match.group(1).strip()

                    model_id = f"schema_{class_name.lower()}_java"
                    entities.append({
                        "id": model_id,
                        "type": "data_model",
                        "name": f"Java Record: {class_name}" if is_java_record else f"Lombok Model: {class_name}",
                        "description": f"Automated test DTO payload containing properties:\n" + "\n".join(fields_list),
                        "metadata": {
                            "source_file": file_path,
                            "class_name": class_name,
                            "language": "java",
                            "is_lombok_data": "@Data" in content,
                            "is_lombok_builder": "@Builder" in content,
                            "is_java_record": is_java_record,
                            "fields": fields_meta
                        }
                    })

            # Sub-Branch B: Java Page Object Model Classes Ingestion
            elif is_page_object:
                if class_name:
                    page_id = f"qaf_pom_{class_name.lower()}"

                    resolved_bindings = {}
                    resolved_locators = {}
                    
                    # B.1. Parse QAF FindBy annotations
                    findby_reg = re.compile(
                        r'@FindBy\(\s*locator\s*=\s*["\']([^"\']+)["\']\s*\)\s*(?:private|public|protected)?\s+([a-zA-Z0-9_<>\s?,]+)\s+(\w+);'
                    )
                    for match in findby_reg.finditer(content):
                        loc_key, _, element_name = match.group(1), match.group(2), match.group(3)
                        literal_selector = self.shared_context["locator_cache"].get(loc_key, "Unresolved: Key missing during compilation pass")
                        resolved_bindings[element_name] = {"key_reference": loc_key, "literal_selector": literal_selector}
                        resolved_locators[element_name] = literal_selector

                    # B.2. Parse standard Selenium By fields
                    selenium_by_reg = re.compile(
                        r'(?:private|public|protected)?\s+(?:final\s+)?By\s+(\w+)\s*=\s*By\s*\.\s*(id|name|xpath|cssSelector|linkText|partialLinkText|tagName|className)\s*\(\s*([\'"])(.*?)\3\s*\);'
                    )
                    strategy_map = {
                        "id": "id=",
                        "name": "name=",
                        "xpath": "xpath=",
                        "cssSelector": "css=",
                        "linkText": "link=",
                        "partialLinkText": "link=",
                        "className": "class=",
                        "tagName": "tag="
                    }
                    for match in selenium_by_reg.finditer(content):
                        element_name, strategy, _, query = match.group(1), match.group(2), match.group(3), match.group(4)
                        normalized_strategy = strategy_map.get(strategy, "")
                        full_selector = f"{normalized_strategy}{query}"
                        resolved_locators[element_name] = full_selector
                        resolved_bindings[element_name] = {"key_reference": "", "literal_selector": full_selector}

                    entities.append({
                        "id": page_id,
                        "type": "ui_page_object",
                        "name": f"QAF POM: {class_name}",
                        "description": f"Decoupled Selenium Page Object Class incorporating {len(resolved_locators)} structural selector components.",
                        "metadata": {
                            "source_file": file_path,
                            "framework": "qaf_selenium",
                            "class_name": class_name,
                            "bindings": resolved_bindings,
                            "locators": resolved_locators
                        }
                    })

            # Sub-Branch C: Class node mapping for non-lombok step classes or helper utilities
            if not is_lombok_model and not is_page_object:
                if class_name:
                    is_step_class = "step" in file_name.lower()
                    node_type = "code_component" if is_step_class else "test_utility"
                    utility_id = f"util_{class_name.lower()}" if node_type == "test_utility" else f"component_{class_name.lower()}"
                    
                    entities.append({
                        "id": utility_id,
                        "type": node_type,
                        "name": f"{'Step Class' if is_step_class else 'Test Utility'}: {class_name}",
                        "description": f"Java {node_type} defined in automation suite: {file_name}",
                        "metadata": {
                            "source_file": file_path,
                            "class_name": class_name,
                            "language": "java"
                        }
                    })

            # Sub-Branch D: QAF Step Hooks and RestAssured Execution Call Mapping
            qaf_step_matches = re.finditer(r'@QAFTestStep\(\s*description\s*=\s*["\']([^"\'\n]+)["\']\)', content)
            for q_match in qaf_step_matches:
                step_text = q_match.group(1)
                step_id = f"java_step_{re.sub(r'[^a-zA-Z0-9_]', '_', step_text.lower())}"
                entities.append({
                    "id": step_id,
                    "type": "test_step_definition",
                    "name": f"QAF Java Step: {step_text}",
                    "description": f"Java execution statement referenced by QAF test engine workflow bundles.",
                    "metadata": {"source_file": file_path, "step_expression": step_text}
                })

            # Native RestAssured client parsing pipelines.
            # Only match string arguments that look like URL paths (start with /)
            # to avoid false positives from list.get(0), map.get("key"), Optional.get(), etc.
            ra_matches = re.finditer(r'\.(get|post|put|delete)\s*\(\s*["\']([^"\'\n]+)["\']\s*\)', content, re.IGNORECASE)
            for ra_match in ra_matches:
                verb, route_path = ra_match.group(1).upper(), ra_match.group(2)
                # Filter: route_path must start with '/' — eliminates map keys and literals
                if not route_path.startswith("/"):
                    continue
                from parsers.id_utils import generate_node_id
                endpoint_id = generate_node_id("api_endpoint", f"{verb} {route_path}")

                entities.append({
                    "id": endpoint_id,
                    "type": "api_endpoint",
                    "name": f"RestAssured Trigger: {verb} {route_path}",
                    "description": f"Automated REST validation interface executed inside testing framework layer.",
                    "metadata": {"source_file": file_path, "http_method": verb, "target_route": route_path}
                })

        return {
            "raw_text": content,
            "entities": entities,
            "relationships": relationships,
            # Fix-C: structured parse warnings (duplicate keys, etc.)
            "warnings": _parse_warnings,
        }
