"""The CE + PE desk: two live books on one tab.

Phil, 2026-09-08: "I need these CE and PE as a page like others ... just give
the basic controls like exit chart etc etc" — and, asked whether it should be
one tab or two, "Yes that is better" to a single tab holding both books.

They are not cascade strategies. Both run on engine/live.py as strategy-builder
deployments, so this tab is a VIEW over runs that already exist and every action
goes to the live routes the single-run page already uses. The one new endpoint
is /api/live/runs, because /api/live/status answers for the FIRST running engine
and a two-book desk needs both.

The traps this file pins are the ones this repo has paid for before: a button
whose action is missing from PF_DELEGATED_ACTIONS renders and does nothing at
all, a duplicate element id fails silently, and a status payload that ships its
event log on every poll is how cascade/status came to send 3.75MB every three
seconds.
"""

import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML = open(os.path.join(ROOT, "strategy.html"), encoding="utf-8").read()
APP_JS = open(os.path.join(ROOT, "static", "philforge-app.js"), encoding="utf-8").read()
CSS = open(os.path.join(ROOT, "static", "philforge-app.css"), encoding="utf-8").read()
APP_PY = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()

IDS = (
    "oc-tab-cepe",
    "oc-tabbtn-cepe",
    "oc-cepe-badge",
    "oc-cepe-recipe",
    "oc-cepe-tiles",
    "oc-cepe-books",
    "oc-cepe-closed-rows",
    "oc-cepe-closed-count",
    "oc-cepe-info",
    "oc-cepe-monitor-updated",
)


