"""Wave 5 regression tests — SPEC-001 through SPEC-006.

Covers:
  SPEC-001 (010): QAF colon-format locator prefix detection + .locator key strip
  SPEC-002 (011): Word parser empty-section / pagination-only guard
  SPEC-003 (012): Java class name extraction skips Javadoc prose
  SPEC-004 (013): Scenario Outline rows get unique node IDs
  SPEC-005 (014): NFR business_rule nodes excluded from TESTS scoring
  SPEC-006 (015): Non-locator .properties entries emit a data_model config node
"""

import os
import sys
import re
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from parsers.test_framework_parsers import QafAutomationParser, _LOCATOR_STRATEGY_PREFIXES
from parsers.java_utils import strip_block_comments, JAVA_CLASS_DECL_RE
from parsers.word_parser import WordParser, _PAGINATION_RE, _MIN_MEANINGFUL_CHARS
from parsers.feature_parser import FeatureParser
from db.graph_store import GraphStore
from engine.edges.heuristic import HeuristicEdgeMapper


# ===========================================================================
# SPEC-001 — QAF Locator Format Mismatch
# ===========================================================================

class TestQafLocatorPrefixes:
    """_LOCATOR_STRATEGY_PREFIXES must include both colon and equals formats."""

    def test_colon_formats_present(self):
        assert "id:" in _LOCATOR_STRATEGY_PREFIXES
        assert "css:" in _LOCATOR_STRATEGY_PREFIXES
        assert "xpath:" in _LOCATOR_STRATEGY_PREFIXES
        assert "name:" in _LOCATOR_STRATEGY_PREFIXES
        assert "link:" in _LOCATOR_STRATEGY_PREFIXES
        assert "class:" in _LOCATOR_STRATEGY_PREFIXES
        assert "tag:" in _LOCATOR_STRATEGY_PREFIXES

    def test_equals_formats_still_present(self):
        assert "id=" in _LOCATOR_STRATEGY_PREFIXES
        assert "css=" in _LOCATOR_STRATEGY_PREFIXES
        assert "xpath=" in _LOCATOR_STRATEGY_PREFIXES


