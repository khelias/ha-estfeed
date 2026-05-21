# Estfeed Cost Statistic Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish per-electricity-meter cost and compensation external statistics computed from Estfeed consumption/production × Elering NPS spot price, so the HA Energy dashboard can display monetary values for the estfeed grid source.

**Architecture:** Adds an Elering NPS HTTP client (`nps.py`) and pure pricing functions (`pricing.py`). Extends `statistics.py` with a `CostStream` type + writer. The coordinator co-fetches prices alongside consumption per chunk and writes two new external statistics (`estfeed:<slug>_cost_<suffix>` and `…_compensation_<suffix>`) using the same `prior_sum`/`latest_seen` machinery as energy stats. Options-change triggers a cost-only rebuild; first install on an upgraded entry triggers a one-time cost-only initial fill that leaves existing energy stats untouched.

**Tech Stack:** Python 3.12+, Home Assistant custom component, aiohttp, voluptuous, pytest + pytest-homeassistant-custom-component, aioresponses, freezegun.

**Spec:** `docs/superpowers/specs/2026-05-21-estfeed-cost-statistic-design.md`

---

## File Structure

**New files:**
- `custom_components/estfeed/nps.py` — `EleringNpsClient`, `NpsError`, hourly EE-zone price cache
- `custom_components/estfeed/pricing.py` — pure functions `apply_tariff`, `make_tariff`, `compute_cost_rows`
- `tests/test_nps.py` — NPS client tests (mocked HTTP via aioresponses)
- `tests/test_pricing.py` — pricing pure-function tests

**Modified files:**
- `custom_components/estfeed/const.py` — add `CONF_VAT_PERCENT`, `CONF_MARGIN_EUR_PER_KWH`, defaults
- `custom_components/estfeed/statistics.py` — add `CostStream`, `async_write_cost_statistics`
- `custom_components/estfeed/coordinator.py` — add NPS client, `cost_streams_for`, `_build_tariff`, `async_rebuild_cost`, `last_nps_error`, extend `_fetch_meter_window` with cost branch + `cost_only` param
- `custom_components/estfeed/__init__.py` — extend `_async_options_updated` for VAT/margin changes; upgrade-time cost-initial-fill probe
- `custom_components/estfeed/config_flow.py` — add VAT% and margin fields to options step
- `custom_components/estfeed/diagnostics.py` — surface `cost_stream_ids`, `last_nps_error`, `nps_cache_size`
- `custom_components/estfeed/translations/en.json`, `et.json` — labels for new options
- `tests/test_statistics.py`, `tests/test_coordinator.py`, `tests/test_config_flow.py`, `tests/test_diagnostics.py` — extend
- `README.md` — document cost statistic + Energy dashboard wiring

---

## Task 1: Pricing pure functions — `apply_tariff` and `make_tariff`

**Files:**
- Create: `custom_components/estfeed/pricing.py`
- Test: `tests/test_pricing.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_pricing.py
"""Tests for pricing pure functions."""

from __future__ import annotations

import pytest

from custom_components.estfeed.pricing import apply_tariff, make_tariff


def test_apply_tariff_vat_only():
    # 0.05 €/kWh * (1 + 0.22) = 0.061
    assert apply_tariff(0.05, vat_percent=22.0, margin_eur_per_kwh=0.0) == pytest.approx(0.061)


def test_apply_tariff_margin_only():
    # 0.05 + 0.007 margin, no VAT
    assert apply_tariff(0.05, vat_percent=0.0, margin_eur_per_kwh=0.007) == pytest.approx(0.057)


def test_apply_tariff_vat_and_margin():
    # 0.05 * 1.22 + 0.007 = 0.068
    assert apply_tariff(0.05, vat_percent=22.0, margin_eur_per_kwh=0.007) == pytest.approx(0.068)


def test_apply_tariff_negative_margin_for_discount():
    # promotional discount: net rate below spot
    assert apply_tariff(0.05, vat_percent=0.0, margin_eur_per_kwh=-0.01) == pytest.approx(0.04)


def test_apply_tariff_zero_spot():
    # NPS hour with 0 €/kWh: still picks up margin (VAT on zero is zero)
    assert apply_tariff(0.0, vat_percent=22.0, margin_eur_per_kwh=0.005) == pytest.approx(0.005)


def test_apply_tariff_negative_spot():
    # NPS occasionally goes negative; tariff math should still work
    assert apply_tariff(-0.02, vat_percent=22.0, margin_eur_per_kwh=0.005) == pytest.approx(
        -0.02 * 1.22 + 0.005
    )


def test_make_tariff_returns_callable():
    tariff = make_tariff(vat_percent=22.0, margin_eur_per_kwh=0.007)
    assert callable(tariff)
    assert tariff(0.05) == pytest.approx(0.068)


def test_make_tariff_captures_arguments():
    tariff_22 = make_tariff(22.0, 0.0)
    tariff_24 = make_tariff(24.0, 0.0)
    assert tariff_22(0.05) != tariff_24(0.05)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_pricing.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'custom_components.estfeed.pricing'`

- [ ] **Step 3: Write minimal implementation**

```python
# custom_components/estfeed/pricing.py
"""Pure pricing helpers: tariff math and cost row construction."""

from __future__ import annotations

from collections.abc import Callable


def apply_tariff(
    spot_eur_per_kwh: float,
    vat_percent: float,
    margin_eur_per_kwh: float,
) -> float:
    """Apply VAT and a fixed per-kWh margin to a Nord Pool spot price.

    Formula: ``spot * (1 + vat_percent/100) + margin``. Negative margins
    are allowed (promotional discounts); negative spots are passed
    through (NPS occasionally settles negative).
    """
    return spot_eur_per_kwh * (1 + vat_percent / 100) + margin_eur_per_kwh


def make_tariff(
    vat_percent: float,
    margin_eur_per_kwh: float,
) -> Callable[[float], float]:
    """Curry apply_tariff so compute_cost_rows can call ``tariff(spot)``."""
    return lambda spot: apply_tariff(spot, vat_percent, margin_eur_per_kwh)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_pricing.py -v`
Expected: PASS — all 8 tests green.

- [ ] **Step 5: Commit**

```bash
git add custom_components/estfeed/pricing.py tests/test_pricing.py
git commit -m "Add pricing pure functions for VAT and margin tariff"
```

---

## Task 2: Pricing — `compute_cost_rows`

**Files:**
- Modify: `custom_components/estfeed/pricing.py`
- Test: `tests/test_pricing.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pricing.py`:

