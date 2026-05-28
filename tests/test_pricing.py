"""Tests for pricing pure functions."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from custom_components.estfeed.api import AccountingInterval
from custom_components.estfeed.const import Kind
from custom_components.estfeed.pricing import (
    apply_tariff,
    compute_cost_rows,
    compute_cost_rows_from_hourly,
    make_tariff,
)


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
    assert tariff(0.05) == pytest.approx(0.068)


def test_make_tariff_captures_arguments():
    tariff_22 = make_tariff(22.0, 0.0)
    tariff_24 = make_tariff(24.0, 0.0)
    assert tariff_22(0.05) != tariff_24(0.05)


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
