"""Pure pricing helpers: tariff math and cost row construction."""

from __future__ import annotations

from collections.abc import Callable, Container, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, tzinfo
from typing import Any

from homeassistant.components.recorder.models import StatisticData

from .api import AccountingInterval, interval_value
from .const import Kind

# A tariff prices one hour: (spot EUR/kWh, top-of-hour UTC) -> EUR/kWh incl. VAT.
Tariff = Callable[[float, datetime], float]
# A fixed charge for one hour regardless of energy: top-of-hour UTC -> EUR.
HourlyFee = Callable[[datetime], float]


def hours_in_local_month(hour_utc: datetime, tz: tzinfo) -> float:
    """Real hours in the local calendar month containing ``hour_utc`` (DST aware)."""
    local = hour_utc.astimezone(tz)
    month_start = datetime(local.year, local.month, 1, tzinfo=tz)
    next_month = (month_start + timedelta(days=32)).replace(day=1)
    return (next_month.astimezone(UTC) - month_start.astimezone(UTC)).total_seconds() / 3600


def monthly_fee_per_hour(monthly_fee_at: Callable[[datetime], float], tz: tzinfo) -> HourlyFee:
    """Spread a monthly charge evenly over the hours of its local month.

    ``monthly_fee_at(hour)`` returns the monthly charge in force for that hour
    (it may differ between tariff periods). Summed over a whole month the
    shares add up to exactly that month's charge.
    """

    def fee(hour_utc: datetime) -> float:
        return monthly_fee_at(hour_utc) / hours_in_local_month(hour_utc, tz)

    return fee


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
    use_spot: bool = True,
) -> Tariff:
    """Curry apply_tariff so cost builders can call ``tariff(spot, hour)``.

    With ``use_spot`` off the spot price is ignored (treated as 0), so a
    fixed-price contract is modelled as ``(grid + fees) x VAT + margin`` where
    ``margin`` carries the fixed energy price incl. VAT.
    """
    grid_tariff = grid if grid is not None else GridTariff()

    def tariff(spot_eur_per_kwh: float, hour_utc: datetime) -> float:
        return apply_tariff(
            spot_eur_per_kwh if use_spot else 0.0,
            vat_percent,
            margin_eur_per_kwh,
            grid_eur_per_kwh=grid_tariff.rate(hour_utc),
            fees_eur_per_kwh=fees_eur_per_kwh,
        )

    return tariff


def scheduled_tariff(
    schedule: Sequence[tuple[date, Tariff]], current: Tariff, tz: tzinfo
) -> Tariff:
    """Price each hour with the tariff that was in force on its local date.

    ``schedule`` holds closed periods as ``(until, tariff)``: a period covers
    local dates before ``until`` and after the previous period's ``until``.
    Hours on or after the last ``until`` use ``current``. This keeps history
    priced at the contract it was billed under when the tariff later changes.
    """
    ordered = sorted(schedule, key=lambda item: item[0])

    def tariff(spot_eur_per_kwh: float, hour_utc: datetime) -> float:
        local_day = hour_utc.astimezone(tz).date()
        for until, period_tariff in ordered:
            if local_day < until:
                return period_tariff(spot_eur_per_kwh, hour_utc)
        return current(spot_eur_per_kwh, hour_utc)

    return tariff


def insert_tariff_period(
    history: Sequence[Mapping[str, Any]],
    current: Mapping[str, Any],
    effective_from: date,
) -> list[dict[str, Any]]:
    """Archive ``current`` so a new tariff starts applying on ``effective_from``.

    ``history`` is the list of closed periods (each a tariff mapping with an
    ISO ``"until"`` date); ``current`` is the tariff in force after the last
    of them. The result closes whatever was in force on ``effective_from`` at
    that date and drops periods that would only have started on or after it,
    because the new tariff supersedes them. Earlier periods are untouched.
    """
    eff = effective_from.isoformat()
    kept: list[dict[str, Any]] = []
    prev_until: str | None = None
    for period in sorted(history, key=lambda p: str(p["until"])):
        if prev_until is not None and prev_until >= eff:
            return kept
        if str(period["until"]) > eff:
            kept.append({**period, "until": eff})
            return kept
        kept.append(dict(period))
        prev_until = str(period["until"])
    if prev_until is None or prev_until < eff:
        kept.append({**current, "until": eff})
    return kept


def cost_for_window(
    intervals: Iterable[AccountingInterval],
    kind: Kind,
    start_utc: datetime,
    end_utc: datetime,
    prices: Mapping[datetime, float],
    tariff: Tariff,
    hourly_fee: HourlyFee | None = None,
) -> tuple[float, int]:
    """Price the intervals whose hour starts in [start_utc, end_utc).

    ``hourly_fee`` adds a fixed share (e.g. of a monthly subscription) to every
    priced hour that has data.

    Sub-hourly intervals are summed into their hour first (the tariff and the
    price are hourly). Returns ``(cost, hours_without_price)``: hours with no
    cached price are skipped and counted so callers can flag an incomplete
    total instead of silently under-reporting.
    """
    hourly: dict[datetime, float] = {}
    for ival in intervals:
        value = interval_value(ival, kind)
        if value is None:
            continue
        hour = ival.period_start.replace(minute=0, second=0, microsecond=0)
        if start_utc <= hour < end_utc:
            hourly[hour] = hourly.get(hour, 0.0) + float(value)
    total, missing = 0.0, 0
    for hour, kwh in hourly.items():
        price = prices.get(hour)
        if price is None:
            missing += 1
            continue
        total += kwh * tariff(price, hour) + (hourly_fee(hour) if hourly_fee else 0.0)
    return total, missing


def compute_cost_rows(
    intervals: list[AccountingInterval],
    kind: Kind,
    prices: dict[datetime, float],
    tariff: Tariff,
    prior_sum: float,
    hourly_fee: HourlyFee | None = None,
) -> list[StatisticData]:
    """Build cumulative-sum cost rows from raw intervals and an hourly price map.

    Buckets intervals into hours (matching ``statistics.compute_statistic_rows``),
    multiplies each hour's summed kWh by ``tariff(prices[hour], hour)``, and
    produces a running cumulative-sum series in EUR. Skips hours where the price
    is missing (NPS gap or future hour). Rounds to 4 decimal places (€0.0001).
    """
    hourly: dict[Any, float] = {}
    for ival in intervals:
        value = interval_value(ival, kind)
        if value is None:
            continue
        bucket = ival.period_start.replace(minute=0, second=0, microsecond=0)
        hourly[bucket] = hourly.get(bucket, 0.0) + float(value)

    return compute_cost_rows_from_hourly(hourly, prices, tariff, prior_sum, hourly_fee)


def compute_cost_rows_from_hourly(
    hourly_energy: dict[datetime, float],
    prices: dict[datetime, float],
    tariff: Tariff,
    prior_sum: float,
    hourly_fee: HourlyFee | None = None,
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
        fee = hourly_fee(start) if hourly_fee else 0.0
        cost = round(hourly_energy[start] * tariff(price, start) + fee, 4)
        running = round(running + cost, 4)
        rows.append({"start": start, "state": running, "sum": running})
    return rows