class TestQafLocatorParsing:
    """QAF colon-format .properties files populate locator_cache correctly."""

    LOCATORS_CONTENT = """\
# QAF locator definitions
FLD_APP_NO.locator=id:login-app-no-input
FLD_MOBILE.locator=id:login-mobile-input
LNK_RESEND_OTP.locator=css:a.resumeapp-resendCode
BTN_VERIFY.locator=xpath://button[@id='verify']
"""

    def _parse(self, tmp_path, content=None):
        f = tmp_path / "locators.properties"
        f.write_text(content or self.LOCATORS_CONTENT)
        shared = {"locator_cache": {}}
        parser = QafAutomationParser(shared_context=shared)
        result = parser.parse(str(f))
        return result, shared

    def test_locator_cache_populated(self, tmp_path):
        _, shared = self._parse(tmp_path)
        assert "FLD_APP_NO" in shared["locator_cache"]
        assert "LNK_RESEND_OTP" in shared["locator_cache"]
        assert "BTN_VERIFY" in shared["locator_cache"]

    def test_locator_suffix_stripped(self, tmp_path):
        _, shared = self._parse(tmp_path)
        # Keys must NOT carry the .locator suffix
        assert "FLD_APP_NO.locator" not in shared["locator_cache"]
        assert "FLD_APP_NO" in shared["locator_cache"]

    def test_colon_format_values_preserved(self, tmp_path):
        _, shared = self._parse(tmp_path)
        assert shared["locator_cache"]["FLD_APP_NO"] == "id:login-app-no-input"
        assert shared["locator_cache"]["LNK_RESEND_OTP"] == "css:a.resumeapp-resendCode"

    def test_xpath_startswith_slash_also_captured(self, tmp_path):
        _, shared = self._parse(tmp_path)
        assert "BTN_VERIFY" in shared["locator_cache"]
        assert shared["locator_cache"]["BTN_VERIFY"].startswith("xpath:")

    def test_locator_repo_entity_emitted(self, tmp_path):
        result, _ = self._parse(tmp_path)
        pom_nodes = [e for e in result["entities"] if e["type"] == "ui_page_object"]
        assert len(pom_nodes) == 1
        assert pom_nodes[0]["id"] == "qaf_repo_locators_properties"

    def test_equals_format_still_accepted(self, tmp_path):
        """Selenium-style id=value must still be captured (regression guard)."""
        content = "BTN_LOGIN.locator=id=login-button\n"
        _, shared = self._parse(tmp_path, content)
        assert "BTN_LOGIN" in shared["locator_cache"]
        assert shared["locator_cache"]["BTN_LOGIN"] == "id=login-button"

    def test_zero_unresolved_bindings_when_java_parsed_after_properties(self, tmp_path):
        """End-to-end: Java @FindBy annotations resolve against the populated cache."""
        _, shared = self._parse(tmp_path)

        java_content = """\
package com.example;
import com.qaf.selenium.ui.WebDriverBaseTestPage;
import com.qaf.testng.ui.webdriver.annotations.FindBy;

public class ResumeApplicationPage extends WebDriverBaseTestPage {
    @FindBy(locator = "FLD_APP_NO")
    private QAFWebElement fldAppNo;

    @FindBy(locator = "LNK_RESEND_OTP")
    private QAFWebElement lnkResendOtp;
}
"""
        java_file = tmp_path / "ResumeApplicationPage.java"
        java_file.write_text(java_content)
        parser = QafAutomationParser(shared_context=shared)
        result = parser.parse(str(java_file))
        pom_nodes = [e for e in result["entities"] if e["type"] == "ui_page_object"]
        assert len(pom_nodes) == 1
        bindings = pom_nodes[0]["metadata"]["bindings"]
        unresolved = [
            k for k, v in bindings.items()
            if "Unresolved" in v.get("literal_selector", "")
        ]
        assert unresolved == [], f"Still unresolved: {unresolved}"


# ===========================================================================
# SPEC-003 — Java Class Name Javadoc Collision
# ===========================================================================

