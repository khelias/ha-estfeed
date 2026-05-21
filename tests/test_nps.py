"""Tests for the Elering NPS price client."""

from __future__ import annotations

import re
from datetime import UTC, datetime

import aiohttp
import pytest
from aioresponses import aioresponses

from custom_components.estfeed.nps import EleringNpsClient, NpsError

# Match the NPS endpoint regardless of query string — aiohttp percent-encodes
# the start/end ISO timestamps, which makes literal URL matching brittle.
NPS_URL_RE = re.compile(r"^https://dashboard\.elering\.ee/api/nps/price")


@pytest.fixture
async def session():
    connector = aiohttp.TCPConnector(force_close=True)
    async with aiohttp.ClientSession(connector=connector) as s:
        yield s


def _stub_response(prices_by_hour: dict[datetime, float]) -> dict:
    return {
        "success": True,
        "data": {
            "ee": [
                {"timestamp": int(ts.timestamp()), "price": price_eur_per_mwh}
                for ts, price_eur_per_mwh in prices_by_hour.items()
            ]
        },
    }


@pytest.mark.asyncio
async def test_async_get_prices_happy_path(session):
    start = datetime(2026, 5, 21, 0, tzinfo=UTC)
    end = datetime(2026, 5, 21, 3, tzinfo=UTC)
    # NPS returns EUR/MWh; client converts to EUR/kWh.
    raw = {
        datetime(2026, 5, 21, 0, tzinfo=UTC): 50.0,  # → 0.050
        datetime(2026, 5, 21, 1, tzinfo=UTC): 45.5,  # → 0.0455
        datetime(2026, 5, 21, 2, tzinfo=UTC): 60.0,  # → 0.060
    }
    with aioresponses() as m:
        m.get(
            NPS_URL_RE,
            payload=_stub_response(raw),
        )
        client = EleringNpsClient(session)
        prices = await client.async_get_prices(start, end)
    assert prices == {
        datetime(2026, 5, 21, 0, tzinfo=UTC): pytest.approx(0.050),
        datetime(2026, 5, 21, 1, tzinfo=UTC): pytest.approx(0.0455),
        datetime(2026, 5, 21, 2, tzinfo=UTC): pytest.approx(0.060),
    }


@pytest.mark.asyncio
async def test_async_get_prices_caches_per_hour(session):
    start = datetime(2026, 5, 21, 0, tzinfo=UTC)
    end = datetime(2026, 5, 21, 2, tzinfo=UTC)
    raw = {
        datetime(2026, 5, 21, 0, tzinfo=UTC): 50.0,
        datetime(2026, 5, 21, 1, tzinfo=UTC): 50.0,
    }
    with aioresponses() as m:
        m.get(
            NPS_URL_RE,
            payload=_stub_response(raw),
        )
        client = EleringNpsClient(session)
        first = await client.async_get_prices(start, end)
        # Second call covering the same hours must NOT hit the network.
        second = await client.async_get_prices(start, end)
    assert first == second
    # aioresponses only stubbed one match — a second call would raise.


@pytest.mark.asyncio
async def test_async_get_prices_partial_cache_miss(session):
    """Asking for a range that overlaps the cache should only fetch the gap."""
    raw_first = {datetime(2026, 5, 21, h, tzinfo=UTC): 50.0 for h in range(2)}
    raw_second = {datetime(2026, 5, 21, h, tzinfo=UTC): 60.0 for h in range(2, 5)}
    with aioresponses() as m:
        m.get(NPS_URL_RE, payload=_stub_response(raw_first))
        m.get(NPS_URL_RE, payload=_stub_response(raw_second))
        client = EleringNpsClient(session)
        await client.async_get_prices(
            datetime(2026, 5, 21, 0, tzinfo=UTC),
            datetime(2026, 5, 21, 2, tzinfo=UTC),
        )
        prices = await client.async_get_prices(
            datetime(2026, 5, 21, 0, tzinfo=UTC),
            datetime(2026, 5, 21, 5, tzinfo=UTC),
        )
    # All five hours returned, mixing cache + fresh fetch
    assert len(prices) == 5
    assert prices[datetime(2026, 5, 21, 0, tzinfo=UTC)] == pytest.approx(0.050)
    assert prices[datetime(2026, 5, 21, 4, tzinfo=UTC)] == pytest.approx(0.060)


@pytest.mark.asyncio
async def test_async_get_prices_5xx_raises_nps_error(session):
    with aioresponses() as m:
        m.get(NPS_URL_RE, status=502)
        client = EleringNpsClient(session)
        with pytest.raises(NpsError):
            await client.async_get_prices(
                datetime(2026, 5, 21, 0, tzinfo=UTC),
                datetime(2026, 5, 21, 1, tzinfo=UTC),
            )


@pytest.mark.asyncio
async def test_async_get_prices_empty_range_no_http(session):
    """start == end means nothing to fetch; cache or not, return empty without HTTP."""
    with aioresponses():
        client = EleringNpsClient(session)
        result = await client.async_get_prices(
            datetime(2026, 5, 21, 0, tzinfo=UTC),
            datetime(2026, 5, 21, 0, tzinfo=UTC),
        )
    assert result == {}


@pytest.mark.asyncio
async def test_async_get_prices_cache_size_property(session):
    raw = {datetime(2026, 5, 21, h, tzinfo=UTC): 50.0 for h in range(3)}
    with aioresponses() as m:
        m.get(NPS_URL_RE, payload=_stub_response(raw))
        client = EleringNpsClient(session)
        assert client.cache_size == 0
        await client.async_get_prices(
            datetime(2026, 5, 21, 0, tzinfo=UTC),
            datetime(2026, 5, 21, 3, tzinfo=UTC),
        )
        assert client.cache_size == 3
