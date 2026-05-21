"""Pure pricing helpers: tariff math and cost row construction."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from homeassistant.components.recorder.models import StatisticData

from .api import AccountingInterval
from .const import Kind


def apply_tariff(
    spot_eur_per_kwh: float,
    vat_percent: float,
    margin_eur_per_kwh: float,
) -> float:
    """Apply VAT and a fixed per-kWh margin to a Nord Pool spot price.

    Formula: ``spot * (1 + vat_percent/100) + margin``. Negative margins are
    allowed (promotional discounts); negative spots are passed through (NPS
    occasionally settles negative).
    """
    return spot_eur_per_kwh * (1 + vat_percent / 100) + margin_eur_per_kwh


def make_tariff(
    vat_percent: float,
    margin_eur_per_kwh: float,
) -> Callable[[float], float]:
    """Curry apply_tariff so compute_cost_rows can call ``tariff(spot)``."""
    return lambda spot: apply_tariff(spot, vat_percent, margin_eur_per_kwh)


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
