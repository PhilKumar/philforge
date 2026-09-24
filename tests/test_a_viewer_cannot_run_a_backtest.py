"""A read-only account must not be able to start a backtest.

Phil reported 24-Sep-2026 that the viewer login he uses to show the site around
appeared to have backtest access, and worried it could wedge the box. He was
right about what he saw and the risk was real in kind: a 1m backtest has frozen
the site for 35 minutes before now (proj_philforge_backtest_froze_site).

The server was never actually open — every backtest STARTS with a POST, and the
viewer gate refuses all unsafe methods. What was missing was on the page: not
one control in strategy.html carried a read-only marker, so a viewer saw live
Backtest buttons that would fail with a 403 when pressed.

Both ends are pinned here: the server keeps refusing, and the page stops
offering.
"""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import auth  # noqa: E402

PAGE = (ROOT / "strategy.html").read_text(encoding="utf-8")
APP = (ROOT / "app.py").read_text(encoding="utf-8")

# Every route that STARTS work, as opposed to reading a stored result.
BACKTEST_STARTS = (
    "/api/backtest",
    "/api/backtest/jobs",
    "/api/cascade/backtest",
    "/api/candle-entry/backtest",
    "/api/gap-carry/backtest",
    "/api/supertrend/backtest",
    "/api/fib-boundary/backtest",
    "/api/recovery/backtest",
)


class TheServerRefusesEveryBacktestStart(unittest.TestCase):
    def test_no_viewer_may_post_to_any_of_them(self):
        for path in BACKTEST_STARTS:
            with self.subTest(path=path):
                self.assertFalse(auth.viewer_may_call("POST", path))

    def test_none_of_them_is_on_the_write_allowlist(self):
        for path in auth.VIEWER_WRITE_ALLOWLIST:
            self.assertNotIn("backtest", path.lower())

    def test_every_backtest_start_really_is_a_post(self):
        """The gate is by METHOD, so a GET that starts work would walk straight through.

        /api/backtest/jobs is the one path with both: POST starts a replay, GET
        reports an in-flight one so a reloaded page can reattach. That GET is
        covered by its own test below rather than banned here.
        """
        for path in BACKTEST_STARTS:
            if path == "/api/backtest/jobs":
                continue
            with self.subTest(path=path):
                has_get = re.search(r'@app\.get\("' + re.escape(path) + r'"\)', APP) is not None
                self.assertFalse(has_get, f"{path} is reachable by GET, which viewers are allowed")

    def test_the_one_shared_path_only_READS_on_get(self):
        body = APP.split('@app.get("/api/backtest/jobs")', 1)[1].split("@app.")[0]
        for starter in ("_run_backtest", "create_task", "BackgroundTask", "executor"):
            with self.subTest(starter=starter):
                self.assertNotIn(starter, body, "the GET must not kick off work")

    def test_reading_a_finished_backtest_is_still_allowed(self):
        """A viewer is meant to SEE results; they just cannot make new ones."""
        for path in (
            "/api/gap-carry/backtests/latest",
            "/api/supertrend/backtests/latest/chart",
            "/api/fib-boundary/backtests",
        ):
            with self.subTest(path=path):
                self.assertTrue(auth.viewer_may_call("GET", path))