class TheTabExistsAndIsWired(unittest.TestCase):
    def test_every_id_is_present_exactly_once(self):
        for element_id in IDS:
            self.assertEqual(HTML.count(f'id="{element_id}"'), 1, f"{element_id} is missing or duplicated")

    def test_the_tab_is_registered_so_the_panel_can_be_shown(self):
        self.assertIn("'cepe'", APP_JS.split("const _OC_TABS")[1].split("]")[0])
        self.assertIn("else if (tab === 'cepe') refreshCePeStatus();", APP_JS)

    def test_the_button_points_at_the_panel(self):
        self.assertIn('data-oc-tab="cepe"', HTML)
        self.assertIn('aria-controls="oc-tab-cepe"', HTML)

    def test_every_action_is_delegated_and_defined(self):
        """A data-pf-action missing from the allowlist is a button that does nothing."""
        allow = APP_JS[APP_JS.index("const PF_DELEGATED_ACTIONS") :]
        allow = allow[: allow.index("]")]
        for action in ("cepeStop", "cepeExit", "setCePeFilter", "openCePeTearsheet"):
            with self.subTest(action=action):
                self.assertIn(f"'{action}'", allow, f"{action} is not delegated")
                self.assertIn(f"window.{action} = {action};", APP_JS, f"{action} is not on window")

    def test_every_scrollable_table_on_this_tab_is_keyboard_reachable(self):
        """axe: scrollable-region-focusable, serious. The ten-column ledger and
        the holdings table both overflow, and this rule already refused one push
        on the tearsheet's wider table."""
        panel = HTML[HTML.index('id="oc-tab-cepe"') :]
        panel = panel[: panel.index('id="oc-tab-gapcarry"')] if 'id="oc-tab-gapcarry"' in panel else panel
        for wrap in re.findall(r'<div class="ocp-table-wrap"([^>]*)>', panel):
            self.assertIn("tabindex", wrap, "a scrollable table on this tab cannot be reached by keyboard")
        card = APP_JS.split("function _cepeBookCard(run)")[1].split("\nfunction renderCePe")[0]
        wrap = re.search(r'<div class="ocp-table-wrap"([^`]*?)>', card)
        self.assertIsNotNone(wrap)
        self.assertIn("tabindex", wrap.group(1))

    def test_the_new_panel_is_in_the_e2e_accessibility_sweep(self):
        e2e = open(os.path.join(ROOT, "e2e-tests", "tests", "01-smoke.spec.ts"), encoding="utf-8").read()
        self.assertIn("['#oc-tabbtn-cepe', '#oc-tab-cepe']", e2e)
        self.assertIn("await expect(page.locator('#oc-tabbtn-cepe')).toHaveAttribute('aria-selected', 'true')", e2e)

    def test_there_is_no_start_button_on_this_desk(self):
        """A Start that only explained itself in a toast was a button that did
        nothing; deploying a run belongs to the strategy builder."""
        self.assertNotIn("cepeStart", APP_JS)
        card = APP_JS.split("function _cepeBookCard(run)")[1].split("\nfunction renderCePe")[0]
        self.assertNotIn("▶ Start", card)
        self.assertNotIn('data-pf-action="cepeStart"', card)

    def test_the_desk_has_one_index_chart_not_one_per_book(self):
        """Both books decide off the same NIFTY chart (Phil, 2026-09-09:
        "Put a live nifty chart button common in this page")."""
        for tf in ("5m", "15m", "1h"):
            self.assertIn(f'data-cepe-tf="{tf}"', HTML, tf)
        self.assertEqual(HTML.count('data-pf-action="openCePeIndexChart"'), 3)
        allow = APP_JS[APP_JS.index("const PF_DELEGATED_ACTIONS") :]
        self.assertIn("'openCePeIndexChart'", allow[: allow.index("]")])
        self.assertIn("window.openCePeIndexChart = openCePeIndexChart;", APP_JS)
        card = APP_JS.split("function _cepeBookCard(run)")[1].split("\nfunction renderCePe")[0]
        self.assertNotIn("openCePeIndexChart", card, "the chart belongs to the page, not each card")

    def test_the_index_chart_reuses_the_one_renderer(self):
        """One chart vocabulary on this page: same overlay, same drawer, same
        server-side analytics as every other entry chart."""
        fn = APP_JS.split("async function openCePeIndexChart(")[1].split("\nfunction _startLiveEntryChartPolling")[0]
        self.assertIn("pfBenchDrawChart", fn)
        self.assertIn("_ensureLiveEntryChartOverlay", fn)
        self.assertIn("/api/live/index-chart", fn)
        route = APP_PY.split('@app.get("/api/live/index-chart")')[1].split("@app.get")[0]
        self.assertIn("_chart_session_analytics", route)
        self.assertIn('"overlays"', route)

    def test_the_index_chart_stops_polling_when_it_is_closed(self):
        fn = APP_JS.split("async function openCePeIndexChart(")[1].split("\nfunction _startLiveEntryChartPolling")[0]
        self.assertIn("clearInterval", fn)
        self.assertIn("is-open", fn)

    def test_live_and_paper_are_separate_pages(self):
        """Phil, 2026-09-09: "I want paper and live separated by 2 pages inside
        the CE PE strategy.. not on the same" — a switch, not two stacked
        sections. Which page a book lands on is decided by real_orders, never
        by its name."""
        self.assertIn("const _CEPE_PAGES", APP_JS)
        block = APP_JS.split("const _CEPE_PAGES")[1][:400]
        self.assertIn("'live'", block)
        self.assertIn("'paper'", block)
        self.assertIn("r.real_orders", block)
        self.assertNotIn("_CEPE_SECTIONS", APP_JS, "the stacked-section model is gone")

    def test_only_one_page_is_shown_at_a_time(self):
        """The whole point: standing on Live must not render a paper book."""
        body = APP_JS.split("function renderCePe(data)")[1].split("\nasync function refreshCePeStatus")[0]
        self.assertIn("const all = everything.filter(page.test)", body)
        self.assertIn("books.innerHTML = runs.map(_cepeBookCard)", body)
        for stale in ("cepe-section-head", "cepe-section-live", "cepe-section-paper"):
            self.assertNotIn(stale, CSS, f"{stale} belonged to the stacked model")

    def test_the_switch_is_a_registered_action(self):
        """An unregistered action renders and then does nothing when clicked."""
        self.assertIn("'setCePeMode'", APP_JS)
        self.assertIn("function setCePeMode(", APP_JS)
        self.assertIn('data-pf-action="setCePeMode"', HTML)
        self.assertIn('data-cepe-mode="live"', HTML)
        self.assertIn('data-cepe-mode="paper"', HTML)

    def test_the_totals_belong_to_the_page_not_the_desk(self):
        """data.day_total covers every book, so it would show live money on the
        paper page. The tiles must sum the books actually on screen."""
        body = APP_JS.split("function renderCePe(data)")[1].split("\nasync function refreshCePeStatus")[0]
        self.assertIn("const dayTotal = runs.reduce(", body)
        self.assertIn("const bookedTotal = runs.reduce(", body)
        tiles = body.split("tiles.innerHTML = [")[1][:400]
        self.assertNotIn("data.day_total", tiles)
        self.assertNotIn("data.booked_total", tiles)

    def test_the_switch_sits_above_the_numbers_it_governs(self):
        """Reading order: pick the page, then read that page's totals."""
        self.assertLess(HTML.index('class="cepe-pages"'), HTML.index('id="oc-cepe-tiles"'))

    def test_an_empty_live_page_does_not_strand_a_paper_only_desk(self):
        body = APP_JS.split("function renderCePe(data)")[1].split("\nasync function refreshCePeStatus")[0]
        self.assertIn("if (!_cepeModePinned)", body)
        self.assertIn("_CEPE_PAGES.find(pg => everything.some(pg.test))", body)

    def test_the_books_have_a_layout(self):
        self.assertIn(".oc-cepe-books", CSS)
        self.assertIn("grid-template-columns", CSS.split(".oc-cepe-books")[1][:300])


