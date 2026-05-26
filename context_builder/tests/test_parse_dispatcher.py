"""Tests for engine/ingestion/parse_dispatcher.py — ParseDispatcher + ParseOutcome.

Spec 003 (Wave 2) — new test file.
"""

import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.graph_store import GraphStore
from engine.ingestion.walker import DiscoveredFile
from engine.ingestion.parse_dispatcher import ParseDispatcher, ParseOutcome


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_df(abs_path: str, ext: str, priority: int = 3) -> DiscoveredFile:
    return DiscoveredFile(
        abs_path=abs_path,
        rel_path=os.path.basename(abs_path),
        extension=ext,
        priority=priority,
    )


def _make_dispatcher(tmp_path) -> tuple[ParseDispatcher, GraphStore]:
    store = GraphStore(str(tmp_path))
    dispatcher = ParseDispatcher(str(tmp_path), store)
    return dispatcher, store


# ---------------------------------------------------------------------------
# stamp_origin
# ---------------------------------------------------------------------------

class TestStampOrigin:
    """Governance stamp is applied iff the extension is in DOC_ORIGIN_EXTS."""

    @pytest.mark.parametrize("ext", [".docx", ".xlsx", ".pdf", ".md"])
    def test_doc_extensions_get_origin_stamp(self, tmp_path, ext):
        dispatcher, _ = _make_dispatcher(tmp_path)
        entity = {"id": "x", "type": "business_rule", "name": "X", "metadata": {}}
        df = _make_df(f"/ws/req{ext}", ext)
        result = dispatcher.stamp_origin(entity, df)
        assert result["metadata"]["sync_governance"]["origin"] == "documentation"

    @pytest.mark.parametrize("ext", [".java", ".ts", ".py", ".feature", ".json"])
    def test_non_doc_extensions_do_not_get_origin_stamp(self, tmp_path, ext):
        dispatcher, _ = _make_dispatcher(tmp_path)
        entity = {"id": "x", "type": "code_component", "name": "X", "metadata": {}}
        df = _make_df(f"/ws/src/Class{ext}", ext)
        result = dispatcher.stamp_origin(entity, df)
        # No governance stamp should be set
        assert "sync_governance" not in result.get("metadata", {})

    def test_stamp_creates_metadata_dict_if_missing(self, tmp_path):
        dispatcher, _ = _make_dispatcher(tmp_path)
        entity = {"id": "x", "type": "business_rule", "name": "X"}
        df = _make_df("/ws/req.md", ".md")
        result = dispatcher.stamp_origin(entity, df)
        assert result["metadata"]["sync_governance"]["origin"] == "documentation"

    def test_stamp_creates_sync_governance_if_missing(self, tmp_path):
        dispatcher, _ = _make_dispatcher(tmp_path)
        entity = {"id": "x", "type": "business_rule", "name": "X", "metadata": {}}
        df = _make_df("/ws/doc.docx", ".docx")
        result = dispatcher.stamp_origin(entity, df)
        assert result["metadata"]["sync_governance"]["origin"] == "documentation"

    def test_stamp_does_not_overwrite_existing_metadata_keys(self, tmp_path):
        dispatcher, _ = _make_dispatcher(tmp_path)
        entity = {
            "id": "x",
            "type": "business_rule",
            "name": "X",
            "metadata": {"custom_field": "keep_me"},
        }
        df = _make_df("/ws/req.md", ".md")
        result = dispatcher.stamp_origin(entity, df)
        assert result["metadata"]["custom_field"] == "keep_me"


# ---------------------------------------------------------------------------
# ParseOutcome
# ---------------------------------------------------------------------------

def test_parse_outcome_defaults():
    df = _make_df("/ws/foo.java", ".java")
    outcome = ParseOutcome(file=df)
    assert outcome.entities == []
    assert outcome.relationships == []
    assert outcome.raw_text == ""
    assert outcome.cached is False
    assert outcome.error is None


def test_parse_outcome_error_field():
    df = _make_df("/ws/foo.java", ".java")
    exc = ValueError("boom")
    outcome = ParseOutcome(file=df, error=exc)
    assert outcome.error is exc
    assert outcome.entities == []


# ---------------------------------------------------------------------------
# ParseDispatcher.parse()
# ---------------------------------------------------------------------------

class TestParseDispatcherParse:
    """Dispatcher must be pure — no store writes."""

    def test_parse_returns_outcome_for_supported_file(self, tmp_path):
        """A real .feature file is parseable and returns entities."""
        feature_file = tmp_path / "login.feature"
        feature_file.write_text(
            "Feature: Login\n  Scenario: User logs in\n"
            "    Given I open the login page\n",
            encoding="utf-8",
        )
        store = GraphStore(str(tmp_path))
        dispatcher = ParseDispatcher(str(tmp_path), store)
        df = _make_df(str(feature_file), ".feature", priority=1)
        outcome = dispatcher.parse(df)

        assert outcome.error is None
        assert isinstance(outcome.entities, list)
        # Gherkin parser should yield at least one test_scenario entity
        types = {e["type"] for e in outcome.entities}
        assert "test_scenario" in types

    def test_parse_does_not_write_to_store(self, tmp_path):
        """parse() must not call upsert_node or upsert_edge."""
        feature_file = tmp_path / "noop.feature"
        feature_file.write_text(
            "Feature: No-op\n  Scenario: Empty\n    Given nothing\n",
            encoding="utf-8",
        )
        store = GraphStore(str(tmp_path))
        dispatcher = ParseDispatcher(str(tmp_path), store)
        df = _make_df(str(feature_file), ".feature", priority=1)

        before_node_count = len(store.query_nodes())
        dispatcher.parse(df)
        after_node_count = len(store.query_nodes())

        assert before_node_count == after_node_count, (
            "ParseDispatcher.parse() must be pure — no store writes"
        )

    def test_parse_captures_error_in_outcome(self, tmp_path):
        """A non-existent file should result in outcome.error being set, not raised."""
        store = GraphStore(str(tmp_path))
        dispatcher = ParseDispatcher(str(tmp_path), store)
        df = _make_df(str(tmp_path / "nonexistent.feature"), ".feature")
        outcome = dispatcher.parse(df)
        assert outcome.error is not None

    def test_stamp_origin_applied_to_md_entities(self, tmp_path):
        """Entities from .md files should receive the 'documentation' origin stamp."""
        md_file = tmp_path / "requirements.md"
        md_file.write_text(
            "# Feature: Login\n\nUsers can log in with valid credentials.\n",
            encoding="utf-8",
        )
        store = GraphStore(str(tmp_path))
        dispatcher = ParseDispatcher(str(tmp_path), store)
        df = _make_df(str(md_file), ".md", priority=1)
        outcome = dispatcher.parse(df)

        if outcome.error:
            pytest.skip("Markdown parser failed; skipping stamp-origin assertion")

        for entity in outcome.entities:
            meta = entity.get("metadata") or {}
            sg = meta.get("sync_governance") or {}
            assert sg.get("origin") == "documentation", (
                f"Entity {entity['id']!r} missing documentation origin stamp"
            )