class TestJavaUtils:
    """strip_block_comments and JAVA_CLASS_DECL_RE prevent Javadoc false matches."""

    JAVADOC_SOURCE = """\
/**
 * Utility class providing composite click, input, and wait operations
 * for Selenium-based automation tests.
 */
public class CommonActions extends BaseActions {
    public void click() {}
}
"""

    def test_strip_removes_javadoc(self):
        cleaned = strip_block_comments(self.JAVADOC_SOURCE)
        assert "providing" not in cleaned
        assert "CommonActions" in cleaned

    def test_regex_matches_correct_class(self):
        cleaned = strip_block_comments(self.JAVADOC_SOURCE)
        m = JAVA_CLASS_DECL_RE.search(cleaned)
        assert m is not None
        assert m.group(1) == "CommonActions"

    def test_regex_rejects_lowercase_prose_word(self):
        # 'providing' is lowercase — JAVA_CLASS_DECL_RE requires PascalCase
        m = JAVA_CLASS_DECL_RE.search("class providing something")
        assert m is None

    def test_enum_and_interface_captured(self):
        source = "public interface PaymentGateway {}"
        m = JAVA_CLASS_DECL_RE.search(source)
        assert m is not None
        assert m.group(1) == "PaymentGateway"

    def test_enum_captured(self):
        source = "public enum PolicyStatus { ACTIVE, CANCELLED }"
        m = JAVA_CLASS_DECL_RE.search(source)
        assert m is not None
        assert m.group(1) == "PolicyStatus"

    def test_full_parse_produces_correct_class_node(self, tmp_path):
        java_file = tmp_path / "CommonActions.java"
        java_file.write_text(self.JAVADOC_SOURCE)
        shared = {"locator_cache": {}}
        parser = QafAutomationParser(shared_context=shared)
        result = parser.parse(str(java_file))
        ids = [e["id"] for e in result["entities"]]
        assert "util_providing" not in ids, "False-match 'providing' should not appear"
        # CommonActions is neither a POM nor a Lombok model, so it falls to utility
        util_nodes = [e for e in result["entities"] if "commonactions" in e["id"]]
        assert len(util_nodes) >= 1

    def test_wrong_id_not_created(self, tmp_path):
        java_file = tmp_path / "CommonActions.java"
        java_file.write_text(self.JAVADOC_SOURCE)
        shared = {"locator_cache": {}}
        parser = QafAutomationParser(shared_context=shared)
        result = parser.parse(str(java_file))
        ids = [e["id"] for e in result["entities"]]
        assert "util_providing" not in ids

    # -- Fix-A: strip_block_comments dangling */ hardening --

    def test_strip_removes_dangling_close_marker(self):
        """Malformed input with */ inside a comment body must leave no dangling */."""
        src = "/** outer /* inner */ class WrongClass */ public class RightClass {}"
        cleaned = strip_block_comments(src)
        assert "*/" not in cleaned, f"Dangling */ still present: {repr(cleaned)}"

    def test_regex_picks_right_class_after_pseudo_nested_comment(self):
        """After stripping a pseudo-nested comment the real class declaration is findable.

        Fix-A guarantees no dangling */ remains; the orphaned text from inside the
        comment body ('class WrongClass') can't be fully removed without a recursive
        parser for invalid Java.  Using multi-line input ensures the real 'public class'
        declaration starts at its own line-start and is reachable via findall.
        """
        src = (
            "/** outer /* inner */ class WrongClass */\n"
            "public class RightClass {}"
        )
        cleaned = strip_block_comments(src)
        assert "*/" not in cleaned, f"Dangling */ still present: {repr(cleaned)}"
        all_found = JAVA_CLASS_DECL_RE.findall(cleaned)
        assert "RightClass" in all_found, (
            f"RightClass not found in {all_found}; cleaned: {repr(cleaned)}"
        )

    def test_multiple_independent_comments_all_stripped(self):
        """Multiple non-overlapping block comments are all removed cleanly."""
        src = "/* header */ public class MyService {\n    /* impl */ void doWork() {}\n}"
        cleaned = strip_block_comments(src)
        assert "/*" not in cleaned, "Open-comment marker remained"
        assert "*/" not in cleaned, "Close-comment marker remained"
        m = JAVA_CLASS_DECL_RE.search(cleaned)
        assert m is not None and m.group(1) == "MyService"


# ===========================================================================
# SPEC-004 — Scenario Outline ID Uniqueness
# ===========================================================================

OUTLINE_FEATURE = """\
Feature: Resume application validation

  Scenario Outline: Application Number fails validation with invalid formats
    Given I am on the Resume Application page
    When I enter "<app_number>" in the Application Number field
    Then I should see the error "<error_message>"

    Examples:
      | app_number           | error_message                          |
      | abc                  | Minimum 8 characters required          |
      | abc!@#               | Only alphanumeric and - allowed        |
      | 12345678901234567    | Maximum 15 characters exceeded         |
"""

PLAIN_FEATURE = """\
Feature: Login

  Scenario: User logs in successfully
    Given I am on the login page
    When I enter valid credentials
    Then I should be redirected to the dashboard
"""