class TheDeskShowsBothBooks(unittest.TestCase):
    def test_the_endpoint_answers_for_every_run_not_the_first(self):
        self.assertIn('@app.get("/api/live/runs")', APP_PY)
        body = APP_PY.split('@app.get("/api/live/runs")')[1].split("@app.get")[0]
        self.assertIn("for run_id, engine, is_live_registry in pairs", body)

    def test_it_reads_both_registries(self):
        """A paper deployment of the same strategy lands in paper_engines; a desk
        that reads only live_engines showed two books when five were loaded."""
        body = APP_PY.split('@app.get("/api/live/runs")')[1].split("@app.get")[0]
        self.assertIn("_registry_bucket(live_engines, user_id)", body)
        self.assertIn("_registry_bucket(paper_engines, user_id)", body)

    def test_real_orders_is_decided_by_the_deploy_config_not_by_mode(self):
        """engine.mode is the string "auto" on EVERY live engine and never
        changes. Comparing it to "live" painted a book placing real orders as
        PAPER, which is the most dangerous label this page can get wrong."""
        body = APP_PY.split('@app.get("/api/live/runs")')[1].split("@app.get")[0]
        self.assertIn('"real_orders"', body)
        self.assertIn("order_type", body)
        card = APP_JS.split("function _cepeBookCard(run)")[1].split("\nfunction renderCePe")[0]
        self.assertIn("run.real_orders", card)
        self.assertNotIn("=== 'live'", card, "the badge must not compare mode to 'live'")
        # The badge reads "LIVE" in the danger colour; the words stay on its hover.
        self.assertIn("REAL ORDERS", card, "nothing on the card says the orders are real")
        self.assertIn("badgeTone", card)


