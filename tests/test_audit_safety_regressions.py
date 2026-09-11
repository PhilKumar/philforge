"""Offline regressions: no broker connection, app startup or live database."""

import ast
import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import pytest
from starlette.requests import Request

ROOT = Path(__file__).resolve().parents[1]


def functions(path, names, namespace):
    tree = ast.parse(path.read_text())
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
    assert len(nodes) == len(names)
    for node in nodes:
        node.decorator_list = []
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)
    return namespace


@pytest.fixture
def origins():
    return functions(
        ROOT / "app.py",
        {
            "_request_is_https",
            "_normalize_origin_value",
            "_allowed_request_origins",
            "_browser_origin_allowed",
        },
        {"Request": Request, "_urlparse": urlparse, "_CORS_ALLOWED_ORIGINS": ["https://philforge.in"]},
    )


def request(**headers):
    return Request(
        {
            "type": "http",
            "scheme": "https",
            "path": "/api/test",
            "server": ("testserver", 443),
            "headers": [
                (k.replace("_", "-").encode(), v.encode()) for k, v in {"host": "testserver", **headers}.items()
            ],
        }
    )


@pytest.mark.parametrize("origin", ["null", "", "https://[bad", "https://evil.example", "http://testserver"])
def test_explicit_bad_origin_fails_closed(origins, origin):
    assert not origins["_browser_origin_allowed"](request(origin=origin))


def test_forwarded_host_cannot_expand_csrf_allowlist(origins):
    assert not origins["_browser_origin_allowed"](
        request(origin="https://evil.example", x_forwarded_host="evil.example")
    )


def test_same_origin_and_headerless_api_clients_keep_working(origins):
    for headers in (
        {},
        {"origin": "https://testserver"},
        {"referer": "https://testserver/app"},
        {"origin": "https://philforge.in"},
    ):
        assert origins["_browser_origin_allowed"](request(**headers))
    assert not origins["_browser_origin_allowed"](request(origin="https://testserver", sec_fetch_site="cross-site"))


@pytest.mark.parametrize("origin", ["null", "", "https://[bad", "https://evil.example"])
def test_websocket_rejects_explicit_invalid_origin_before_authentication(origins, origin):
    class Socket:
        headers = {"origin": origin}
        closed = None

        async def close(self, **kwargs):
            self.closed = kwargs

    namespace = {**origins, "WebSocket": Socket}
    endpoint = functions(ROOT / "app.py", {"websocket_endpoint"}, namespace)["websocket_endpoint"]
    ws = Socket()
    asyncio.run(endpoint(ws))
    assert ws.closed == {"code": 4003, "reason": "Forbidden origin"}


@pytest.fixture
def analytics():
    return functions(
        ROOT / "app.py",
        {"_chart_session_analytics"},
        {"datetime": datetime, "IST": timezone(timedelta(hours=5, minutes=30))},
    )["_chart_session_analytics"]


def test_support_4_and_5_use_previous_session_without_lookahead(analytics):
    candles = [
        {"t": 1788758400, "o": 100, "h": 120, "l": 90, "c": 93},
        {"t": 1788844800, "o": 110, "h": 1000, "l": 1, "c": 700},
    ]
    levels = {x["label"]: x["price"] for x in analytics(candles)["lines"]}
    pivot = (120 + 90 + 93) / 3
    s3 = 90 - 2 * (120 - pivot)
    assert levels["S4"] == pytest.approx(s3 - 30, abs=0.0001)
    assert levels["S5"] == pytest.approx(s3 - 60, abs=0.0001)
    assert levels["CPR BC"] <= levels["CPR P"] <= levels["CPR TC"]
    candles[-1].update(h=3000, l=0, c=10)
    assert levels == {x["label"]: x["price"] for x in analytics(candles)["lines"]}


def test_no_previous_session_does_not_invent_supports(analytics):
    assert analytics([])["lines"] == []
    assert analytics([{"t": 1788758400, "o": 100, "h": 120, "l": 90, "c": 93}])["lines"] == []


def test_saved_decision_evidence_includes_deep_support_and_real_pivot_key():
    from engine.backtest import DECISION_WHY_INDICATOR_KEYS

    assert {"CPR_S4", "CPR_S5", "CPR_Pivot"} <= set(DECISION_WHY_INDICATOR_KEYS)


def test_legacy_rebuilder_cannot_replace_current_ladder_report(tmp_path):
    guard = functions(ROOT / "tools/tearsheet/rebuild_data.py", {"_guard_current_report"}, {"json": json})[
        "_guard_current_report"
    ]
    report = tmp_path / "report.json"
    original = json.dumps({"deployed": {"basis": "ladder"}, "live_config": {"lots": 2}})
    report.write_text(original)
    with pytest.raises(SystemExit, match="Refusing to overwrite"):
        guard(report)
    assert report.read_text() == original


def test_report_dates_totals_and_generated_landing_agree():
    from tools.tearsheet import update_landing

    data = json.loads(update_landing.DATA.read_text())
    head = data["headline"]["combined"]
    assert data["window"]["to"] >= head["last"]
    assert sum(row[3] for row in data["series"]) == head["trades"]
    assert len(data["series"]) == data["daily"]["trading_days"]
    assert data["series"][-1][2] == pytest.approx(head["net"], abs=1)
    for rows in (data["by_month"], data["by_dow"], data["by_year"]["combined"]):
        assert sum(row["net"] for row in rows.values()) == pytest.approx(head["net"], abs=0.02)
        assert sum(row["n"] for row in rows.values()) == head["trades"]
    for row in data["sizing"]:
        assert row["roi"] == round(100 * row["net"] / row["funded"])
    for name, text in update_landing.render(data).items():
        assert (update_landing._LANDING / name).read_text() == text
    assert "%/yr" not in update_landing.render(data)["forge.html"]
    report = (ROOT / "docs/assets/backtest-tearsheet-5yr.html").read_text()
    assert "not annual returns or CAGR" in report
    assert "Live today is 4 lots" not in report