```python
from datetime import UTC, datetime

from custom_components.estfeed.api import AccountingInterval
from custom_components.estfeed.const import Kind
from custom_components.estfeed.pricing import compute_cost_rows


def _interval(hour: int, minute: int, consumption: float | None = None,
              production: float | None = None) -> AccountingInterval:
    return AccountingInterval(
        period_start=datetime(2026, 5, 21, hour, minute, tzinfo=UTC),
        consumption_kwh=consumption,
        production_kwh=production,
        consumption_m3=None,
        production_m3=None,
    )


def test_compute_cost_rows_single_hour():
    intervals = [_interval(10, 0, consumption=2.0)]
    prices = {datetime(2026, 5, 21, 10, tzinfo=UTC): 0.05}  # €0.05/kWh post-tariff
    tariff = make_tariff(vat_percent=0.0, margin_eur_per_kwh=0.0)
    rows = compute_cost_rows(intervals, Kind.CONSUMPTION, prices, tariff, prior_sum=0.0)
    assert len(rows) == 1
    assert rows[0]["start"] == datetime(2026, 5, 21, 10, tzinfo=UTC)
    # 2.0 kWh * 0.05 €/kWh = 0.10 €
    assert rows[0]["sum"] == pytest.approx(0.10)
    assert rows[0]["state"] == rows[0]["sum"]


def test_compute_cost_rows_aggregates_quarter_hour_intervals():
    intervals = [
        _interval(10, 0, consumption=0.1),
        _interval(10, 15, consumption=0.2),
        _interval(10, 30, consumption=0.3),
        _interval(10, 45, consumption=0.4),
    ]
    prices = {datetime(2026, 5, 21, 10, tzinfo=UTC): 0.05}
    tariff = make_tariff(0.0, 0.0)
    rows = compute_cost_rows(intervals, Kind.CONSUMPTION, prices, tariff, prior_sum=0.0)
    assert len(rows) == 1
    # 1.0 kWh * 0.05 = 0.05
    assert rows[0]["sum"] == pytest.approx(0.05)


def test_compute_cost_rows_applies_tariff():
    intervals = [_interval(10, 0, consumption=2.0)]
    prices = {datetime(2026, 5, 21, 10, tzinfo=UTC): 0.05}
    tariff = make_tariff(vat_percent=22.0, margin_eur_per_kwh=0.01)
    rows = compute_cost_rows(intervals, Kind.CONSUMPTION, prices, tariff, prior_sum=0.0)
    # tariff(0.05) = 0.05*1.22 + 0.01 = 0.071; * 2.0 kWh = 0.142
    assert rows[0]["sum"] == pytest.approx(0.142)


def test_compute_cost_rows_skips_missing_price_hours():
    intervals = [
        _interval(10, 0, consumption=1.0),
        _interval(11, 0, consumption=1.0),
        _interval(12, 0, consumption=1.0),
    ]
    # Only 10:00 and 12:00 have prices; 11:00 is missing.
    prices = {
        datetime(2026, 5, 21, 10, tzinfo=UTC): 0.05,
        datetime(2026, 5, 21, 12, tzinfo=UTC): 0.06,
    }
    tariff = make_tariff(0.0, 0.0)
    rows = compute_cost_rows(intervals, Kind.CONSUMPTION, prices, tariff, prior_sum=0.0)
    assert len(rows) == 2
    starts = [r["start"] for r in rows]
    assert datetime(2026, 5, 21, 11, tzinfo=UTC) not in starts
    # Cumulative continues across the gap: 0.05 then 0.05+0.06=0.11
    assert rows[0]["sum"] == pytest.approx(0.05)
    assert rows[1]["sum"] == pytest.approx(0.11)


def test_compute_cost_rows_skips_none_values():
    intervals = [
        _interval(10, 0, consumption=None),
        _interval(11, 0, consumption=2.0),
    ]
    prices = {
        datetime(2026, 5, 21, 10, tzinfo=UTC): 0.05,
        datetime(2026, 5, 21, 11, tzinfo=UTC): 0.05,
    }
    tariff = make_tariff(0.0, 0.0)
    rows = compute_cost_rows(intervals, Kind.CONSUMPTION, prices, tariff, prior_sum=0.0)
    assert len(rows) == 1
    assert rows[0]["start"] == datetime(2026, 5, 21, 11, tzinfo=UTC)
    assert rows[0]["sum"] == pytest.approx(0.10)


def test_compute_cost_rows_carries_prior_sum():
    intervals = [_interval(10, 0, consumption=2.0)]
    prices = {datetime(2026, 5, 21, 10, tzinfo=UTC): 0.05}
    tariff = make_tariff(0.0, 0.0)
    rows = compute_cost_rows(intervals, Kind.CONSUMPTION, prices, tariff, prior_sum=100.0)
    # 100.0 + 0.10
    assert rows[0]["sum"] == pytest.approx(100.10)


def test_compute_cost_rows_production_kind():
    intervals = [_interval(10, 0, production=1.5)]
    prices = {datetime(2026, 5, 21, 10, tzinfo=UTC): 0.04}
    tariff = make_tariff(0.0, 0.0)
    rows = compute_cost_rows(intervals, Kind.PRODUCTION, prices, tariff, prior_sum=0.0)
    assert rows[0]["sum"] == pytest.approx(0.06)


def test_compute_cost_rows_sorts_by_start():
    intervals = [
        _interval(12, 0, consumption=1.0),
        _interval(10, 0, consumption=1.0),
        _interval(11, 0, consumption=1.0),
    ]
    prices = {
        datetime(2026, 5, 21, h, tzinfo=UTC): 0.05 for h in (10, 11, 12)
    }
    tariff = make_tariff(0.0, 0.0)
    rows = compute_cost_rows(intervals, Kind.CONSUMPTION, prices, tariff, prior_sum=0.0)
    starts = [r["start"] for r in rows]
    assert starts == sorted(starts)


def test_compute_cost_rows_rounds_to_four_decimals():
    # 0.333333 kWh * 0.05 = 0.0166666...; rounds to 0.0167
    intervals = [_interval(10, 0, consumption=0.333333)]
    prices = {datetime(2026, 5, 21, 10, tzinfo=UTC): 0.05}
    tariff = make_tariff(0.0, 0.0)
    rows = compute_cost_rows(intervals, Kind.CONSUMPTION, prices, tariff, prior_sum=0.0)
    assert rows[0]["sum"] == pytest.approx(0.0167)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_pricing.py -v`
Expected: FAIL with `ImportError: cannot import name 'compute_cost_rows' from 'custom_components.estfeed.pricing'`

- [ ] **Step 3: Write the implementation**

Append to `custom_components/estfeed/pricing.py`:

```python
from datetime import datetime
from typing import Any

from homeassistant.components.recorder.models import StatisticData

from .api import AccountingInterval
from .const import Kind


def _interval_value(interval: AccountingInterval, kind: Kind) -> float | None:
    """Pick the relevant kWh/m³ field for a given kind (mirrors statistics.py)."""
    if kind == Kind.CONSUMPTION:
        if interval.consumption_kwh is not None:
            return interval.consumption_kwh
        return interval.consumption_m3
    if interval.production_kwh is not None:
        return interval.production_kwh
    return interval.production_m3


def compute_cost_rows(
    intervals: list[AccountingInterval],
    kind: Kind,
    prices: dict[datetime, float],
    tariff: Callable[[float], float],
    prior_sum: float,
) -> list[StatisticData]:
    """Build cumulative-sum cost rows from raw intervals and an hourly price map.

    Buckets intervals into hours (matching ``statistics.compute_statistic_rows``),
    multiplies each hour's summed kWh by ``tariff(prices[hour])``, and produces a
    running cumulative-sum series in EUR. Skips hours where the price is missing
    (NPS gap or future hour). Rounds to 4 decimal places (€0.0001).
    """
    hourly: dict[Any, float] = {}
    for ival in intervals:
        value = _interval_value(ival, kind)
        if value is None:
            continue
        bucket = ival.period_start.replace(minute=0, second=0, microsecond=0)
        hourly[bucket] = hourly.get(bucket, 0.0) + float(value)

    rows: list[StatisticData] = []
    running = prior_sum
    for start in sorted(hourly):
        price = prices.get(start)
        if price is None:
            continue
        cost = round(hourly[start] * tariff(price), 4)
        running = round(running + cost, 4)
        rows.append({"start": start, "state": running, "sum": running})
    return rows
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_pricing.py -v`
Expected: PASS — all 17 tests green (8 from Task 1, 9 new).

- [ ] **Step 5: Commit**

