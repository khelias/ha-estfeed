"""Tests for pricing pure functions."""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from custom_components.estfeed.api import AccountingInterval
from custom_components.estfeed.const import Kind
from custom_components.estfeed.pricing import (
    GridTariff,
    apply_tariff,
    compute_cost_rows,
    compute_cost_rows_from_hourly,
    cost_for_window,
    make_tariff,
)

HOUR = datetime(2026, 5, 21, 10, tzinfo=UTC)  # Thu, a plain weekday hour
TALLINN = ZoneInfo("Europe/Tallinn")


def _interval(
    hour: int,
    minute: int = 0,
    consumption: float | None = None,
    production: float | None = None,
) -> AccountingInterval:
    return AccountingInterval(
        period_start=datetime(2026, 5, 21, hour, minute, tzinfo=UTC),
        consumption_kwh=consumption,
        production_kwh=production,
        consumption_m3=None,
        production_m3=None,
    )


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
    assert tariff(0.05, HOUR) == pytest.approx(0.068)


def test_make_tariff_captures_arguments():
    tariff_22 = make_tariff(22.0, 0.0)
    tariff_24 = make_tariff(24.0, 0.0)
    assert tariff_22(0.05, HOUR) != tariff_24(0.05, HOUR)


def test_compute_cost_rows_single_hour():
    intervals = [_interval(10, consumption=2.0)]
    prices = {datetime(2026, 5, 21, 10, tzinfo=UTC): 0.05}
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
    intervals = [_interval(10, consumption=2.0)]
    prices = {datetime(2026, 5, 21, 10, tzinfo=UTC): 0.05}
    tariff = make_tariff(vat_percent=22.0, margin_eur_per_kwh=0.01)
    rows = compute_cost_rows(intervals, Kind.CONSUMPTION, prices, tariff, prior_sum=0.0)
    # tariff(0.05) = 0.05*1.22 + 0.01 = 0.071; * 2.0 kWh = 0.142
    assert rows[0]["sum"] == pytest.approx(0.142)


