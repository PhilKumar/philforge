"""The bot token must never reach the log.

Found 2026-09-23 while chasing Phil's missing alerts: the production journal
held his LIVE Telegram bot token in clear text, once per alert, because httpx
logs each request's full URL at INFO and the token sits in the Telegram URL's
path. The token had to be revoked.

Broker calls go through the same client, and a query-string credential would
leak the same way, so this is not only about Telegram.
"""

import logging
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = (ROOT / "app.py").read_text(encoding="utf-8")
ALERTER = (ROOT / "alerter.py").read_text(encoding="utf-8")


class RequestUrlsAreNotLogged(unittest.TestCase):
    def test_httpx_is_held_at_warning(self):
        self.assertIn('logging.getLogger("httpx").setLevel(logging.WARNING)', APP)
        self.assertIn('logging.getLogger("httpcore").setLevel(logging.WARNING)', APP)

    def test_it_is_set_where_logging_is_configured(self):
        """Later in the file and an import could log a URL before it applies."""
        self.assertLess(
            APP.index('logging.getLogger("httpx").setLevel'),
            APP.index("_logger = logging.getLogger(__name__)"),
            "silence httpx as part of configuring logging, not afterwards",
        )

    def test_it_really_silences_an_info_request_line(self):
        """The assertion above is text; this one exercises the logger."""
        logging.getLogger("httpx").setLevel(logging.WARNING)
        with self.assertLogs("httpx", level="WARNING") as caught:
            logging.getLogger("httpx").info("HTTP Request: POST https://api.telegram.org/botSECRET/sendMessage")
            logging.getLogger("httpx").warning("kept")
        self.assertEqual(len(caught.records), 1)
        self.assertNotIn("SECRET", caught.records[0].getMessage())


class ADeadTokenSaysSoPlainly(unittest.TestCase):
    def test_a_401_is_an_error_not_a_passing_warning(self):
        """It means EVERY alert is being dropped until someone acts."""
        body = ALERTER[ALERTER.index("async def _send_telegram(") :]
        body = body[: body.index("\nasync def ")]
        self.assertIn("if resp.status_code == 401:", body)
        self.assertIn("_log.error(", body)
        self.assertIn("BotFather", body)

    def test_no_log_call_in_the_alerter_passes_the_token_or_the_url(self):
        """The URL carries the token in its path; neither may be an argument."""
        for line in ALERTER.splitlines():
            if "_log." not in line:
                continue
            self.assertNotIn("TELEGRAM_BOT_TOKEN", line, line.strip())
            self.assertNotIn(", url", line, line.strip())
            self.assertNotIn("(url", line, line.strip())


if __name__ == "__main__":
    unittest.main()