```bash
git add custom_components/estfeed/pricing.py tests/test_pricing.py
git commit -m "Add compute_cost_rows for hourly cost stat construction"
```

---

## Task 3: Elering NPS client — `EleringNpsClient`

**Files:**
- Create: `custom_components/estfeed/nps.py`
- Test: `tests/test_nps.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_nps.py
"""Tests for the Elering NPS price client."""

from __future__ import annotations

from datetime import UTC, datetime

import aiohttp
import pytest
from aioresponses import aioresponses

from custom_components.estfeed.nps import EleringNpsClient, NpsError


@pytest.fixture
async def session():
    async with aiohttp.ClientSession() as s:
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
        datetime(2026, 5, 21, 0, tzinfo=UTC): 50.0,   # → 0.050
        datetime(2026, 5, 21, 1, tzinfo=UTC): 45.5,   # → 0.0455
        datetime(2026, 5, 21, 2, tzinfo=UTC): 60.0,   # → 0.060
    }
    with aioresponses() as m:
        m.get(
            "https://dashboard.elering.ee/api/nps/price",
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
    raw = {datetime(2026, 5, 21, 0, tzinfo=UTC): 50.0,
           datetime(2026, 5, 21, 1, tzinfo=UTC): 50.0}
    with aioresponses() as m:
        m.get(
            "https://dashboard.elering.ee/api/nps/price",
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
        m.get("https://dashboard.elering.ee/api/nps/price", payload=_stub_response(raw_first))
        m.get("https://dashboard.elering.ee/api/nps/price", payload=_stub_response(raw_second))
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
        m.get("https://dashboard.elering.ee/api/nps/price", status=502)
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
        m.get("https://dashboard.elering.ee/api/nps/price", payload=_stub_response(raw))
        client = EleringNpsClient(session)
        assert client.cache_size == 0
        await client.async_get_prices(
            datetime(2026, 5, 21, 0, tzinfo=UTC),
            datetime(2026, 5, 21, 3, tzinfo=UTC),
        )
        assert client.cache_size == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_nps.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'custom_components.estfeed.nps'`

- [ ] **Step 3: Write the implementation**

```python
# custom_components/estfeed/nps.py
"""Elering NPS (Nord Pool spot) price client.

Wraps the public dashboard endpoint at
``https://dashboard.elering.ee/api/nps/price`` to fetch hourly EE-zone
spot prices. Maintains a process-lifetime in-memory cache keyed by
top-of-hour UTC so a single coordinator tick that walks overlapping
windows hits the network at most once per gap.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import aiohttp

NPS_PRICE_URL = "https://dashboard.elering.ee/api/nps/price"
NPS_TIMEOUT_SECONDS = 30

_LOGGER = logging.getLogger(__name__)


class NpsError(Exception):
    """Raised when the NPS endpoint returns a non-200 status or unparseable body."""


class EleringNpsClient:
    """Async client for Elering's public NPS price endpoint.

    No auth required. Returns prices in EUR/kWh (converted from the
    endpoint's native EUR/MWh).
    """

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session
        # Top-of-hour UTC → EUR/kWh
        self._cache: dict[datetime, float] = {}

    @property
    def cache_size(self) -> int:
        return len(self._cache)

    async def async_get_prices(
        self, start: datetime, end: datetime
    ) -> dict[datetime, float]:
        """Return hourly EE prices in [start, end) keyed by top-of-hour UTC.

        Cache-first: every hour already present in the cache is returned
        without an HTTP call. The gap (if any) is fetched in a single
        request covering the missing range. Empty ranges return ``{}``
        without hitting the network.
        """
        start = start.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
        end = end.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
        if end <= start:
            return {}

        wanted: list[datetime] = []
        cursor = start
        while cursor < end:
            wanted.append(cursor)
            cursor += timedelta(hours=1)

        missing = [h for h in wanted if h not in self._cache]
        if missing:
            await self._fetch_range(missing[0], missing[-1] + timedelta(hours=1))

        return {h: self._cache[h] for h in wanted if h in self._cache}

    async def _fetch_range(self, start: datetime, end: datetime) -> None:
        params = {
            "start": start.isoformat().replace("+00:00", "Z"),
            "end": end.isoformat().replace("+00:00", "Z"),
        }
        try:
            async with self._session.get(
                NPS_PRICE_URL,
                params=params,
                timeout=aiohttp.ClientTimeout(total=NPS_TIMEOUT_SECONDS),
            ) as resp:
                if resp.status != 200:
                    raise NpsError(f"NPS returned status {resp.status}")
                payload = await resp.json(content_type=None)
        except aiohttp.ClientError as err:
            raise NpsError(f"NPS request failed: {err}") from err
        except TimeoutError as err:
            raise NpsError(f"NPS request timed out: {err}") from err

        rows = (payload or {}).get("data", {}).get("ee", [])
        for row in rows:
            ts = row.get("timestamp")
            price_eur_per_mwh = row.get("price")
            if ts is None or price_eur_per_mwh is None:
                continue
            hour = datetime.fromtimestamp(int(ts), tz=UTC).replace(
                minute=0, second=0, microsecond=0
            )
            self._cache[hour] = float(price_eur_per_mwh) / 1000.0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_nps.py -v`
Expected: PASS — all 6 tests green.

- [ ] **Step 5: Commit**

```bash
git add custom_components/estfeed/nps.py tests/test_nps.py
git commit -m "Add Elering NPS price client with per-hour cache"
```

---

## Task 4: Constants — VAT and margin

**Files:**
- Modify: `custom_components/estfeed/const.py`

- [ ] **Step 1: Add the new constants**

Edit `custom_components/estfeed/const.py`, insert after the existing `MIN_BACKFILL_MONTHS` line:

```python
CONF_VAT_PERCENT: Final = "vat_percent"
CONF_MARGIN_EUR_PER_KWH: Final = "margin_eur_per_kwh"
DEFAULT_VAT_PERCENT: Final = 22.0
DEFAULT_MARGIN_EUR_PER_KWH: Final = 0.0
```

- [ ] **Step 2: Run the existing test suite to verify no regression**

Run: `pytest tests/ -x -q`
Expected: All tests still pass — nothing imports the new constants yet.

- [ ] **Step 3: Commit**

```bash
git add custom_components/estfeed/const.py
git commit -m "Add VAT% and margin config keys + defaults"
```

---

## Task 5: `CostStream` type and writer in `statistics.py`

**Files:**
- Modify: `custom_components/estfeed/statistics.py`
- Test: `tests/test_statistics.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_statistics.py`:

```python
from collections.abc import Callable
from datetime import UTC, datetime
from unittest.mock import Mock, patch

import pytest

from custom_components.estfeed.api import AccountingInterval
from custom_components.estfeed.const import Kind
from custom_components.estfeed.statistics import (
    CostStream,
    async_write_cost_statistics,
)


def _flat_tariff() -> Callable[[float], float]:
    return lambda spot: spot  # identity → easy arithmetic in tests


@pytest.mark.asyncio
async def test_async_write_cost_statistics_writes_eur_metadata(hass):
    intervals = [
        AccountingInterval(
            period_start=datetime(2026, 5, 21, 10, tzinfo=UTC),
            consumption_kwh=2.0,
            production_kwh=None,
            consumption_m3=None,
            production_m3=None,
        )
    ]
    prices = {datetime(2026, 5, 21, 10, tzinfo=UTC): 0.05}
    stream = CostStream(
        statistic_id="estfeed:home_cost_089n",
        name="Home cost (089n)",
        unit="EUR",
        kind=Kind.CONSUMPTION,
    )
    with patch(
        "custom_components.estfeed.statistics.async_add_external_statistics",
        new=Mock(),
    ) as mock_add:
        result = await async_write_cost_statistics(
            hass, stream, intervals, prices, _flat_tariff(), prior_sum=0.0
        )
    mock_add.assert_called_once()
    metadata, rows = mock_add.call_args.args[1], mock_add.call_args.args[2]
    assert metadata["statistic_id"] == "estfeed:home_cost_089n"
    assert metadata["unit_of_measurement"] == "EUR"
    assert metadata["has_sum"] is True
    assert metadata["has_mean"] is False
    # No unit_class for monetary streams — unit_class is for unit conversion.
    assert "unit_class" not in metadata
    assert len(rows) == 1
    assert rows[0]["sum"] == pytest.approx(0.10)
    assert result == pytest.approx(0.10)


@pytest.mark.asyncio
async def test_async_write_cost_statistics_noop_when_no_priceable_rows(hass):
    """No matching prices → nothing to write, returns prior_sum unchanged."""
    intervals = [
        AccountingInterval(
            period_start=datetime(2026, 5, 21, 10, tzinfo=UTC),
            consumption_kwh=2.0,
            production_kwh=None,
            consumption_m3=None,
            production_m3=None,
        )
    ]
    prices: dict[datetime, float] = {}
    stream = CostStream(
        statistic_id="estfeed:home_cost_089n",
        name="x",
        unit="EUR",
        kind=Kind.CONSUMPTION,
    )
    with patch(
        "custom_components.estfeed.statistics.async_add_external_statistics",
        new=Mock(),
    ) as mock_add:
        result = await async_write_cost_statistics(
            hass, stream, intervals, prices, _flat_tariff(), prior_sum=42.0
        )
    mock_add.assert_not_called()
    assert result == 42.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_statistics.py -v -k cost`
Expected: FAIL with `ImportError: cannot import name 'CostStream'`

- [ ] **Step 3: Add `CostStream` and `async_write_cost_statistics` to `statistics.py`**

Append to `custom_components/estfeed/statistics.py`:

```python
from collections.abc import Callable

from .pricing import compute_cost_rows


@dataclass(frozen=True, slots=True)
class CostStream:
    """Identifies one cost/compensation external statistics stream."""

    statistic_id: str
    name: str
    unit: str          # currency code, e.g. "EUR"
    kind: Kind         # CONSUMPTION → cost; PRODUCTION → compensation


async def async_write_cost_statistics(
    hass: HomeAssistant,
    stream: CostStream,
    intervals: list[AccountingInterval],
    prices: dict[datetime, float],
    tariff: Callable[[float], float],
    prior_sum: float,
) -> float:
    """Compute cost rows for one meter+kind and publish them.

    Returns the running cumulative sum after writing (equal to ``prior_sum``
    if no priceable rows were produced). Caller chains the return value as
    the next chunk's ``prior_sum`` to avoid a read-after-write hazard
    against HA's recorder (statistics writes may not flush synchronously).
    """
    rows = compute_cost_rows(intervals, stream.kind, prices, tariff, prior_sum=prior_sum)
    if not rows:
        return prior_sum
    metadata: StatisticMetaData = {
        "source": DOMAIN,
        "statistic_id": stream.statistic_id,
        "name": stream.name,
        "unit_of_measurement": stream.unit,
        "has_sum": True,
        "has_mean": False,
    }
    if _MEAN_TYPE_NONE is not None:
        metadata["mean_type"] = _MEAN_TYPE_NONE  # type: ignore[typeddict-unknown-key]
    # NOTE: no unit_class — unit_class is for energy/volume/mass conversion.
    async_add_external_statistics(hass, metadata, rows)
    last_sum = rows[-1].get("sum")
    return float(last_sum) if last_sum is not None else prior_sum
```

