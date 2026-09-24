"""Two things Phil found in the viewer login on 24-Sep-2026.

1. No closed paper campaigns on ANY strategy. The viewer read-allowlist carried
   "/api/paper/", but the ledgers live at "/api/paper-campaigns/...". A prefix
   match is literal, so the route fell through to the viewer's OWN account —
   which has no campaigns — and every console showed an empty list. The
   allowlist's own comment predicts exactly this: "a route added later shows
   them nothing instead of showing them too much." It failed safe, and it
   failed.

2. The entry/exit condition dropdowns opened "without CSS". They are native
   <select> elements: the list is drawn by the OS, and with no colour scheme
   declared the browser paints it in its default light panel while the options
   inherit the site's pale text. Not viewer-specific — he just noticed it there.
"""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import auth  # noqa: E402

CSS = (ROOT / "static" / "philforge-app.css").read_text(encoding="utf-8")


class AViewerCanReadTheClosedCampaigns(unittest.TestCase):
    STRATEGIES = ("gap-carry", "supertrend", "candle-entry", "fib-boundary", "recovery", "cascade")

    def test_every_strategys_ledger_is_answered_from_the_owners_account(self):
        for strategy in self.STRATEGIES:
            path = f"/api/paper-campaigns/{strategy}"
            with self.subTest(strategy=strategy):
                self.assertTrue(
                    auth.viewer_reads_owner_data("GET", path),
                    f"{path} falls back to the viewer's own empty account",
                )

    def test_the_chart_behind_a_closed_campaign_is_shared_too(self):
        path = "/api/paper-campaigns/recovery/NIFTY:5:CE:20260924T0915/chart"
        self.assertTrue(auth.viewer_reads_owner_data("GET", path))

    def test_deleting_a_campaign_is_still_refused(self):
        """Read-only means read-only. The method gate is what stops this."""
        path = "/api/paper-campaigns/gap-carry/anything"
        self.assertFalse(auth.viewer_may_call("DELETE", path))
        self.assertFalse(auth.viewer_reads_owner_data("DELETE", path))

    def test_the_shorter_prefix_was_never_enough(self):
        """Guards the actual mistake: assuming /api/paper/ covers these."""
        self.assertFalse("/api/paper-campaigns/".startswith("/api/paper/"))


class TheNativeDropdownFollowsTheSiteTheme(unittest.TestCase):
    def _rule(self, selector):
        m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", CSS)
        return " ".join(m.group(1).split()) if m else None

    def test_a_select_declares_a_colour_scheme(self):
        """Without this the OS paints the popup light whatever the page does."""
        self.assertIn("select { color-scheme: dark; }", CSS)

    def test_the_options_carry_their_own_colours(self):
        """For the platforms that honour page styling on option elements."""
        body = self._rule("select option,\n  select optgroup")
        self.assertIsNotNone(body, "option/optgroup must be styled")
        self.assertIn("background", body)
        self.assertIn("color", body)

    def test_light_theme_gets_a_light_popup(self):
        self.assertIn('html[data-theme="light"] select { color-scheme: light; }', CSS)

    def test_the_dark_option_colours_are_not_pale_on_pale(self):
        """The bug was light text on a white panel — assert real contrast."""
        body = self._rule("select option,\n  select optgroup")
        bg = re.search(r"background:\s*(#[0-9a-fA-F]{6})", body).group(1)
        fg = re.search(r"color:\s*(#[0-9a-fA-F]{6})", body).group(1)

        def luma(hex_colour):
            r, g, b = (int(hex_colour[i : i + 2], 16) / 255 for i in (1, 3, 5))
            return 0.2126 * r + 0.7152 * g + 0.0722 * b

        self.assertGreater(abs(luma(fg) - luma(bg)), 0.5, f"{fg} on {bg} is not readable")


if __name__ == "__main__":
    unittest.main()
