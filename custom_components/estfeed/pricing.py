"""Pure pricing helpers: tariff math and cost row construction."""

from __future__ import annotations

from collections.abc import Callable


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