Add the missing `datetime` import at the top of the file if not already present (it's used in the function signature):

```python
from datetime import datetime
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_statistics.py -v`
Expected: PASS — all old + 2 new tests green.

- [ ] **Step 5: Commit**

```bash
git add custom_components/estfeed/statistics.py tests/test_statistics.py
git commit -m "Add CostStream type and async_write_cost_statistics"
```

---

## Task 6: Coordinator — `cost_streams_for`, `_build_tariff`, `last_nps_error`

**Files:**
- Modify: `custom_components/estfeed/coordinator.py`
- Test: `tests/test_coordinator.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_coordinator.py` (mirror the style of existing coordinator tests — pass `hass`, build a coordinator directly):

```python
from custom_components.estfeed.api import CommodityType, MeteringPoint, Period
from custom_components.estfeed.const import CONF_MARGIN_EUR_PER_KWH, CONF_VAT_PERCENT
from custom_components.estfeed.coordinator import EstfeedCoordinator
from custom_components.estfeed.statistics import CostStream


def _electric_meter() -> MeteringPoint:
    return MeteringPoint(
        eic="38ZEE-00720089-N",
        commodity_type=CommodityType.ELECTRICITY,
        periods=[Period(start=datetime(2020, 1, 1, tzinfo=UTC), end=None)],
    )


def _gas_meter() -> MeteringPoint:
    return MeteringPoint(
        eic="38ZEE-00720099-G",
        commodity_type=CommodityType.NATURAL_GAS,
        periods=[Period(start=datetime(2020, 1, 1, tzinfo=UTC), end=None)],
    )


def test_cost_streams_for_electricity_returns_two_streams(hass):
    client = Mock()
    coord = EstfeedCoordinator(hass=hass, client=client, slug="home", options={})
    coord.meters = [_electric_meter()]
    streams = coord.cost_streams_for(_electric_meter())
    assert len(streams) == 2
    ids = {s.statistic_id for s in streams}
    assert ids == {"estfeed:home_cost_089n", "estfeed:home_compensation_089n"}
    assert all(isinstance(s, CostStream) for s in streams)
    assert all(s.unit == "EUR" for s in streams)


def test_cost_streams_for_gas_returns_empty(hass):
    client = Mock()
    coord = EstfeedCoordinator(hass=hass, client=client, slug="home", options={})
    coord.meters = [_gas_meter()]
    assert coord.cost_streams_for(_gas_meter()) == []


def test_build_tariff_applies_configured_vat_and_margin(hass):
    client = Mock()
    coord = EstfeedCoordinator(
        hass=hass,
        client=client,
        slug="home",
        options={CONF_VAT_PERCENT: 22.0, CONF_MARGIN_EUR_PER_KWH: 0.01},
    )
    tariff = coord._build_tariff()
    # 0.05 * 1.22 + 0.01 = 0.071
    assert tariff(0.05) == pytest.approx(0.071)


def test_build_tariff_uses_defaults_when_options_missing(hass):
    client = Mock()
    coord = EstfeedCoordinator(hass=hass, client=client, slug="home", options={})
    tariff = coord._build_tariff()
    # Default VAT=22.0, margin=0.0
    assert tariff(0.05) == pytest.approx(0.061)


def test_last_nps_error_starts_none(hass):
    client = Mock()
    coord = EstfeedCoordinator(hass=hass, client=client, slug="home", options={})
    assert coord.last_nps_error is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_coordinator.py -v -k "cost_streams or build_tariff or last_nps"`
Expected: FAIL — `cost_streams_for` / `_build_tariff` / `last_nps_error` not yet on the coordinator.

- [ ] **Step 3: Modify `coordinator.py`**

In `custom_components/estfeed/coordinator.py`:

(a) Update imports near the top of `coordinator.py`. The existing import block already imports most names; add the new ones:

```python
from homeassistant.helpers.aiohttp_client import async_get_clientsession
```

Extend the existing `from .const import (...)` block to also include `CONF_MARGIN_EUR_PER_KWH`, `CONF_VAT_PERCENT`, `DEFAULT_MARGIN_EUR_PER_KWH`, `DEFAULT_VAT_PERCENT`.

Add new imports:

```python
from .nps import EleringNpsClient, NpsError
from .pricing import make_tariff
```

Extend the existing `from .statistics import (...)` block to also include `CostStream` and `async_write_cost_statistics`.

The existing pattern `meter.commodity_type.value == "ELECTRICITY"` is fine — no new `CommodityType` import is needed.

(b) Extend `__init__`:

Inside `EstfeedCoordinator.__init__`, after the existing attribute assignments, add:

```python
        # NPS price client + last-fetch-error for diagnostics
        self._nps = EleringNpsClient(async_get_clientsession(hass))
        self.last_nps_error: str | None = None
```

(c) Add new methods to the coordinator class, placed near `streams_for`:

```python
    def cost_streams_for(self, meter: MeteringPoint) -> list[CostStream]:
        """Cost + compensation streams for one meter; empty list for gas."""
        if meter.commodity_type.value != "ELECTRICITY":
            return []
        suffix = eic_suffix(meter.eic)
        currency = self.hass.config.currency or "EUR"
        return [
            CostStream(
                statistic_id=f"{DOMAIN}:{self.slug}_cost_{suffix}",
                name=f"{self.slug} cost ({meter.eic})",
                unit=currency,
                kind=Kind.CONSUMPTION,
            ),
            CostStream(
                statistic_id=f"{DOMAIN}:{self.slug}_compensation_{suffix}",
                name=f"{self.slug} compensation ({meter.eic})",
                unit=currency,
                kind=Kind.PRODUCTION,
            ),
        ]

    def _build_tariff(self):
        """Construct the curried tariff function from current options."""
        vat = float(self.options.get(CONF_VAT_PERCENT, DEFAULT_VAT_PERCENT))
        margin = float(self.options.get(CONF_MARGIN_EUR_PER_KWH, DEFAULT_MARGIN_EUR_PER_KWH))
        return make_tariff(vat, margin)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_coordinator.py -v -k "cost_streams or build_tariff or last_nps"`
Expected: PASS — all 5 new tests green. Existing coordinator tests still pass.

- [ ] **Step 5: Commit**

```bash
git add custom_components/estfeed/coordinator.py tests/test_coordinator.py
git commit -m "Add cost_streams_for, _build_tariff, last_nps_error to coordinator"
```

---

## Task 7: Coordinator — cost branch in `_fetch_meter_window` with `cost_only` param

**Files:**
- Modify: `custom_components/estfeed/coordinator.py`
- Test: `tests/test_coordinator.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_coordinator.py`:

```python
from unittest.mock import AsyncMock

from custom_components.estfeed.api import AccountingInterval, MeterData


def _hour_interval(hour: int, consumption: float) -> AccountingInterval:
    return AccountingInterval(
        period_start=datetime(2026, 5, 21, hour, tzinfo=UTC),
        consumption_kwh=consumption,
        production_kwh=0.0,
        consumption_m3=None,
        production_m3=None,
    )


@pytest.mark.asyncio
async def test_fetch_meter_window_writes_cost_and_compensation_for_electricity(hass):
    client = Mock()
    client.get_metering_data = AsyncMock(
        return_value=[
            MeterData(
                eic="38ZEE-00720089-N",
                intervals=[_hour_interval(10, 2.0), _hour_interval(11, 3.0)],
                error=None,
            )
        ]
    )
    coord = EstfeedCoordinator(hass=hass, client=client, slug="home", options={})
    coord.meters = [_electric_meter()]
    # Stub NPS to return identity-like prices so we can predict cost.
    coord._nps.async_get_prices = AsyncMock(
        return_value={
            datetime(2026, 5, 21, 10, tzinfo=UTC): 0.05,
            datetime(2026, 5, 21, 11, tzinfo=UTC): 0.05,
        }
    )
    with patch(
        "custom_components.estfeed.coordinator.async_write_meter_statistics",
        new=AsyncMock(return_value=5.0),
    ) as mock_energy, patch(
        "custom_components.estfeed.coordinator.async_write_cost_statistics",
        new=AsyncMock(return_value=0.305),
    ) as mock_cost:
        await coord._fetch_meter_window(
            datetime(2026, 5, 21, 10, tzinfo=UTC),
            datetime(2026, 5, 21, 12, tzinfo=UTC),
            write_stats=True,
            force_start=True,
        )
    # 2 energy streams (consumption + production) and 2 cost streams (cost + compensation)
    assert mock_energy.await_count == 2
    assert mock_cost.await_count == 2
    cost_ids = {call.args[1].statistic_id for call in mock_cost.await_args_list}
    assert cost_ids == {"estfeed:home_cost_089n", "estfeed:home_compensation_089n"}


@pytest.mark.asyncio
async def test_fetch_meter_window_skips_cost_for_gas_meter(hass):
    client = Mock()
    client.get_metering_data = AsyncMock(
        return_value=[MeterData(eic="38ZEE-00720099-G", intervals=[], error=None)]
    )
    coord = EstfeedCoordinator(hass=hass, client=client, slug="home", options={})
    coord.meters = [_gas_meter()]
    coord._nps.async_get_prices = AsyncMock(return_value={})
    with patch(
        "custom_components.estfeed.coordinator.async_write_cost_statistics",
        new=AsyncMock(),
    ) as mock_cost:
        await coord._fetch_meter_window(
            datetime(2026, 5, 21, 10, tzinfo=UTC),
            datetime(2026, 5, 21, 12, tzinfo=UTC),
            write_stats=True,
            force_start=True,
        )
    mock_cost.assert_not_called()
    coord._nps.async_get_prices.assert_not_called()


@pytest.mark.asyncio
async def test_fetch_meter_window_records_nps_error_on_failure(hass):
    client = Mock()
    client.get_metering_data = AsyncMock(
        return_value=[
            MeterData(
                eic="38ZEE-00720089-N",
                intervals=[_hour_interval(10, 2.0)],
                error=None,
            )
        ]
    )
    coord = EstfeedCoordinator(hass=hass, client=client, slug="home", options={})
    coord.meters = [_electric_meter()]
    coord._nps.async_get_prices = AsyncMock(side_effect=NpsError("boom"))
    with patch(
        "custom_components.estfeed.coordinator.async_write_meter_statistics",
        new=AsyncMock(return_value=2.0),
    ) as mock_energy, patch(
        "custom_components.estfeed.coordinator.async_write_cost_statistics",
        new=AsyncMock(),
    ) as mock_cost:
        await coord._fetch_meter_window(
            datetime(2026, 5, 21, 10, tzinfo=UTC),
            datetime(2026, 5, 21, 12, tzinfo=UTC),
            write_stats=True,
            force_start=True,
        )
    # Energy stats still written; cost skipped; error captured.
    assert mock_energy.await_count == 2
    mock_cost.assert_not_called()
    assert coord.last_nps_error is not None
    assert "boom" in coord.last_nps_error


@pytest.mark.asyncio
async def test_fetch_meter_window_cost_only_skips_energy_writes(hass):
    client = Mock()
    client.get_metering_data = AsyncMock(
        return_value=[
            MeterData(
                eic="38ZEE-00720089-N",
                intervals=[_hour_interval(10, 2.0)],
                error=None,
            )
        ]
    )
    coord = EstfeedCoordinator(hass=hass, client=client, slug="home", options={})
    coord.meters = [_electric_meter()]
    coord._nps.async_get_prices = AsyncMock(
        return_value={datetime(2026, 5, 21, 10, tzinfo=UTC): 0.05}
    )
    with patch(
        "custom_components.estfeed.coordinator.async_write_meter_statistics",
        new=AsyncMock(),
    ) as mock_energy, patch(
        "custom_components.estfeed.coordinator.async_write_cost_statistics",
        new=AsyncMock(return_value=0.1),
    ) as mock_cost:
        await coord._fetch_meter_window(
            datetime(2026, 5, 21, 10, tzinfo=UTC),
            datetime(2026, 5, 21, 12, tzinfo=UTC),
            write_stats=True,
            force_start=True,
            cost_only=True,
        )
    mock_energy.assert_not_called()
    assert mock_cost.await_count == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_coordinator.py -v -k "cost_and_comp or skips_cost_for_gas or nps_error or cost_only"`
Expected: FAIL — `_fetch_meter_window` doesn't yet have the cost branch or `cost_only` param.

- [ ] **Step 3: Modify `_fetch_meter_window` in `coordinator.py`**

Replace the existing `_fetch_meter_window` body with the version below. The changes are:
- New parameter `cost_only: bool = False`
- Energy-stat writes guarded by `not cost_only`
- After the energy writes, a new "cost branch" fetches prices once per chunk and writes both cost streams.

```python
    async def _fetch_meter_window(
        self,
        meter: MeteringPoint,
        start: datetime,
        end: datetime,
        *,
        write_stats: bool,
        force_start: bool,
        cost_only: bool = False,
    ) -> None:
        streams = self.streams_for(meter)
        cost_streams = self.cost_streams_for(meter)

        per_stream_start: dict[str, datetime | None] = {}
        if force_start:
            for stream in streams:
                per_stream_start[stream.statistic_id] = None
        else:
            for stream in streams:
                per_stream_start[stream.statistic_id] = await self._latest_seen_for_stream(stream)

        seen = [s for s in per_stream_start.values() if s is not None]
        fetch_start = start if force_start or not seen else max(start, min(seen))

        prior_sums: dict[str, float] = {}
        if write_stats and not cost_only:
            for stream in streams:
                prior_sums[stream.statistic_id] = (
                    0.0 if force_start else await self._prior_sum_for_stream(stream)
                )

        # Per-cost-stream resume points and prior sums (only for electricity meters).
        cost_per_stream_start: dict[str, datetime | None] = {}
        cost_prior_sums: dict[str, float] = {}
        if write_stats and cost_streams:
            for cstream in cost_streams:
                # Re-use the StatisticStream-shaped latest_seen logic by faking a
                # StatisticStream with the cost stream's id.
                fake = StatisticStream(
                    statistic_id=cstream.statistic_id,
                    name=cstream.name,
                    unit=cstream.unit,
                    kind=cstream.kind,
                )
                cost_per_stream_start[cstream.statistic_id] = (
                    None if force_start else await self._latest_seen_for_stream(fake)
                )
                cost_prior_sums[cstream.statistic_id] = (
                    0.0 if force_start else await self._prior_sum_for_stream(fake)
                )

        tariff = self._build_tariff() if cost_streams else None

        cursor = fetch_start
        while cursor < end:
            chunk_end = min(cursor + timedelta(days=MAX_DAYS_PER_REQUEST), end)
            results = await self._client.get_metering_data(
                cursor, chunk_end, self.resolution, eics=[meter.eic]
            )

            # Fetch prices for this chunk once if any cost stream needs them.
            prices: dict[datetime, float] | None = None
            if write_stats and cost_streams:
                try:
                    prices = await self._nps.async_get_prices(cursor, chunk_end)
                    self.last_nps_error = None
                except NpsError as err:
                    self.last_nps_error = str(err)
                    _LOGGER.warning(
                        "NPS fetch failed for %s..%s: %s", cursor, chunk_end, err
                    )
                    prices = None

            for md in results:
                if md.error is not None:
                    self.last_meter_errors[md.eic] = md.error.code
                    _LOGGER.warning(
                        "Estfeed returned error for meter %s: %s (traceId=%s)",
                        md.eic, md.error.code, md.error.trace_id,
                    )
                    continue
                self.last_meter_errors.pop(md.eic, None)

                # Energy streams (consumption / production)
                for stream in streams:
                    threshold = per_stream_start[stream.statistic_id]
                    relevant = (
                        md.intervals
                        if threshold is None
                        else [i for i in md.intervals if i.period_start >= threshold]
                    )
                    if write_stats and not cost_only:
                        prior_sums[stream.statistic_id] = await async_write_meter_statistics(
                            self.hass,
                            stream,
                            relevant,
                            prior_sum=prior_sums[stream.statistic_id],
                        )
                    self._update_cache(meter.eic, stream.kind, relevant)

                # Cost streams (only if write_stats and prices available)
                if write_stats and cost_streams and prices is not None and tariff is not None:
                    for cstream in cost_streams:
                        threshold = cost_per_stream_start[cstream.statistic_id]
                        relevant = (
                            md.intervals
                            if threshold is None
                            else [i for i in md.intervals if i.period_start >= threshold]
                        )
                        cost_prior_sums[cstream.statistic_id] = await async_write_cost_statistics(
                            self.hass,
                            cstream,
                            relevant,
                            prices,
                            tariff,
                            prior_sum=cost_prior_sums[cstream.statistic_id],
                        )

            cursor = chunk_end
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_coordinator.py -v`
Expected: PASS — all 4 new tests green; existing tests still pass.

- [ ] **Step 5: Commit**

```bash
git add custom_components/estfeed/coordinator.py tests/test_coordinator.py
git commit -m "Add cost branch and cost_only path to _fetch_meter_window"
```

---

## Task 8: Coordinator — `async_rebuild_cost`

**Files:**
- Modify: `custom_components/estfeed/coordinator.py`
- Test: `tests/test_coordinator.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_coordinator.py`:

```python
@pytest.mark.asyncio
async def test_async_rebuild_cost_calls_fetch_with_cost_only_force_start(hass):
    client = Mock()
    coord = EstfeedCoordinator(
        hass=hass,
        client=client,
        slug="home",
        options={CONF_BACKFILL_MONTHS: 6},
    )
    coord.meters = [_electric_meter()]
    with patch.object(
        coord, "_fetch_meter_window", new=AsyncMock()
    ) as mock_fetch:
        await coord.async_rebuild_cost()
    mock_fetch.assert_awaited_once()
    kwargs = mock_fetch.await_args.kwargs
    assert kwargs["cost_only"] is True
    assert kwargs["force_start"] is True
    assert kwargs["write_stats"] is True
    # Window spans backfill_months * 30 days
    span_days = (mock_fetch.await_args.args[2] - mock_fetch.await_args.args[1]).days
    assert span_days >= 6 * 30 - 1
```

(Note: `CONF_BACKFILL_MONTHS` is already imported in the test file from earlier tasks.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_coordinator.py::test_async_rebuild_cost_calls_fetch_with_cost_only_force_start -v`
Expected: FAIL — `AttributeError: 'EstfeedCoordinator' object has no attribute 'async_rebuild_cost'`

- [ ] **Step 3: Add `async_rebuild_cost` to `coordinator.py`**

Add to `EstfeedCoordinator`, near `async_initial_backfill`:

```python
    async def async_rebuild_cost(self) -> None:
        """Recompute cost/compensation statistics over the configured window.

        Used after options changes (VAT/margin) and on first run for entries
        upgraded from a pre-cost version. Does not touch energy statistics.
        """
        if not self.meters:
            return
        end = datetime.now(tz=UTC)
        start = end - timedelta(days=self.backfill_months * 30)
        for meter in self.meters:
            await self._fetch_meter_window(
                meter, start, end,
                write_stats=True, force_start=True, cost_only=True,
            )
        self.async_update_listeners()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_coordinator.py::test_async_rebuild_cost_calls_fetch_with_cost_only_force_start -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add custom_components/estfeed/coordinator.py tests/test_coordinator.py
git commit -m "Add async_rebuild_cost for VAT/margin rebuilds"
```

---

## Task 9: Config flow — VAT% and margin options fields

**Files:**
- Modify: `custom_components/estfeed/config_flow.py`
- Test: `tests/test_config_flow.py`

- [ ] **Step 1: Write the failing test**

Read the existing `tests/test_config_flow.py` first to match its style, then append:

```python
from custom_components.estfeed.const import (
    CONF_MARGIN_EUR_PER_KWH,
    CONF_VAT_PERCENT,
    DEFAULT_MARGIN_EUR_PER_KWH,
    DEFAULT_VAT_PERCENT,
)


@pytest.mark.asyncio
async def test_options_flow_persists_vat_and_margin(hass):
    """Options form accepts VAT% and margin and stores them in entry.options."""
    # Build a config entry as the existing tests do (adapt to the pattern in
    # test_config_flow.py — likely uses MockConfigEntry from
    # pytest_homeassistant_custom_component).
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    from custom_components.estfeed.const import DOMAIN

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"client_id": "x", "client_secret": "y", "friendly_name": "Home"},
        options={},
        unique_id="x",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "form"

    submission = {
        "resolution": "one_hour",
        "backfill_months": 12,
        CONF_VAT_PERCENT: 24.0,
        CONF_MARGIN_EUR_PER_KWH: 0.015,
    }
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], submission
    )
    assert result["type"] == "create_entry"
    assert entry.options[CONF_VAT_PERCENT] == 24.0
    assert entry.options[CONF_MARGIN_EUR_PER_KWH] == 0.015