class ThePageStopsOfferingWhatCannotWork(unittest.TestCase):
    def _buttons_for(self, pattern):
        return [b for b in re.findall(r"<button\b[^>]*>", PAGE) if re.search(pattern, b)]

    def test_every_run_backtest_button_is_locked(self):
        buttons = self._buttons_for(r'data-pf-action="run[A-Za-z]*Backtest"|onclick="runBacktest\(\)"')
        self.assertGreaterEqual(len(buttons), 6, "expected a Backtest button per strategy")
        for b in buttons:
            with self.subTest(button=b[:80]):
                self.assertIn("read-only-lock", b)

    def test_every_delete_backtest_button_is_locked(self):
        buttons = self._buttons_for(r'data-pf-action="delete[A-Za-z]*Backtest"')
        self.assertGreaterEqual(len(buttons), 5)
        for b in buttons:
            with self.subTest(button=b[:80]):
                self.assertIn("read-only-lock", b)

    def test_the_chart_toggles_are_NOT_locked(self):
        """Viewing a chart is a read. Locking it would hide results a viewer may see."""
        for b in self._buttons_for(r'data-pf-action="toggle[A-Za-z]*BacktestChart"'):
            with self.subTest(button=b[:80]):
                self.assertNotIn("read-only-lock", b)

    def test_the_lock_class_actually_does_something(self):
        css = (ROOT / "static" / "philforge-app.css").read_text(encoding="utf-8")
        self.assertIn("read-only-account", css)
        rule = re.search(r"html\.read-only-account\s+\.read-only-lock\s*\{([^}]*)\}", css)
        self.assertIsNotNone(rule, "the lock class must have a rule")
        self.assertIn("pointer-events: none", rule.group(1))

    def test_the_page_only_locks_when_the_account_is_read_only(self):
        """The class goes on <html> from the session, so ordinary users are untouched."""
        js = (ROOT / "static" / "philforge-app.js").read_text(encoding="utf-8")
        self.assertIn("'read-only-account', data.role === 'viewer'", js)


class EveryWriteControlOnThePageIsLocked(unittest.TestCase):
    """The rule, not just the backtest buttons.

    Classified by what the handler actually DOES — a fetch with a POST, PUT,
    DELETE or PATCH — rather than by what the button is called, because a name
    is not a permission.
    """

    SELF_SERVICE = {
        # A viewer keeps control of their OWN login. These match
        # auth.VIEWER_WRITE_ALLOWLIST, so the server accepts them too.
        "logoutUser",
        "changeOwnPasswordFromSettings",
        "disableMfa",
        "startMfaEnrollment",
        "verifyMfaEnrollment",
        "registerPasskey",
    }

    @classmethod
    def setUpClass(cls):
        cls.tags = re.findall(r"<(?:button|a|input|select)\b[^>]*>", PAGE)
        cls.js = "\n".join(
            (ROOT / "static" / name).read_text(encoding="utf-8")
            for name in ("philforge-app.js", "philforge-two-red.js")
        )

    def _handler_body(self, name):
        for pat in (
            rf"\basync\s+function\s+{re.escape(name)}\s*\(",
            rf"\bfunction\s+{re.escape(name)}\s*\(",
            rf"\b{re.escape(name)}\s*[:=]\s*async\s+function",
            rf"\b{re.escape(name)}\s*[:=]\s*async\s*\(",
        ):
            m = re.search(pat, self.js)
            if not m:
                continue
            i = self.js.index("{", m.end() - 1)
            depth = 0
            for j in range(i, len(self.js)):
                if self.js[j] == "{":
                    depth += 1
                elif self.js[j] == "}":
                    depth -= 1
                    if depth == 0:
                        return self.js[i:j]
        return None

    def test_a_control_that_writes_is_locked(self):
        checked = 0
        for tag in self.tags:
            names = {a or b for a, b in re.findall(r'data-pf-action="([A-Za-z0-9_]+)"|onclick="([A-Za-z0-9_]+)\(', tag)}
            for name in names - self.SELF_SERVICE:
                body = self._handler_body(name)
                if not body or not re.search(r"method\s*:\s*['\"](POST|PUT|DELETE|PATCH)", body, re.I):
                    continue
                checked += 1
                with self.subTest(action=name):
                    self.assertIn(
                        "read-only-lock",
                        tag,
                        f"{name} writes but is offered to read-only accounts",
                    )
        self.assertGreater(checked, 30, "the classifier found too few write controls to be believable")

    def test_the_viewers_own_login_controls_stay_usable(self):
        for tag in self.tags:
            names = {a or b for a, b in re.findall(r'data-pf-action="([A-Za-z0-9_]+)"|onclick="([A-Za-z0-9_]+)\(', tag)}
            for name in names & self.SELF_SERVICE:
                with self.subTest(action=name):
                    self.assertNotIn("read-only-lock", tag, f"{name} is the viewer's own and must work")


if __name__ == "__main__":
    unittest.main()
