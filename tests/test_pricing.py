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