def test_compute_cost_rows_skips_missing_price_hours():
    intervals = [
        _interval(10, consumption=1.0),
        _interval(11, consumption=1.0),
        _interval(12, consumption=1.0),
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
        _interval(10, consumption=None),
        _interval(11, consumption=2.0),
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
    intervals = [_interval(10, consumption=2.0)]
    prices = {datetime(2026, 5, 21, 10, tzinfo=UTC): 0.05}
    tariff = make_tariff(0.0, 0.0)
    rows = compute_cost_rows(intervals, Kind.CONSUMPTION, prices, tariff, prior_sum=100.0)
    # 100.0 + 0.10
    assert rows[0]["sum"] == pytest.approx(100.10)


def test_compute_cost_rows_production_kind():
    intervals = [_interval(10, production=1.5)]
    prices = {datetime(2026, 5, 21, 10, tzinfo=UTC): 0.04}
    tariff = make_tariff(0.0, 0.0)
    rows = compute_cost_rows(intervals, Kind.PRODUCTION, prices, tariff, prior_sum=0.0)
    assert rows[0]["sum"] == pytest.approx(0.06)


def test_compute_cost_rows_sorts_by_start():
    intervals = [
        _interval(12, consumption=1.0),
        _interval(10, consumption=1.0),
        _interval(11, consumption=1.0),
    ]
    prices = {datetime(2026, 5, 21, h, tzinfo=UTC): 0.05 for h in (10, 11, 12)}
    tariff = make_tariff(0.0, 0.0)
    rows = compute_cost_rows(intervals, Kind.CONSUMPTION, prices, tariff, prior_sum=0.0)
    starts = [r["start"] for r in rows]
    assert starts == sorted(starts)


def test_compute_cost_rows_rounds_to_four_decimals():
    # 0.333333 kWh * 0.05 = 0.0166666...; rounds to 0.0167
    intervals = [_interval(10, consumption=0.333333)]
    prices = {datetime(2026, 5, 21, 10, tzinfo=UTC): 0.05}
    tariff = make_tariff(0.0, 0.0)
    rows = compute_cost_rows(intervals, Kind.CONSUMPTION, prices, tariff, prior_sum=0.0)
    assert rows[0]["sum"] == pytest.approx(0.0167)


def test_compute_cost_rows_from_hourly_basic():
    # Two hours of energy, flat 0.05 €/kWh, no VAT/margin → cumulative cost.
    hourly = {
        datetime(2026, 5, 21, 10, tzinfo=UTC): 2.0,
        datetime(2026, 5, 21, 11, tzinfo=UTC): 3.0,
    }
    prices = {
        datetime(2026, 5, 21, 10, tzinfo=UTC): 0.05,
        datetime(2026, 5, 21, 11, tzinfo=UTC): 0.05,
    }
    tariff = make_tariff(0.0, 0.0)
    rows = compute_cost_rows_from_hourly(hourly, prices, tariff, prior_sum=0.0)
    assert [r["sum"] for r in rows] == [pytest.approx(0.1), pytest.approx(0.25)]


def test_compute_cost_rows_from_hourly_skips_missing_price():
    hourly = {
        datetime(2026, 5, 21, 10, tzinfo=UTC): 2.0,
        datetime(2026, 5, 21, 11, tzinfo=UTC): 3.0,
    }
    # Only hour 11 has a price.
    prices = {datetime(2026, 5, 21, 11, tzinfo=UTC): 0.05}
    tariff = make_tariff(0.0, 0.0)
    rows = compute_cost_rows_from_hourly(hourly, prices, tariff, prior_sum=0.0)
    assert len(rows) == 1
    assert rows[0]["start"] == datetime(2026, 5, 21, 11, tzinfo=UTC)
    assert rows[0]["sum"] == pytest.approx(0.15)


def test_compute_cost_rows_delegates_to_hourly_builder():
    # compute_cost_rows must produce identical output to first bucketing
    # intervals then calling the hourly builder.
    intervals = [
        _interval(10, 0, consumption=0.5),
        _interval(10, 15, consumption=0.5),
        _interval(11, 0, consumption=1.0),
    ]
    prices = {datetime(2026, 5, 21, h, tzinfo=UTC): 0.05 for h in (10, 11)}
    tariff = make_tariff(22.0, 0.0)
    via_intervals = compute_cost_rows(intervals, Kind.CONSUMPTION, prices, tariff, prior_sum=0.0)
    via_hourly = compute_cost_rows_from_hourly(
        {
            datetime(2026, 5, 21, 10, tzinfo=UTC): 1.0,
            datetime(2026, 5, 21, 11, tzinfo=UTC): 1.0,
        },
        prices,
        tariff,
        prior_sum=0.0,
    )
    assert via_intervals == via_hourly


def test_apply_tariff_grid_and_fees_are_taxed_with_spot():
    # (0.05 + 0.0369 + 0.0219) * 1.24 + 0.0047: an Estonian invoice stack
    assert apply_tariff(
        0.05,
        vat_percent=24.0,
        margin_eur_per_kwh=0.0047,
        grid_eur_per_kwh=0.0369,
        fees_eur_per_kwh=0.0219,
    ) == pytest.approx(0.1088 * 1.24 + 0.0047)


def _grid(**kw) -> GridTariff:
    base = {"day_eur_per_kwh": 0.04, "night_eur_per_kwh": 0.02, "tz": TALLINN}
    return GridTariff(**{**base, **kw})


def test_grid_tariff_weekday_day_and_night_window_wraps_midnight():
    grid = _grid(night_on_weekends=False, night_on_holidays=False)
    # Thu 2026-05-21, Tallinn is UTC+3 in May: 09 UTC = 12 local, 20 UTC = 23 local
    assert grid.rate(datetime(2026, 5, 21, 9, tzinfo=UTC)) == 0.04
    assert grid.rate(datetime(2026, 5, 21, 20, tzinfo=UTC)) == 0.02
    # boundaries: 06:59 local is night, 07:00 local is day, 22:00 local is night
    assert grid.is_night(datetime(2026, 5, 21, 3, tzinfo=UTC))
    assert not grid.is_night(datetime(2026, 5, 21, 4, tzinfo=UTC))
    assert grid.is_night(datetime(2026, 5, 21, 19, tzinfo=UTC))


def test_grid_tariff_night_window_without_wrap():
    grid = _grid(night_start_hour=0, night_end_hour=6, night_on_weekends=False)
    assert grid.is_night(datetime(2026, 5, 20, 22, tzinfo=UTC))  # 01:00 local Thu
    assert not grid.is_night(datetime(2026, 5, 21, 3, tzinfo=UTC))  # 06:00 local


def test_grid_tariff_equal_start_and_end_means_no_night_window():
    grid = _grid(night_start_hour=7, night_end_hour=7, night_on_weekends=False)
    assert not grid.is_night(datetime(2026, 5, 21, 0, tzinfo=UTC))


def test_grid_tariff_weekends_and_holidays():
    holiday = date(2026, 8, 20)  # Thu, Estonian public holiday
    grid = _grid(holidays={holiday})
    assert grid.is_night(datetime(2026, 5, 23, 9, tzinfo=UTC))  # Sat noon
    assert grid.is_night(datetime(2026, 8, 20, 9, tzinfo=UTC))  # holiday noon
    assert not grid.is_night(datetime(2026, 8, 19, 9, tzinfo=UTC))  # Wed noon
    off = _grid(night_on_weekends=False, night_on_holidays=False, holidays={holiday})
    assert not off.is_night(datetime(2026, 5, 23, 9, tzinfo=UTC))
    assert not off.is_night(datetime(2026, 8, 20, 9, tzinfo=UTC))


def test_grid_tariff_defaults_are_neutral():
    # No rates configured: the grid fee is zero whatever the hour, so the
    # tariff reduces to the pre-0.3 spot x VAT + margin.
    assert GridTariff().rate(datetime(2026, 5, 23, 9, tzinfo=UTC)) == 0.0
    tariff = make_tariff(22.0, 0.01)
    assert tariff(0.05, datetime(2026, 5, 23, 9, tzinfo=UTC)) == pytest.approx(0.071)


def test_make_tariff_picks_rate_per_hour():
    tariff = make_tariff(24.0, 0.0, fees_eur_per_kwh=0.01, grid=_grid(night_on_weekends=False))
    day = tariff(0.05, datetime(2026, 5, 21, 9, tzinfo=UTC))
    night = tariff(0.05, datetime(2026, 5, 21, 20, tzinfo=UTC))
    assert day == pytest.approx((0.05 + 0.04 + 0.01) * 1.24)
    assert night == pytest.approx((0.05 + 0.02 + 0.01) * 1.24)


def test_compute_cost_rows_from_hourly_passes_hour_to_tariff():
    seen: list[datetime] = []

    def tariff(spot: float, hour: datetime) -> float:
        seen.append(hour)
        return spot

    hourly = {datetime(2026, 5, 21, h, tzinfo=UTC): 1.0 for h in (10, 11)}
    prices = {datetime(2026, 5, 21, h, tzinfo=UTC): 0.05 for h in (10, 11)}
    compute_cost_rows_from_hourly(hourly, prices, tariff, prior_sum=0.0)
    assert seen == sorted(hourly)


def test_cost_for_window_prices_only_hours_inside_window():
    intervals = [_interval(h, consumption=2.0) for h in (9, 10, 11, 12)]
    prices = {datetime(2026, 5, 21, h, tzinfo=UTC): 0.05 for h in (9, 10, 11, 12)}
    tariff = make_tariff(0.0, 0.0)
    total, missing = cost_for_window(
        intervals,
        Kind.CONSUMPTION,
        datetime(2026, 5, 21, 10, tzinfo=UTC),
        datetime(2026, 5, 21, 12, tzinfo=UTC),
        prices,
        tariff,
    )
    # hours 10 and 11 only: 2 * 2.0 kWh * 0.05
    assert total == pytest.approx(0.20)
    assert missing == 0


def test_cost_for_window_sums_quarters_and_counts_missing_prices():
    intervals = [
        _interval(10, 0, consumption=0.5),
        _interval(10, 15, consumption=0.5),
        _interval(11, 0, consumption=1.0),  # no price for 11:00
        _interval(12, 0, consumption=None),  # unsettled, ignored
    ]
    prices = {datetime(2026, 5, 21, 10, tzinfo=UTC): 0.10}
    tariff = make_tariff(24.0, 0.01)
    total, missing = cost_for_window(
        intervals,
        Kind.CONSUMPTION,
        datetime(2026, 5, 21, 0, tzinfo=UTC),
        datetime(2026, 5, 22, 0, tzinfo=UTC),
        prices,
        tariff,
    )
    assert total == pytest.approx(1.0 * (0.10 * 1.24 + 0.01))
    assert missing == 1


def test_cost_for_window_uses_hourly_tariff():
    grid = _grid(night_on_weekends=False)
    tariff = make_tariff(0.0, 0.0, grid=grid)
    # 09 UTC = 12 local (day, 0.04), 20 UTC = 23 local (night, 0.02); spot 0 isolates the grid fee
    intervals = [_interval(9, consumption=1.0), _interval(20, consumption=1.0)]
    prices = {datetime(2026, 5, 21, h, tzinfo=UTC): 0.0 for h in (9, 20)}
    total, _ = cost_for_window(
        intervals,
        Kind.CONSUMPTION,
        datetime(2026, 5, 21, 0, tzinfo=UTC),
        datetime(2026, 5, 22, 0, tzinfo=UTC),
        prices,
        tariff,
    )
    assert total == pytest.approx(0.06)


def test_cost_for_window_production_kind_empty_when_no_production():
    intervals = [_interval(10, consumption=1.0)]
    prices = {datetime(2026, 5, 21, 10, tzinfo=UTC): 0.05}
    total, missing = cost_for_window(
        intervals,
        Kind.PRODUCTION,
        datetime(2026, 5, 21, 0, tzinfo=UTC),
        datetime(2026, 5, 22, 0, tzinfo=UTC),
        prices,
        make_tariff(0.0, 0.0),
    )
    assert (total, missing) == (0.0, 0)