@pytest.mark.asyncio
async def test_options_flow_defaults_to_22_percent_vat_and_zero_margin(hass):
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    from custom_components.estfeed.const import DOMAIN

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"client_id": "x", "client_secret": "y", "friendly_name": "Home"},
        options={},
        unique_id="x",
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    # Schema defaults reflect DEFAULT_VAT_PERCENT and DEFAULT_MARGIN_EUR_PER_KWH
    schema = result["data_schema"]
    rendered = {k.schema if hasattr(k, "schema") else k: k.default()
                for k in schema.schema.keys() if hasattr(k, "default")}
    assert rendered[CONF_VAT_PERCENT] == DEFAULT_VAT_PERCENT
    assert rendered[CONF_MARGIN_EUR_PER_KWH] == DEFAULT_MARGIN_EUR_PER_KWH
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_config_flow.py -v -k "vat or margin"`
Expected: FAIL — options schema doesn't include the new fields.

- [ ] **Step 3: Add fields to `config_flow.py`**

In `EstfeedOptionsFlow.async_step_init`, extend imports at top:

```python
from .const import (
    CONF_BACKFILL_MONTHS,
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_FRIENDLY_NAME,
    CONF_MARGIN_EUR_PER_KWH,
    CONF_RESOLUTION,
    CONF_VAT_PERCENT,
    DEFAULT_BACKFILL_MONTHS,
    DEFAULT_FRIENDLY_NAME,
    DEFAULT_MARGIN_EUR_PER_KWH,
    DEFAULT_VAT_PERCENT,
    DOMAIN,
    MAX_BACKFILL_MONTHS,
    MIN_BACKFILL_MONTHS,
    Resolution,
)
```

Then replace the schema in `async_step_init`:

```python
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_RESOLUTION,
                    default=current.get(CONF_RESOLUTION, Resolution.HOUR.value),
                ): vol.In([Resolution.HOUR.value, Resolution.QUARTER_HOUR.value]),
                vol.Required(
                    CONF_BACKFILL_MONTHS,
                    default=current.get(CONF_BACKFILL_MONTHS, DEFAULT_BACKFILL_MONTHS),
                ): vol.All(int, vol.Range(min=MIN_BACKFILL_MONTHS, max=MAX_BACKFILL_MONTHS)),
                vol.Required(
                    CONF_VAT_PERCENT,
                    default=current.get(CONF_VAT_PERCENT, DEFAULT_VAT_PERCENT),
                ): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=100.0)),
                vol.Required(
                    CONF_MARGIN_EUR_PER_KWH,
                    default=current.get(CONF_MARGIN_EUR_PER_KWH, DEFAULT_MARGIN_EUR_PER_KWH),
                ): vol.All(vol.Coerce(float), vol.Range(min=-1.0, max=1.0)),
            }
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_config_flow.py -v`
Expected: PASS — new tests green, old tests still green.

- [ ] **Step 5: Commit**

```bash
git add custom_components/estfeed/config_flow.py tests/test_config_flow.py
git commit -m "Add VAT% and margin to options flow"
```

---

## Task 10: `__init__.py` — auto-rebuild on options change + upgrade-time cost-initial-fill

**Files:**
- Modify: `custom_components/estfeed/__init__.py`
- Test: `tests/test_init.py` (create if missing — check first)

No new test file is added for this task — `tests/test_init.py` does not exist in this codebase, and the rebuild behavior is covered at the unit level by `test_async_rebuild_cost_calls_fetch_with_cost_only_force_start` in Task 8. The wiring tested here is a one-line dispatch from `_async_options_updated` to `coordinator.async_rebuild_cost`, which is exercised end-to-end during manual verification (Task 14 / out-of-band).

- [ ] **Step 1: Modify `__init__.py`**

(a) Extend `_async_options_updated` to detect VAT/margin changes and trigger a rebuild:

```python
async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    coordinator: EstfeedCoordinator = hass.data[DOMAIN][entry.entry_id]
    old_options = dict(coordinator.options)
    new_options = {**entry.data, **entry.options}
    coordinator.options = new_options

    # If VAT or margin changed, schedule a cost-only rebuild over the
    # configured backfill window. The NPS cache absorbs repeated price
    # fetches so even an 84-month rebuild is cheap.
    from .const import CONF_MARGIN_EUR_PER_KWH, CONF_VAT_PERCENT

    if (
        old_options.get(CONF_VAT_PERCENT) != new_options.get(CONF_VAT_PERCENT)
        or old_options.get(CONF_MARGIN_EUR_PER_KWH)
        != new_options.get(CONF_MARGIN_EUR_PER_KWH)
    ):
        hass.async_create_background_task(
            coordinator.async_rebuild_cost(), name=f"{DOMAIN}_cost_rebuild"
        )