class TestScenarioOutlineIdUniqueness:

    def _parse_feature(self, tmp_path, content, filename="test.feature"):
        f = tmp_path / filename
        f.write_text(content)
        parser = FeatureParser()
        return parser.parse(str(f))

    def test_outline_produces_three_distinct_nodes(self, tmp_path):
        result = self._parse_feature(tmp_path, OUTLINE_FEATURE)
        outline_nodes = [
            e for e in result["entities"]
            if "application_number_fails_validation" in e["id"]
        ]
        assert len(outline_nodes) == 3, (
            f"Expected 3 nodes, got {len(outline_nodes)}: {[n['id'] for n in outline_nodes]}"
        )

    def test_outline_node_ids_all_distinct(self, tmp_path):
        result = self._parse_feature(tmp_path, OUTLINE_FEATURE)
        outline_ids = [
            e["id"] for e in result["entities"]
            if "application_number_fails_validation" in e["id"]
        ]
        assert len(set(outline_ids)) == 3, f"Duplicate IDs: {outline_ids}"

    def test_outline_ids_carry_ex_suffix(self, tmp_path):
        result = self._parse_feature(tmp_path, OUTLINE_FEATURE)
        outline_ids = [
            e["id"] for e in result["entities"]
            if "application_number_fails_validation" in e["id"]
        ]
        assert any("_ex1" in nid for nid in outline_ids)
        assert any("_ex2" in nid for nid in outline_ids)
        assert any("_ex3" in nid for nid in outline_ids)

    def test_outline_nodes_carry_row_metadata(self, tmp_path):
        result = self._parse_feature(tmp_path, OUTLINE_FEATURE)
        outline_nodes = [
            e for e in result["entities"]
            if "application_number_fails_validation" in e["id"]
        ]
        indices = {n["metadata"]["example_row_index"] for n in outline_nodes}
        assert indices == {0, 1, 2}

    def test_outline_nodes_have_different_step_content(self, tmp_path):
        result = self._parse_feature(tmp_path, OUTLINE_FEATURE)
        outline_nodes = [
            e for e in result["entities"]
            if "application_number_fails_validation" in e["id"]
        ]
        steps_sets = [frozenset(n["metadata"]["steps"]) for n in outline_nodes]
        assert len(set(steps_sets)) == 3, "Step sets are identical — expansion failed"

    def test_plain_scenario_has_no_row_suffix(self, tmp_path):
        result = self._parse_feature(tmp_path, PLAIN_FEATURE, "login.feature")
        plain_nodes = [e for e in result["entities"] if e["type"] == "test_scenario"]
        assert len(plain_nodes) == 1
        assert plain_nodes[0]["metadata"]["example_row_index"] is None
        assert "_ex" not in plain_nodes[0]["id"]

    def test_outline_part_of_edges_all_emitted(self, tmp_path):
        result = self._parse_feature(tmp_path, OUTLINE_FEATURE)
        # Relationships use "relationship" key (not "type") and "source_id" (not "source")
        part_of_edges = [
            r for r in result["relationships"]
            if r["relationship"] == "PART_OF"
            and "application_number_fails_validation" in r["source_id"]
        ]
        assert len(part_of_edges) == 3


# ===========================================================================
# SPEC-002 — Word Parser Empty Section Detection
# ===========================================================================

