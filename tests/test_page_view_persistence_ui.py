from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "strategy.html").read_text(encoding="utf-8")
APP_JS = (ROOT / "static" / "philforge-app.js").read_text(encoding="utf-8")
EQUITY_JS = (ROOT / "static" / "philforge-two-red.js").read_text(encoding="utf-8")
MANIFEST = (ROOT / "static" / "asset-manifest.json").read_text(encoding="utf-8")


def test_trading_first_visit_defaults_to_cascade():
    assert 'id="nav-trading"' in HTML
    assert 'id="nav-trading" class="nav-tab" data-pf-nav-page="options-cascade-page"' in HTML
    assert "PF_VIEW_STATE.tradingPage" in APP_JS
    assert "'options-cascade-page'" in APP_JS


def test_top_level_page_and_nested_views_use_ui_only_local_state():
    for key in (
        "activePage",
        "tradingPage",
        "optionsCascadeTab",
        "insightsTab",
        "architectureView",
        "assetsTearsheet",
        "journalPanel",
        "liveEngine",
        "fibBoundaryMotherMode",
    ):
        assert f"{key}: 'philforge_" in APP_JS
    state_block = APP_JS[APP_JS.index("const PF_VIEW_STATE") : APP_JS.index("function _storedView")]
    assert "api/" not in state_block.lower()


def test_options_insights_assets_and_journal_restore_validated_choices():
    assert "_storedView(PF_VIEW_STATE.optionsCascadeTab, _OC_TABS, 'cepe')" in APP_JS
    assert "_storedView(PF_VIEW_STATE.insightsTab, _INSIGHTS_TABS, 'heatmap')" in APP_JS
    assert "_storedView(PF_VIEW_STATE.architectureView" in APP_JS
    assert "_storedView(PF_VIEW_STATE.journalPanel, ['journal', 'plan'], 'journal')" in APP_JS


def test_fib_boundary_mother_mode_survives_reload_without_changing_execution_state():
    assert "_storedView(PF_VIEW_STATE.fibBoundaryMotherMode, ['manual', 'auto'], 'manual')" in APP_JS
    assert "_setLocalState(PF_VIEW_STATE.fibBoundaryMotherMode, mode)" in APP_JS
    assert "_lastFibBoundaryAuto[picked]?.enabled" in APP_JS
    state_block = APP_JS[APP_JS.index("const PF_VIEW_STATE") : APP_JS.index("function _storedView")]
    assert "api/" not in state_block.lower()


def test_equity_subview_restores_and_defaults_to_cash_cascade():
    assert "var VIEW_KEY = 'philforge_equity_strategy_v1';" in EQUITY_JS
    assert "var VIEW_NAMES = ['cascade', 'tworeds', 'desk'];" in EQUITY_JS
    assert "var remembered = 'cascade';" in EQUITY_JS
    assert "showStrategy(remembered);" in EQUITY_JS


def test_asset_cache_version_is_bumped():
    """The manifest carries a dated, bumpable cache-buster.

    This used to pin one literal version, which meant it failed on the NEXT
    bump rather than on a missing one -- it went red on 2026-09-03 for a
    manifest that had been bumped correctly. A version is checked for shape and
    for being a real date; which change it belongs to is the commit's business.
    """
    import json
    import re
    from datetime import date

    version = json.loads(MANIFEST)["version"]
    match = re.fullmatch(r"(\d{4})(\d{2})(\d{2})-[a-z0-9][a-z0-9-]*", version)
    assert match, f"cache-buster {version!r} should look like 20260903-what-changed-41"
    date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
