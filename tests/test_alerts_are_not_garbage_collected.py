"""An alert that was fired must actually be sent.

Phil, 2026-09-23: "I am not getting the scalp alerts in telegram."

`alerter.alert` fired every alert with `loop.create_task(...)` and kept nothing.
The event loop holds only a WEAK reference to a task, so between the call and
the first await the garbage collector is free to finalise it -- and nothing is
logged when that happens. It is the worst failure a notifier can have: silent,
intermittent, and invisible in exactly the moment it matters.

This affected EVERY strategy, not only Scalp: entries, exits, engine starts,
broker failures and the 09:00 NIFTY close all go through this one function.
"""

import asyncio
import gc
import unittest
from unittest.mock import patch

import alerter


class AnAlertSurvivesTheCollector(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        alerter._inflight.clear()

    async def test_the_task_is_held_until_it_finishes(self):
        started = asyncio.Event()
        release = asyncio.Event()
        sent = []

        async def _slow(html, plain):
            started.set()
            await release.wait()
            sent.append(html)

        with (
            patch.object(alerter, "_dispatch", _slow),
            patch.object(alerter, "_TELEGRAM_OK", True),
        ):
            alerter.alert("Scalp Entry", "NIFTY 23600PE", level="info")
            await started.wait()
            # THE MOMENT THE OLD CODE LOST IT: nothing else refers to the task.
            for _ in range(3):
                gc.collect()
            self.assertEqual(len(alerter._inflight), 1, "the alert was not held")
            release.set()
            await asyncio.sleep(0)
            await asyncio.wait(set(alerter._inflight), timeout=2)

        self.assertEqual(len(sent), 1, "the alert never reached the transport")

    async def test_the_set_empties_itself_when_the_alert_is_done(self):
        """Otherwise a long-running desk leaks one task object per alert."""

        async def _quick(html, plain):
            return None

        with (
            patch.object(alerter, "_dispatch", _quick),
            patch.object(alerter, "_TELEGRAM_OK", True),
        ):
            for _ in range(5):
                alerter.alert("Trade Exit", "done", level="info")
            self.assertEqual(len(alerter._inflight), 5)
            await asyncio.wait(set(alerter._inflight), timeout=2)
        await asyncio.sleep(0)
        self.assertEqual(len(alerter._inflight), 0, "finished alerts must not pile up")

    async def test_a_failing_alert_does_not_take_the_caller_down(self):
        """Fire-and-forget means the trade path never sees the notifier fail."""

        async def _boom(html, plain):
            raise RuntimeError("telegram is down")

        with (
            patch.object(alerter, "_dispatch", _boom),
            patch.object(alerter, "_TELEGRAM_OK", True),
        ):
            alerter.alert("Scalp Exit", "boom", level="warn")
            await asyncio.wait(set(alerter._inflight), timeout=2)
        await asyncio.sleep(0)
        self.assertEqual(len(alerter._inflight), 0)

    async def test_nothing_is_held_when_no_channel_is_configured(self):
        with (
            patch.object(alerter, "_TELEGRAM_OK", False),
            patch.object(alerter, "_DISCORD_OK", False),
        ):
            alerter.alert("Trade Entry", "ignored")
        self.assertEqual(len(alerter._inflight), 0)


class ShutdownWaitsForWhatIsInFlight(unittest.IsolatedAsyncioTestCase):
    async def test_the_last_alerts_before_a_deploy_are_waited_for(self):
        """Closing the client under them logged errors instead of sending."""
        alerter._inflight.clear()
        source = __import__("pathlib").Path(alerter.__file__).read_text(encoding="utf-8")
        body = source[source.index("async def shutdown()") :]
        self.assertIn("await asyncio.wait(set(_inflight), timeout=5)", body)
        self.assertLess(
            body.index("asyncio.wait"),
            body.index("aclose()"),
            "the client must not close before the alerts have gone",
        )


if __name__ == "__main__":
    unittest.main()