class TestWordParserEmptySectionGuard:
    """_PAGINATION_RE and _create_section_entity content gate."""

    # -- _PAGINATION_RE unit tests --

    def test_pagination_re_matches_page_n_of_m(self):
        assert _PAGINATION_RE.fullmatch("Page 1 of 3")

    def test_pagination_re_matches_multiline_pages(self):
        assert _PAGINATION_RE.fullmatch("Page 1 of 3\nPage 2 of 3")

    def test_pagination_re_matches_numeric_fraction(self):
        assert _PAGINATION_RE.fullmatch("1/3")

    def test_pagination_re_does_not_match_real_content(self):
        assert not _PAGINATION_RE.fullmatch(
            "The application must validate the OTP within 30 seconds of generation."
        )

    def test_pagination_re_does_not_match_partial_pagination_with_content(self):
        assert not _PAGINATION_RE.fullmatch("Page 1 of 3\nSome real requirement here.")

    # -- Integration via _create_section_entity --

    def _make_parser(self):
        parser = WordParser()
        parser._warnings = []
        return parser

    def test_pagination_only_skipped_and_warned(self):
        parser = self._make_parser()
        entities = []
        parser._create_section_entity(
            entities, "test.docx", "/path/test.docx",
            "Business / Functional Requirements (FR)",
            "Page 1 of 3\nPage 2 of 3",
        )
        assert entities == [], "No entity should be created for pagination-only content"
        assert len(parser._warnings) == 1
        assert parser._warnings[0]["reason"] == "pagination_only"
        assert parser._warnings[0]["heading"] == "Business / Functional Requirements (FR)"

    def test_empty_content_skipped_and_warned(self):
        parser = self._make_parser()
        entities = []
        parser._create_section_entity(
            entities, "test.docx", "/path/test.docx", "Empty Section", "   "
        )
        assert entities == []
        assert parser._warnings[0]["reason"] == "section_empty"

    def test_too_short_content_skipped_and_warned(self):
        parser = self._make_parser()
        entities = []
        parser._create_section_entity(
            entities, "test.docx", "/path/test.docx", "Short", "Hi"
        )
        assert entities == []
        assert parser._warnings[0]["reason"] == "content_too_short"

    def test_legitimate_content_creates_entity(self):
        parser = self._make_parser()
        entities = []
        content = "The system must validate OTP within 30 seconds of generation per NFR-SEC-001."
        parser._create_section_entity(
            entities, "test.docx", "/path/test.docx", "OTP Requirements", content
        )
        assert len(entities) == 1
        assert entities[0]["type"] in ("product_feature", "business_rule")
        assert parser._warnings == []

    def test_min_meaningful_chars_threshold(self):
        """Content of exactly _MIN_MEANINGFUL_CHARS chars must pass the gate."""
        parser = self._make_parser()
        entities = []
        content = "x" * _MIN_MEANINGFUL_CHARS
        parser._create_section_entity(
            entities, "test.docx", "/path/test.docx", "Heading", content
        )
        assert len(entities) == 1

    def test_one_below_threshold_is_warned(self):
        parser = self._make_parser()
        entities = []
        content = "x" * (_MIN_MEANINGFUL_CHARS - 1)
        parser._create_section_entity(
            entities, "test.docx", "/path/test.docx", "Heading", content
        )
        assert entities == []
        assert parser._warnings[0]["reason"] == "content_too_short"


# ===========================================================================
# SPEC-005 — NFR rule_origin Tagging + TESTS Edge Filtering
# ===========================================================================

class TestNfrOriginTagging:
    """word_parser._create_section_entity tags rule_origin correctly."""

    def _make_parser(self):
        parser = WordParser()
        parser._warnings = []
        return parser

    def test_nfr_section_tagged_nfr(self):
        parser = self._make_parser()
        entities = []
        content = (
            "The system must maintain 99.9% uptime and respond within 200ms for "
            "all search queries under normal load conditions as defined in SLA-PERF-001."
        )
        parser._create_section_entity(
            entities, "BRD.docx", "/path/BRD.docx",
            "Non-Functional Requirements (NFR)", content
        )
        assert len(entities) == 1
        assert entities[0]["metadata"]["rule_origin"] == "nfr"

    def test_security_heading_tagged_nfr(self):
        parser = self._make_parser()
        entities = []
        content = (
            "All data at rest must be encrypted using AES-256. TLS 1.2+ required for "
            "all API communication. Sessions expire after 15 minutes of inactivity."
        )
        parser._create_section_entity(
            entities, "BRD.docx", "/path/BRD.docx", "Security Requirements", content
        )
        assert entities[0]["metadata"]["rule_origin"] == "nfr"

    def test_functional_rule_tagged_functional(self):
        parser = self._make_parser()
        entities = []
        content = (
            "The application number must be between 8 and 15 alphanumeric characters. "
            "Special characters other than hyphens are not permitted."
        )
        parser._create_section_entity(
            entities, "BRD.docx", "/path/BRD.docx", "Field Validation Rules", content
        )
        assert entities[0]["metadata"]["rule_origin"] == "functional"


