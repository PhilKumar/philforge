"""Two engines asking for the same 31 strikes in the same instant is ONE call.

WHY. Dhan answered the LAST asker with a 429, and the last asker is always the
live engine -- the paper books scan a second earlier. On 2026-09-03 that cost a
real trade: live fell back to modelled premiums and bought 23750 at Rs 294.73
where paper bought 23800 at Rs 252.60. A TTL shelf does not fix it, because at a
candle close nobody has an answer BACK yet; every caller sees an empty shelf.

So the first caller fetches and the rest wait on that same fetch.
"""

import asyncio

import pytest

from broker.dhan import DhanClient


class _CountingClient(DhanClient):
    """A client that records how many times the wire was actually used."""

    def __init__(self):
        self.calls: list[list[int]] = []
        self.release = asyncio.Event()

    async def async_get_ltp(self, security_ids, exchange_segment="NSE_EQ"):
        self.calls.append(sorted(int(s) for s in security_ids))
        await self.release.wait()  # hold the "response" so the others pile up
        return {exchange_segment: {str(sid): {"last_price": 100.0 + sid} for sid in security_ids}}


@pytest.fixture(autouse=True)
def _clean_shelves():
    from broker import dhan as dhan_module

    dhan_module._api_cache.clear()
    dhan_module._ltp_inflight.clear()
    yield
    dhan_module._ltp_inflight.clear()


def test_concurrent_callers_share_one_fetch():
    async def scenario():
        client = _CountingClient()
        strikes = list(range(47280, 47311))

        askers = [
            asyncio.create_task(client.async_get_ltp_prices(strikes, exchange_segment="NSE_FNO")) for _ in range(4)
        ]
        await asyncio.sleep(0)  # let all four reach the fetch
        await asyncio.sleep(0)
        client.release.set()
        answers = await asyncio.gather(*askers)

        assert len(client.calls) == 1, f"{len(client.calls)} calls went to Dhan, expected 1"
        for prices in answers:
            assert len(prices) == len(strikes), "a waiting caller got a short answer"
            assert prices[strikes[0]] == 100.0 + strikes[0]

    asyncio.run(scenario())


def test_a_caller_giving_up_does_not_cancel_the_others_fetch():
    async def scenario():
        client = _CountingClient()
        strikes = [47280, 47281]

        leader = asyncio.create_task(client.async_get_ltp_prices(strikes, exchange_segment="NSE_FNO"))
        await asyncio.sleep(0)
        follower = asyncio.create_task(client.async_get_ltp_prices(strikes, exchange_segment="NSE_FNO"))
        await asyncio.sleep(0)
        follower.cancel()
        with pytest.raises(asyncio.CancelledError):
            await follower

        client.release.set()
        prices = await leader
        assert prices[47280] == 100.0 + 47280
        assert len(client.calls) == 1

    asyncio.run(scenario())


def test_a_failed_fetch_is_raised_to_everyone_waiting():
    """A 429 must reach the waiters, so their own retry logic runs. Silently
    handing back an empty shelf is how live ended up pricing from a model."""

    class _FailingClient(DhanClient):
        def __init__(self):
            self.calls = 0
            self.release = asyncio.Event()

        async def async_get_ltp(self, security_ids, exchange_segment="NSE_EQ"):
            self.calls += 1
            await self.release.wait()
            raise RuntimeError("Dhan API 429")

    async def scenario():
        client = _FailingClient()
        strikes = [47280, 47281]
        leader = asyncio.create_task(client.async_get_ltp_prices(strikes, exchange_segment="NSE_FNO"))
        await asyncio.sleep(0)
        follower = asyncio.create_task(client.async_get_ltp_prices(strikes, exchange_segment="NSE_FNO"))
        await asyncio.sleep(0)
        client.release.set()

        for task in (leader, follower):
            with pytest.raises(RuntimeError):
                await task
        assert client.calls == 1

    asyncio.run(scenario())


def test_the_next_asker_after_a_flight_starts_a_new_one():
    """The registry must not strand a finished flight and answer from it forever."""

    async def scenario():
        client = _CountingClient()
        client.release.set()
        strikes = [47280]
        await client.async_get_ltp_prices(strikes, exchange_segment="NSE_FNO", ttl=0.0)
        await client.async_get_ltp_prices(strikes, exchange_segment="NSE_FNO", ttl=0.0)
        assert len(client.calls) == 2, "a fresh ask reused a completed flight"

    asyncio.run(scenario())


def test_a_bad_response_shape_does_not_strand_the_waiters():
    """The flight must resolve even when the fetch succeeds and the PARSE fails.

    A waiter that is never released waits forever -- and a live entry waiting
    forever on a strike price is worse than one that got a 429.
    """

    class _GarbageClient(DhanClient):
        def __init__(self):
            self.release = asyncio.Event()

        async def async_get_ltp(self, security_ids, exchange_segment="NSE_EQ"):
            await self.release.wait()
            return "not a dict"  # noqa: RET504 - the point is that this is wrong

    async def scenario():
        client = _GarbageClient()
        strikes = [47280]
        leader = asyncio.create_task(client.async_get_ltp_prices(strikes, exchange_segment="NSE_FNO"))
        await asyncio.sleep(0)
        follower = asyncio.create_task(client.async_get_ltp_prices(strikes, exchange_segment="NSE_FNO"))
        await asyncio.sleep(0)
        client.release.set()

        with pytest.raises(AttributeError):
            await leader
        with pytest.raises(AttributeError):
            await asyncio.wait_for(follower, timeout=1.0)

        from broker import dhan as dhan_module

        assert dhan_module._ltp_inflight == {}, "a finished flight was left in the registry"

    asyncio.run(scenario())
