"""Pure pricing helpers: tariff math and cost row construction."""

from __future__ import annotations

from collections.abc import Callable, Container
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, tzinfo
from typing import Any

from homeassistant.components.recorder.models import StatisticData

from .api import AccountingInterval
from .const import Kind

# A tariff prices one hour: (spot EUR/kWh, top-of-hour UTC) -> EUR/kWh incl. VAT.
Tariff = Callable[[float, datetime], float]


@dataclass(frozen=True)
class GridTariff:
    """Time-of-use grid transfer fee (EUR/kWh excl. VAT) added to the spot price.

    Night runs from ``night_start_hour`` to ``night_end_hour`` in the local
    ``tz`` and may wrap past midnight (22 -> 7). Weekends and public holidays
    count as night when the respective flag is on; ``holidays`` is any container
    of ``date`` objects, e.g. ``holidays.country_holidays("EE")``. With both
    rates at zero the grid fee disappears and the tariff reduces to
    ``spot x VAT + margin``, which keeps the pre-0.3 behaviour.
    """

    day_eur_per_kwh: float = 0.0
    night_eur_per_kwh: float = 0.0
    night_start_hour: int = 22
    night_end_hour: int = 7
    night_on_weekends: bool = True
    night_on_holidays: bool = True
    tz: tzinfo = UTC
    holidays: Container[date] = field(default_factory=frozenset)

    def is_night(self, hour_utc: datetime) -> bool:
        """True when the hour starting at ``hour_utc`` is billed at the night rate."""
        local = hour_utc.astimezone(self.tz)
        if self.night_on_weekends and local.weekday() >= 5:
            return True
        if self.night_on_holidays and local.date() in self.holidays:
            return True
        hour, start, end = local.hour, self.night_start_hour, self.night_end_hour
        if start == end:
            return False
        if start < end:
            return start <= hour < end
        return hour >= start or hour < end

    def rate(self, hour_utc: datetime) -> float:
        """Grid fee for the hour starting at ``hour_utc``, EUR/kWh excl. VAT."""
        return self.night_eur_per_kwh if self.is_night(hour_utc) else self.day_eur_per_kwh


def apply_tariff(
    spot_eur_per_kwh: float,
    vat_percent: float,
    margin_eur_per_kwh: float,
    grid_eur_per_kwh: float = 0.0,
    fees_eur_per_kwh: float = 0.0,
) -> float:
    """Turn a Nord Pool spot price into the billed price per kWh.

    Formula: ``(spot + grid + fees) * (1 + vat_percent/100) + margin``. Grid
    transfer and the per-kWh fees (renewable levy, excise, ...) are quoted
    excl. VAT on Estonian invoices, so they are taxed together with the spot;
    the supplier margin is usually quoted incl. VAT and lands after it.
    Negative margins are allowed (promotional discounts); negative spots are
    passed through (NPS occasionally settles negative).
    """
    return (spot_eur_per_kwh + grid_eur_per_kwh + fees_eur_per_kwh) * (
        1 + vat_percent / 100
    ) + margin_eur_per_kwh


def make_tariff(
    vat_percent: float,
    margin_eur_per_kwh: float,
    fees_eur_per_kwh: float = 0.0,
    grid: GridTariff | None = None,
) -> Tariff:
    """Curry apply_tariff so cost builders can call ``tariff(spot, hour)``."""
    grid_tariff = grid if grid is not None else GridTariff()

    def tariff(spot_eur_per_kwh: float, hour_utc: datetime) -> float:
        return apply_tariff(
            spot_eur_per_kwh,
            vat_percent,
            margin_eur_per_kwh,
            grid_eur_per_kwh=grid_tariff.rate(hour_utc),
            fees_eur_per_kwh=fees_eur_per_kwh,
        )

    return tariff


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
    tariff: Tariff,
    prior_sum: float,
) -> list[StatisticData]:
    """Build cumulative-sum cost rows from raw intervals and an hourly price map.

    Buckets intervals into hours (matching ``statistics.compute_statistic_rows``),
    multiplies each hour's summed kWh by ``tariff(prices[hour], hour)``, and
    produces a running cumulative-sum series in EUR. Skips hours where the price
    is missing (NPS gap or future hour). Rounds to 4 decimal places (€0.0001).
    """
    hourly: dict[Any, float] = {}
    for ival in intervals:
        value = _interval_value(ival, kind)
        if value is None:
            continue
        bucket = ival.period_start.replace(minute=0, second=0, microsecond=0)
        hourly[bucket] = hourly.get(bucket, 0.0) + float(value)

    return compute_cost_rows_from_hourly(hourly, prices, tariff, prior_sum)


def compute_cost_rows_from_hourly(
    hourly_energy: dict[datetime, float],
    prices: dict[datetime, float],
    tariff: Tariff,
    prior_sum: float,
) -> list[StatisticData]:
    """Build cumulative-sum cost rows from a per-hour energy map.

    ``hourly_energy`` maps top-of-hour UTC to the kWh consumed/produced that
    hour. Each hour's energy is multiplied by ``tariff(prices[hour], hour)`` and
    accumulated into a running EUR series. Hours with no matching price (NPS
    gap or future hour) are skipped. Rounds to 4 decimals (€0.0001).

    Deriving cost from stored hourly energy (rather than re-fetched API
    intervals) keeps the cost statistic exactly consistent with the published
    consumption/production statistics it is meant to price.
    """
    rows: list[StatisticData] = []
    running = prior_sum
    for start in sorted(hourly_energy):
        price = prices.get(start)
        if price is None:
            continue
        cost = round(hourly_energy[start] * tariff(price, start), 4)
        running = round(running + cost, 4)
        rows.append({"start": start, "state": running, "sum": running})
    return rows