class TestNfrTestsEdgeFiltering:
    """HeuristicEdgeMapper must NOT emit TESTS edges to NFR-tagged business_rule nodes."""

    def _seed(self, tmp_path, nodes):
        store = GraphStore(str(tmp_path))
        for n in nodes:
            store.upsert_node(
                n["id"], n["type"], n.get("name", n["id"]),
                n.get("description", ""),
                n.get("metadata", {}),
            )
        return store

    def test_nfr_node_excluded_from_tests_scoring(self, tmp_path):
        nodes = [
            {
                "id": "scenario_otp_countdown_timer",
                "type": "test_scenario",
                "name": "Scenario: OTP countdown timer counts down from 30 to 0",
                "description": "OTP session lock minutes security countdown timer verification",
                "metadata": {},
            },
            {
                "id": "rule_nfr_table_1",
                "type": "business_rule",
                "name": "NFR Table 1",
                "description": "OTP session lock minutes security performance accessibility",
                "metadata": {"rule_origin": "nfr"},
            },
            {
                "id": "rule_otp_field_validation",
                "type": "business_rule",
                "name": "OTP Field Validation Rule",
                "description": "OTP countdown timer must expire within 30 seconds of generation",
                "metadata": {"rule_origin": "functional"},
            },
        ]
        store = self._seed(tmp_path, nodes)
        mapper = HeuristicEdgeMapper()
        edges = mapper.map(store)

        nfr_tests_edges = [
            e for e in edges
            if e["relationship"] == "TESTS" and e["target_id"] == "rule_nfr_table_1"
        ]
        assert nfr_tests_edges == [], (
            f"TESTS edge to NFR node should not be emitted: {nfr_tests_edges}"
        )

    def test_functional_rule_still_receives_tests_edge(self, tmp_path):
        # TF-IDF requires a 4-node corpus so shared tokens earn a non-zero IDF weight.
        # The scenario name must be a COMPLETE subset of the rule name tokens so the
        # binary cosine formula gives > 0.75 (same pattern as the existing mapper tests).
        nodes = [
            {
                "id": "scenario_otp_countdown",
                "type": "test_scenario",
                "name": "otp countdown timer",       # exact subset of rule below
                "description": "",
                "metadata": {},
            },
            {
                "id": "rule_otp_countdown",
                "type": "business_rule",
                "name": "otp countdown timer validation",   # superset — extra token only here
                "description": "",
                "metadata": {"rule_origin": "functional"},
            },
            # Padding nodes with entirely different vocabulary to boost IDF of shared tokens
            {
                "id": "br_unrelated",
                "type": "business_rule",
                "name": "endorsement eligibility window",
                "description": "",
                "metadata": {"rule_origin": "functional"},
            },
            {
                "id": "sc_unrelated",
                "type": "test_scenario",
                "name": "renewal reinstatement flow",
                "description": "",
                "metadata": {},
            },
        ]
        store = self._seed(tmp_path, nodes)
        mapper = HeuristicEdgeMapper()
        edges = mapper.map(store)

        functional_tests = [
            e for e in edges
            if e["relationship"] == "TESTS" and e["target_id"] == "rule_otp_countdown"
        ]
        assert len(functional_tests) > 0, (
            "Expected at least one TESTS edge to functional rule node"
        )

    def test_node_without_rule_origin_treated_as_functional(self, tmp_path):
        """Nodes lacking rule_origin metadata default to 'functional' (backward compat)."""
        nodes = [
            {
                "id": "scenario_dob_validation",
                "type": "test_scenario",
                "name": "birthdate dob validation",    # complete subset of rule below
                "description": "",
                "metadata": {},
            },
            {
                "id": "rule_dob_eligibility",
                "type": "business_rule",
                "name": "birthdate dob validation eligibility",   # superset — extra token
                "description": "",
                "metadata": {},   # no rule_origin key — must default to "functional"
            },
            # Padding nodes
            {
                "id": "br_unrelated",
                "type": "business_rule",
                "name": "endorsement reinstatement window",
                "description": "",
                "metadata": {},
            },
            {
                "id": "sc_unrelated",
                "type": "test_scenario",
                "name": "renewal surcharge calculation",
                "description": "",
                "metadata": {},
            },
        ]
        store = self._seed(tmp_path, nodes)
        mapper = HeuristicEdgeMapper()
        edges = mapper.map(store)

        tests_edges = [
            e for e in edges
            if e["relationship"] == "TESTS" and e["target_id"] == "rule_dob_eligibility"
        ]
        assert len(tests_edges) > 0, "Legacy nodes without rule_origin must still be scored"


