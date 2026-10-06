"""Only a page's FIRST banner pins under the frozen header.

Options has two banners -- the Trading banner, then its own "Options · Pick a
strategy" card. Pinned to the same top, the second slid over the first as the
page scrolled (Phil, 2026-10-06: "it merges and creates chaos").
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSS = (ROOT / "static" / "philforge-app.css").read_text(encoding="utf-8")
HTML = (ROOT / "strategy.html").read_text(encoding="utf-8")

BANNERS = ":is(.pf-workspace-hero, .oc-hero, .trading-workspace-head)"


class OneBannerPins(unittest.TestCase):
    def test_the_sticky_rule_excludes_every_banner_after_the_first(self):
        block = CSS.split("FROZEN TOPS, EVERY PAGE")[1]
        rule = block.split("position: sticky;\n    top: var(--pf-freeze-hero-top")[0]
        self.assertIn(f"{BANNERS}:not(", rule)
        self.assertIn(f"{BANNERS} ~ *", rule)

    def test_options_really_has_two_banners_the_trading_one_first(self):
        page = HTML.split('<div id="options-cascade-page"')[1].split('<div id="')[0]
        self.assertLess(page.index('class="trading-workspace-head"'), page.index('class="card-glass oc-hero"'))


if __name__ == "__main__":
    unittest.main()
