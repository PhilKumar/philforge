"""01/02-Oct-2026: a token minted at 22:49 IST worked for funds but Dhan refused
every chart request on it with DH-902 ("User has not subscribed to Data APIs")
although the subscription ran to 27-Oct. Nothing treated that as a token
problem, every engine start died at its history seed, and Phil got four
"engine driver stopped -- check any open broker position" alerts for engines
that had never begun."""

import unittest
from unittest import mock

import broker.dhan as dhan

DH902 = {
    "errorType": "Invalid_Access",
    "errorCode": "DH-902",
    "errorMessage": "HTTP Status 451. User has not subscribed to Data APIs or does not have access to Trading APIs.",
}


class _Resp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
        self.text = str(body)

    def json(self):
        return self._body


class ATokenRefusedDataIsReplaced(unittest.TestCase):
    def setUp(self):
        dhan._last_token_refresh.clear()

    def test_dh902_is_recognised_and_nothing_else_is(self):
        self.assertTrue(dhan._is_no_data_access_response(_Resp(401, DH902)))
        self.assertFalse(dhan._is_no_data_access_response(_Resp(400, {"errorCode": "DH-906"})))
        self.assertFalse(dhan._is_no_data_access_response(_Resp(429, {"errorCode": "DH-904"})))
        self.assertFalse(dhan._is_no_data_access_response(_Resp(200, {})))

    def test_one_fresh_token_then_the_same_request_again(self):
        answers = [_Resp(401, DH902), _Resp(200, {"open": []})]
        session = mock.Mock()
        session.request.side_effect = lambda *a, **k: answers.pop(0)
        sent_tokens = []
        orig = session.request.side_effect
        session.request.side_effect = lambda m, u, headers=None, **k: (
            sent_tokens.append(headers["access-token"]),
            orig(),
        )[1]
        with mock.patch.object(dhan, "_http_session", session):
            resp = dhan._request_with_retry(
                "POST",
                "https://api.dhan.co/v2/charts/intraday",
                headers={"access-token": "OLD"},
                json={},
                refresh_token_func=lambda: "NEW",
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(sent_tokens, ["OLD", "NEW"])

    def test_at_most_one_new_token_per_half_hour(self):
        session = mock.Mock()
        session.request.return_value = _Resp(401, DH902)
        refreshes = []
        with mock.patch.object(dhan, "_http_session", session):
            for _ in range(5):
                dhan._request_with_retry(
                    "POST",
                    "https://api.dhan.co/v2/charts/intraday",
                    headers={"access-token": "T"},
                    json={},
                    refresh_token_func=lambda: refreshes.append(1) or "T2",
                )
        self.assertEqual(len(refreshes), 1, "a refused-data token must not be replaced on every poll")


class AnEngineThatNeverRanSaysSo(unittest.TestCase):
    def test_never_ran_and_the_plain_reason(self):
        import app

        eng = mock.Mock(positions=[], in_trade=False, _last_processed_candle_time=None)
        self.assertTrue(app._engine_never_ran(eng))
        eng_open = mock.Mock(positions=[{"status": "open"}], in_trade=True, _last_processed_candle_time=None)
        self.assertFalse(app._engine_never_ran(eng_open))
        detail = 'Exception: Dhan API error 401: {"errorCode":"DH-902"}'
        self.assertEqual(app._death_reason_key(detail), "DH-902")
        self.assertIn("DH-902", app._plain_death_reason(detail))
        self.assertEqual(
            app._death_reason_key("Exception: Dhan API circuit breaker is OPEN — skipping candle fetch"),
            "circuit-breaker",
        )


if __name__ == "__main__":
    unittest.main()


class FourDeathsOneMessage(unittest.TestCase):
    def test_the_same_failure_is_told_once_and_as_could_not_start(self):
        import asyncio

        import app

        app._ENGINE_DEATH_TOLD.clear()
        sent = []

        async def _die():
            raise Exception('Dhan API error 401: {"errorCode":"DH-902"}')

        async def _run_twice():
            for _ in range(2):
                eng = mock.Mock(positions=[], in_trade=False, _last_processed_candle_time=None)
                task = asyncio.ensure_future(_die())
                app._supervise_engine_task(task, eng, run_id="PE_NoTarget")
                await asyncio.sleep(0)
                await asyncio.sleep(0)

        with mock.patch.object(app.alerter, "alert", side_effect=lambda title, *a, **k: sent.append(title)):
            asyncio.run(_run_twice())
        self.assertEqual(sent, ["Engine Could Not Start"])