# ===========================================================================
# SPEC-006 — Properties Config Node Extraction
# ===========================================================================

class TestPropertiesConfigExtraction:

    APP_PROPERTIES_CONTENT = """\
# Application runtime configuration
otp.timer.seconds=30
otp.max.resend.attempts=3
otp.session.lock.minutes=15
app.number.min.length=8
app.number.max.length=15
dob.min.age=18
dob.max.age=65
env.base.url=https://uat.example.com
env.browser=chrome
feature.otp.enabled=true
feature.dob.enabled=true
log.level=INFO
retry.count=2
"""

    def _parse(self, tmp_path, content, filename="application.properties"):
        f = tmp_path / filename
        f.write_text(content)
        shared = {"locator_cache": {}}
        parser = QafAutomationParser(shared_context=shared)
        result = parser.parse(str(f))
        return result, shared

    def test_config_node_created(self, tmp_path):
        result, _ = self._parse(tmp_path, self.APP_PROPERTIES_CONTENT)
        config_nodes = [e for e in result["entities"] if e["type"] == "data_model"]
        assert len(config_nodes) == 1, f"Expected 1 config node, got {len(config_nodes)}"

    def test_config_node_id(self, tmp_path):
        result, _ = self._parse(tmp_path, self.APP_PROPERTIES_CONTENT)
        ids = [e["id"] for e in result["entities"]]
        assert "config_application_properties" in ids

    def test_config_subtype(self, tmp_path):
        result, _ = self._parse(tmp_path, self.APP_PROPERTIES_CONTENT)
        node = next(e for e in result["entities"] if e.get("type") == "data_model")
        assert node["metadata"]["subtype"] == "config"

    def test_config_values_present(self, tmp_path):
        result, _ = self._parse(tmp_path, self.APP_PROPERTIES_CONTENT)
        node = next(e for e in result["entities"] if e.get("type") == "data_model")
        config = node["metadata"]["config"]
        assert config["otp.timer.seconds"] == "30"
        assert config["otp.max.resend.attempts"] == "3"
        assert config["otp.session.lock.minutes"] == "15"
        assert config["app.number.min.length"] == "8"
        assert config["app.number.max.length"] == "15"
        assert config["dob.min.age"] == "18"
        assert config["dob.max.age"] == "65"

    def test_shared_context_config_cache_populated(self, tmp_path):
        _, shared = self._parse(tmp_path, self.APP_PROPERTIES_CONTENT)
        assert "config_cache" in shared
        assert shared["config_cache"]["otp.timer.seconds"] == "30"

    def test_locator_file_produces_no_config_node(self, tmp_path):
        """A pure locator file should NOT produce a config data_model node."""
        locator_content = """\
FLD_APP_NO.locator=id:login-app-no-input
FLD_MOBILE.locator=id:login-mobile-input
"""
        result, _ = self._parse(tmp_path, locator_content, "locators.properties")
        config_nodes = [e for e in result["entities"] if e["type"] == "data_model"]
        assert config_nodes == [], "Pure locator file should not emit a config node"

    def test_locator_file_still_emits_pom_node(self, tmp_path):
        locator_content = """\
FLD_APP_NO.locator=id:login-app-no-input
FLD_MOBILE.locator=id:login-mobile-input
"""
        result, _ = self._parse(tmp_path, locator_content, "locators.properties")
        pom_nodes = [e for e in result["entities"] if e["type"] == "ui_page_object"]
        assert len(pom_nodes) == 1

    def test_mixed_file_emits_both_nodes(self, tmp_path):
        """A file with both locators and config entries emits both node types."""
        mixed_content = """\
# locator
BTN_SUBMIT.locator=id:submit-button
# config
timeout.seconds=10
"""
        result, _ = self._parse(tmp_path, mixed_content, "mixed.properties")
        pom_nodes = [e for e in result["entities"] if e["type"] == "ui_page_object"]
        config_nodes = [e for e in result["entities"] if e["type"] == "data_model"]
        assert len(pom_nodes) == 1
        assert len(config_nodes) == 1

    def test_comments_and_blank_lines_ignored(self, tmp_path):
        content = """\
# This is a comment
! Also a comment

valid.key=valid_value
"""
        result, _ = self._parse(tmp_path, content)
        config_nodes = [e for e in result["entities"] if e["type"] == "data_model"]
        assert len(config_nodes) == 1
        config = config_nodes[0]["metadata"]["config"]
        assert "valid.key" in config
        assert "#" not in str(config.keys())

    # -- Fix-B: config_cache always present in shared_context --

    def test_config_cache_present_for_locator_only_file(self, tmp_path):
        """config_cache key must exist even when no config entries are parsed."""
        content = "FLD_ONLY.locator=id:some-input\n"
        _, shared = self._parse(tmp_path, content, "locators.properties")
        assert "config_cache" in shared, "config_cache must be present for locator-only files"
        assert shared["config_cache"] == {}

    def test_config_cache_present_for_comment_only_file(self, tmp_path):
        """config_cache key must exist even for a comment-only .properties file."""
        _, shared = self._parse(tmp_path, "# just a comment\n! another comment\n")
        assert "config_cache" in shared, "config_cache must be present for comment-only files"
        assert shared["config_cache"] == {}

    def test_config_cache_merged_across_two_parse_calls(self, tmp_path):
        """Two sequential parses into the same shared_context accumulate both config maps."""
        f1 = tmp_path / "a.properties"
        f1.write_text("key.alpha=1\n")
        f2 = tmp_path / "b.properties"
        f2.write_text("key.beta=2\n")
        shared = {"locator_cache": {}}
        parser = QafAutomationParser(shared_context=shared)
        parser.parse(str(f1))
        parser.parse(str(f2))
        assert "config_cache" in shared
        assert shared["config_cache"]["key.alpha"] == "1"
        assert shared["config_cache"]["key.beta"] == "2"

    # -- Fix-C: duplicate key detection and warnings --

    def test_duplicate_config_key_emits_warning(self, tmp_path):
        """A duplicate config key must produce a structured warning in the result."""
        content = "key.one=first\nkey.one=second\n"
        result, _ = self._parse(tmp_path, content)
        dups = [w for w in result.get("warnings", []) if w.get("reason") == "duplicate_key"]
        assert len(dups) == 1, f"Expected 1 duplicate warning, got: {dups}"
        assert dups[0]["key"] == "key.one"
        assert dups[0]["type"] == "config"

    def test_duplicate_config_key_last_value_wins(self, tmp_path):
        """Last occurrence of a duplicate config key must win in the emitted node."""
        content = "key.one=first\nkey.one=second\n"
        result, _ = self._parse(tmp_path, content)
        node = next(e for e in result["entities"] if e["type"] == "data_model")
        assert node["metadata"]["config"]["key.one"] == "second"

    def test_duplicate_locator_key_emits_warning(self, tmp_path):
        """A duplicate locator key must produce a structured warning in the result."""
        content = "BTN.locator=id:btn-old\nBTN.locator=id:btn-new\n"
        result, shared = self._parse(tmp_path, content, "locators.properties")
        dups = [w for w in result.get("warnings", []) if w.get("reason") == "duplicate_key"]
        assert len(dups) == 1, f"Expected 1 duplicate warning, got: {dups}"
        assert dups[0]["key"] == "BTN"
        assert dups[0]["type"] == "locator"
        # Last value still wins in the locator cache
        assert shared["locator_cache"]["BTN"] == "id:btn-new"

    def test_no_warnings_for_clean_file(self, tmp_path):
        """A well-formed .properties file with no duplicates must emit no warnings."""
        result, _ = self._parse(tmp_path, self.APP_PROPERTIES_CONTENT)
        assert result.get("warnings", []) == []
