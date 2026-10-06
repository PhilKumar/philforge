"""The blueprint freezes its header and search bar; every other page does not.

Phil, 2026-08-25, on the CryptoForge blueprint: "The scroll is going under the
search bar... I want everything above search bar including the search bar to be
freeze while scrolling down".

The reader's toolbar already pinned itself 112px down the viewport, as though a
frozen header stood there. The app header is not sticky, so that band was empty
and the document scrolled through it in plain sight, and the bar itself was
only 97% opaque, so text stayed legible straight through it.
"""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = Path(__file__).resolve().parent.parent
APP_JS = (ROOT / "static" / "philforge-app.js").read_text(encoding="utf-8")
APP_CSS = (ROOT / "static" / "philforge-app.css").read_text(encoding="utf-8")
DOC_CSS = (ROOT / "static" / "architecture-document.css").read_text(encoding="utf-8")
READER_JS = (ROOT / "static" / "philforge-architecture-page.js").read_text(encoding="utf-8")


class FrozenHeaderTests(unittest.TestCase):
    def test_the_header_is_sticky_only_under_the_blueprint_class(self):
        self.assertIn("body.pf-blueprint-open .header-shell {", APP_CSS)
        block = APP_CSS.split("body.pf-blueprint-open .header-shell {")[1].split("}")[0]
        self.assertIn("position: sticky", block)
        self.assertIn("top: 0", block)
        # Opaque: the document passes directly beneath it.
        self.assertIn("background: var(--bg)", block)

    def test_the_plain_header_is_still_not_sticky(self):
        """Every other page keeps its 156px of header scrolling away."""
        block = APP_CSS.split("  .header-shell {")[1].split("}")[0]
        self.assertIn("position: relative", block)

    def test_the_class_is_turned_on_for_the_blueprint_and_off_elsewhere(self):
        self.assertIn("document.body.classList.toggle('pf-blueprint-open'", APP_JS)
        self.assertIn("_setBlueprintFrozenHeader(isBlueprint);", APP_JS)
        # Leaving by any other route must release it -- both navigation paths,
        # which now also release the frozen tearsheet.
        release = "else {\n    _setBlueprintFrozenHeader(false);\n    _setTearsheetFrozen(false);\n  }"
        self.assertEqual(APP_JS.count(release), 2)

    def test_the_bar_is_pinned_to_the_measured_header_height(self):
        body = APP_JS.split("function _syncBlueprintStickyOffset()")[1].split("\n}")[0]
        self.assertIn("getBoundingClientRect()", body)
        self.assertIn("marginBottom", body)
        # Inline on the shell inside the shadow root, or the shadow stylesheet wins.
        self.assertIn("shadowRoot", body)
        self.assertIn("setProperty('--reader-sticky-top'", body)

    def test_the_toolbar_is_opaque_in_both_themes(self):
        """At .97 the document's text was still readable through the bar."""
        self.assertIn("background: #0c1320;", DOC_CSS)
        self.assertNotIn("background: rgba(12,19,32,.97)", DOC_CSS)
        self.assertIn('html[data-theme="light"] .reader-toolbar { background: #fafcfe; }', DOC_CSS)
        self.assertIn(
            ':host-context(html[data-theme="light"]) .reader-toolbar { background: #fafcfe; }',
            READER_JS,
        )


if __name__ == "__main__":
    unittest.main()