```

(b) Add an upgrade-time cost-initial-fill detection inside `async_setup_entry`. After the existing `needs_backfill` block, add a parallel `needs_cost_backfill` probe:

```python
    needs_cost_backfill = False
    for meter in meters:
        if meter.commodity_type.value != "ELECTRICITY":
            continue
        for cstream in coordinator.cost_streams_for(meter):
            existing = await recorder.async_add_executor_job(
                get_last_statistics, hass, 1, cstream.statistic_id, True, {"sum"}
            )
            if not existing.get(cstream.statistic_id):
                needs_cost_backfill = True
                break
        if needs_cost_backfill:
            break
```

After the existing `if needs_backfill: …` / `else: …` block, add:

```python
    if needs_cost_backfill and not needs_backfill:
        # Energy stats already exist; only cost is missing — do a cost-only fill
        # that doesn't disturb the (correct) energy series.
        hass.async_create_background_task(
            coordinator.async_rebuild_cost(), name=f"{DOMAIN}_cost_initial_fill"
        )
```

(If `needs_backfill` is true, the full initial backfill already covers cost via the new branch in `_fetch_meter_window` — no separate cost fill needed.)

- [ ] **Step 2: Run the full suite to verify no regression**

Run: `pytest tests/ -x -q`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add custom_components/estfeed/__init__.py
git commit -m "Auto-rebuild cost on options change; cost fill on upgrade"
```