class TheTwoBooksAreToldApart(unittest.TestCase):
    def test_each_side_has_its_own_colour(self):
        self.assertIn("const _CEPE_SIDE", APP_JS)
        block = APP_JS.split("const _CEPE_SIDE")[1][:300]
        self.assertIn("CE:", block)
        self.assertIn("PE:", block)

    def test_the_filter_exists_and_is_delegated(self):
        for key in ("all", "CE", "PE"):
            self.assertIn(f'data-cepe-filter="{key}"', HTML)
        allow = APP_JS[APP_JS.index("const PF_DELEGATED_ACTIONS") :]
        self.assertIn("'setCePeFilter'", allow[: allow.index("]")])
        self.assertIn("window.setCePeFilter = setCePeFilter;", APP_JS)

    def test_the_filter_reaches_the_ledger_as_well_as_the_books(self):
        body = APP_JS.split("function renderCePe(data)")[1].split("\nasync function refreshCePeStatus")[0]
        self.assertIn("_cepeFilter", body)
        self.assertIn("cepe-tattoo", body, "ledger rows must carry the side mark")

    def test_the_tattoo_and_filter_are_styled(self):
        for cls in (".cepe-tattoo", ".cepe-filter", ".cepe-figs"):
            self.assertIn(cls, CSS, cls)

    def test_the_recipe_is_one_short_line(self):
        """It went from one clipped sentence, to three lines, to one short line
        (Phil, 2026-09-09: "Put all in one row with simple texts")."""
        body = APP_JS.split("function _cepeRecipe()")[1].split("\n}")[0]
        self.assertNotIn("cepe-recipe-line", body, "the three-line version is back")
        for word in ("CE", "PE", "one trade a day"):
            self.assertIn(word, body, word)

    def test_the_poll_payload_leaves_out_the_heavy_fields(self):
        """A console polls this every few seconds."""
        body = APP_PY.split('@app.get("/api/live/runs")')[1].split("@app.get")[0]
        self.assertNotIn('"event_log": engine.event_log', body)
        self.assertIn("closed[-25:]", body, "the whole closed book must not ship on every poll")

    def test_one_broken_engine_cannot_blank_the_desk(self):
        body = APP_PY.split('@app.get("/api/live/runs")')[1].split("@app.get")[0]
        self.assertIn("except Exception as exc", body)

    def test_the_exit_button_speaks_the_route_s_own_language(self):
        """/api/live/exit-position takes position_index, not leg_num."""
        body = APP_JS.split("async function cepeExit(")[1].split("\n}")[0]
        self.assertIn("position_index", body)
        self.assertNotIn("leg_num", body)
        self.assertIn("confirm(", body, "selling at market must be confirmed")

    def test_the_chart_button_calls_the_journal_the_way_the_live_page_does(self):
        body = APP_JS.split("function renderCePe(data)")[1].split("\nasync function refreshCePeStatus")[0]
        self.assertIn("openLiveTradeJournal(", body)

    def test_the_desk_says_when_a_leg_has_no_broker_stop(self):
        body = APP_JS.split("function _cepeBookCard(run)")[1].split("\nfunction renderCePe")[0]
        self.assertIn("no stop", body)
        self.assertIn("No broker stop for this leg", body)

    def test_the_expiry_day_size_is_shown_where_it_is_set(self):
        body = APP_JS.split("function _cepeBookCard(run)")[1].split("\nfunction renderCePe")[0]
        self.assertIn("expiry_day_lots", body)

    def test_polling_stops_when_the_tab_is_hidden(self):
        body = APP_JS.split("async function refreshCePeStatus()")[1].split("\n}")[0]
        self.assertIn("style.display !== 'none'", body)


class TheDocumentationIsHonest(unittest.TestCase):
    def test_the_info_panel_carries_both_languages(self):
        doc = HTML.split('id="oc-cepe-info"')[1].split("</div>\n        </div>")[0]
        self.assertIn('data-pf-lang="en"', doc)
        self.assertIn('data-pf-lang="ta"', doc)

    def test_it_states_the_risk_and_not_only_the_return(self):
        doc = HTML.split('id="oc-cepe-info"')[1][:14000]
        self.assertIn("pf-info-warn", doc)
        for fact in ("not a guaranteed performance floor", "bias a backtest in either direction", "future losses"):
            self.assertIn(fact, doc, f"the warning omits {fact}")
        self.assertNotIn("these figures understate rather than flatter", doc)


if __name__ == "__main__":
    unittest.main()
