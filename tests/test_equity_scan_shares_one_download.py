"""The Equity page's two scans share ONE download of the universe per day.

Phil, 2026-09-22: "the scan results on the equity page taking more time". A
fresh scan is ~220 Dhan history calls (~90 s), and the page asks for two at
once -- the Cash Cascade at Rs 1L and the two-red ladder at Rs 2L. The result
cache is keyed by those settings, so each scan downloaded the same histories.
"""

import asyncio
import os
import unittest
from unittest import mock

os.environ.setdefault("PHILFORGE_SKIP_STARTUP_JOBS", "1")
os.environ.setdefault("PHILFORGE_STARTUP_ENGINE_RESTORE", "0")

import app  # noqa: E402


class OneDownloadPerDay(unittest.TestCase):
    def setUp(self):
        app._CASCADE_SCAN_ROWS.clear()
        self.addCleanup(app._CASCADE_SCAN_ROWS.clear)
        self.calls = 0

        async def fake_history(_broker, stock, _semaphore):
            self.calls += 1
            await asyncio.sleep(0)
            return stock["symbol"]

        patcher = mock.patch.object(app, "_cascade_scan_history", fake_history)
        patcher.start()
        self.addCleanup(patcher.stop)
        universe = mock.patch.object(app, "TERMINAL_STOCKS", [{"symbol": s} for s in ("A", "B", "C")])
        universe.start()
        self.addCleanup(universe.stop)
        resolve = mock.patch.object(app, "_resolve_terminal_stock", lambda symbol: {"symbol": symbol})
        resolve.start()
        self.addCleanup(resolve.stop)

    def _two_at_once(self):
        async def both():
            return await asyncio.gather(app._cascade_scan_rows(None), app._cascade_scan_rows(None))

        return asyncio.run(both())

    def test_two_scans_at_once_download_the_universe_once(self):
        (rows_a, n_a), (rows_b, n_b) = self._two_at_once()
        self.assertEqual(self.calls, 3)
        self.assertEqual(rows_a, ["A", "B", "C"])
        self.assertEqual(rows_a, rows_b)
        self.assertEqual((n_a, n_b), (3, 3))

    def test_a_later_scan_with_other_settings_reuses_it(self):
        asyncio.run(app._cascade_scan_rows(None))
        asyncio.run(app._cascade_scan_rows(None))
        self.assertEqual(self.calls, 3)

    def test_refresh_downloads_again(self):
        asyncio.run(app._cascade_scan_rows(None))
        asyncio.run(app._cascade_scan_rows(None, refresh=True))
        self.assertEqual(self.calls, 6)


if __name__ == "__main__":
    unittest.main()