---

## Task 11: Diagnostics — surface cost stream IDs, NPS error, cache size

**Files:**
- Modify: `custom_components/estfeed/diagnostics.py`
- Test: `tests/test_diagnostics.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_diagnostics.py` (match the style of the existing tests there):

```python
@pytest.mark.asyncio
async def test_diagnostics_includes_cost_stream_ids_and_nps_state(hass):
    # Adapt fixture setup from existing tests in this file.
    # The assertion focuses on the new keys, regardless of fixture style.
    diagnostics = await _get_diagnostics_for_a_configured_entry(hass)  # helper from existing tests

    coord_block = diagnostics["coordinator"]
    assert "cost_stream_ids" in coord_block
    assert isinstance(coord_block["cost_stream_ids"], list)
    assert "last_nps_error" in coord_block
    assert "nps_cache_size" in coord_block
    assert isinstance(coord_block["nps_cache_size"], int)
```

(Adapt `_get_diagnostics_for_a_configured_entry` to your existing helper. If the file doesn't have one, write a direct call to `async_get_config_entry_diagnostics(hass, entry)`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_diagnostics.py -v`
Expected: FAIL — new keys absent.

- [ ] **Step 3: Modify `diagnostics.py`**

Add to the `"coordinator": { … }` block (after the `cumulative_since_reset` entry):

```python
            "cost_stream_ids": [
                cstream.statistic_id
                for m in coordinator.meters
                for cstream in coordinator.cost_streams_for(m)
            ],
            "last_nps_error": coordinator.last_nps_error,
            "nps_cache_size": coordinator._nps.cache_size,
```

Note: `_nps` is intentionally private on the coordinator but exposed here for diagnostics. If your project lint rejects `_nps` from outside the class, add a `nps_cache_size` property to the coordinator and use that instead:

```python
# In coordinator.py — optional alternative:
    @property
    def nps_cache_size(self) -> int:
        return self._nps.cache_size
```

Then in `diagnostics.py`: `"nps_cache_size": coordinator.nps_cache_size`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_diagnostics.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add custom_components/estfeed/diagnostics.py custom_components/estfeed/coordinator.py tests/test_diagnostics.py
git commit -m "Surface cost stream IDs and NPS state in diagnostics"
```

---

## Task 12: Translations — add labels for new options

**Files:**
- Modify: `custom_components/estfeed/translations/en.json`
- Modify: `custom_components/estfeed/translations/et.json`

- [ ] **Step 1: Edit `en.json`**

In the `options.step.init.data` block, add:

```json
"vat_percent": "VAT percentage (%)",
"margin_eur_per_kwh": "Margin (EUR/kWh, can be negative)"
```

- [ ] **Step 2: Edit `et.json`**

In the corresponding block, add:

```json
"vat_percent": "Käibemaks (%)",
"margin_eur_per_kwh": "Marginaal (EUR/kWh, võib olla negatiivne)"
```

- [ ] **Step 3: Verify JSON is valid**

Run: `python -c 'import json; json.load(open("custom_components/estfeed/translations/en.json")); json.load(open("custom_components/estfeed/translations/et.json"))'`
Expected: no output, exit code 0.

- [ ] **Step 4: Commit**

```bash
git add custom_components/estfeed/translations/en.json custom_components/estfeed/translations/et.json
git commit -m "Add translations for VAT and margin options"
```

---

## Task 13: README — document cost statistic + Energy dashboard wiring

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Locate the right section**

Read `README.md` and find the section that documents external statistics
(usually near the consumption/production stream description).

- [ ] **Step 2: Add a new subsection documenting cost statistics**

Insert a subsection titled "Cost & compensation statistics" with content along these lines (write actual prose, not placeholders):

```markdown
### Cost & compensation statistics

For electricity meters, the integration also publishes two derived
external statistics in your configured HA currency (defaults to EUR):

- `estfeed:<slug>_cost_<suffix>` — cumulative cost of consumed energy
- `estfeed:<slug>_compensation_<suffix>` — cumulative compensation for produced energy

Both are computed by multiplying each hour's consumption/production by
the matching Nord Pool spot price for the EE bidding zone, then applying
a configurable tariff: `spot × (1 + VAT%/100) + margin`. Defaults:
VAT 22 %, margin 0 €/kWh.

To use these in the Home Assistant Energy dashboard:

1. Go to **Settings → Dashboards → Energy → Grid consumption**.
2. Pick `estfeed:<slug>_consumption_<suffix>` as the consumed-energy
   statistic.
3. Under "Use an entity tracking the total costs", select
   `estfeed:<slug>_cost_<suffix>`.
4. Repeat for "Return to grid" → `estfeed:<slug>_production_<suffix>` and
   `estfeed:<slug>_compensation_<suffix>`.

Adjusting VAT or margin in the integration options automatically rebuilds
the cost/compensation history over the configured backfill window so the
Energy dashboard reflects the new tariff retroactively.

Gas meters do not publish cost statistics (no NPS gas price source).
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "Document cost/compensation statistics and Energy dashboard wiring"
```

---

## Task 14: Full suite + lint + format

**Files:** (none — verification only)

- [ ] **Step 1: Run the full test suite**

Run: `pytest tests/ -v`
Expected: All tests pass.

- [ ] **Step 2: Run ruff format and check**

Run: `ruff format custom_components/ tests/ && ruff check custom_components/ tests/`
Expected: clean.

- [ ] **Step 3: Run mypy**

Run: `mypy custom_components/estfeed/`
Expected: clean (or matching pre-existing baseline).

- [ ] **Step 4: Commit any format-only changes**

```bash
git status --short
# if any files changed:
git add -A
git commit -m "Apply ruff format after cost statistic implementation"
```

- [ ] **Step 5: Final verification**

Run: `git log --oneline | head -20`
Confirm the commit series tells a coherent story (pricing → NPS client → constants → CostStream → coordinator → options → init → diagnostics → translations → README → format).

---

## Out-of-band: manual verification on a live HA instance

These steps require a running HA with the integration loaded. They are
NOT part of the CI/test pipeline but should be performed before
considering the feature shipped.

1. Restart HA with the new integration version installed.
2. Open **Developer Tools → Statistics** and search for `estfeed:` —
   confirm `_cost_*` and `_compensation_*` statistic IDs are present
   with non-zero last sums.
3. Open the entry's **Diagnostics** download — confirm
   `coordinator.cost_stream_ids` lists the new IDs and
   `coordinator.nps_cache_size` > 0.
4. Go to the Energy dashboard config — confirm the new statistic IDs
   appear in the `stat_cost` / `stat_compensation` dropdowns. Wire them
   up.
5. After one hourly tick, check the Energy dashboard's cost column for
   the current day — values should look reasonable given recent
   consumption × current EE spot price.
6. Change VAT to 24 % in integration options. Within a minute, confirm
   the cost graph for the past window updates to reflect the new rate.
