"""Tests for engine/ingestion/walker.py — IngestionWalker + DiscoveredFile.

Spec 003 (Wave 2) — new test file that pins current discovery + priority behaviour.
"""

import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.ingestion.walker import DiscoveredFile, IngestionWalker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_workspace(tmp_path, files: dict[str, str]) -> str:
    """Write *files* (relpath -> content) under *tmp_path* and return the root."""
    for relpath, content in files.items():
        abs_path = tmp_path / relpath
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(content, encoding="utf-8")
    return str(tmp_path)


# ---------------------------------------------------------------------------
# DiscoveredFile
# ---------------------------------------------------------------------------

def test_discovered_file_is_frozen():
    df = DiscoveredFile(
        abs_path="/a/b/c.java",
        rel_path="b/c.java",
        extension=".java",
        priority=3,
    )
    with pytest.raises((AttributeError, TypeError)):
        df.priority = 99  # type: ignore[misc]


def test_discovered_file_fields():
    df = DiscoveredFile(abs_path="/x/foo.feature", rel_path="foo.feature",
                        extension=".feature", priority=1)
    assert df.abs_path == "/x/foo.feature"
    assert df.rel_path == "foo.feature"
    assert df.extension == ".feature"
    assert df.priority == 1


# ---------------------------------------------------------------------------
# Priority ordering
# ---------------------------------------------------------------------------

class TestPriorityFor:
    """Unit-tests for the static _priority_for helper."""

    def test_locator_file_priority_0(self):
        assert IngestionWalker._priority_for(".properties", "src/locators.properties") == 0

    def test_loc_extension_priority_0(self):
        assert IngestionWalker._priority_for(".loc", "data/ui.loc") == 0

    def test_locator_in_name_priority_0(self):
        assert IngestionWalker._priority_for(".java", "pages/locatorsHelper.java") == 0

    def test_selector_in_name_priority_0(self):
        assert IngestionWalker._priority_for(".ts", "utils/selectorUtils.ts") == 0

    def test_feature_file_priority_1(self):
        assert IngestionWalker._priority_for(".feature", "features/login.feature") == 1

    def test_bdd_extension_priority_1(self):
        assert IngestionWalker._priority_for(".bdd", "specs/order.bdd") == 1

    def test_md_file_priority_1(self):
        assert IngestionWalker._priority_for(".md", "docs/requirements.md") == 1

    def test_page_in_name_priority_2(self):
        assert IngestionWalker._priority_for(".java", "pages/LoginPage.java") == 2

    def test_pom_in_name_priority_2(self):
        assert IngestionWalker._priority_for(".java", "pages/LoginPOM.java") == 2

    def test_standard_class_priority_3(self):
        assert IngestionWalker._priority_for(".java", "steps/LoginSteps.java") == 3

    def test_standard_ts_priority_3(self):
        assert IngestionWalker._priority_for(".ts", "utils/helpers.ts") == 3


# ---------------------------------------------------------------------------
# IngestionWalker.discover()
# ---------------------------------------------------------------------------

class TestIngestionWalkerDiscover:
    """Integration-style tests that create real temp file trees."""

    def test_discovers_supported_extensions(self, tmp_path):
        ws = make_workspace(tmp_path, {
            "features/login.feature": "",
            "steps/loginSteps.java": "",
            "docs/README.md": "",
        })
        walker = IngestionWalker(ws)
        discovered = list(walker.discover())
        extensions = {df.extension for df in discovered}
        assert ".feature" in extensions
        assert ".java" in extensions
        assert ".md" in extensions

    def test_prunes_excluded_dirs(self, tmp_path):
        ws = make_workspace(tmp_path, {
            "src/main/App.java": "",
            "node_modules/dep/index.js": "",
            ".git/config": "",
            "target/App.class": "",  # .class not in PARSER_MAP but just in case
        })
        walker = IngestionWalker(ws)
        discovered = list(walker.discover())
        paths = [df.rel_path for df in discovered]
        # node_modules, .git, target should all be pruned
        assert not any("node_modules" in p for p in paths)
        assert not any(".git" in p for p in paths)
        assert not any("target" in p for p in paths)

    def test_properties_files_included(self, tmp_path):
        ws = make_workspace(tmp_path, {
            "config/locators.properties": "btn.login=id=loginBtn",
        })
        walker = IngestionWalker(ws)
        discovered = list(walker.discover())
        assert any(df.extension == ".properties" for df in discovered)

    def test_priority_ordering_is_correct(self, tmp_path):
        """Locators (0) < features (1) < pages (2) < steps (3)."""
        ws = make_workspace(tmp_path, {
            "steps/LoginSteps.java": "",          # priority 3
            "pages/LoginPage.java": "",           # priority 2
            "features/login.feature": "",         # priority 1
            "config/locators.properties": "",     # priority 0
        })
        walker = IngestionWalker(ws)
        discovered = list(walker.discover())
        priorities = [df.priority for df in discovered]
        assert priorities == sorted(priorities), "Files must be in ascending priority order"

    def test_rel_path_uses_forward_slashes(self, tmp_path):
        ws = make_workspace(tmp_path, {"src/deep/nested/App.java": ""})
        walker = IngestionWalker(ws)
        discovered = list(walker.discover())
        for df in discovered:
            assert "\\" not in df.rel_path

    def test_extra_prune_dirs_are_respected(self, tmp_path):
        ws = make_workspace(tmp_path, {
            "src/App.java": "",
            "custom_vendor/lib.java": "",
        })
        walker = IngestionWalker(ws, extra_prune={"custom_vendor"})
        discovered = list(walker.discover())
        paths = [df.rel_path for df in discovered]
        assert not any("custom_vendor" in p for p in paths)

    def test_empty_workspace_returns_empty(self, tmp_path):
        walker = IngestionWalker(str(tmp_path))
        discovered = list(walker.discover())
        assert discovered == []

    def test_context_builder_dir_is_pruned(self, tmp_path):
        ws = make_workspace(tmp_path, {
            ".context_builder/graph.json": "{}",
            "src/App.java": "",
        })
        walker = IngestionWalker(ws)
        discovered = list(walker.discover())
        paths = [df.rel_path for df in discovered]
        assert not any(".context_builder" in p for p in paths)
